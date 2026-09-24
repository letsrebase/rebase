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
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session
from test_matches import SIGNER, TABLES, TODAY, _body, _documents, _framework, _setup

from rebase_core import signing as signing_module
from rebase_core.audit import AdminActionService
from rebase_core.contract_schemas import FiscalData, MatchRead, SendReport
from rebase_core.contracts.fields import ContractFailed, Value
from rebase_core.contracts.render import Renderer
from rebase_core.db import session_factory
from rebase_core.documenso import Outcome, WebhookBody, outcome_from_webhook
from rebase_core.errors import DocumensoFailed, InvalidState, SigningUnavailable
from rebase_core.fiscal import FiscalService
from rebase_core.mail import EmailSender, Mail, RecordingSender
from rebase_core.matches import MatchService
from rebase_core.models import ContractDocument, Freelancer, Match
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


def _active_framework(
    session: Session, freelancer_id: UUID, admin_id: UUID, signed_at: datetime = SIGNED_AT
) -> ContractDocument:
    """A framework agreement as a signature leaves one."""
    document = ContractDocument(
        kind="quadro",
        freelancer_id=freelancer_id,
        text_version="0.1",
        testo_bozza=False,
        data={},
        pdf=b"%PDF-quadro",
        stato="firmato",
        signed_at=signed_at,
        created_by=admin_id,
    )
    session.add(document)
    session.commit()
    return document


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


# ---- the webhook (REB-391) --------------------------------------------------------------


def _sent(
    session: Session,
    renderer: FakeRenderer,
    fake: FakeDocumenso,
    sender: RecordingSender,
    freelancer_id: UUID,
    company_id: UUID,
    admin_id: UUID,
) -> MatchRead:
    """A draft match, sent: its framework agreement `inviato`, its letter waiting."""
    match = _draft(session, renderer, freelancer_id, company_id, admin_id)
    _signing(session, renderer, fake, sender).send_match(match.id, admin_id)
    return match


def _webhook(fake: FakeDocumenso, envelope_id: str, event: str) -> Outcome:
    outcome = outcome_from_webhook(WebhookBody.model_validate(fake.webhook(envelope_id, event)))
    assert outcome is not None
    return outcome


def _envelope_of(document: ContractDocument) -> str:
    assert document.documenso_id is not None
    return document.documenso_id


def _try_lock_nowait(session: Session, document_id: UUID) -> bool:
    """`True` when `document_id` could be locked immediately, `False` when another
    transaction already holds it (`FOR UPDATE NOWAIT`): the proof that a caller has, or
    has not, taken this row yet (fix round 1, I1)."""
    try:
        session.execute(
            select(ContractDocument.id)
            .where(ContractDocument.id == document_id)
            .with_for_update(nowait=True)
        )
        return True
    except OperationalError:
        session.rollback()
        return False


def test_a_completion_signs_the_document_with_the_signers_date_and_calls_nobody(
    clean: Session,
) -> None:
    """The webhook's transaction moves the row and nothing else: Documenso gets its
    answer before any download or mail (probe § 11.2)."""
    admin_id, freelancer_id, company_id = _setup(clean)
    renderer, fake, sender = FakeRenderer(draft=False), FakeDocumenso(), RecordingSender()
    _sent(clean, renderer, fake, sender, freelancer_id, company_id, admin_id)
    envelope = _envelope_of(_framework_of(clean, freelancer_id))
    fake.sign(envelope, SIGNED_AT)
    calls, mails = len(fake.calls), len(sender.sent)

    signed = _signing(clean, renderer, fake, sender).apply(
        _webhook(fake, envelope, "DOCUMENT_COMPLETED")
    )

    quadro = _framework_of(clean, freelancer_id)
    assert signed == quadro.id
    assert (quadro.stato, quadro.signed_at, quadro.signed_pdf) == ("firmato", SIGNED_AT, None)
    assert (len(fake.calls), len(sender.sent)) == (calls, mails)


def test_a_second_delivery_waits_for_the_first_and_changes_nothing(
    hub_engine: Engine, clean: Session
) -> None:
    """Review Focus 1: Documenso retries at once, and even while a slow first delivery is
    still running (probe § 5). The second waits on the row, then finds it signed."""
    admin_id, freelancer_id, company_id = _setup(clean)
    renderer, fake, sender = FakeRenderer(draft=False), FakeDocumenso(), RecordingSender()
    _sent(clean, renderer, fake, sender, freelancer_id, company_id, admin_id)
    framework = _framework_of(clean, freelancer_id)
    envelope = _envelope_of(framework)
    fake.sign(envelope, SIGNED_AT)
    outcome = _webhook(fake, envelope, "DOCUMENT_COMPLETED")
    factory = session_factory(hub_engine)
    first, second = factory(), factory()
    results: list[UUID | None] = []

    def deliver_again() -> None:
        results.append(_signing(second, renderer, fake, sender).apply(outcome))

    try:
        # The first delivery, caught holding the row with its transition not committed.
        held = first.scalars(
            select(ContractDocument).where(ContractDocument.id == framework.id).with_for_update()
        ).one()
        worker = threading.Thread(target=deliver_again)
        worker.start()
        worker.join(timeout=0.5)
        assert worker.is_alive(), "the second delivery did not wait for the first one's lock"
        held.stato, held.signed_at = "firmato", SIGNED_AT
        first.commit()
        worker.join(timeout=5)
        assert not worker.is_alive()
    finally:
        first.close()
        second.close()
    assert results == [None]


def test_apply_locks_the_freelancer_row_before_the_document(
    hub_engine: Engine, clean: Session
) -> None:
    """Fix round 1, I1(a): a deterministic proof of `apply`'s lock order, not one left to
    thread scheduling. A gate session holds the freelancer's row; `apply` runs in its own
    thread and must block there -- proven not just by staying alive, but by a fourth
    session managing to lock the letter's own document row with `FOR UPDATE NOWAIT`
    while the gate holds: if `apply` had taken the document first (the old order), that
    NOWAIT probe would fail. Releasing the gate lets `apply` finish: the letter signs and
    its match turns active."""
    admin_id, freelancer_id, company_id = _setup(clean)
    _active_framework(clean, freelancer_id, admin_id)
    renderer, fake, sender = FakeRenderer(draft=False), FakeDocumenso(), RecordingSender()
    match = _draft(clean, renderer, freelancer_id, company_id, admin_id)
    _signing(clean, renderer, fake, sender).send_match(match.id, admin_id)
    letter = _letter_of(clean, match.id)
    envelope = _envelope_of(letter)
    fake.sign(envelope, SIGNED_AT)
    outcome = _webhook(fake, envelope, "DOCUMENT_COMPLETED")
    factory = session_factory(hub_engine)
    gate, worker_session, probe = factory(), factory(), factory()
    results: list[UUID | None] = []

    def run_apply() -> None:
        results.append(_signing(worker_session, renderer, fake, sender).apply(outcome))

    try:
        gate.execute(select(Freelancer.id).where(Freelancer.id == freelancer_id).with_for_update())
        worker = threading.Thread(target=run_apply)
        worker.start()
        worker.join(timeout=0.5)
        assert worker.is_alive(), "apply did not wait on the freelancer's row"
        # apply must not have locked the document yet: a NOWAIT probe on it succeeds.
        assert _try_lock_nowait(probe, letter.id), (
            "apply already held the document's row before the freelancer's"
        )
        probe.rollback()
        gate.commit()
        worker.join(timeout=5)
        assert not worker.is_alive()
    finally:
        gate.close()
        worker_session.close()
        probe.close()
    assert results == [letter.id]
    clean.expire_all()
    assert _letter_of(clean, match.id).stato == "firmato"
    assert clean.get(Match, match.id).stato == "attivo"  # type: ignore[union-attr]


def test_finish_releases_a_waiting_letter_only_after_locking_the_freelancer_row(
    hub_engine: Engine, clean: Session
) -> None:
    """Fix round 1, I1(b): the same gate proof for `finish` -> `_send_waiting`. A
    framework just signed, its letter still waiting: while another session holds the
    freelancer's row, the letter's own row is still free to a `FOR UPDATE NOWAIT` probe
    (proving `_send_waiting` has not reached it yet); releasing the gate lets it go, and
    the letter leaves."""
    admin_id, freelancer_id, company_id = _setup(clean)
    renderer, fake, sender = FakeRenderer(draft=False), FakeDocumenso(), RecordingSender()
    match = _sent(clean, renderer, fake, sender, freelancer_id, company_id, admin_id)
    envelope = _envelope_of(_framework_of(clean, freelancer_id))
    fake.sign(envelope, SIGNED_AT)
    signing = _signing(clean, renderer, fake, sender)
    signed = signing.apply(_webhook(fake, envelope, "DOCUMENT_COMPLETED"))
    assert signed is not None
    letter = _letter_of(clean, match.id)
    factory = session_factory(hub_engine)
    gate, worker_session, probe = factory(), factory(), factory()

    def run_finish() -> None:
        _signing(worker_session, renderer, fake, sender).finish(signed)

    try:
        gate.execute(select(Freelancer.id).where(Freelancer.id == freelancer_id).with_for_update())
        worker = threading.Thread(target=run_finish)
        worker.start()
        worker.join(timeout=0.5)
        assert worker.is_alive(), (
            "finish did not wait on the freelancer's row before releasing the letter"
        )
        assert _try_lock_nowait(probe, letter.id), (
            "_send_waiting already held the letter's row before the freelancer's"
        )
        probe.rollback()
        gate.commit()
        worker.join(timeout=5)
        assert not worker.is_alive()
    finally:
        gate.close()
        worker_session.close()
        probe.close()
    clean.expire_all()
    assert _letter_of(clean, match.id).stato == "inviato"


@pytest.mark.parametrize("apply_first", [True, False])
def test_a_letters_webhook_and_a_resend_of_its_match_never_deadlock(
    apply_first: bool, hub_engine: Engine, clean: Session
) -> None:
    """Fix round 1, I1(c): `apply`'s lock order now matches `send_match`'s (freelancer,
    match, document/letter), so the two never deadlock -- forced deterministically, in
    both orders, behind one gate on the freelancer's row, rather than left to thread
    scheduling."""
    admin_id, freelancer_id, company_id = _setup(clean)
    _active_framework(clean, freelancer_id, admin_id)
    renderer, fake, sender = FakeRenderer(draft=False), FakeDocumenso(), RecordingSender()
    match = _draft(clean, renderer, freelancer_id, company_id, admin_id)
    _signing(clean, renderer, fake, sender).send_match(match.id, admin_id)
    envelope = _envelope_of(_letter_of(clean, match.id))
    fake.sign(envelope, SIGNED_AT)
    outcome = _webhook(fake, envelope, "DOCUMENT_COMPLETED")
    factory = session_factory(hub_engine)
    gate, webhook_session, resend_session = factory(), factory(), factory()
    errors: list[BaseException] = []
    results: dict[str, object] = {}

    def deliver() -> None:
        try:
            results["apply"] = _signing(webhook_session, renderer, fake, sender).apply(outcome)
        except BaseException as exc:  # noqa: BLE001 -- surfaced on the main thread below
            errors.append(exc)

    def resend() -> None:
        try:
            _signing(resend_session, renderer, fake, sender).send_match(match.id, admin_id)
        except InvalidState as exc:
            results["resend"] = exc
        except BaseException as exc:  # noqa: BLE001 -- surfaced on the main thread below
            errors.append(exc)

    first_fn, second_fn = (deliver, resend) if apply_first else (resend, deliver)
    try:
        gate.execute(select(Freelancer.id).where(Freelancer.id == freelancer_id).with_for_update())
        first_worker = threading.Thread(target=first_fn)
        first_worker.start()
        first_worker.join(timeout=0.5)
        assert first_worker.is_alive(), "the first operation did not wait on the freelancer's row"
        second_worker = threading.Thread(target=second_fn)
        second_worker.start()
        second_worker.join(timeout=0.5)
        assert second_worker.is_alive(), "the second operation did not wait on the freelancer's row"
        gate.commit()
        first_worker.join(timeout=5)
        second_worker.join(timeout=5)
        assert not first_worker.is_alive() and not second_worker.is_alive()
    finally:
        gate.close()
        webhook_session.close()
        resend_session.close()
    assert not errors, errors
    # The letter was already `inviato` before either thread ran: a resend of a match
    # already out for signature always finds nothing left to send, in either order.
    assert isinstance(results.get("resend"), InvalidState)


def test_finish_downloads_and_mails_the_signed_copy_once(clean: Session) -> None:
    admin_id, freelancer_id, company_id = _setup(clean)
    renderer, fake, sender = FakeRenderer(draft=False), FakeDocumenso(), RecordingSender()
    _sent(clean, renderer, fake, sender, freelancer_id, company_id, admin_id)
    envelope = _envelope_of(_framework_of(clean, freelancer_id))
    fake.sign(envelope, SIGNED_AT)
    signing = _signing(clean, renderer, fake, sender)
    signed = signing.apply(_webhook(fake, envelope, "DOCUMENT_COMPLETED"))
    assert signed is not None
    before = len(sender.sent)

    signing.finish(signed)
    signing.finish(signed)

    quadro = _framework_of(clean, freelancer_id)
    assert quadro.signed_pdf == fake.signed_pdf(envelope)
    downloads = [call for call in fake.calls if call[1].endswith("/download?version=signed")]
    assert len(downloads) == 1
    copies = [mail for mail in sender.sent[before:] if mail.attachments]
    assert [(mail.to, mail.subject) for mail in copies] == [
        ("ada@studio.it", "Firmato: contratto quadro rebase"),
        (CONTRACTS_MAIL, "Firmato da Ada Lovelace: contratto quadro rebase"),
    ]
    assert all(
        mail.attachments[0].filename == "contratto-quadro-v0.1-firmato.pdf"
        and mail.attachments[0].content == quadro.signed_pdf
        for mail in copies
    )


def test_a_download_that_fails_is_done_by_the_next_finish(clean: Session) -> None:
    admin_id, freelancer_id, company_id = _setup(clean)
    renderer, fake, sender = FakeRenderer(draft=False), FakeDocumenso(), RecordingSender()
    _sent(clean, renderer, fake, sender, freelancer_id, company_id, admin_id)
    envelope = _envelope_of(_framework_of(clean, freelancer_id))
    fake.sign(envelope, SIGNED_AT)
    signing = _signing(clean, renderer, fake, sender)
    signed = signing.apply(_webhook(fake, envelope, "DOCUMENT_COMPLETED"))
    assert signed is not None
    fake.fail("download", 500, "Internal server error")

    signing.finish(signed)
    assert _framework_of(clean, freelancer_id).signed_pdf is None

    signing.finish(signed)
    assert _framework_of(clean, freelancer_id).signed_pdf == fake.signed_pdf(envelope)


def test_a_framework_signed_late_at_night_releases_its_letter_with_the_rome_date(
    clean: Session,
) -> None:
    """Review Focus 2: signed at 23:30 UTC on 30 September, which is 1 October in Rome.
    The letter that waited leaves on its own, citing that date, mailed as its own."""
    admin_id, freelancer_id, company_id = _setup(clean)
    renderer, fake, sender = FakeRenderer(draft=False), FakeDocumenso(), RecordingSender()
    match = _sent(clean, renderer, fake, sender, freelancer_id, company_id, admin_id)
    envelope = _envelope_of(_framework_of(clean, freelancer_id))
    fake.sign(envelope, SIGNED_AT)
    signing = _signing(clean, renderer, fake, sender, today=date(2026, 10, 1))
    signed = signing.apply(_webhook(fake, envelope, "DOCUMENT_COMPLETED"))
    assert signed is not None

    signing.finish(signed)

    letter = _letter_of(clean, match.id)
    assert letter.stato == "inviato"
    assert letter.data["data-contratto-quadro"] == "1° ottobre 2026"
    assert letter.data["firma-rebase"] == "Documento emesso da rebase il 1° ottobre 2026"
    assert letter.sent_by == admin_id
    assert sender.sent[-1].subject == f"Da firmare: lettera di incarico n. {letter.numero}"
    assert len(fake.envelopes) == 2


def test_a_release_documenso_refuses_waits_for_the_next_finish(clean: Session) -> None:
    admin_id, freelancer_id, company_id = _setup(clean)
    renderer, fake, sender = FakeRenderer(draft=False), FakeDocumenso(), RecordingSender()
    match = _sent(clean, renderer, fake, sender, freelancer_id, company_id, admin_id)
    envelope = _envelope_of(_framework_of(clean, freelancer_id))
    fake.sign(envelope, SIGNED_AT)
    signing = _signing(clean, renderer, fake, sender)
    signed = signing.apply(_webhook(fake, envelope, "DOCUMENT_COMPLETED"))
    assert signed is not None
    fake.fail("create", 500, "Internal server error")

    signing.finish(signed)
    assert _letter_of(clean, match.id).stato == "in_attesa"

    signing.finish(signed)
    assert _letter_of(clean, match.id).stato == "inviato"


def test_a_draft_matchs_letter_is_not_released(clean: Session) -> None:
    """Only a match an admin sent is released; a draft's letter leaves with its match."""
    admin_id, freelancer_id, company_id = _setup(clean)
    renderer, fake, sender = FakeRenderer(draft=False), FakeDocumenso(), RecordingSender()
    sent = _sent(clean, renderer, fake, sender, freelancer_id, company_id, admin_id)
    envelope = _envelope_of(_framework_of(clean, freelancer_id))
    draft = _draft(clean, renderer, freelancer_id, company_id, admin_id)
    fake.sign(envelope, SIGNED_AT)
    signing = _signing(clean, renderer, fake, sender)
    signed = signing.apply(_webhook(fake, envelope, "DOCUMENT_COMPLETED"))
    assert signed is not None

    signing.finish(signed)

    assert _letter_of(clean, sent.id).stato == "inviato"
    assert _letter_of(clean, draft.id).stato == "in_attesa"


def test_a_signed_letter_turns_its_match_active(clean: Session) -> None:
    admin_id, freelancer_id, company_id = _setup(clean)
    _active_framework(clean, freelancer_id, admin_id)
    renderer, fake, sender = FakeRenderer(draft=False), FakeDocumenso(), RecordingSender()
    match = _sent(clean, renderer, fake, sender, freelancer_id, company_id, admin_id)
    envelope = _envelope_of(_letter_of(clean, match.id))
    fake.sign(envelope, SIGNED_AT)

    _signing(clean, renderer, fake, sender).apply(_webhook(fake, envelope, "DOCUMENT_COMPLETED"))

    clean.expire_all()
    assert clean.get(Match, match.id).stato == "attivo"  # type: ignore[union-attr]
    assert _letter_of(clean, match.id).stato == "firmato"


def test_a_refused_document_is_cancelled_with_the_freelancers_reason(clean: Session) -> None:
    admin_id, freelancer_id, company_id = _setup(clean)
    renderer, fake, sender = FakeRenderer(draft=False), FakeDocumenso(), RecordingSender()
    match = _sent(clean, renderer, fake, sender, freelancer_id, company_id, admin_id)
    envelope = _envelope_of(_framework_of(clean, freelancer_id))
    fake.reject(envelope, "La PEC indicata non è la mia")

    signed = _signing(clean, renderer, fake, sender).apply(
        _webhook(fake, envelope, "DOCUMENT_REJECTED")
    )

    quadro = _framework_of(clean, freelancer_id)
    assert signed is None
    assert (quadro.stato, quadro.cancel_reason) == (
        "annullato",
        "Rifiutato dal freelance: La PEC indicata non è la mia",
    )
    # The letter keeps waiting: «Invia per la firma» on its match writes a new framework.
    assert _letter_of(clean, match.id).stato == "in_attesa"


def test_a_cancellation_on_documenso_cancels_and_a_late_event_is_ignored(clean: Session) -> None:
    admin_id, freelancer_id, company_id = _setup(clean)
    renderer, fake, sender = FakeRenderer(draft=False), FakeDocumenso(), RecordingSender()
    _sent(clean, renderer, fake, sender, freelancer_id, company_id, admin_id)
    envelope = _envelope_of(_framework_of(clean, freelancer_id))
    fake.client().cancel(envelope, "Annullato a mano.")
    signing = _signing(clean, renderer, fake, sender)

    assert signing.apply(_webhook(fake, envelope, "DOCUMENT_CANCELLED")) is None
    quadro = _framework_of(clean, freelancer_id)
    assert (quadro.stato, quadro.cancel_reason) == ("annullato", "Annullato su Documenso.")
    # An envelope already cancelled, or one the hub never recorded, moves nothing.
    fake.sign(envelope, SIGNED_AT)
    assert signing.apply(_webhook(fake, envelope, "DOCUMENT_COMPLETED")) is None
    assert _framework_of(clean, freelancer_id).stato == "annullato"
    assert signing.apply(Outcome("envelope_sconosciuto", "completed", SIGNED_AT)) is None


def test_a_release_with_no_mail_sender_leaves_the_letters_waiting(clean: Session) -> None:
    """Fix round 1, M4: `send_match` refuses up front without a mail sender, so a release
    must not dispatch a letter to Documenso that nobody could then be told about. The
    sender is checked before Documenso is touched, not after."""
    admin_id, freelancer_id, company_id = _setup(clean)
    renderer, fake = FakeRenderer(draft=False), FakeDocumenso()
    match = _sent(clean, renderer, fake, RecordingSender(), freelancer_id, company_id, admin_id)
    envelope = _envelope_of(_framework_of(clean, freelancer_id))
    fake.sign(envelope, SIGNED_AT)
    signing = _signing(clean, renderer, fake, RecordingSender())
    signed = signing.apply(_webhook(fake, envelope, "DOCUMENT_COMPLETED"))
    assert signed is not None
    envelopes_before = len(fake.envelopes)

    _signing(clean, renderer, fake, None).finish(signed)

    assert _letter_of(clean, match.id).stato == "in_attesa"
    assert len(fake.envelopes) == envelopes_before


def test_two_concurrent_finishes_download_and_mail_the_signed_copy_once(
    hub_engine: Engine, clean: Session
) -> None:
    """Fix round 1, M5: the webhook's own `finish` and an admin's «Aggiorna stato»
    refresh can overlap on the same freshly signed document. `_store_signed_copy`'s row
    lock serialises them: exactly one download, and exactly one pair of signed-copy
    mails (to the freelancer and to rebase), however many callers race for it."""
    admin_id, freelancer_id, company_id = _setup(clean)
    _active_framework(clean, freelancer_id, admin_id)
    renderer, fake, sender = FakeRenderer(draft=False), FakeDocumenso(), RecordingSender()
    match = _draft(clean, renderer, freelancer_id, company_id, admin_id)
    _signing(clean, renderer, fake, sender).send_match(match.id, admin_id)
    envelope = _envelope_of(_letter_of(clean, match.id))
    fake.sign(envelope, SIGNED_AT)
    signed = _signing(clean, renderer, fake, sender).apply(
        _webhook(fake, envelope, "DOCUMENT_COMPLETED")
    )
    assert signed is not None
    factory = session_factory(hub_engine)
    gate, first_session, second_session = factory(), factory(), factory()
    before = len(sender.sent)

    def call_finish(session: Session) -> None:
        _signing(session, renderer, fake, sender).finish(signed)

    try:
        gate.execute(
            select(ContractDocument.id).where(ContractDocument.id == signed).with_for_update()
        )
        first = threading.Thread(target=call_finish, args=(first_session,))
        second = threading.Thread(target=call_finish, args=(second_session,))
        first.start()
        first.join(timeout=0.5)
        assert first.is_alive(), "the first finish did not wait on the document row"
        second.start()
        second.join(timeout=0.5)
        assert second.is_alive(), "the second finish did not wait on the document row"
        gate.commit()
        first.join(timeout=5)
        second.join(timeout=5)
        assert not first.is_alive() and not second.is_alive()
    finally:
        gate.close()
        first_session.close()
        second_session.close()
    clean.expire_all()
    letter = _letter_of(clean, match.id)
    assert letter.signed_pdf == fake.signed_pdf(envelope)
    downloads = [call for call in fake.calls if call[1].endswith("/download?version=signed")]
    assert len(downloads) == 1
    copies = [mail for mail in sender.sent[before:] if mail.attachments]
    assert len(copies) == 2
