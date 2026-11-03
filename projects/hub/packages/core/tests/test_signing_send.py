"""«Invia per la firma»: what leaves, in which order, and what a refusal of Documenso, the mail or
the signer setting leaves behind (REB-387 phase 3, REB-406, REB-432). Helpers:
`signing_fixtures`."""

import threading
from datetime import date
from uuid import UUID

import pytest
from fakes_contracts import FakeRenderer
from fakes_documenso import FakeDocumenso
from match_fixtures import (
    FRAMEWORK_VERSION,
    SIGNER,
    TODAY,
    _body,
    _documents,
    _framework,
    _setup,
)
from signing_fixtures import (
    CONTRACTS_MAIL,
    NOW,
    SIGNED_AT,
    RefusingSender,
    _draft,
    _fiscal,
    _framework_of,
    _letter_of,
    _matches,
    _signing,
    clean,  # noqa: F401  (fixture)
)
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from rebase_core import signing as signing_module
from rebase_core.audit import AdminActionService
from rebase_core.contract_schemas import MatchRead, SendReport
from rebase_core.contracts.fields import ContractFailed
from rebase_core.db import session_factory
from rebase_core.errors import (
    DocumensoFailed,
    InvalidState,
    SigningUnavailable,
)
from rebase_core.mail import RecordingSender
from rebase_core.matches import MatchService
from rebase_core.models import ContractDocument
from rebase_core.signing import SigningService


def test_the_first_send_hands_documenso_the_framework_and_the_letter_waits(
    clean: Session,
) -> None:
    admin_id, freelancer_id, company_id = _setup(clean)
    renderer, fake, sender = FakeRenderer(draft=False), FakeDocumenso(), RecordingSender()
    match = _draft(clean, renderer, freelancer_id, company_id, admin_id)

    report = _signing(clean, renderer, fake, sender).send_match(match.id, admin_id)

    assert (report.sent, report.mail_sent) == ("framework", True)
    assert (report.match.status, report.match.letter.status) == ("awaiting_signature", "pending")
    framework = _framework_of(clean, freelancer_id)
    [envelope] = fake.envelopes.values()
    assert framework.status == "sent"
    assert (framework.documenso_id, framework.documenso_item_id) == (envelope.id, envelope.item_id)
    assert framework.signing_url == f"https://firma.letsrebase.test/sign/{envelope.token}"
    assert (framework.sent_at, framework.sent_by) == (NOW, admin_id)
    assert envelope.payload["title"] == "Contratto quadro rebase"
    assert envelope.payload["externalId"] == str(framework.id)
    assert envelope.filename == f"framework-agreement-v{FRAMEWORK_VERSION}.pdf"
    assert envelope.payload["meta"]["distributionMethod"] == "NONE"
    assert not any(envelope.payload["meta"]["emailSettings"].values())
    fields = envelope.payload["recipients"][0]["fields"]
    assert [field["type"] for field in fields] == ["DATE", "SIGNATURE", "SIGNATURE"]
    assert envelope.payload["recipients"][0]["email"] == "ada@studio.it"
    assert renderer.signing[-1] is True
    # The blanks Documenso's fields come from are read off the signing copy, the same
    # one just typeset (REB-406).
    assert renderer.blanks_signing[-1] is True
    [mail] = sender.sent
    assert (mail.to, mail.subject) == ("ada@studio.it", "Da firmare: contratto quadro rebase")
    assert framework.signing_url in mail.text
    assert AdminActionService(clean).timeline("match", match.id)[0].kind == "documents_sent"


def test_the_sent_copy_says_the_day_it_left_and_prints_the_tax_data_saved_since_the_draft(
    clean: Session,
) -> None:
    """REB-390: the draft was saved on the 23rd with the old address; the admin
    corrected the tax data and sends on the 25th. What leaves is today's."""
    admin_id, freelancer_id, company_id = _setup(clean)
    renderer, fake = FakeRenderer(draft=False), FakeDocumenso()
    match = _draft(clean, renderer, freelancer_id, company_id, admin_id)
    _fiscal(clean, freelancer_id, admin_id, domicile="Corso Como 1, Milano")

    _signing(clean, renderer, fake, RecordingSender(), today=date(2026, 9, 25)).send_match(
        match.id, admin_id
    )

    framework = _framework_of(clean, freelancer_id)
    assert framework.data["firma-rebase"] == "Documento emesso da rebase il 25 settembre 2026"
    assert framework.data["professionista-domicilio"] == "Corso Como 1, Milano"
    document, data = renderer.calls[-1]
    assert document == "framework-agreement" and data == framework.data
    assert framework.pdf == b"%PDF-1.7 fake framework-agreement"
    assert framework.draft_text is False


def test_with_an_active_framework_the_letter_leaves_and_cites_its_signature(
    clean: Session,
) -> None:
    admin_id, freelancer_id, company_id = _setup(clean)
    _framework(clean, freelancer_id, admin_id, signed_at=SIGNED_AT)
    renderer, fake, sender = FakeRenderer(draft=False), FakeDocumenso(), RecordingSender()
    match = _draft(clean, renderer, freelancer_id, company_id, admin_id)
    assert match.letter.status == "generated"

    report = _signing(clean, renderer, fake, sender).send_match(match.id, admin_id)

    assert (report.sent, report.match.letter.status) == ("letter", "sent")
    letter = _letter_of(clean, match.id)
    assert letter.data["data-contratto-quadro"] == "1° ottobre 2026"
    [envelope] = fake.envelopes.values()
    assert envelope.payload["title"] == f"Lettera di incarico n. {letter.number}"
    assert envelope.filename == f"engagement-letter-{letter.number}.pdf"
    assert [mail.subject for mail in sender.sent] == [
        f"Da firmare: lettera di incarico n. {letter.number}"
    ]
    # Spec § 1h, proven on the sent copy itself (REB-406): what rebase
    # agreed with the client (the request's 777.77 a day) reaches neither the data
    # handed to the renderer nor the mail that tells the freelancer to sign.
    document, data = renderer.calls[-1]
    assert document == "engagement-letter"
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

    assert (report.sent, report.mail_sent) == (None, None)
    assert (report.match.status, report.match.letter.status) == ("awaiting_signature", "pending")
    assert len(fake.envelopes) == 1 and len(sender.sent) == 1
    # A send that sends nothing leaves no `documents_sent` trail of its own: the second
    # match's timeline still holds only what `create` wrote for it.
    assert [a.kind for a in AdminActionService(clean).timeline("match", second.id)] == [
        "match_created"
    ]


def test_two_matches_sent_at_once_send_the_framework_once(
    hub_engine: Engine, clean: Session
) -> None:
    """REB-390: two admins send two matches of one freelancer at the same moment,
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
        held.status = "sent"
        held.documenso_id, held.documenso_item_id = "envelope_primo", "envelope_item_primo"
        held.signing_url = "https://firma.letsrebase.test/sign/primo"
        first_admin.commit()
        worker.join(timeout=5)
        assert not worker.is_alive()
    finally:
        first_admin.close()
        second_admin.close()
    assert [(r.sent, r.match.letter.status) for r in reports] == [(None, "pending")]
    assert fake.envelopes == {}


def test_a_draft_text_never_leaves(clean: Session) -> None:
    admin_id, freelancer_id, company_id = _setup(clean)
    renderer, fake, sender = FakeRenderer(draft=True), FakeDocumenso(), RecordingSender()
    match = _draft(clean, renderer, freelancer_id, company_id, admin_id)

    with pytest.raises(InvalidState, match="ancora una bozza") as caught:
        _signing(clean, renderer, fake, sender).send_match(match.id, admin_id)

    # The way out is named too (REB-406).
    assert "REBASE_CONTRACTS_ALLOW_DRAFT" in caught.value.message
    assert fake.calls == [] and sender.sent == []
    assert _matches(clean, renderer).reads.get(match.id).status == "draft"
    assert _framework_of(clean, freelancer_id).status == "generated"


def test_a_draft_text_leaves_on_the_preview_when_the_setting_allows_it(clean: Session) -> None:
    """REB-406: `REBASE_CONTRACTS_ALLOW_DRAFT` lets a draft leave, only
    where that setting is true (the preview's own `.env`), so the text can be tested end
    to end with Documenso before it loses its `draft` status."""
    admin_id, freelancer_id, company_id = _setup(clean)
    renderer, fake, sender = FakeRenderer(draft=True), FakeDocumenso(), RecordingSender()
    match = _draft(clean, renderer, freelancer_id, company_id, admin_id)

    report = _signing(clean, renderer, fake, sender, allow_draft=True).send_match(
        match.id, admin_id
    )

    assert report.sent == "framework"
    framework = _framework_of(clean, freelancer_id)
    assert framework.draft_text is True
    assert framework.status == "sent"
    [envelope] = fake.envelopes.values()
    assert envelope.pdf == framework.pdf == b"%PDF-1.7 fake framework-agreement"


def test_without_rebases_signer_nothing_leaves(clean: Session) -> None:
    admin_id, freelancer_id, company_id = _setup(clean)
    renderer, fake = FakeRenderer(draft=False), FakeDocumenso()
    match = _draft(clean, renderer, freelancer_id, company_id, admin_id)

    with pytest.raises(SigningUnavailable, match="REBASE_SIGNER_JSON") as caught:
        _signing(clean, renderer, fake, RecordingSender(), signer={}).send_match(match.id, admin_id)

    assert "rebase-sede" in caught.value.message
    assert fake.calls == []


def test_documenso_refusing_the_distribution_marks_nothing_sent(clean: Session) -> None:
    """REB-406: the envelope exists on Documenso, the distribution fails. Nothing
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
    framework = _framework_of(clean, freelancer_id)
    assert (framework.status, framework.documenso_id, framework.signing_url, framework.sent_at) == (
        "generated",
        None,
        None,
        None,
    )
    assert _matches(clean, renderer).reads.get(match.id).status == "draft"
    assert sender.sent == []
    again = signing.send_match(match.id, admin_id)
    assert again.sent == "framework"
    assert again.match.letter.number == match.letter.number


def test_a_refused_mail_leaves_the_document_sent_and_says_so(clean: Session) -> None:
    """REB-406, the other half: Documenso took it, the mail provider did not."""
    admin_id, freelancer_id, company_id = _setup(clean)
    renderer = FakeRenderer(draft=False)
    match = _draft(clean, renderer, freelancer_id, company_id, admin_id)

    report = _signing(clean, renderer, FakeDocumenso(), RefusingSender()).send_match(
        match.id, admin_id
    )

    assert (report.sent, report.mail_sent) == ("framework", False)
    assert _framework_of(clean, freelancer_id).status == "sent"


def test_without_documenso_or_without_mail_the_send_says_why(clean: Session) -> None:
    admin_id, freelancer_id, company_id = _setup(clean)
    renderer = FakeRenderer(draft=False)
    match = _draft(clean, renderer, freelancer_id, company_id, admin_id)
    with pytest.raises(SigningUnavailable, match="Documenso"):
        _signing(clean, renderer, None, RecordingSender()).send_match(match.id, admin_id)
    with pytest.raises(SigningUnavailable, match="email"):
        _signing(clean, renderer, FakeDocumenso(), None).send_match(match.id, admin_id)
    assert _matches(clean, renderer).reads.get(match.id).status == "draft"


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
    """A framework agreement refused or cancelled while its letter waited:
    «Invia per la firma» on the match writes a new one from today's tax data and sends it."""
    admin_id, freelancer_id, company_id = _setup(clean)
    renderer, fake = FakeRenderer(draft=False), FakeDocumenso()
    match = _draft(clean, renderer, freelancer_id, company_id, admin_id)
    signing = _signing(clean, renderer, fake, RecordingSender())
    signing.send_match(match.id, admin_id)
    refused = _framework_of(clean, freelancer_id)
    refused.status, refused.cancel_reason = (
        "cancelled",
        "Rifiutato dal freelance sul sito di firma.",
    )
    clean.commit()

    report = signing.send_match(match.id, admin_id)

    assert report.sent == "framework"
    assert [d.status for d in _documents(clean, freelancer_id, "framework")] == [
        "cancelled",
        "sent",
    ]
    assert len(fake.envelopes) == 2


def test_to_create_match_racing_to_send_waits_for_it_and_does_not_annul_the_framework_it_dispatched(
    monkeypatch: pytest.MonkeyPatch, hub_engine: Engine, clean: Session
) -> None:
    """Global Constraints' lock order: a send in progress must block a concurrent
    `create()` («Crea match») for a second match of the same freelancer -- the shape of
    `test_matches.test_two_admins_matching_the_same_freelancer_at_once_never_leave_two_open_frameworks`,
    but with a send on one side. `active_framework` is the next call `send_match` makes
    after taking the freelancer's row lock, so pausing it there proves the lock is taken
    before, not merely before the commit. Once the send commits its framework as
    `sent`, `create()` must find it already out for signature and leave it alone
    rather than annul it as a stale `generated` one (`pending_framework`, `MatchService.
    create`)."""
    admin_id, freelancer_id, company_id = _setup(clean)
    renderer, fake = FakeRenderer(draft=False), FakeDocumenso()
    match = _draft(clean, renderer, freelancer_id, company_id, admin_id)
    factory = session_factory(hub_engine)
    sender_session, creator_session = factory(), factory()
    paused, release = threading.Event(), threading.Event()
    real_active_framework = signing_module.active_framework

    def paced_active_framework(
        session: Session, freelancer_id: UUID, regime: str
    ) -> ContractDocument | None:
        if session is sender_session:
            paused.set()
            assert release.wait(timeout=5), "the test never released the send"
        return real_active_framework(session, freelancer_id, regime)

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
    assert second.letter.status == "pending"
    assert [d.status for d in _documents(clean, freelancer_id, "framework")] == ["sent"]


def test_a_malformed_signer_setting_does_not_break_construction_or_a_read(clean: Session) -> None:
    """REB-406: a malformed REBASE_SIGNER_JSON must not turn every
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

    assert signing.matches.reads.get(match.id).status == "draft"


def test_a_malformed_signer_setting_503s_only_the_send_it_breaks(clean: Session) -> None:
    """REB-406: parsed lazily, so the refusal names the setting only
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
    """REB-406: the lazy path (`signer_json=""`, the default) 503s
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


def test_a_refused_distribute_deletes_the_orphaned_envelope(clean: Session) -> None:
    """REB-432: `create` leaves a still-`DRAFT` envelope on Documenso, `distribute` is
    refused. The fix's best-effort delete removes it outright -- Documenso deletes a
    draft or a pending envelope with no refusal by its state (probe § 4) -- so a
    retried send does not pile up drafts under the same externalId, and the admin
    reads the original refusal alone. The card's «Done when»: no envelope is left on
    the fake."""
    admin_id, freelancer_id, company_id = _setup(clean)
    renderer, fake, sender = FakeRenderer(draft=False), FakeDocumenso(), RecordingSender()
    match = _draft(clean, renderer, freelancer_id, company_id, admin_id)
    fake.fail("distribute", 400, "Recipient is missing a signature field")

    with pytest.raises(DocumensoFailed) as caught:
        _signing(clean, renderer, fake, sender).send_match(match.id, admin_id)

    assert caught.value.message == (
        "Documenso ha rifiutato la richiesta: Recipient is missing a signature field"
    )
    assert fake.envelopes == {}


def test_a_refused_delete_falls_back_to_cancelling_the_orphan(
    clean: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """REB-432: a delete Documenso refuses for any reason falls back to a cancel, which
    a still-`PENDING` envelope still answers (probe § 4). `get` succeeds (the envelope
    already exists on Documenso, as after a real create) and this test then moves it to
    `PENDING` itself, simulating Documenso having processed the distribute server-side
    even though the client's own parsing of the answer fails -- the same setup REB-406's
    own orphan test used, since `fake.fail('distribute', ...)` alone never advances the
    envelope past `DRAFT`. The delete is then forced to fail too, so the fallback runs;
    the original refusal is still what reaches the admin."""
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
    fake.fail("delete", 500, "Internal server error")

    with pytest.raises(DocumensoFailed) as caught:
        _signing(clean, renderer, fake, sender).send_match(match.id, admin_id)

    assert caught.value.message == (
        "Documenso ha rifiutato la richiesta: Recipient is missing a signature field"
    )
    [envelope] = fake.envelopes.values()
    assert envelope.status == "CANCELLED"
