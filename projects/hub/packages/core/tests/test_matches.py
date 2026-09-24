"""REB-387 phase 2: a match written from a card and a request, and the documents it
generates, never sent. Every test hands `FakeRenderer`: the real typesetting is
`test_contract_render.py`'s."""

import threading
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

import pytest
from fakes_contracts import FailingRenderer, FakeRenderer
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import Engine, func, select, text
from sqlalchemy.orm import Session

from rebase_core import matches as matches_module
from rebase_core.audit import AdminActionService
from rebase_core.companies import CompanyService
from rebase_core.contract_schemas import (
    ClienteData,
    FiscalData,
    LetteraFields,
    MatchCreate,
    MatchListItem,
)
from rebase_core.contracts.fields import FIELD, TERM, ContractFailed, Value
from rebase_core.contracts.render import Rendered, Renderer, text_path
from rebase_core.db import session_factory
from rebase_core.errors import InvalidState, NotFound, ValidationFailed
from rebase_core.fiscal import FiscalService
from rebase_core.freelancers import FreelancerService
from rebase_core.matches import MatchService
from rebase_core.models import ContractDocument, Freelancer, Match, User
from rebase_core.schemas import CompanyCreate, FreelancerCreate, StatusChange

PDF = b"%PDF-1.7\n1 0 obj<<>>endobj\n%%EOF\n"
TODAY = date(2026, 9, 23)
# Fiction, like the public example: rebase's own fields as the setting would carry them.
SIGNER: dict[str, Value] = {
    "rebase-sede": "Milano",
    "rebase-cf": "00000000000",
    "rebase-piva": "00000000000",
    "rebase-codice-destinatario": "0000000",
    "rebase-pec": "rebase@pec.example",
    "rebase-rappresentante": "Nome Cognome",
}
# "signups" is here (Task 6's controller pre-flight scan) so `test_matches_api.py`'s own
# fixture can clear the same tables in the same order: the two lists must not drift.
TABLES = (
    "admin_actions",
    "contract_documents",
    "matches",
    "contract_letter_counters",
    "freelancer_fiscal",
    "comments",
    "freelancers",
    "companies",
    "users",
    "signups",
)


@pytest.fixture
def clean(hub_session: Session) -> Iterator[Session]:
    yield hub_session
    hub_session.rollback()
    for table in TABLES:
        hub_session.execute(text(f"DELETE FROM {table}"))
    hub_session.commit()


def _admin(session: Session) -> UUID:
    admin = User(email="ivan@rebase.it", nome="Ivan", cognome="", role="admin")
    session.add(admin)
    session.commit()
    return admin.id


def _card(session: Session) -> UUID:
    row, _ = FreelancerService(session).apply(
        FreelancerCreate(
            nome="Ada",
            cognome="Lovelace",
            email="ada@studio.it",
            tariffa_giornaliera=Decimal("450"),
            posizione="Backend developer",
            remoto="remoto",
        ),
        PDF,
        "cv.pdf",
        "application/pdf",
    )
    return row.id


def _request(session: Session, **change: object) -> UUID:
    """A request whose budget is a number that must appear nowhere in the flow."""
    payload: dict[str, object] = {
        "nome_azienda": "ACME Srl",
        "referente_nome": "Wile",
        "referente_cognome": "E.",
        "email": "wile@acme.it",
        "telefono": "+39 345 1234567",
        "figura_richiesta": "Backend developer",
        "progetto": "Le API del prodotto, per tre mesi.",
        "periodo_da": date(2026, 10, 1),
        "durata": "3 mesi",
        "budget_giornaliero": Decimal("777.77"),
        "remoto": "ibrido",
        "giorni_presenza": 2,
        "numero_risorse": 1,
    }
    payload.update(change)
    row, _ = CompanyService(session).request(CompanyCreate(**payload))  # type: ignore[arg-type]
    return row.id


def _fiscal(session: Session, freelancer_id: UUID, admin_id: UUID) -> None:
    FiscalService(session).save(
        freelancer_id,
        FiscalData(
            codice_fiscale="LVLDAA85T50H501Z",
            partita_iva="01234567890",
            domicilio="Via Roma 1, Milano",
        ),
        admin_id,
    )


def _setup(session: Session) -> tuple[UUID, UUID, UUID]:
    admin_id, freelancer_id, company_id = _admin(session), _card(session), _request(session)
    _fiscal(session, freelancer_id, admin_id)
    return admin_id, freelancer_id, company_id


def _body(company_id: UUID) -> MatchCreate:
    return MatchCreate(
        company_id=company_id,
        cliente=ClienteData(
            cliente_ragione_sociale="ACME S.r.l.", cliente_piva="01234567890", cliente_sede="Milano"
        ),
        lettera=LetteraFields(
            ruolo="Backend developer",
            attivita="Le API del prodotto.",
            data_inizio=date(2026, 10, 1),
            compenso=Decimal("450"),
            giorni_pagamento=30,
            fine_mese=True,
        ),
    )


def _service(
    session: Session, renderer: Renderer | None = None, today: date = TODAY
) -> MatchService:
    return MatchService(session, renderer or FakeRenderer(), SIGNER, today=lambda: today)


def _framework(
    session: Session,
    freelancer_id: UUID,
    admin_id: UUID,
    *,
    stato: str = "firmato",
    signed_at: datetime | None = datetime(2026, 10, 1, 9, 0, tzinfo=UTC),
    notice_at: datetime | None = None,
    text_version: str = "0.1",
) -> ContractDocument:
    """A framework agreement as phase 3 will leave one: only a signature makes these."""
    document = ContractDocument(
        kind="quadro",
        freelancer_id=freelancer_id,
        text_version=text_version,
        testo_bozza=False,
        data={},
        pdf=b"%PDF-quadro",
        stato=stato,
        signed_at=signed_at,
        notice_at=notice_at,
        created_by=admin_id,
    )
    session.add(document)
    session.commit()
    return document


def _documents(
    session: Session, freelancer_id: UUID, kind: str | None = None
) -> list[ContractDocument]:
    stmt = select(ContractDocument).where(ContractDocument.freelancer_id == freelancer_id)
    if kind is not None:
        stmt = stmt.where(ContractDocument.kind == kind)
    return list(session.scalars(stmt.order_by(ContractDocument.created_at, ContractDocument.id)))


def test_the_first_match_writes_the_framework_agreement_and_a_letter_that_waits_for_it(
    clean: Session,
) -> None:
    admin_id, freelancer_id, company_id = _setup(clean)
    renderer = FakeRenderer()
    match = _service(clean, renderer).create(freelancer_id, _body(company_id), admin_id)

    assert match.stato == "bozza"
    assert (match.lettera.numero, match.lettera.stato) == ("2026-001", "in_attesa")
    quadro, lettera = _documents(clean, freelancer_id)
    assert (quadro.kind, quadro.stato, quadro.match_id, quadro.numero) == (
        "quadro",
        "generato",
        None,
        None,
    )
    assert (quadro.text_version, quadro.testo_bozza) == ("0.1", True)
    assert (lettera.kind, lettera.match_id) == ("lettera", match.id)
    assert lettera.data["data-contratto-quadro"] is None
    assert [document for document, _ in renderer.calls] == [
        "contratto-quadro",
        "lettera-di-incarico",
    ]
    assert [a.kind for a in AdminActionService(clean).timeline("match", match.id)] == [
        "match_created"
    ]


def test_the_framework_prints_the_freelancer_and_rebases_signer_and_leaves_the_signing_blanks(
    clean: Session,
) -> None:
    admin_id, freelancer_id, company_id = _setup(clean)
    renderer = FakeRenderer()
    _service(clean, renderer).create(freelancer_id, _body(company_id), admin_id)
    (quadro_doc, quadro), (letter_doc, letter) = renderer.calls
    assert quadro["professionista-nome"] == "Ada Lovelace"
    assert quadro["professionista-cf"] == "LVLDAA85T50H501Z"
    assert quadro["professionista-email"] == "ada@studio.it"
    assert quadro["professionista-pec"] == "non indicata"
    assert quadro["rebase-rappresentante"] == "Nome Cognome"
    assert quadro["luogo-firma"] == "firmato elettronicamente"
    assert quadro["firma-rebase"] == "Documento emesso da rebase il 23 settembre 2026"
    assert quadro["data-firma"] is None and quadro["firma-professionista"] is None
    for document, data in ((quadro_doc, quadro), (letter_doc, letter)):
        asked = set(FIELD.findall(text_path(document).read_text(encoding="utf-8")))
        assert asked - {TERM} <= set(data), document
    assert letter["professionista-piva"] == "01234567890"
    assert letter["cliente-ragione-sociale"] == "ACME S.r.l."
    assert letter["compenso"] == 450


def test_with_an_active_framework_only_the_letter_is_written_and_it_cites_the_signature_date(
    clean: Session,
) -> None:
    """Review Focus 3: 23:30 UTC on 30 September is 1 October in Rome."""
    admin_id, freelancer_id, company_id = _setup(clean)
    _framework(clean, freelancer_id, admin_id, signed_at=datetime(2026, 9, 30, 23, 30, tzinfo=UTC))
    renderer = FakeRenderer()
    match = _service(clean, renderer).create(freelancer_id, _body(company_id), admin_id)
    assert match.lettera.stato == "generato"
    assert [document for document, _ in renderer.calls] == ["lettera-di-incarico"]
    assert renderer.calls[0][1]["data-contratto-quadro"] == "1° ottobre 2026"
    assert len(_documents(clean, freelancer_id, "quadro")) == 1


@pytest.mark.parametrize("stato", ["firmato", "disdetto"])
def test_a_framework_with_a_notice_recorded_is_no_longer_active(clean: Session, stato: str) -> None:
    admin_id, freelancer_id, company_id = _setup(clean)
    _framework(
        clean, freelancer_id, admin_id, stato=stato, notice_at=datetime(2026, 11, 1, tzinfo=UTC)
    )
    match = _service(clean).create(freelancer_id, _body(company_id), admin_id)
    assert match.lettera.stato == "in_attesa"
    assert [d.stato for d in _documents(clean, freelancer_id, "quadro")] == [stato, "generato"]


def test_an_unsent_framework_from_an_earlier_draft_is_replaced_not_duplicated(
    clean: Session,
) -> None:
    admin_id, freelancer_id, company_id = _setup(clean)
    service = _service(clean)
    service.create(freelancer_id, _body(company_id), admin_id)
    second = service.create(freelancer_id, _body(company_id), admin_id)
    stale, current = _documents(clean, freelancer_id, "quadro")
    assert [stale.stato, current.stato] == [
        "annullato",
        "generato",
    ]
    entries = AdminActionService(clean).timeline("match", second.id)
    created = next(a for a in entries if a.kind == "match_created")
    assert created.payload["quadri_annullati"] == [str(stale.id)]


def test_a_framework_already_out_for_signature_is_waited_for_not_replaced(clean: Session) -> None:
    admin_id, freelancer_id, company_id = _setup(clean)
    _framework(clean, freelancer_id, admin_id, stato="inviato", signed_at=None)
    renderer = FakeRenderer()
    match = _service(clean, renderer).create(freelancer_id, _body(company_id), admin_id)
    assert match.lettera.stato == "in_attesa"
    assert [document for document, _ in renderer.calls] == ["lettera-di-incarico"]


def test_letters_are_numbered_per_year_in_the_order_they_are_written(clean: Session) -> None:
    admin_id, freelancer_id, company_id = _setup(clean)
    numbers = [
        _service(clean, today=day).create(freelancer_id, _body(company_id), admin_id).lettera.numero
        for day in (date(2026, 12, 31), date(2026, 12, 31), date(2027, 1, 1))
    ]
    assert numbers == ["2026-001", "2026-002", "2027-001"]


def test_a_render_that_fails_takes_no_number_and_leaves_nothing_behind(clean: Session) -> None:
    admin_id, freelancer_id, company_id = _setup(clean)
    _service(clean).create(freelancer_id, _body(company_id), admin_id)
    with pytest.raises(ContractFailed):
        _service(clean, FailingRenderer(document="lettera-di-incarico")).create(
            freelancer_id, _body(company_id), admin_id
        )
    assert clean.scalar(select(func.count()).select_from(Match)) == 1
    # The earlier framework was not replaced by a generation that failed.
    assert [d.stato for d in _documents(clean, freelancer_id, "quadro")] == ["generato"]
    assert (
        _service(clean).create(freelancer_id, _body(company_id), admin_id).lettera.numero
        == "2026-002"
    )


def test_the_client_budget_reaches_neither_the_prefill_nor_the_letter(clean: Session) -> None:
    # The whole amount with its dot: a bare "777" could turn up inside a UUID's hex or a
    # timestamp's microseconds and fail this test for nothing.
    admin_id, freelancer_id, company_id = _setup(clean)
    service = _service(clean)
    assert "777.77" not in service.prefill(freelancer_id, company_id).model_dump_json()
    match = service.create(freelancer_id, _body(company_id), admin_id)
    letter = clean.get(ContractDocument, match.lettera.id)
    assert letter is not None
    assert not any("777.77" in str(value) for value in letter.data.values())
    assert not any("budget" in key for key in letter.data)


def test_the_prefill_suggests_from_the_request_the_card_and_rebases_defaults(
    clean: Session,
) -> None:
    admin_id, freelancer_id, company_id = _setup(clean)
    prefill = _service(clean).prefill(freelancer_id, company_id)
    lettera = prefill.lettera
    assert (lettera.ruolo, lettera.attivita) == (
        "Backend developer",
        "Le API del prodotto, per tre mesi.",
    )
    assert (lettera.data_inizio, lettera.impegno) == (date(2026, 10, 1), "3 mesi")
    assert lettera.luogo == "in parte da remoto, con 2 giornate a settimana presso il Cliente"
    assert lettera.referente_cliente == "Wile E."
    assert (lettera.modalita, lettera.unita) == ("a giornata", "a giornata")
    assert lettera.compenso == Decimal("450.00")
    assert (lettera.giorni_pagamento, lettera.fine_mese) == (30, True)
    assert prefill.cliente.cliente_ragione_sociale == "ACME Srl"
    assert prefill.cliente.cliente_piva is None
    assert prefill.fiscale is not None and prefill.fiscale.partita_iva == "01234567890"
    assert (prefill.quadro_attivo, prefill.quadro_necessario, prefill.lettera_in_attesa) == (
        None,
        True,
        True,
    )


def test_a_card_without_a_day_rate_prefills_no_fee_and_the_letter_needs_one(clean: Session) -> None:
    """Review Focus 2: a card drafted from a signup has no rate yet."""
    admin_id, freelancer_id, company_id = _setup(clean)
    card = clean.get(Freelancer, freelancer_id)
    assert card is not None
    card.tariffa_giornaliera = None
    clean.commit()
    assert _service(clean).prefill(freelancer_id, company_id).lettera.compenso is None
    with pytest.raises(PydanticValidationError, match="compenso"):
        LetteraFields(
            ruolo="Backend developer",
            attivita="API",
            data_inizio=date(2026, 10, 1),
            giorni_pagamento=30,
            fine_mese=True,
        )  # type: ignore[call-arg]


def test_the_client_data_come_back_from_the_same_company_users_last_match(clean: Session) -> None:
    admin_id, freelancer_id, company_id = _setup(clean)
    _service(clean).create(freelancer_id, _body(company_id), admin_id)
    # The same referente files a second request: another row, the same client.
    second = _request(clean, nome_azienda="ACME", figura_richiesta="Frontend developer")
    cliente = _service(clean).prefill(freelancer_id, second).cliente
    assert (cliente.cliente_ragione_sociale, cliente.cliente_piva, cliente.cliente_sede) == (
        "ACME S.r.l.",
        "01234567890",
        "Milano",
    )


def test_a_cancelled_matchs_client_is_never_prefilled(clean: Session) -> None:
    admin_id, freelancer_id, company_id = _setup(clean)
    match = _service(clean).create(freelancer_id, _body(company_id), admin_id)
    _service(clean).cancel(match.id, admin_id)
    # The same referente files a second request: another row, the same user.
    second = _request(clean, nome_azienda="ACME", figura_richiesta="Frontend developer")
    cliente = _service(clean).prefill(freelancer_id, second).cliente
    assert (cliente.cliente_ragione_sociale, cliente.cliente_piva, cliente.cliente_sede) == (
        "ACME",
        None,
        None,
    )


def test_a_closed_request_can_be_neither_previewed_nor_matched(clean: Session) -> None:
    admin_id, freelancer_id, company_id = _setup(clean)
    CompanyService(clean).set_status(company_id, StatusChange(stato="chiuso"))
    service = _service(clean)
    for call in (
        lambda: service.create(freelancer_id, _body(company_id), admin_id),
        lambda: service.preview(freelancer_id, _body(company_id), "lettera"),
    ):
        with pytest.raises(ValidationFailed) as refused:
            call()
        assert refused.value.details["field"] == "company_id"


def test_a_match_needs_the_tax_data_saved_first(clean: Session) -> None:
    admin_id, freelancer_id, company_id = _admin(clean), _card(clean), _request(clean)
    with pytest.raises(ValidationFailed) as refused:
        _service(clean).create(freelancer_id, _body(company_id), admin_id)
    assert refused.value.details["field"] == "fiscale"


def test_a_deleted_card_or_request_is_not_found(clean: Session) -> None:
    admin_id, freelancer_id, company_id = _setup(clean)
    CompanyService(clean).soft_delete(company_id, admin_id)
    with pytest.raises(NotFound):
        _service(clean).prefill(freelancer_id, company_id)
    FreelancerService(clean).soft_delete(freelancer_id, admin_id)
    with pytest.raises(NotFound):
        _service(clean).for_freelancer(freelancer_id)


def test_cancelling_a_draft_cancels_its_letter_and_leaves_the_framework(clean: Session) -> None:
    admin_id, freelancer_id, company_id = _setup(clean)
    service = _service(clean)
    match = service.create(freelancer_id, _body(company_id), admin_id)
    cancelled = service.cancel(match.id, admin_id)
    assert (cancelled.stato, cancelled.lettera.stato) == ("annullato", "annullato")
    assert cancelled.cancelled_at is not None
    assert [d.stato for d in _documents(clean, freelancer_id, "quadro")] == ["generato"]
    with pytest.raises(InvalidState):
        service.cancel(match.id, admin_id)
    kinds = [a.kind for a in AdminActionService(clean).timeline("match", match.id)]
    assert kinds == ["match_cancelled", "match_created"]


def test_only_an_active_match_can_be_closed(clean: Session) -> None:
    admin_id, freelancer_id, company_id = _setup(clean)
    service = _service(clean)
    match = service.create(freelancer_id, _body(company_id), admin_id)
    with pytest.raises(InvalidState):
        service.close(match.id, admin_id)
    row = clean.get(Match, match.id)
    assert row is not None
    row.stato = "attivo"  # only a signed letter gets a match here (phase 3)
    clean.commit()
    assert service.close(match.id, admin_id).stato == "concluso"


def test_the_contracts_page_reads_the_active_framework_its_dates_and_the_matches_newest_first(
    clean: Session,
) -> None:
    admin_id, freelancer_id, company_id = _setup(clean)
    _framework(clean, freelancer_id, admin_id, text_version="0.0")
    service = _service(clean, today=date(2026, 12, 1))
    first = service.create(freelancer_id, _body(company_id), admin_id)
    second = service.create(freelancer_id, _body(company_id), admin_id)
    page = service.for_freelancer(freelancer_id)
    assert page.quadro is not None and page.quadro.attivo
    assert (page.quadro.rinnovo, page.quadro.ultimo_giorno_disdetta) == (
        date(2027, 10, 1),
        date(2027, 9, 1),
    )
    assert page.quadro.nuova_versione is True
    assert [m.id for m in page.matches] == [second.id, first.id]
    assert page.fiscale is not None


def test_a_match_stays_on_the_page_after_its_request_is_deleted(clean: Session) -> None:
    """Review Focus 4: a soft-deleted request still names the match that came from it."""
    admin_id, freelancer_id, company_id = _setup(clean)
    service = _service(clean)
    match = service.create(freelancer_id, _body(company_id), admin_id)
    CompanyService(clean).soft_delete(company_id, admin_id)
    assert [(m.id, m.nome_azienda) for m in service.for_freelancer(freelancer_id).matches] == [
        (match.id, "ACME Srl")
    ]
    assert service.get(match.id).nome_azienda == "ACME Srl"


def test_a_preview_renders_without_saving_numbering_or_replacing_anything(clean: Session) -> None:
    admin_id, freelancer_id, company_id = _setup(clean)
    renderer = FakeRenderer()
    service = _service(clean, renderer)
    service.create(freelancer_id, _body(company_id), admin_id)
    letter = service.preview(freelancer_id, _body(company_id), "lettera")
    assert letter.content.startswith(b"%PDF-")
    assert renderer.calls[-1][0] == "lettera-di-incarico"
    assert renderer.calls[-1][1]["numero"] is None
    service.preview(freelancer_id, _body(company_id), "quadro")
    assert renderer.calls[-1][0] == "contratto-quadro"
    assert clean.scalar(select(func.count()).select_from(Match)) == 1
    assert [d.stato for d in _documents(clean, freelancer_id, "quadro")] == ["generato"]
    assert service.create(freelancer_id, _body(company_id), admin_id).lettera.numero == "2026-002"


def test_a_document_downloads_as_its_own_pdf_and_a_missing_signed_copy_is_not_found(
    clean: Session,
) -> None:
    admin_id, freelancer_id, company_id = _setup(clean)
    service = _service(clean)
    match = service.create(freelancer_id, _body(company_id), admin_id)
    pdf = service.document_pdf(match.lettera.id)
    assert (pdf.filename, pdf.content[:5]) == ("lettera-di-incarico-2026-001.pdf", b"%PDF-")
    quadro = _documents(clean, freelancer_id, "quadro")[0]
    assert service.document_pdf(quadro.id).filename == "contratto-quadro-v0.1.pdf"
    with pytest.raises(NotFound):
        service.document_pdf(match.lettera.id, signed=True)


def test_two_admins_matching_the_same_freelancer_at_once_never_leave_two_open_frameworks(
    monkeypatch: pytest.MonkeyPatch, hub_engine: Engine, clean: Session
) -> None:
    """Review fix round 1: two real `create()` calls, on two sessions, for the same
    freelancer -- the shape of `test_two_letters_taken_at_once_get_two_numbers`, but for
    the framework agreement rather than the letter counter.

    Postgres itself already serializes the two `Match` inserts a beat later (both name
    the freelancer as a foreign key), so pausing an *unrelated* session on a bare
    `SELECT ... FOR UPDATE` and then calling `create()` on a second session -- blocked or
    not -- proves nothing about *this* fix: it blocks at the `Match` insert either way, fix
    or no fix, with the framework's fate already decided by then. What must actually be
    tested is that `create()`'s own transaction takes its lock *before* it ever reads
    `active_framework`/`pending_framework`, not merely somewhere before it commits.

    So this test drives two genuine `create()` calls: `next_letter_number` -- the next
    real statement after the fix's own lock, and one the fix's own docstring names by
    name -- is patched to pause the first session there, after everything upstream of it
    has already run once. The second session's `create()` is then started for real:
    without the fix it races ahead of the paused first one, reads "no active, no
    pending" same as the first did, and writes its own `generato` framework -- so when
    the first is released and finishes writing *its* framework from the same stale
    read, both commit and two open frameworks exist at once (RED). With the fix, the
    second cannot get past its own first statement (the same row lock, already held,
    uncommitted, by the first) until the first commits; only then does it see the
    first's `generato` framework, cancel it, and write its own -- exactly one open
    framework survives (GREEN)."""
    admin_id, freelancer_id, company_id = _setup(clean)
    factory = session_factory(hub_engine)
    first, second = factory(), factory()
    paused = threading.Event()
    release = threading.Event()
    real_next_letter_number = matches_module.next_letter_number

    def paced_next_letter_number(session: Session, year: int) -> str:
        if session is first:
            paused.set()
            assert release.wait(timeout=5), "the test never released the first create()"
        return real_next_letter_number(session, year)

    monkeypatch.setattr(matches_module, "next_letter_number", paced_next_letter_number)

    errors: list[BaseException] = []

    def run(session: Session) -> None:
        try:
            MatchService(session, FakeRenderer(), SIGNER, today=lambda: TODAY).create(
                freelancer_id, _body(company_id), admin_id
            )
        except BaseException as exc:  # noqa: BLE001 -- surfaced on the main thread below
            errors.append(exc)

    try:
        first_worker = threading.Thread(target=run, args=(first,))
        first_worker.start()
        assert paused.wait(timeout=5), "the first create() never reached next_letter_number"

        second_worker = threading.Thread(target=run, args=(second,))
        second_worker.start()
        second_worker.join(timeout=0.5)
        assert second_worker.is_alive(), (
            "the second create() read the freelancer while the first's transaction, "
            "past the same lock, was still open"
        )

        release.set()
        first_worker.join(timeout=5)
        second_worker.join(timeout=5)
        assert not first_worker.is_alive()
        assert not second_worker.is_alive()
        assert not errors, errors

        assert [q.stato for q in _documents(clean, freelancer_id, "quadro")] == [
            "annullato",
            "generato",
        ]
    finally:
        first.close()
        second.close()


@pytest.mark.parametrize("change", ["request_closed", "tax_data_corrected"])
def test_a_create_waiting_for_the_freelancers_lock_reads_the_request_and_tax_data_after_it(
    hub_engine: Engine, clean: Session, change: str
) -> None:
    """Greptile 4092036031: `create` used to read the tax data and check the request
    before taking the freelancer's row lock, so what another admin committed while it
    waited there -- a closed request, a corrected VAT number -- never reached it. Here
    another transaction holds that lock (a tax-data save or another match does), the
    creating session has already read both through a prefill, and the change commits
    while `create` waits: it must refuse the closed request, or print the new tax
    data."""
    admin_id, freelancer_id, company_id = _setup(clean)
    factory = session_factory(hub_engine)
    holder, creator = factory(), factory()
    outcome: list[object] = []
    service = MatchService(creator, FakeRenderer(), SIGNER, today=lambda: TODAY)
    service.prefill(freelancer_id, company_id)

    def run() -> None:
        try:
            outcome.append(service.create(freelancer_id, _body(company_id), admin_id))
        except BaseException as exc:  # noqa: BLE001 -- surfaced on the main thread below
            outcome.append(exc)

    worker = threading.Thread(target=run)
    try:
        holder.execute(
            select(Freelancer.id).where(Freelancer.id == freelancer_id).with_for_update()
        )
        worker.start()
        worker.join(timeout=0.5)
        assert worker.is_alive(), "create() did not wait for the freelancer's row lock"

        # Each commits the holder's transaction, which releases the lock.
        if change == "request_closed":
            CompanyService(holder).set_status(company_id, StatusChange(stato="chiuso"))
        else:
            FiscalService(holder).save(
                freelancer_id,
                FiscalData(
                    codice_fiscale="LVLDAA85T50H501Z",
                    partita_iva="09876543210",
                    domicilio="Corso Buenos Aires 2, Milano",
                ),
                admin_id,
            )
        worker.join(timeout=5)
        assert not worker.is_alive()

        [result] = outcome
        if change == "request_closed":
            assert isinstance(result, ValidationFailed), result
            assert result.details["field"] == "company_id"
            assert clean.scalar(select(func.count()).select_from(Match)) == 0
        else:
            assert not isinstance(result, BaseException), result
            quadro, lettera = _documents(clean, freelancer_id)
            assert quadro.data["professionista-piva"] == "09876543210"
            assert quadro.data["professionista-domicilio"] == "Corso Buenos Aires 2, Milano"
            assert lettera.data["professionista-piva"] == "09876543210"
    finally:
        holder.close()
        if worker.is_alive():
            worker.join(timeout=5)
        creator.close()


def test_closing_a_request_while_its_match_is_typeset_waits_for_the_match(
    hub_engine: Engine, clean: Session
) -> None:
    """Greptile 4092036031, the other half: the request is read under a shared lock
    that lasts to the commit, so an admin closing it while `create` typesets waits, and
    the draft is never saved for a request already closed."""
    admin_id, freelancer_id, company_id = _setup(clean)
    factory = session_factory(hub_engine)
    creator, closer = factory(), factory()
    rendering, release = threading.Event(), threading.Event()

    @dataclass
    class PausedRenderer(FakeRenderer):
        def render(self, document: str, data: Mapping[str, Value]) -> Rendered:
            if not rendering.is_set():
                rendering.set()
                assert release.wait(timeout=5), "the test never released the render"
            return super().render(document, data)

    errors: list[BaseException] = []

    def create() -> None:
        try:
            MatchService(creator, PausedRenderer(), SIGNER, today=lambda: TODAY).create(
                freelancer_id, _body(company_id), admin_id
            )
        except BaseException as exc:  # noqa: BLE001 -- surfaced on the main thread below
            errors.append(exc)

    def close() -> None:
        try:
            CompanyService(closer).set_status(company_id, StatusChange(stato="chiuso"))
        except BaseException as exc:  # noqa: BLE001 -- surfaced on the main thread below
            errors.append(exc)

    creating, closing = threading.Thread(target=create), threading.Thread(target=close)
    try:
        creating.start()
        assert rendering.wait(timeout=5), "create() never reached the render"

        closing.start()
        closing.join(timeout=0.5)
        assert closing.is_alive(), "the request was closed while its match was being typeset"

        release.set()
        creating.join(timeout=5)
        closing.join(timeout=5)
        assert not creating.is_alive()
        assert not closing.is_alive()
        assert not errors, errors
        assert clean.scalar(select(func.count()).select_from(Match)) == 1
    finally:
        release.set()
        for worker in (creating, closing):
            if worker.is_alive():
                worker.join(timeout=5)
        creator.close()
        closer.close()


# ---- the admin's «Match» list (REB-413) -------------------------------------------------


def _second_card(session: Session, **change: object) -> UUID:
    payload: dict[str, object] = {
        "nome": "Grace",
        "cognome": "Hopper",
        "email": "grace@studio.it",
        "tariffa_giornaliera": Decimal("500"),
        "posizione": "Frontend developer",
        "remoto": "remoto",
    }
    payload.update(change)
    row, _ = FreelancerService(session).apply(
        FreelancerCreate(**payload),  # type: ignore[arg-type]
        PDF,
        "cv.pdf",
        "application/pdf",
    )
    return row.id


def test_the_match_list_is_newest_first_with_its_letter_and_period(clean: Session) -> None:
    admin_id, freelancer_id, company_id = _setup(clean)
    service = _service(clean, today=date(2026, 12, 1))
    first = service.create(freelancer_id, _body(company_id), admin_id)
    second = service.create(freelancer_id, _body(company_id), admin_id)
    page = service.list_all(stato=None, q=None, limit=100, offset=0)
    assert page.totale == 2
    assert [item.id for item in page.items] == [second.id, first.id]
    row = page.items[0]
    assert row.freelancer_id == freelancer_id
    assert (row.freelancer_nome, row.freelancer_cognome, row.freelancer_email) == (
        "Ada",
        "Lovelace",
        "ada@studio.it",
    )
    assert (row.nome_azienda, row.figura_richiesta) == ("ACME Srl", "Backend developer")
    assert row.stato == "bozza"
    assert (row.lettera_numero, row.lettera_stato) == (second.lettera.numero, second.lettera.stato)
    assert row.lettera_data_inizio == "1° ottobre 2026"
    assert row.lettera_data_fine is None
    assert row.created_by_nome == "Ivan"
    assert row.created_by_email == "ivan@rebase.it"


def test_the_match_list_filters_by_state_and_refuses_an_unknown_one(clean: Session) -> None:
    admin_id, freelancer_id, company_id = _setup(clean)
    service = _service(clean)
    cancelled = service.create(freelancer_id, _body(company_id), admin_id)
    service.cancel(cancelled.id, admin_id)
    draft = service.create(freelancer_id, _body(company_id), admin_id)

    page = service.list_all(stato="bozza", q=None, limit=100, offset=0)
    assert [item.id for item in page.items] == [draft.id]
    assert page.totale == 1

    with pytest.raises(ValidationFailed) as refused:
        service.list_all(stato="chissà", q=None, limit=100, offset=0)
    assert refused.value.details["field"] == "stato"


def test_the_match_list_search_matches_the_freelancer_and_the_company(clean: Session) -> None:
    admin_id, freelancer_id, company_id = _setup(clean)
    service = _service(clean)
    service.create(freelancer_id, _body(company_id), admin_id)

    other_freelancer_id = _second_card(clean)
    _fiscal(clean, other_freelancer_id, admin_id)
    other_company_id = _request(clean, nome_azienda="Bianchi Srl", figura_richiesta="Designer")
    service.create(
        other_freelancer_id,
        MatchCreate(
            company_id=other_company_id,
            cliente=ClienteData(
                cliente_ragione_sociale="Bianchi", cliente_piva="09876543210", cliente_sede="Roma"
            ),
            lettera=LetteraFields(
                ruolo="Designer",
                attivita="Il design del prodotto.",
                data_inizio=date(2026, 10, 1),
                compenso=Decimal("500"),
                giorni_pagamento=30,
                fine_mese=True,
            ),
        ),
        admin_id,
    )

    by_surname = service.list_all(stato=None, q="lovelace", limit=100, offset=0)
    assert [item.freelancer_email for item in by_surname.items] == ["ada@studio.it"]

    by_email = service.list_all(stato=None, q="GRACE@studio.it", limit=100, offset=0)
    assert [item.freelancer_email for item in by_email.items] == ["grace@studio.it"]

    by_company = service.list_all(stato=None, q="bianchi", limit=100, offset=0)
    assert [item.nome_azienda for item in by_company.items] == ["Bianchi Srl"]


def test_the_match_list_still_names_a_soft_deleted_requests_company(clean: Session) -> None:
    admin_id, freelancer_id, company_id = _setup(clean)
    service = _service(clean)
    match = service.create(freelancer_id, _body(company_id), admin_id)
    CompanyService(clean).soft_delete(company_id, admin_id)

    page = service.list_all(stato=None, q=None, limit=100, offset=0)
    assert [(item.id, item.nome_azienda) for item in page.items] == [(match.id, "ACME Srl")]


def test_the_match_list_hides_a_soft_deleted_freelancers_match(clean: Session) -> None:
    admin_id, freelancer_id, company_id = _setup(clean)
    service = _service(clean)
    service.create(freelancer_id, _body(company_id), admin_id)
    FreelancerService(clean).soft_delete(freelancer_id, admin_id)

    page = service.list_all(stato=None, q=None, limit=100, offset=0)
    assert page.items == []
    assert page.totale == 0


def test_the_match_list_paginates_with_a_total(clean: Session) -> None:
    admin_id, freelancer_id, company_id = _setup(clean)
    service = _service(clean, today=date(2026, 12, 1))
    ids = [service.create(freelancer_id, _body(company_id), admin_id).id for _ in range(3)]
    newest_first = list(reversed(ids))

    first_page = service.list_all(stato=None, q=None, limit=2, offset=0)
    assert first_page.totale == 3
    assert [item.id for item in first_page.items] == newest_first[:2]

    second_page = service.list_all(stato=None, q=None, limit=2, offset=2)
    assert [item.id for item in second_page.items] == newest_first[2:]


def test_the_match_list_shows_a_match_with_two_letters_once_with_its_current_letter(
    clean: Session,
) -> None:
    """Greptile 4092036056: the schema lets a match have more than one letter (phase 3
    regenerates a waiting one on the framework's signature). The list must still show
    the match once, count it once, and carry the letter `get` and `for_freelancer` call
    its own: the newest."""
    admin_id, freelancer_id, company_id = _setup(clean)
    service = _service(clean)
    match = service.create(freelancer_id, _body(company_id), admin_id)
    newer = service.create(freelancer_id, _body(company_id), admin_id)
    waiting = clean.get(ContractDocument, match.lettera.id)
    assert waiting is not None
    waiting.stato = "annullato"
    clean.add(
        ContractDocument(
            kind="lettera",
            freelancer_id=freelancer_id,
            match_id=match.id,
            numero="2026-003",
            text_version=waiting.text_version,
            testo_bozza=False,
            data={**waiting.data, "data-inizio": "2 novembre 2026"},
            pdf=b"%PDF-regenerated",
            stato="generato",
            created_by=admin_id,
        )
    )
    clean.commit()

    page = service.list_all(stato=None, q=None, limit=100, offset=0)
    assert page.totale == 2
    assert [item.id for item in page.items] == [newer.id, match.id]
    row = page.items[1]
    assert (row.lettera_numero, row.lettera_stato, row.lettera_data_inizio) == (
        "2026-003",
        "generato",
        "2 novembre 2026",
    )
    assert service.get(match.id).lettera.numero == row.lettera_numero

    first_page = service.list_all(stato=None, q=None, limit=1, offset=0)
    second_page = service.list_all(stato=None, q=None, limit=1, offset=1)
    assert [item.id for item in first_page.items + second_page.items] == [newer.id, match.id]


def test_the_match_list_row_carries_no_tax_field_and_no_budget() -> None:
    fields = set(MatchListItem.model_fields)
    assert not any("budget" in name for name in fields)
    assert fields.isdisjoint({"codice_fiscale", "partita_iva", "domicilio", "pec"})
