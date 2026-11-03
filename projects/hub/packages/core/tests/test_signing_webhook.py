"""The webhook's side (REB-391, REB-431): `apply`, the confirmation `finish` makes with Documenso
itself, and the lock order they keep. Helpers: `signing_fixtures`."""

import threading
from uuid import UUID

import pytest
from fakes_contracts import FakeRenderer
from fakes_documenso import FakeDocumenso
from match_fixtures import (
    _setup,
)
from signing_fixtures import (
    SIGNED_AT,
    _active_framework,
    _draft,
    _envelope_of,
    _framework_of,
    _letter_of,
    _sent,
    _signing,
    _try_lock_nowait,
    _webhook,
    clean,  # noqa: F401  (fixture)
)
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from rebase_core.db import session_factory
from rebase_core.errors import (
    InvalidState,
)
from rebase_core.mail import RecordingSender
from rebase_core.models import ContractDocument, Freelancer, Match
from rebase_core.signing_recovery import SweepResult


def test_apply_alone_leaves_a_completion_unconfirmed_and_calls_nobody(
    clean: Session,
) -> None:
    """The webhook's own transaction moves nothing for a completion (REB-431):
    Documenso gets its answer before any confirmation, download or mail (probe §
    11.2). It only names the document for `finish`, which confirms and signs it."""
    admin_id, freelancer_id, company_id = _setup(clean)
    renderer, fake, sender = FakeRenderer(draft=False), FakeDocumenso(), RecordingSender()
    _sent(clean, renderer, fake, sender, freelancer_id, company_id, admin_id)
    envelope = _envelope_of(_framework_of(clean, freelancer_id))
    fake.sign(envelope, SIGNED_AT)
    calls, mails = len(fake.calls), len(sender.sent)

    signed = _signing(clean, renderer, fake, sender).apply(
        _webhook(fake, envelope, "DOCUMENT_COMPLETED")
    )

    framework = _framework_of(clean, freelancer_id)
    assert signed == framework.id
    assert (framework.status, framework.signed_at, framework.signed_pdf) == ("sent", None, None)
    assert (len(fake.calls), len(sender.sent)) == (calls, mails)


def test_to_forged_completion_for_an_envelope_still_pending_leaves_it_sent_and_mails_nothing(
    clean: Session,
) -> None:
    """REB-431's own case: a `DOCUMENT_COMPLETED` event whose envelope Documenso still
    reports `PENDING` -- the secret alone, without the hub's own API token, can no
    longer move a document to `signed`."""
    admin_id, freelancer_id, company_id = _setup(clean)
    renderer, fake, sender = FakeRenderer(draft=False), FakeDocumenso(), RecordingSender()
    _sent(clean, renderer, fake, sender, freelancer_id, company_id, admin_id)
    envelope = _envelope_of(_framework_of(clean, freelancer_id))
    signing = _signing(clean, renderer, fake, sender)
    signed = signing.apply(_webhook(fake, envelope, "DOCUMENT_COMPLETED"))
    assert signed is not None
    before = len(sender.sent)

    signing.recovery.finish(signed)

    framework = _framework_of(clean, freelancer_id)
    assert (framework.status, framework.signed_at, framework.signed_pdf) == ("sent", None, None)
    assert len(sender.sent) == before


def test_the_next_finish_after_the_fake_is_actually_signed_confirms_and_signs_it(
    clean: Session,
) -> None:
    """The same document as above, still `sent` because the forged event changed
    nothing: once Documenso genuinely reports it `COMPLETED`, the next `finish` (or
    `sweep`) confirms it, with the signer's own date."""
    admin_id, freelancer_id, company_id = _setup(clean)
    renderer, fake, sender = FakeRenderer(draft=False), FakeDocumenso(), RecordingSender()
    _sent(clean, renderer, fake, sender, freelancer_id, company_id, admin_id)
    envelope = _envelope_of(_framework_of(clean, freelancer_id))
    signing = _signing(clean, renderer, fake, sender)
    signed = signing.apply(_webhook(fake, envelope, "DOCUMENT_COMPLETED"))
    assert signed is not None
    signing.recovery.finish(signed)
    assert _framework_of(clean, freelancer_id).status == "sent"

    fake.sign(envelope, SIGNED_AT)
    signing.recovery.finish(signed)

    framework = _framework_of(clean, freelancer_id)
    assert (framework.status, framework.signed_at) == ("signed", SIGNED_AT)


def test_the_sweep_picks_up_an_sent_document_whose_envelope_is_completed(
    clean: Session,
) -> None:
    """REB-431: a webhook the hub missed entirely, not merely one whose own follow-up
    died -- `_to_finish` must still find a document `sent` with an envelope, so the
    sweep confirms and signs it (probe § 11.3)."""
    admin_id, freelancer_id, company_id = _setup(clean)
    renderer, fake, sender = FakeRenderer(draft=False), FakeDocumenso(), RecordingSender()
    _sent(clean, renderer, fake, sender, freelancer_id, company_id, admin_id)
    envelope = _envelope_of(_framework_of(clean, freelancer_id))
    fake.sign(envelope, SIGNED_AT)

    result = _signing(clean, renderer, fake, sender).recovery.sweep()

    assert result == SweepResult(touched=1, unconfirmed=0)
    framework = _framework_of(clean, freelancer_id)
    assert (framework.status, framework.signed_at) == ("signed", SIGNED_AT)


def test_the_sweep_recovers_a_rejection_the_webhook_never_delivered(clean: Session) -> None:
    """REB-431: a rejection, not only a completion, can be lost the same way (probe
    § 11.3). `_confirm_completion` already holds the envelope it read for the
    completion check; applying a `REJECTED` outcome from that same envelope, the way
    `apply` itself would, means the sweep recovers this too, instead of warning about
    the same envelope forever."""
    admin_id, freelancer_id, company_id = _setup(clean)
    renderer, fake, sender = FakeRenderer(draft=False), FakeDocumenso(), RecordingSender()
    _sent(clean, renderer, fake, sender, freelancer_id, company_id, admin_id)
    envelope = _envelope_of(_framework_of(clean, freelancer_id))
    fake.reject(envelope, "La PEC indicata non è la mia")

    result = _signing(clean, renderer, fake, sender).recovery.sweep()

    assert result == SweepResult(touched=1, unconfirmed=0)
    framework = _framework_of(clean, freelancer_id)
    assert (framework.status, framework.cancel_reason) == (
        "cancelled",
        "Rifiutato dal freelance: La PEC indicata non è la mia",
    )


def test_the_sweep_recovers_a_cancellation_the_webhook_never_delivered(clean: Session) -> None:
    """REB-431: the same recovery for a cancellation made directly on Documenso."""
    admin_id, freelancer_id, company_id = _setup(clean)
    renderer, fake, sender = FakeRenderer(draft=False), FakeDocumenso(), RecordingSender()
    _sent(clean, renderer, fake, sender, freelancer_id, company_id, admin_id)
    envelope = _envelope_of(_framework_of(clean, freelancer_id))
    fake.client().cancel(envelope, "Annullato a mano.")

    result = _signing(clean, renderer, fake, sender).recovery.sweep()

    assert result == SweepResult(touched=1, unconfirmed=0)
    framework = _framework_of(clean, freelancer_id)
    assert (framework.status, framework.cancel_reason) == ("cancelled", "Annullato su Documenso.")


def test_documenso_unreachable_during_the_confirmation_leaves_it_sent_and_raises_nothing(
    clean: Session,
) -> None:
    """REB-431: Documenso not answering at all while `_confirm_completion` tries to read
    the envelope (`DocumensoFailed`, probe § 5's own UNREACHABLE) is logged and left for
    the next `finish` or `sweep`; it must not raise past `finish`."""
    admin_id, freelancer_id, company_id = _setup(clean)
    renderer, fake, sender = FakeRenderer(draft=False), FakeDocumenso(), RecordingSender()
    _sent(clean, renderer, fake, sender, freelancer_id, company_id, admin_id)
    envelope = _envelope_of(_framework_of(clean, freelancer_id))
    fake.sign(envelope, SIGNED_AT)
    signing = _signing(clean, renderer, fake, sender)
    signed = signing.apply(_webhook(fake, envelope, "DOCUMENT_COMPLETED"))
    assert signed is not None
    fake.down("get")

    moved = signing.recovery.finish(signed)

    assert moved is False
    framework = _framework_of(clean, freelancer_id)
    assert (framework.status, framework.signed_at, framework.signed_pdf) == ("sent", None, None)


def test_no_documenso_configured_during_the_confirmation_leaves_it_sent_and_raises_nothing(
    clean: Session,
) -> None:
    """REB-431: `self._documenso()` itself refuses (`SigningUnavailable`) when this
    environment has no Documenso configured at all -- a routine state (a preview with
    signing off), not a failure worth a traceback on every sweep run. `finish` swallows
    it the same way, moves nothing, and returns False."""
    admin_id, freelancer_id, company_id = _setup(clean)
    renderer, fake, sender = FakeRenderer(draft=False), FakeDocumenso(), RecordingSender()
    _sent(clean, renderer, fake, sender, freelancer_id, company_id, admin_id)
    envelope = _envelope_of(_framework_of(clean, freelancer_id))
    fake.sign(envelope, SIGNED_AT)
    signed = _signing(clean, renderer, fake, sender).apply(
        _webhook(fake, envelope, "DOCUMENT_COMPLETED")
    )
    assert signed is not None

    moved = _signing(clean, renderer, None, sender).recovery.finish(signed)

    assert moved is False
    framework = _framework_of(clean, freelancer_id)
    assert (framework.status, framework.signed_at, framework.signed_pdf) == ("sent", None, None)


def test_a_second_delivery_waits_for_the_first_and_changes_nothing(
    hub_engine: Engine, clean: Session
) -> None:
    """REB-391: Documenso retries at once, and even while a slow first caller still
    holds the row uncommitted (probe § 5). The row here is forged into `signed`
    directly, standing in for what `finish`'s own confirmation would eventually commit
    (`apply` itself never writes `signed` for a completion any more, REB-431): the
    point is `apply`'s own guard, that a row no longer `sent`, however it got there,
    is nothing left for a second, redundant delivery to do."""
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
        # Stands in for `finish`'s own confirmation, mid-transaction and not yet
        # committed.
        held = first.scalars(
            select(ContractDocument).where(ContractDocument.id == framework.id).with_for_update()
        ).one()
        worker = threading.Thread(target=deliver_again)
        worker.start()
        worker.join(timeout=0.5)
        assert worker.is_alive(), "the second delivery did not wait for the first one's lock"
        held.status, held.signed_at = "signed", SIGNED_AT
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
    """REB-391: a deterministic proof of `apply`'s lock order, not one left to
    thread scheduling. A gate session holds the freelancer's row; `apply` runs in its own
    thread and must block there -- proven not just by staying alive, but by a fourth
    session managing to lock the letter's own document row with `FOR UPDATE NOWAIT`
    while the gate holds: if `apply` had taken the document first (the old order), that
    NOWAIT probe would fail. Releasing the gate lets `apply` finish -- `apply` itself
    still takes this same lock order even though a completion no longer moves the row
    there (REB-431); `finish`, called after, is what actually signs the letter and
    turns its match active."""
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
    assert _letter_of(clean, match.id).status == "sent"

    _signing(clean, renderer, fake, sender).recovery.finish(letter.id)

    clean.expire_all()
    assert _letter_of(clean, match.id).status == "signed"
    assert clean.get(Match, match.id).status == "active"  # type: ignore[union-attr]


def test_finish_confirms_only_after_locking_the_freelancer_row(
    hub_engine: Engine, clean: Session
) -> None:
    """REB-431: the same deterministic gate proof, for `_confirm_completion`'s own lock
    order. A gate session holds the freelancer's row; `finish`, on a document still
    `sent`, must wait there before it ever locks the document itself -- proven not
    just by staying alive, but by a probe session managing to lock the document's own
    row with `FOR UPDATE NOWAIT` while the gate holds: if `_confirm_completion` had
    locked the document first, that NOWAIT probe would fail. Releasing the gate lets it
    confirm the envelope, sign the document, then run the rest of `finish` as usual."""
    admin_id, freelancer_id, company_id = _setup(clean)
    renderer, fake, sender = FakeRenderer(draft=False), FakeDocumenso(), RecordingSender()
    _sent(clean, renderer, fake, sender, freelancer_id, company_id, admin_id)
    framework = _framework_of(clean, freelancer_id)
    envelope = _envelope_of(framework)
    fake.sign(envelope, SIGNED_AT)
    signing = _signing(clean, renderer, fake, sender)
    signed = signing.apply(_webhook(fake, envelope, "DOCUMENT_COMPLETED"))
    assert signed is not None
    factory = session_factory(hub_engine)
    gate, worker_session, probe = factory(), factory(), factory()

    def run_finish() -> None:
        _signing(worker_session, renderer, fake, sender).recovery.finish(signed)

    try:
        gate.execute(select(Freelancer.id).where(Freelancer.id == freelancer_id).with_for_update())
        worker = threading.Thread(target=run_finish)
        worker.start()
        worker.join(timeout=0.5)
        assert worker.is_alive(), "finish did not wait on the freelancer's row to confirm"
        assert _try_lock_nowait(probe, signed), (
            "_confirm_completion already held the document's row before the freelancer's"
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
    framework = _framework_of(clean, freelancer_id)
    assert (framework.status, framework.signed_at) == ("signed", SIGNED_AT)


def test_finish_releases_a_waiting_letter_only_after_locking_the_freelancer_row(
    hub_engine: Engine, clean: Session
) -> None:
    """REB-391: the same gate proof for `finish` -> `_send_waiting`. A framework
    already confirmed `signed` (its own confirmation is `_confirm_completion`'s own
    gate test below, not this one), its letter still waiting: while another session
    holds the freelancer's row, the letter's own row is still free to a `FOR UPDATE
    NOWAIT` probe (proving `_send_waiting` has not reached it yet); releasing the gate
    lets it go, and the letter leaves.

    Committing `signed` directly, rather than through `apply` then `finish`'s own
    confirmation (REB-431), keeps this test about `_send_waiting`'s lock order alone:
    `_confirm_completion` finds the row already signed and returns at once, without
    itself touching the freelancer's row, so the gate can isolate `_send_waiting`'s own
    first lock the way it always did."""
    admin_id, freelancer_id, company_id = _setup(clean)
    renderer, fake, sender = FakeRenderer(draft=False), FakeDocumenso(), RecordingSender()
    match = _sent(clean, renderer, fake, sender, freelancer_id, company_id, admin_id)
    framework = _framework_of(clean, freelancer_id)
    envelope = _envelope_of(framework)
    fake.sign(envelope, SIGNED_AT)
    framework.status, framework.signed_at = "signed", SIGNED_AT
    clean.commit()
    signed = framework.id
    letter = _letter_of(clean, match.id)
    factory = session_factory(hub_engine)
    gate, worker_session, probe = factory(), factory(), factory()

    def run_finish() -> None:
        _signing(worker_session, renderer, fake, sender).recovery.finish(signed)

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
    assert _letter_of(clean, match.id).status == "sent"


@pytest.mark.parametrize("apply_first", [True, False])
def test_a_letters_webhook_and_a_resend_of_its_match_never_deadlock(
    apply_first: bool, hub_engine: Engine, clean: Session
) -> None:
    """REB-391: `apply`'s lock order now matches `send_match`'s (freelancer,
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
    # The letter was already `sent` before either thread ran: a resend of a match
    # already out for signature always finds nothing left to send, in either order.
    assert isinstance(results.get("resend"), InvalidState)
