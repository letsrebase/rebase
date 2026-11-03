"""What the admin area reads of a match (REB-413, REB-477): the contracts page, the «Match» list and
the words each match and document say. Helpers: `match_fixtures`."""

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

import pytest
from fakes_contracts import FakeRenderer
from match_fixtures import (
    PDF,
    QUADRO_VERSION,
    _admin,
    _body,
    _card,
    _documents,
    _fiscal,
    _framework,
    _framework_now,
    _request,
    _second_card,
    _service,
    _setup,
    _waiting,
    clean,  # noqa: F401  (fixture)
)
from sqlalchemy import event, func, select, text
from sqlalchemy.orm import Session

from rebase_core.companies import CompanyService
from rebase_core.contract_schemas import (
    ClienteData,
    LetteraFields,
    MatchCreate,
    MatchListItem,
)
from rebase_core.errors import NotFound, ValidationFailed
from rebase_core.freelancers import FreelancerService
from rebase_core.models import ContractDocument, Match


def test_an_active_matchs_situazione_says_where_its_hours_stand_on_pigro(clean: Session) -> None:
    """`get` and `list_all` both read `match_words` through `_match_words` (REB-498,
    spec § 3.5): the wiring, not the sentences themselves, which `test_match_words.py`
    covers."""
    admin_id, freelancer_id, company_id = _setup(clean)
    service = _service(clean)
    match = service.create(freelancer_id, _body(company_id), admin_id)
    letter = clean.get(ContractDocument, match.lettera.id)
    assert letter is not None
    letter.stato = "firmato"
    letter.signed_at = datetime(2026, 9, 28, 9, 0, tzinfo=UTC)
    letter.signed_pdf = PDF
    row = clean.get(Match, match.id)
    assert row is not None
    row.stato = "attivo"
    row.pigro_stato = "da_collegare"
    clean.commit()

    read = service.reads.get(match.id)
    assert read.situazione.endswith(" Pigro non ha ancora il deal: riprova o aspetta lo sweep.")
    assert read.altre_azioni == ["chiudi", "riprova_pigro"]
    listed = service.reads.list_all(stato=None, q=None, limit=100, offset=0).items[0]
    assert listed.situazione == read.situazione

    row.pigro_stato = "collegato"
    clean.commit()
    read = service.reads.get(match.id)
    assert read.situazione.endswith(" Le ore si consuntivano su Pigro.")
    assert read.altre_azioni == ["chiudi"]


def test_the_contracts_page_reads_the_active_framework_its_dates_and_the_matches_newest_first(
    clean: Session,
) -> None:
    admin_id, freelancer_id, company_id = _setup(clean)
    _framework(clean, freelancer_id, admin_id, text_version="0.0")
    service = _service(clean, today=date(2026, 12, 1))
    first = service.create(freelancer_id, _body(company_id), admin_id)
    second = service.create(freelancer_id, _body(company_id), admin_id)
    page = service.reads.for_freelancer(freelancer_id)
    assert page.quadro is not None and page.quadro.attivo
    assert (page.quadro.rinnovo, page.quadro.ultimo_giorno_disdetta) == (
        date(2027, 10, 1),
        date(2027, 9, 1),
    )
    assert page.quadro.nuova_versione is True
    assert [m.id for m in page.matches] == [second.id, first.id]
    assert page.fiscale is not None


def test_a_match_stays_on_the_page_after_its_request_is_deleted(clean: Session) -> None:
    """A soft-deleted request still names the match that came from it."""
    admin_id, freelancer_id, company_id = _setup(clean)
    service = _service(clean)
    match = service.create(freelancer_id, _body(company_id), admin_id)
    CompanyService(clean).soft_delete(company_id, admin_id)
    assert [
        (m.id, m.nome_azienda) for m in service.reads.for_freelancer(freelancer_id).matches
    ] == [(match.id, "ACME Srl")]
    assert service.reads.get(match.id).nome_azienda == "ACME Srl"


def test_a_preview_renders_without_saving_numbering_or_replacing_anything(clean: Session) -> None:
    admin_id, freelancer_id, company_id = _setup(clean)
    renderer = FakeRenderer()
    service = _service(clean, renderer)
    service.create(freelancer_id, _body(company_id), admin_id)
    letter = service.drafts.preview(freelancer_id, _body(company_id), "lettera")
    assert letter.content.startswith(b"%PDF-")
    assert renderer.calls[-1][0] == "lettera-di-incarico"
    assert renderer.calls[-1][1]["numero"] is None
    service.drafts.preview(freelancer_id, _body(company_id), "quadro")
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
    pdf = service.reads.document_pdf(match.lettera.id)
    assert (pdf.filename, pdf.content[:5]) == ("lettera-di-incarico-2026-001.pdf", b"%PDF-")
    quadro = _documents(clean, freelancer_id, "quadro")[0]
    assert (
        service.reads.document_pdf(quadro.id).filename == f"contratto-quadro-v{QUADRO_VERSION}.pdf"
    )
    with pytest.raises(NotFound):
        service.reads.document_pdf(match.lettera.id, signed=True)


def test_the_match_list_is_newest_first_with_its_letter_and_period(clean: Session) -> None:
    admin_id, freelancer_id, company_id = _setup(clean)
    service = _service(clean, today=date(2026, 12, 1))
    first = service.create(freelancer_id, _body(company_id), admin_id)
    second = service.create(freelancer_id, _body(company_id), admin_id)
    page = service.reads.list_all(stato=None, q=None, limit=100, offset=0)
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

    page = service.reads.list_all(stato="bozza", q=None, limit=100, offset=0)
    assert [item.id for item in page.items] == [draft.id]
    assert page.totale == 1

    with pytest.raises(ValidationFailed) as refused:
        service.reads.list_all(stato="chissà", q=None, limit=100, offset=0)
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
                modalita="a giornata",
                compenso=Decimal("500"),
                giorni_pagamento=30,
                fine_mese=True,
            ),
        ),
        admin_id,
    )

    by_surname = service.reads.list_all(stato=None, q="lovelace", limit=100, offset=0)
    assert [item.freelancer_email for item in by_surname.items] == ["ada@studio.it"]

    by_email = service.reads.list_all(stato=None, q="GRACE@studio.it", limit=100, offset=0)
    assert [item.freelancer_email for item in by_email.items] == ["grace@studio.it"]

    by_company = service.reads.list_all(stato=None, q="bianchi", limit=100, offset=0)
    assert [item.nome_azienda for item in by_company.items] == ["Bianchi Srl"]


def test_the_match_list_still_names_a_soft_deleted_requests_company(clean: Session) -> None:
    admin_id, freelancer_id, company_id = _setup(clean)
    service = _service(clean)
    match = service.create(freelancer_id, _body(company_id), admin_id)
    CompanyService(clean).soft_delete(company_id, admin_id)

    page = service.reads.list_all(stato=None, q=None, limit=100, offset=0)
    assert [(item.id, item.nome_azienda) for item in page.items] == [(match.id, "ACME Srl")]


def test_the_match_list_hides_a_soft_deleted_freelancers_match(clean: Session) -> None:
    admin_id, freelancer_id, company_id = _setup(clean)
    service = _service(clean)
    service.create(freelancer_id, _body(company_id), admin_id)
    FreelancerService(clean).soft_delete(freelancer_id, admin_id)

    page = service.reads.list_all(stato=None, q=None, limit=100, offset=0)
    assert page.items == []
    assert page.totale == 0


def test_the_match_list_paginates_with_a_total(clean: Session) -> None:
    admin_id, freelancer_id, company_id = _setup(clean)
    service = _service(clean, today=date(2026, 12, 1))
    ids = [service.create(freelancer_id, _body(company_id), admin_id).id for _ in range(3)]
    newest_first = list(reversed(ids))

    first_page = service.reads.list_all(stato=None, q=None, limit=2, offset=0)
    assert first_page.totale == 3
    assert [item.id for item in first_page.items] == newest_first[:2]

    second_page = service.reads.list_all(stato=None, q=None, limit=2, offset=2)
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

    page = service.reads.list_all(stato=None, q=None, limit=100, offset=0)
    assert page.totale == 2
    assert [item.id for item in page.items] == [newer.id, match.id]
    row = page.items[1]
    assert (row.lettera_numero, row.lettera_stato, row.lettera_data_inizio) == (
        "2026-003",
        "generato",
        "2 novembre 2026",
    )
    assert service.reads.get(match.id).lettera.numero == row.lettera_numero

    first_page = service.reads.list_all(stato=None, q=None, limit=1, offset=0)
    second_page = service.reads.list_all(stato=None, q=None, limit=1, offset=1)
    assert [item.id for item in first_page.items + second_page.items] == [newer.id, match.id]


def test_the_match_list_row_carries_no_tax_field_and_no_budget() -> None:
    fields = set(MatchListItem.model_fields)
    assert not any("budget" in name for name in fields)
    assert fields.isdisjoint({"codice_fiscale", "partita_iva", "domicilio", "pec"})


def test_the_contracts_page_says_what_each_document_is_doing(clean: Session) -> None:
    admin_id, freelancer_id, company_id = _setup(clean)
    service = _service(clean)
    match = service.create(freelancer_id, _body(company_id), admin_id)
    page = service.reads.for_freelancer(freelancer_id)
    assert page.quadro is not None
    # `FakeRenderer` writes texts in draft, as the preview's would be.
    assert (page.quadro.situazione, page.quadro.prossima_azione, page.quadro.altre_azioni) == (
        "Parte con «Invia per la firma» sul suo match. Il testo è ancora in bozza.",
        None,
        ["annulla"],
    )
    (read,) = page.matches
    assert (read.situazione, read.prossima_azione, read.altre_azioni) == (
        f"La lettera n. {match.lettera.numero} è pronta: il freelance non ha ancora ricevuto "
        "nulla.",
        "invia",
        ["annulla"],
    )
    assert read.lettera.situazione == "Parte da sola dopo la firma del contratto quadro."
    assert (read.lettera.prossima_azione, read.lettera.altre_azioni) == (None, [])
    listed = service.reads.list_all(stato=None, q=None, limit=100, offset=0).items[0]
    assert listed.situazione == read.situazione


def test_a_signed_framework_reads_its_signature_day_in_rome(clean: Session) -> None:
    admin_id, freelancer_id, _company_id = _setup(clean)
    _framework(clean, freelancer_id, admin_id, signed_at=datetime(2026, 9, 30, 23, 30, tzinfo=UTC))
    quadro = _service(clean, today=date(2026, 12, 1)).reads.for_freelancer(freelancer_id).quadro
    assert quadro is not None
    assert (quadro.situazione, quadro.prossima_azione, quadro.altre_azioni) == (
        "Firmato il 1° ottobre 2026; la copia firmata non è ancora arrivata.",
        "aggiorna_stato",
        ["registra_disdetta"],
    )


def test_a_waiting_letter_reads_its_freelancers_framework_wherever_the_match_is_read(
    clean: Session,
) -> None:
    admin_id, freelancer_id, company_id = _setup(clean)
    service = _service(clean)
    match = service.create(freelancer_id, _body(company_id), admin_id)
    _waiting(clean, match.id)
    numero = match.lettera.numero

    def everywhere() -> tuple[str, str | None]:
        got, page = service.reads.get(match.id), service.reads.for_freelancer(freelancer_id)
        listed = service.reads.list_all(stato=None, q=None, limit=100, offset=0).items[0]
        assert got.situazione == page.matches[0].situazione == listed.situazione
        assert got.prossima_azione == page.matches[0].prossima_azione
        return got.situazione, got.prossima_azione

    _framework_now(clean, freelancer_id, "inviato")
    assert everywhere() == (
        f"La lettera n. {numero} aspetta la firma del contratto quadro e parte da sola dopo.",
        None,
    )
    # Signed, and the letter's release missed: the send dispatches the letter itself.
    _framework_now(clean, freelancer_id, "firmato")
    assert everywhere() == (
        f"La lettera n. {numero} è pronta a partire: il contratto quadro è già firmato.",
        "invia",
    )
    _framework_now(clean, freelancer_id, "generato")
    assert everywhere() == (
        f"La lettera n. {numero} parte dopo il contratto quadro: «Invia per la firma» lo manda "
        "al freelance.",
        "invia",
    )
    for stato in ("annullato", "disdetto"):
        _framework_now(clean, freelancer_id, stato)
        assert everywhere() == (
            f"La lettera n. {numero} aspetta un contratto quadro: «Invia per la firma» ne genera "
            "uno nuovo.",
            "invia",
        )


def test_list_all_reads_frameworks_in_one_query(clean: Session) -> None:
    """Up to 500 rows a page: each row's sentence reads its freelancer's pending framework
    agreement, all of them in one statement, never one per row."""
    admin_id, company_id = _admin(clean), _request(clean)
    service = _service(clean)
    freelancers = [
        _card(clean),
        _second_card(clean),
        _second_card(clean, nome="Katherine", cognome="Johnson", email="katherine@studio.it"),
    ]
    numbers: dict[UUID, str | None] = {}
    for freelancer_id in freelancers:
        _fiscal(clean, freelancer_id, admin_id)
        match = service.create(freelancer_id, _body(company_id), admin_id)
        _waiting(clean, match.id)
        numbers[freelancer_id] = match.lettera.numero
    ada, grace, katherine = freelancers
    _framework_now(clean, grace, "inviato")
    _framework_now(clean, katherine, "firmato")

    statements: list[str] = []

    def record(_c: object, _cur: object, statement: str, *_rest: object) -> None:
        statements.append(statement)

    engine = clean.get_bind()
    event.listen(engine, "before_cursor_execute", record)
    try:
        service.reads.list_all(stato=None, q=None, limit=1, offset=0)
        for_one = len(statements)
        statements.clear()
        page = service.reads.list_all(stato=None, q=None, limit=100, offset=0)
        for_three = len(statements)
    finally:
        event.remove(engine, "before_cursor_execute", record)

    assert len(page.items) == 3
    assert for_three == for_one
    waits = "La lettera n. {} aspetta la firma del contratto quadro e parte da sola dopo."
    sends = (
        "La lettera n. {} parte dopo il contratto quadro: «Invia per la firma» lo manda al "
        "freelance."
    )
    ready = "La lettera n. {} è pronta a partire: il contratto quadro è già firmato."
    assert {item.freelancer_id: item.situazione for item in page.items} == {
        ada: sends.format(numbers[ada]),
        grace: waits.format(numbers[grace]),
        katherine: ready.format(numbers[katherine]),
    }


def test_a_match_listed_with_no_letter_at_all_reads_as_its_state(clean: Session) -> None:
    """`create` always writes a letter; the list still reads a match that has none."""
    admin_id, freelancer_id, company_id = _setup(clean)
    match = _service(clean).create(freelancer_id, _body(company_id), admin_id)
    clean.execute(text("DELETE FROM contract_documents WHERE match_id = :id"), {"id": match.id})
    clean.commit()
    (row,) = _service(clean).reads.list_all(stato=None, q=None, limit=100, offset=0).items
    assert (row.situazione, row.lettera_numero, row.lettera_stato) == ("Da inviare.", None, None)
