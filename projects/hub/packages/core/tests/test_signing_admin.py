"""The admin's actions (REB-407): «Aggiorna stato», «Reinvia email», «Annulla» on a framework
agreement or a match, «Registra disdetta», the cancellation mail and the lock order. Helpers:
`signing_fixtures`."""

import threading
from uuid import UUID

import pytest
from fakes_contracts import FakeRenderer
from fakes_documenso import FakeDocumenso
from match_fixtures import (
    _setup,
)
from signing_fixtures import (
    NOW,
    SIGNED_AT,
    RefusingSender,
    _active_framework,
    _draft,
    _envelope_of,
    _framework_of,
    _letter_of,
    _matches,
    _sent,
    _signed_framework,
    _signing,
    _webhook,
    clean,  # noqa: F401  (fixture)
)
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from rebase_core.audit import AdminActionService
from rebase_core.contract_schemas import MatchRead, SendReport
from rebase_core.db import session_factory
from rebase_core.documenso import (
    UNREACHABLE,
)
from rebase_core.errors import (
    DocumensoFailed,
    InvalidState,
    NotFound,
    SigningUnavailable,
)
from rebase_core.mail import RecordingSender
from rebase_core.models import Freelancer, Match


def test_refresh_applies_a_signature_the_webhook_never_delivered(clean: Session) -> None:
    """Probe § 11.3: four attempts in 160 ms and then nothing. «Aggiorna stato» reads the
    envelope and does everything the webhook and `finish` would have done."""
    renderer, fake, sender = FakeRenderer(draft=False), FakeDocumenso(), RecordingSender()
    _admin_id, freelancer_id, match, envelope = _signed_framework(clean, renderer, fake, sender)

    read = _signing(clean, renderer, fake, sender).admin.refresh(
        _framework_of(clean, freelancer_id).id
    )

    assert (read.status, read.active, read.has_pdf_signed, read.signed_at) == (
        "signed",
        True,
        True,
        SIGNED_AT,
    )
    assert _framework_of(clean, freelancer_id).signed_pdf == fake.signed_pdf(envelope)
    assert _letter_of(clean, match.id).status == "sent"


def test_refresh_of_to_signed_document_with_no_stored_copy_downloads_and_stores_it(
    clean: Session,
) -> None:
    """The recovery path when a document was already confirmed `signed` but its
    download failed on the first `finish` (a crash, or Documenso refusing that one
    call): `refresh`'s own read of the envelope applies nothing new (`apply` is a
    no-op on a document that is not `sent` any more); `finish`, called
    unconditionally, is what retries the download and stores the copy this time."""
    renderer, fake, sender = FakeRenderer(draft=False), FakeDocumenso(), RecordingSender()
    _admin_id, freelancer_id, _match, envelope = _signed_framework(clean, renderer, fake, sender)
    signing = _signing(clean, renderer, fake, sender)
    signed = signing.apply(_webhook(fake, envelope, "DOCUMENT_COMPLETED"))
    assert signed is not None
    fake.fail("download", 500, "Internal server error")
    signing.recovery.finish(signed)
    framework = _framework_of(clean, freelancer_id)
    assert (framework.status, framework.signed_pdf) == ("signed", None)

    read = signing.admin.refresh(framework.id)

    assert (read.status, read.has_pdf_signed) == ("signed", True)
    assert _framework_of(clean, freelancer_id).signed_pdf == fake.signed_pdf(envelope)


def test_refresh_of_a_document_still_waiting_changes_nothing(clean: Session) -> None:
    admin_id, freelancer_id, company_id = _setup(clean)
    renderer, fake, sender = FakeRenderer(draft=False), FakeDocumenso(), RecordingSender()
    _sent(clean, renderer, fake, sender, freelancer_id, company_id, admin_id)
    mails = len(sender.sent)

    read = _signing(clean, renderer, fake, sender).admin.refresh(
        _framework_of(clean, freelancer_id).id
    )

    assert read.status == "sent"
    assert len(sender.sent) == mails


def test_refresh_of_a_document_that_never_left_says_so(clean: Session) -> None:
    admin_id, freelancer_id, company_id = _setup(clean)
    renderer = FakeRenderer(draft=False)
    _draft(clean, renderer, freelancer_id, company_id, admin_id)
    with pytest.raises(InvalidState, match="mai partito"):
        _signing(clean, renderer, FakeDocumenso(), RecordingSender()).admin.refresh(
            _framework_of(clean, freelancer_id).id
        )


def test_resend_mails_the_same_link_again_and_leaves_a_trace(clean: Session) -> None:
    admin_id, freelancer_id, company_id = _setup(clean)
    renderer, fake, sender = FakeRenderer(draft=False), FakeDocumenso(), RecordingSender()
    _sent(clean, renderer, fake, sender, freelancer_id, company_id, admin_id)
    framework = _framework_of(clean, freelancer_id)

    _signing(clean, renderer, fake, sender).admin.resend_mail(framework.id, admin_id)

    first, again = sender.sent[-2:]
    assert first.subject == again.subject == "Da firmare: contratto quadro rebase"
    assert framework.signing_url is not None and framework.signing_url in again.text
    trail = AdminActionService(clean).timeline("freelancer", freelancer_id)
    assert trail[0].kind == "mail_resent"


def test_resend_with_a_refusing_sender_raises_and_records_no_trail(clean: Session) -> None:
    admin_id, freelancer_id, company_id = _setup(clean)
    renderer, fake = FakeRenderer(draft=False), FakeDocumenso()
    _sent(clean, renderer, fake, RecordingSender(), freelancer_id, company_id, admin_id)
    framework = _framework_of(clean, freelancer_id)

    with pytest.raises(SigningUnavailable, match="non è partita"):
        _signing(clean, renderer, fake, RefusingSender()).admin.resend_mail(framework.id, admin_id)

    assert AdminActionService(clean).timeline("freelancer", freelancer_id) == []


def test_resend_refuses_a_document_that_is_not_waiting(clean: Session) -> None:
    admin_id, freelancer_id, company_id = _setup(clean)
    renderer = FakeRenderer(draft=False)
    _draft(clean, renderer, freelancer_id, company_id, admin_id)
    with pytest.raises(InvalidState, match="aspetta la firma"):
        _signing(clean, renderer, FakeDocumenso(), RecordingSender()).admin.resend_mail(
            _framework_of(clean, freelancer_id).id, admin_id
        )


def test_cancelling_a_framework_out_for_signature_cancels_its_envelope_first(
    clean: Session,
) -> None:
    admin_id, freelancer_id, company_id = _setup(clean)
    renderer, fake, sender = FakeRenderer(draft=False), FakeDocumenso(), RecordingSender()
    match = _sent(clean, renderer, fake, sender, freelancer_id, company_id, admin_id)
    envelope = _envelope_of(_framework_of(clean, freelancer_id))

    read = _signing(clean, renderer, fake, sender).admin.cancel_document(
        _framework_of(clean, freelancer_id).id, admin_id
    )

    assert (read.status, read.cancel_reason) == ("cancelled", "Annullato da rebase.")
    assert fake.envelopes[envelope].status == "CANCELLED"
    assert _letter_of(clean, match.id).status == "pending"
    trail = AdminActionService(clean).timeline("freelancer", freelancer_id)
    assert trail[0].kind == "document_cancelled"


def test_a_cancel_documenso_refuses_leaves_the_document_as_it_was(clean: Session) -> None:
    """The freelancer signed a moment before the admin pressed «Annulla»: Documenso
    cancels only a `PENDING` envelope, so the hub reads an Italian sentence pointing at
    «Aggiorna stato» rather than Documenso's own English one, and changes nothing."""
    renderer, fake, sender = FakeRenderer(draft=False), FakeDocumenso(), RecordingSender()
    admin_id, freelancer_id, _match, _envelope = _signed_framework(clean, renderer, fake, sender)
    with pytest.raises(DocumensoFailed, match="«Aggiorna stato»") as raised:
        _signing(clean, renderer, fake, sender).admin.cancel_document(
            _framework_of(clean, freelancer_id).id, admin_id
        )
    assert "Only pending documents can be cancelled" in raised.value.detail
    assert _framework_of(clean, freelancer_id).status == "sent"


def test_a_cancel_when_documenso_is_unreachable_keeps_its_own_sentence(clean: Session) -> None:
    """REB-391: Documenso not answering at all is not the same as Documenso refusing to
    cancel an already-completed envelope. Translating it to `CANCEL_REFUSED` would tell
    the admin the document might already be signed, when it is merely that Documenso is
    down; its own «riprova tra qualche minuto» sentence must reach the admin unchanged,
    and the document stays as it was either way."""
    admin_id, freelancer_id, company_id = _setup(clean)
    renderer, fake, sender = FakeRenderer(draft=False), FakeDocumenso(), RecordingSender()
    _sent(clean, renderer, fake, sender, freelancer_id, company_id, admin_id)
    fake.down("cancel")

    with pytest.raises(DocumensoFailed) as raised:
        _signing(clean, renderer, fake, sender).admin.cancel_document(
            _framework_of(clean, freelancer_id).id, admin_id
        )

    assert raised.value.message == UNREACHABLE
    assert _framework_of(clean, freelancer_id).status == "sent"


def test_cancelling_a_matchs_letter_documenso_refuses_leaves_it_as_it_was(
    clean: Session,
) -> None:
    """The same translation on the other cancel path: a letter Documenso will not
    cancel any more keeps its Italian sentence, and the match stays untouched."""
    admin_id, freelancer_id, company_id = _setup(clean)
    _active_framework(clean, freelancer_id, admin_id)
    renderer, fake, sender = FakeRenderer(draft=False), FakeDocumenso(), RecordingSender()
    match = _sent(clean, renderer, fake, sender, freelancer_id, company_id, admin_id)
    envelope = _envelope_of(_letter_of(clean, match.id))
    fake.sign(envelope, SIGNED_AT)

    with pytest.raises(DocumensoFailed, match="«Aggiorna stato»") as raised:
        _signing(clean, renderer, fake, sender).admin.cancel_match(match.id, admin_id)

    assert "Only pending documents can be cancelled" in raised.value.detail
    assert _letter_of(clean, match.id).status == "sent"
    assert [a.kind for a in AdminActionService(clean).timeline("match", match.id)] == [
        "documents_sent",
        "match_created",
    ]


def test_a_letter_is_cancelled_with_its_match_not_alone(clean: Session) -> None:
    admin_id, freelancer_id, company_id = _setup(clean)
    renderer = FakeRenderer(draft=False)
    match = _draft(clean, renderer, freelancer_id, company_id, admin_id)
    with pytest.raises(InvalidState, match="con il suo match"):
        _signing(clean, renderer, FakeDocumenso(), RecordingSender()).admin.cancel_document(
            _letter_of(clean, match.id).id, admin_id
        )


def test_cancelling_a_match_in_signature_cancels_its_letters_envelope(clean: Session) -> None:
    admin_id, freelancer_id, company_id = _setup(clean)
    _active_framework(clean, freelancer_id, admin_id)
    renderer, fake, sender = FakeRenderer(draft=False), FakeDocumenso(), RecordingSender()
    match = _sent(clean, renderer, fake, sender, freelancer_id, company_id, admin_id)
    envelope = _envelope_of(_letter_of(clean, match.id))

    read = _signing(clean, renderer, fake, sender).admin.cancel_match(match.id, admin_id)

    assert (read.status, read.letter.status) == ("cancelled", "cancelled")
    assert _letter_of(clean, match.id).cancel_reason == "Annullato da rebase con il suo match."
    assert fake.envelopes[envelope].status == "CANCELLED"
    assert AdminActionService(clean).timeline("match", match.id)[0].kind == "match_cancelled"


def test_cancelling_a_draft_match_needs_no_documenso(clean: Session) -> None:
    admin_id, freelancer_id, company_id = _setup(clean)
    renderer = FakeRenderer(draft=False)
    match = _draft(clean, renderer, freelancer_id, company_id, admin_id)
    read = _signing(clean, renderer, None, RecordingSender()).admin.cancel_match(match.id, admin_id)
    assert read.status == "cancelled"


def test_a_notice_ends_the_active_framework_and_the_next_match_writes_a_new_one(
    clean: Session,
) -> None:
    admin_id, freelancer_id, company_id = _setup(clean)
    active = _active_framework(clean, freelancer_id, admin_id)
    renderer = FakeRenderer(draft=False)
    signing = _signing(clean, renderer, FakeDocumenso(), RecordingSender())

    read = signing.admin.record_notice(active.id, admin_id)

    assert (read.status, read.active, read.notice_at) == ("notice_served", False, NOW)
    assert (
        _matches(clean, renderer).drafts.prefill(freelancer_id, company_id).required_framework
        is True
    )
    with pytest.raises(InvalidState, match="attivo"):
        signing.admin.record_notice(active.id, admin_id)
    with pytest.raises(NotFound):
        signing.admin.record_notice(UUID("00000000-0000-7000-8000-000000000000"), admin_id)


# ---- the cancellation mail (REB-407) ------------------------------------------------------
def test_cancelling_a_sent_framework_tells_the_freelancer_by_mail(clean: Session) -> None:
    admin_id, freelancer_id, company_id = _setup(clean)
    renderer, fake, sender = FakeRenderer(draft=False), FakeDocumenso(), RecordingSender()
    _sent(clean, renderer, fake, sender, freelancer_id, company_id, admin_id)
    framework = _framework_of(clean, freelancer_id)
    before = len(sender.sent)

    _signing(clean, renderer, fake, sender).admin.cancel_document(framework.id, admin_id)

    cancellations = [mail for mail in sender.sent[before:] if "annullat" in mail.subject.lower()]
    assert len(cancellations) == 1
    assert cancellations[0].to == "ada@studio.it"


def test_cancelling_a_document_never_sent_mails_nobody(clean: Session) -> None:
    admin_id, freelancer_id, company_id = _setup(clean)
    renderer = FakeRenderer(draft=False)
    _draft(clean, renderer, freelancer_id, company_id, admin_id)
    framework = _framework_of(clean, freelancer_id)
    sender = RecordingSender()

    _signing(clean, renderer, None, sender).admin.cancel_document(framework.id, admin_id)

    assert sender.sent == []


def test_a_refused_cancellation_mail_does_not_undo_the_cancel(clean: Session) -> None:
    admin_id, freelancer_id, company_id = _setup(clean)
    renderer, fake = FakeRenderer(draft=False), FakeDocumenso()
    _sent(clean, renderer, fake, RecordingSender(), freelancer_id, company_id, admin_id)
    framework = _framework_of(clean, freelancer_id)
    sender = RefusingSender()

    read = _signing(clean, renderer, fake, sender).admin.cancel_document(framework.id, admin_id)

    assert read.status == "cancelled"
    assert len(sender.sent) == 1


def test_cancelling_a_match_mails_only_a_letter_that_had_already_left(clean: Session) -> None:
    admin_id, freelancer_id, company_id = _setup(clean)
    _active_framework(clean, freelancer_id, admin_id)
    renderer, fake, sender = FakeRenderer(draft=False), FakeDocumenso(), RecordingSender()
    match = _sent(clean, renderer, fake, sender, freelancer_id, company_id, admin_id)
    before = len(sender.sent)

    _signing(clean, renderer, fake, sender).admin.cancel_match(match.id, admin_id)

    cancellations = [mail for mail in sender.sent[before:] if "annullat" in mail.subject.lower()]
    assert len(cancellations) == 1


def test_cancelling_a_match_whose_letter_still_waits_mails_nobody(clean: Session) -> None:
    admin_id, freelancer_id, company_id = _setup(clean)
    renderer, fake, sender = FakeRenderer(draft=False), FakeDocumenso(), RecordingSender()
    match = _sent(clean, renderer, fake, sender, freelancer_id, company_id, admin_id)
    before = len(sender.sent)

    read = _signing(clean, renderer, fake, sender).admin.cancel_match(match.id, admin_id)

    assert read.letter.status == "cancelled"
    assert len(sender.sent) == before


# ---- the lock order (REB-407) -------------------------------------------------------------
@pytest.mark.parametrize("send_first", [True, False])
def test_a_send_and_a_cancel_of_the_same_draft_never_leave_a_letter_annulled_with_a_live_envelope(
    send_first: bool, hub_engine: Engine, clean: Session
) -> None:
    """A send and a cancel of the same `draft` match, at once, behind one gate on the
    freelancer's row: whichever wins commits first, and the other then sees its result,
    never a stale `draft`. Either the cancel wins (and the send that follows finds
    nothing left to send, an `InvalidState`), or the send wins (and the cancel that
    follows finds the match `awaiting_signature` and cancels its now-live envelope, `cancel_match`'s
    other branch) -- never a letter `cancelled` in the hub whose envelope is still live
    on Documenso. `send_first` forces both orderings deterministically, behind the same
    gate the other lock-order tests in this file use, rather than leaving it to thread
    scheduling: without it, only one order was ever exercised.

    An active framework agreement is set up first, so the match's own letter, not the
    framework agreement, is what a send dispatches: the letter's own `documenso_id` then
    tells the two outcomes apart."""
    admin_id, freelancer_id, company_id = _setup(clean)
    _active_framework(clean, freelancer_id, admin_id)
    renderer, fake = FakeRenderer(draft=False), FakeDocumenso()
    match = _draft(clean, renderer, freelancer_id, company_id, admin_id)
    factory = session_factory(hub_engine)
    gate, sender_session, canceller_session = factory(), factory(), factory()
    results: dict[str, object] = {}
    errors: list[BaseException] = []

    def send() -> None:
        try:
            results["send"] = _signing(
                sender_session, renderer, fake, RecordingSender()
            ).send_match(match.id, admin_id)
        except InvalidState as exc:
            results["send"] = exc
        except BaseException as exc:  # noqa: BLE001 -- surfaced on the main thread below
            errors.append(exc)

    def cancel() -> None:
        try:
            results["cancel"] = _signing(
                canceller_session, renderer, fake, RecordingSender()
            ).admin.cancel_match(match.id, admin_id)
        except InvalidState as exc:
            results["cancel"] = exc
        except BaseException as exc:  # noqa: BLE001 -- surfaced on the main thread below
            errors.append(exc)

    first_fn, second_fn = (send, cancel) if send_first else (cancel, send)
    try:
        gate.execute(select(Freelancer.id).where(Freelancer.id == freelancer_id).with_for_update())
        first_worker = threading.Thread(target=first_fn)
        first_worker.start()
        first_worker.join(timeout=0.5)
        assert first_worker.is_alive(), "the first call did not wait on the freelancer's row"
        second_worker = threading.Thread(target=second_fn)
        second_worker.start()
        second_worker.join(timeout=0.5)
        assert second_worker.is_alive(), "the second call did not wait on the freelancer's row"
        gate.commit()
        first_worker.join(timeout=5)
        second_worker.join(timeout=5)
        assert not first_worker.is_alive() and not second_worker.is_alive()
    finally:
        gate.close()
        sender_session.close()
        canceller_session.close()
    assert not errors, errors
    clean.expire_all()
    letter = _letter_of(clean, match.id)
    match_row = clean.get(Match, match.id)
    assert match_row is not None
    # Whichever order won, the cancel always finds a match it can cancel (`draft` if it
    # went first, `awaiting_signature` if the send got there first): never its own `InvalidState`.
    assert isinstance(results.get("cancel"), MatchRead)
    assert results["cancel"].status == "cancelled"
    assert (letter.status, match_row.status) == ("cancelled", "cancelled")
    # `send_first` must be what decided the winner, not just whatever the gate
    # happened to produce: branching on the outcome alone would still pass if the gate
    # stopped fixing the order (a regression `send_first` exists to catch).
    assert (letter.documenso_id is not None) is send_first
    if send_first:
        # the send won the race and created a live envelope; the cancel that followed
        # cancelled it rather than merely annulling the row.
        assert isinstance(results.get("send"), SendReport)
        assert fake.envelopes[letter.documenso_id].status == "CANCELLED"
    else:
        # the cancel won the race before any send ever created an envelope: the send
        # that followed found nothing left to send, and Documenso never heard from it.
        assert isinstance(results.get("send"), InvalidState)
        assert fake.envelopes == {}
