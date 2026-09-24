"""REB-387 phase 3: a match's documents go out through Documenso and come back signed.
Every test hands `FakeRenderer`, `FakeDocumenso` and a recording mailbox: nothing here
runs pandoc, reaches Documenso or sends a mail."""

import threading
from collections.abc import Iterator
from datetime import UTC, date, datetime
from uuid import UUID

import pytest
from fakes_contracts import FakeRenderer
from fakes_documenso import FakeDocumenso
from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session
from test_matches import SIGNER, TABLES, TODAY, _body, _documents, _framework, _setup

from rebase_core import signing as signing_module
from rebase_core.audit import AdminActionService
from rebase_core.contract_schemas import FiscalData, MatchRead, SendReport
from rebase_core.contracts.fields import ContractFailed, Value
from rebase_core.contracts.render import Renderer
from rebase_core.db import session_factory
from rebase_core.errors import DocumensoFailed, InvalidState, SigningUnavailable
from rebase_core.fiscal import FiscalService
from rebase_core.mail import EmailSender, Mail, RecordingSender
from rebase_core.matches import MatchService
from rebase_core.models import ContractDocument
from rebase_core.signing import SigningService

# 23:30 UTC on 30 September is already 1 October in Rome.
SIGNED_AT = datetime(2026, 9, 30, 23, 30, tzinfo=UTC)
NOW = datetime(2026, 9, 23, 10, 0, tzinfo=UTC)
CONTRACTS_MAIL = "contratti@rebase.test"


class RefusingSender:
    """A provider that turns every mail away, and keeps what it refused."""

    def __init__(self) -> None:
        self.sent: list[Mail] = []

    def send(self, mail: Mail) -> bool:
        self.sent.append(mail)
        return False


def _fiscal(
    session: Session, freelancer_id: UUID, admin_id: UUID, domicilio: str = "Via Roma 1, Milano"
) -> None:
    """Beyond `test_matches._fiscal`: a `domicilio` a test can change, to prove a send
    prints the tax data as they are today rather than as they were on the draft."""
    FiscalService(session).save(
        freelancer_id,
        FiscalData(
            codice_fiscale="LVLDAA85T50H501Z", partita_iva="01234567890", domicilio=domicilio
        ),
        admin_id,
    )


@pytest.fixture
def clean(hub_session: Session) -> Iterator[Session]:
    yield hub_session
    hub_session.rollback()
    for table in TABLES:
        hub_session.execute(text(f"DELETE FROM {table}"))
    hub_session.commit()


def _matches(session: Session, renderer: Renderer) -> MatchService:
    return MatchService(session, renderer, SIGNER, today=lambda: TODAY)


def _signing(
    session: Session,
    renderer: Renderer,
    fake: FakeDocumenso | None,
    sender: EmailSender | None,
    *,
    today: date = TODAY,
    signer: dict[str, Value] | None = None,
    allow_draft: bool = False,
) -> SigningService:
    return SigningService(
        session,
        renderer=renderer,
        documenso=fake.client() if fake is not None else None,
        sender=sender,
        signer=SIGNER if signer is None else signer,
        contracts_mail=CONTRACTS_MAIL,
        allow_draft=allow_draft,
        today=lambda: today,
        now=lambda: NOW,
    )


def _framework_of(session: Session, freelancer_id: UUID) -> ContractDocument:
    return _documents(session, freelancer_id, "quadro")[-1]


def _letter_of(session: Session, match_id: UUID) -> ContractDocument:
    return session.scalars(
        select(ContractDocument).where(ContractDocument.match_id == match_id)
    ).one()


def _draft(
    session: Session, renderer: Renderer, freelancer_id: UUID, company_id: UUID, admin_id: UUID
) -> MatchRead:
    return _matches(session, renderer).create(freelancer_id, _body(company_id), admin_id)


def test_the_first_send_hands_documenso_the_framework_and_the_letter_waits(
    clean: Session,
) -> None:
    admin_id, freelancer_id, company_id = _setup(clean)
    renderer, fake, sender = FakeRenderer(draft=False), FakeDocumenso(), RecordingSender()
    match = _draft(clean, renderer, freelancer_id, company_id, admin_id)

    report = _signing(clean, renderer, fake, sender).send_match(match.id, admin_id)

    assert (report.inviato, report.mail_inviata) == ("quadro", True)
    assert (report.match.stato, report.match.lettera.stato) == ("in_firma", "in_attesa")
    quadro = _framework_of(clean, freelancer_id)
    [envelope] = fake.envelopes.values()
    assert quadro.stato == "inviato"
    assert (quadro.documenso_id, quadro.documenso_item_id) == (envelope.id, envelope.item_id)
    assert quadro.signing_url == f"https://firma.letsrebase.test/sign/{envelope.token}"
    assert (quadro.sent_at, quadro.sent_by) == (NOW, admin_id)
    assert envelope.payload["title"] == "Contratto quadro rebase"
    assert envelope.payload["externalId"] == str(quadro.id)
    assert envelope.filename == "contratto-quadro-v0.1.pdf"
    assert envelope.payload["meta"]["distributionMethod"] == "NONE"
    assert not any(envelope.payload["meta"]["emailSettings"].values())
    fields = envelope.payload["recipients"][0]["fields"]
    assert [field["type"] for field in fields] == ["DATE", "SIGNATURE", "SIGNATURE"]
    assert envelope.payload["recipients"][0]["email"] == "ada@studio.it"
    assert renderer.signing[-1] is True
    # The blanks Documenso's fields come from are read off the signing copy, the same
    # one just typeset (REB-406 fix round 1, M4).
    assert renderer.blanks_signing[-1] is True
    [mail] = sender.sent
    assert (mail.to, mail.subject) == ("ada@studio.it", "Da firmare: contratto quadro rebase")
    assert quadro.signing_url in mail.text
    assert AdminActionService(clean).timeline("match", match.id)[0].kind == "documents_sent"


def test_the_sent_copy_says_the_day_it_left_and_prints_the_tax_data_saved_since_the_draft(
    clean: Session,
) -> None:
    """Review Focus 5: the draft was saved on the 23rd with the old address; the admin
    corrected the tax data and sends on the 25th. What leaves is today's."""
    admin_id, freelancer_id, company_id = _setup(clean)
    renderer, fake = FakeRenderer(draft=False), FakeDocumenso()
    match = _draft(clean, renderer, freelancer_id, company_id, admin_id)
    _fiscal(clean, freelancer_id, admin_id, domicilio="Corso Como 1, Milano")

    _signing(clean, renderer, fake, RecordingSender(), today=date(2026, 9, 25)).send_match(
        match.id, admin_id
    )

    quadro = _framework_of(clean, freelancer_id)
    assert quadro.data["firma-rebase"] == "Documento emesso da rebase il 25 settembre 2026"
    assert quadro.data["professionista-domicilio"] == "Corso Como 1, Milano"
    document, data = renderer.calls[-1]
    assert document == "contratto-quadro" and data == quadro.data
    assert quadro.pdf == b"%PDF-1.7 fake contratto-quadro"
    assert quadro.testo_bozza is False


def test_with_an_active_framework_the_letter_leaves_and_cites_its_signature(
    clean: Session,
) -> None:
    admin_id, freelancer_id, company_id = _setup(clean)
    _framework(clean, freelancer_id, admin_id, signed_at=SIGNED_AT)
    renderer, fake, sender = FakeRenderer(draft=False), FakeDocumenso(), RecordingSender()
    match = _draft(clean, renderer, freelancer_id, company_id, admin_id)
    assert match.lettera.stato == "generato"

    report = _signing(clean, renderer, fake, sender).send_match(match.id, admin_id)

    assert (report.inviato, report.match.lettera.stato) == ("lettera", "inviato")
    letter = _letter_of(clean, match.id)
    assert letter.data["data-contratto-quadro"] == "1° ottobre 2026"
    [envelope] = fake.envelopes.values()
    assert envelope.payload["title"] == f"Lettera di incarico n. {letter.numero}"
    assert envelope.filename == f"lettera-di-incarico-{letter.numero}.pdf"
    assert [mail.subject for mail in sender.sent] == [
        f"Da firmare: lettera di incarico n. {letter.numero}"
    ]
    # Spec § 1h, proven on the sent copy itself (REB-406 fix round 1, M3): what rebase
    # agreed with the client (the request's 777.77 a day) reaches neither the data
    # handed to the renderer nor the mail that tells the freelancer to sign.
    document, data = renderer.calls[-1]
    assert document == "lettera-di-incarico"
    assert not any("budget" in key for key in data)
    assert "777.77" not in str(list(data.values()))
    [mail] = sender.sent
    assert "budget" not in mail.text and "777.77" not in mail.text


def test_a_letter_waiting_on_a_framework_already_out_for_signature_sends_nothing_new(
    clean: Session,
) -> None:
    admin_id, freelancer_id, company_id = _setup(clean)
    renderer, fake, sender = FakeRenderer(draft=False), FakeDocumenso(), RecordingSender()
    first = _draft(clean, renderer, freelancer_id, company_id, admin_id)
    second = _draft(clean, renderer, freelancer_id, company_id, admin_id)
    signing = _signing(clean, renderer, fake, sender)
    signing.send_match(first.id, admin_id)

    report = signing.send_match(second.id, admin_id)

    assert (report.inviato, report.mail_inviata) == (None, None)
    assert (report.match.stato, report.match.lettera.stato) == ("in_firma", "in_attesa")
    assert len(fake.envelopes) == 1 and len(sender.sent) == 1


def test_two_matches_sent_at_once_send_the_framework_once(
    hub_engine: Engine, clean: Session
) -> None:
    """Review Focus 3: two admins send two matches of one freelancer at the same moment,
    and both need the same framework agreement. The second send waits on the framework's
    row, then finds it out for signature: one envelope, and the second letter waits."""
    admin_id, freelancer_id, company_id = _setup(clean)
    renderer, fake = FakeRenderer(draft=False), FakeDocumenso()
    _draft(clean, renderer, freelancer_id, company_id, admin_id)
    second = _draft(clean, renderer, freelancer_id, company_id, admin_id)
    framework_id = _framework_of(clean, freelancer_id).id
    factory = session_factory(hub_engine)
    first_admin, second_admin = factory(), factory()
    reports: list[SendReport] = []

    def send_second() -> None:
        service = _signing(second_admin, renderer, fake, RecordingSender())
        reports.append(service.send_match(second.id, admin_id))

    try:
        # The first admin's send, caught holding the framework's row and not sent yet.
        held = first_admin.scalars(
            select(ContractDocument).where(ContractDocument.id == framework_id).with_for_update()
        ).one()
        worker = threading.Thread(target=send_second)
        worker.start()
        worker.join(timeout=0.5)
        assert worker.is_alive(), "the second send did not wait for the framework's row"
        held.stato = "inviato"
        held.documenso_id, held.documenso_item_id = "envelope_primo", "envelope_item_primo"
        held.signing_url = "https://firma.letsrebase.test/sign/primo"
        first_admin.commit()
        worker.join(timeout=5)
        assert not worker.is_alive()
    finally:
        first_admin.close()
        second_admin.close()
    assert [(r.inviato, r.match.lettera.stato) for r in reports] == [(None, "in_attesa")]
    assert fake.envelopes == {}


def test_a_draft_text_never_leaves(clean: Session) -> None:
    admin_id, freelancer_id, company_id = _setup(clean)
    renderer, fake, sender = FakeRenderer(draft=True), FakeDocumenso(), RecordingSender()
    match = _draft(clean, renderer, freelancer_id, company_id, admin_id)

    with pytest.raises(InvalidState, match="ancora una bozza") as caught:
        _signing(clean, renderer, fake, sender).send_match(match.id, admin_id)

    # The way out is named too (REB-406 fix round 1, M6).
    assert "REBASE_CONTRACTS_ALLOW_DRAFT" in caught.value.message
    assert fake.calls == [] and sender.sent == []
    assert _matches(clean, renderer).get(match.id).stato == "bozza"
    assert _framework_of(clean, freelancer_id).stato == "generato"


def test_a_draft_text_leaves_on_the_preview_when_the_setting_allows_it(clean: Session) -> None:
    """REB-406 controller ruling: `REBASE_CONTRACTS_ALLOW_DRAFT` lets a draft leave, only
    where that setting is true (the preview's own `.env`), so the text can be tested end
    to end with Documenso before it loses its `draft` status."""
    admin_id, freelancer_id, company_id = _setup(clean)
    renderer, fake, sender = FakeRenderer(draft=True), FakeDocumenso(), RecordingSender()
    match = _draft(clean, renderer, freelancer_id, company_id, admin_id)

    report = _signing(clean, renderer, fake, sender, allow_draft=True).send_match(
        match.id, admin_id
    )

    assert report.inviato == "quadro"
    quadro = _framework_of(clean, freelancer_id)
    assert quadro.testo_bozza is True
    assert quadro.stato == "inviato"
    [envelope] = fake.envelopes.values()
    assert envelope.pdf == quadro.pdf == b"%PDF-1.7 fake contratto-quadro"


def test_without_rebases_signer_nothing_leaves(clean: Session) -> None:
    admin_id, freelancer_id, company_id = _setup(clean)
    renderer, fake = FakeRenderer(draft=False), FakeDocumenso()
    match = _draft(clean, renderer, freelancer_id, company_id, admin_id)

    with pytest.raises(SigningUnavailable, match="REBASE_SIGNER_JSON") as caught:
        _signing(clean, renderer, fake, RecordingSender(), signer={}).send_match(match.id, admin_id)

    assert "rebase-sede" in caught.value.message
    assert fake.calls == []


def test_documenso_refusing_the_distribution_marks_nothing_sent(clean: Session) -> None:
    """Review Focus 4: the envelope exists on Documenso, the distribution fails. Nothing
    in the hub says sent, the admin reads Documenso's sentence alone, and the same draft
    can be sent again with the same letter number."""
    admin_id, freelancer_id, company_id = _setup(clean)
    renderer, fake, sender = FakeRenderer(draft=False), FakeDocumenso(), RecordingSender()
    match = _draft(clean, renderer, freelancer_id, company_id, admin_id)
    fake.fail("distribute", 400, "Recipient is missing a signature field")
    signing = _signing(clean, renderer, fake, sender)

    with pytest.raises(DocumensoFailed) as caught:
        signing.send_match(match.id, admin_id)

    assert caught.value.message == (
        "Documenso ha rifiutato la richiesta: Recipient is missing a signature field"
    )
    quadro = _framework_of(clean, freelancer_id)
    assert (quadro.stato, quadro.documenso_id, quadro.signing_url, quadro.sent_at) == (
        "generato",
        None,
        None,
        None,
    )
    assert _matches(clean, renderer).get(match.id).stato == "bozza"
    assert sender.sent == []
    again = signing.send_match(match.id, admin_id)
    assert again.inviato == "quadro"
    assert again.match.lettera.numero == match.lettera.numero


def test_a_refused_mail_leaves_the_document_sent_and_says_so(clean: Session) -> None:
    """Review Focus 4, the other half: Documenso took it, the mail provider did not."""
    admin_id, freelancer_id, company_id = _setup(clean)
    renderer = FakeRenderer(draft=False)
    match = _draft(clean, renderer, freelancer_id, company_id, admin_id)

    report = _signing(clean, renderer, FakeDocumenso(), RefusingSender()).send_match(
        match.id, admin_id
    )

    assert (report.inviato, report.mail_inviata) == ("quadro", False)
    assert _framework_of(clean, freelancer_id).stato == "inviato"


def test_without_documenso_or_without_mail_the_send_says_why(clean: Session) -> None:
    admin_id, freelancer_id, company_id = _setup(clean)
    renderer = FakeRenderer(draft=False)
    match = _draft(clean, renderer, freelancer_id, company_id, admin_id)
    with pytest.raises(SigningUnavailable, match="Documenso"):
        _signing(clean, renderer, None, RecordingSender()).send_match(match.id, admin_id)
    with pytest.raises(SigningUnavailable, match="email"):
        _signing(clean, renderer, FakeDocumenso(), None).send_match(match.id, admin_id)
    assert _matches(clean, renderer).get(match.id).stato == "bozza"


def test_a_cancelled_match_has_nothing_to_send(clean: Session) -> None:
    admin_id, freelancer_id, company_id = _setup(clean)
    renderer = FakeRenderer(draft=False)
    match = _draft(clean, renderer, freelancer_id, company_id, admin_id)
    _matches(clean, renderer).cancel(match.id, admin_id)
    with pytest.raises(InvalidState, match="bozza o in firma"):
        _signing(clean, renderer, FakeDocumenso(), RecordingSender()).send_match(match.id, admin_id)


def test_a_letter_whose_framework_was_refused_gets_a_new_one_when_its_match_is_sent(
    clean: Session,
) -> None:
    """A framework agreement refused or cancelled while its letter waited: «Invia per la
    firma» on the match writes a new one from today's tax data and sends it."""
    admin_id, freelancer_id, company_id = _setup(clean)
    renderer, fake = FakeRenderer(draft=False), FakeDocumenso()
    match = _draft(clean, renderer, freelancer_id, company_id, admin_id)
    signing = _signing(clean, renderer, fake, RecordingSender())
    signing.send_match(match.id, admin_id)
    refused = _framework_of(clean, freelancer_id)
    refused.stato, refused.cancel_reason = "annullato", "Rifiutato dal freelance sul sito di firma."
    clean.commit()

    report = signing.send_match(match.id, admin_id)

    assert report.inviato == "quadro"
    assert [d.stato for d in _documents(clean, freelancer_id, "quadro")] == ["annullato", "inviato"]
    assert len(fake.envelopes) == 2


def test_a_crea_match_racing_a_send_waits_for_it_and_does_not_annul_the_framework_it_dispatched(
    monkeypatch: pytest.MonkeyPatch, hub_engine: Engine, clean: Session
) -> None:
    """Global Constraints' lock order: a send in progress must block a concurrent
    `create()` («Crea match») for a second match of the same freelancer -- the shape of
    `test_matches.test_two_admins_matching_the_same_freelancer_at_once_never_leave_two_open_frameworks`,
    but with a send on one side. `active_framework` is the next call `send_match` makes
    after taking the freelancer's row lock, so pausing it there proves the lock is taken
    before, not merely before the commit. Once the send commits its framework as
    `inviato`, `create()` must find it already out for signature and leave it alone
    rather than annul it as a stale `generato` one (`pending_framework`, `MatchService.
    create`)."""
    admin_id, freelancer_id, company_id = _setup(clean)
    renderer, fake = FakeRenderer(draft=False), FakeDocumenso()
    match = _draft(clean, renderer, freelancer_id, company_id, admin_id)
    factory = session_factory(hub_engine)
    sender_session, creator_session = factory(), factory()
    paused, release = threading.Event(), threading.Event()
    real_active_framework = signing_module.active_framework

    def paced_active_framework(session: Session, freelancer_id: UUID) -> ContractDocument | None:
        if session is sender_session:
            paused.set()
            assert release.wait(timeout=5), "the test never released the send"
        return real_active_framework(session, freelancer_id)

    monkeypatch.setattr(signing_module, "active_framework", paced_active_framework)

    errors: list[BaseException] = []

    def send() -> None:
        try:
            _signing(sender_session, renderer, fake, RecordingSender()).send_match(
                match.id, admin_id
            )
        except BaseException as exc:  # noqa: BLE001 -- surfaced on the main thread below
            errors.append(exc)

    outcome: list[object] = []

    def create_second() -> None:
        try:
            outcome.append(
                MatchService(creator_session, renderer, SIGNER, today=lambda: TODAY).create(
                    freelancer_id, _body(company_id), admin_id
                )
            )
        except BaseException as exc:  # noqa: BLE001 -- surfaced on the main thread below
            outcome.append(exc)

    try:
        sender_worker = threading.Thread(target=send)
        sender_worker.start()
        assert paused.wait(timeout=5), "the send never reached active_framework"

        creator_worker = threading.Thread(target=create_second)
        creator_worker.start()
        creator_worker.join(timeout=0.5)
        assert creator_worker.is_alive(), (
            "create() read the freelancer while the send's transaction, past the same "
            "lock, was still open"
        )

        release.set()
        sender_worker.join(timeout=5)
        creator_worker.join(timeout=5)
        assert not sender_worker.is_alive()
        assert not creator_worker.is_alive()
        assert not errors, errors
    finally:
        sender_session.close()
        creator_session.close()

    assert len(outcome) == 1 and isinstance(outcome[0], MatchRead), outcome
    second = outcome[0]
    assert isinstance(second, MatchRead)
    assert second.lettera.stato == "in_attesa"
    assert [d.stato for d in _documents(clean, freelancer_id, "quadro")] == ["inviato"]


def test_a_malformed_signer_setting_does_not_break_construction_or_a_read(clean: Session) -> None:
    """REB-406 fix round 1, I1 (a): a malformed REBASE_SIGNER_JSON must not turn every
    `SigningDep` route into a 503, only the one that actually typesets. Building the
    service, and a plain read through it, must both work."""
    admin_id, freelancer_id, company_id = _setup(clean)
    renderer, fake = FakeRenderer(draft=False), FakeDocumenso()
    match = _draft(clean, renderer, freelancer_id, company_id, admin_id)

    signing = SigningService(
        clean,
        renderer=renderer,
        documenso=fake.client(),
        sender=RecordingSender(),
        signer_json="{not json",
        contracts_mail=CONTRACTS_MAIL,
        today=lambda: TODAY,
        now=lambda: NOW,
    )

    assert signing.matches.get(match.id).stato == "bozza"


def test_a_malformed_signer_setting_503s_only_the_send_it_breaks(clean: Session) -> None:
    """REB-406 fix round 1, I1 (b): parsed lazily, so the refusal names the setting only
    once a document is actually about to be typeset -- Documenso is never even called."""
    admin_id, freelancer_id, company_id = _setup(clean)
    renderer, fake = FakeRenderer(draft=False), FakeDocumenso()
    match = _draft(clean, renderer, freelancer_id, company_id, admin_id)
    signing = SigningService(
        clean,
        renderer=renderer,
        documenso=fake.client(),
        sender=RecordingSender(),
        signer_json="{not json",
        contracts_mail=CONTRACTS_MAIL,
        today=lambda: TODAY,
        now=lambda: NOW,
    )

    with pytest.raises(ContractFailed) as caught:
        signing.send_match(match.id, admin_id)

    assert "REBASE_SIGNER_JSON" in caught.value.message
    assert fake.calls == []


def test_an_empty_signer_setting_still_refuses_to_send_with_blank_signer_fields(
    clean: Session,
) -> None:
    """REB-406 fix round 1, I1 (c): the lazy path (`signer_json=""`, the default) 503s
    exactly as the eager one already did (`test_without_rebases_signer_nothing_leaves`,
    an explicit `signer={}`) -- it never sends a document with blank signer fields."""
    admin_id, freelancer_id, company_id = _setup(clean)
    renderer, fake = FakeRenderer(draft=False), FakeDocumenso()
    match = _draft(clean, renderer, freelancer_id, company_id, admin_id)
    signing = SigningService(
        clean,
        renderer=renderer,
        documenso=fake.client(),
        sender=RecordingSender(),
        contracts_mail=CONTRACTS_MAIL,
        today=lambda: TODAY,
        now=lambda: NOW,
    )

    with pytest.raises(SigningUnavailable, match="REBASE_SIGNER_JSON") as caught:
        signing.send_match(match.id, admin_id)

    assert "rebase-sede" in caught.value.message
    assert fake.calls == []


def test_a_refused_distribute_cancels_the_orphaned_envelope(
    clean: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """REB-406 fix round 1, M11: `get` succeeds (the envelope already exists on
    Documenso, as after a real create) and this test then moves it to `PENDING` itself,
    simulating Documenso having processed the distribute server-side even though the
    client's own parsing of the answer fails -- `FakeDocumenso.cancel` (probe § 4: only
    a `PENDING` envelope accepts one) would otherwise refuse a cancel just as the real
    API would for a still-`DRAFT` envelope, which `fake.fail('distribute', ...)` alone
    never advances past. The fix's best-effort cancel succeeds here, and the original
    refusal is still what reaches the admin."""
    admin_id, freelancer_id, company_id = _setup(clean)
    renderer, fake, sender = FakeRenderer(draft=False), FakeDocumenso(), RecordingSender()
    match = _draft(clean, renderer, freelancer_id, company_id, admin_id)
    real_get = fake._get

    def get_then_mark_pending(envelope_id: str) -> tuple[int, bytes]:
        status, body = real_get(envelope_id)
        fake.envelopes[envelope_id].status = "PENDING"
        return status, body

    monkeypatch.setattr(fake, "_get", get_then_mark_pending)
    fake.fail("distribute", 400, "Recipient is missing a signature field")

    with pytest.raises(DocumensoFailed) as caught:
        _signing(clean, renderer, fake, sender).send_match(match.id, admin_id)

    assert caught.value.message == (
        "Documenso ha rifiutato la richiesta: Recipient is missing a signature field"
    )
    [envelope] = fake.envelopes.values()
    assert envelope.status == "CANCELLED"
