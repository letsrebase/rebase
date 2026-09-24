"""An unknown outcome resolves exactly, so a double send cannot happen.

This is the file for the previous system's live defect: it sends, then fails to write
the record, then answers `404 'Fattura non trovata per registrare l'invio.'` **while
the email is already delivered**. The operator reads an error and presses the button
again, and the client gets two.

Two halves have to hold at once, and each is the other's failure mode:

* a lost answer must never be written down as «inviata» -- that is a lie about somebody's
  correspondence;
* and it must never be written down as «fallita» either, when the message is sitting in
  the user's Sent folder -- because that reads as an invitation to resend.

Whether Gmail preserves a client-supplied `Message-ID` is **not verified** (see
`docs/superpowers/notes/2026-08-20-gmail-message-id-verification.md`), so
`test_a_gmail_that_rewrote_our_message_id_still_does_not_report_a_delivered_mail_as_failed`
runs the whole path with that assumption switched off. A reconciliation that only works
when the assumption holds is a reconciliation nobody has tested against the case it was
written to survive.
"""

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from fakes.fake_gmail import FakeGmail
from fakes.gmail_fixtures import (
    actor_for,
    connected_account,
    draft_service,
    gmail_settings,
    send_service,
    sync_service,
)
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from pigrocrm.core.activities.models import Activity
from pigrocrm.core.actor import Actor
from pigrocrm.core.customers.models import Customer
from pigrocrm.core.errors import Conflict, NotFound, PermissionDenied
from pigrocrm.core.gmail.models import EmailDraft, GmailMessage, GoogleAccount
from pigrocrm.core.gmail.schemas import SCOPE_SEND, EmailDraftCreate, EmailDraftRead
from pigrocrm.core.people.models import Person

BODY = "Gentile Ada, in allegato la fattura."
SUBJECT = "Fattura 2026/14"


def _customer(session: Session) -> Customer:
    customer = Customer(ragione_sociale="Acme", email="info@acme.it")
    session.add(customer)
    session.flush()
    return customer


def _draft(session: Session, account: GoogleAccount, **overrides: Any) -> EmailDraftRead:
    customer = _customer(session)
    return draft_service(session).create(
        EmailDraftCreate(
            entity_type="customer",
            entity_id=customer.id,
            to_addresses=["ada@acme.it"],
            subject=SUBJECT,
            body_markdown=BODY,
            **overrides,
        ),
        actor_for(account),
    )


def _lost_answer(deliver: bool, **overrides: Any) -> FakeGmail:
    """Gmail's answer never arrives. `deliver=True` is the case that matters: the message
    really did leave, and only the response was lost. That is the previous system's
    live defect, and it is the case a guess gets wrong."""
    return FakeGmail(timeout_on_send=True, deliver_on_timeout=deliver, **overrides)


def _age(session: Session, draft_id: UUID, minutes: int) -> None:
    """Push the attempt back past the grace window. `send_attempted_at` is written by the
    claim, so moving it is the only way to reach the post-window branch without sleeping
    for fifteen minutes -- and a test that slept would be a test nobody runs."""
    row = session.get(EmailDraft, draft_id)
    assert row is not None
    row.send_attempted_at = datetime.now(UTC) - timedelta(minutes=minutes)
    session.commit()


# --- the unknown outcome is named, and never guessed -----------------------------------


def test_a_lost_answer_leaves_the_draft_uncertain_and_never_says_sent(
    db_session: Session,
) -> None:
    """Spec 13, criterion 9, first half. The interface must not write «inviata»."""
    account = connected_account(db_session)
    draft = _draft(db_session, account)
    db_session.commit()

    with pytest.raises(Conflict, match="da verificare"):
        send_service(db_session, _lost_answer(True)).send(draft.id, actor_for(account))
    db_session.rollback()

    row = db_session.get(EmailDraft, draft.id)
    assert row is not None
    assert row.send_state == "incerto"
    assert "sappiamo" in (row.last_error or "")
    # And no outbound row was invented: we do not know that it arrived.
    assert db_session.execute(select(func.count()).select_from(GmailMessage)).scalar_one() == 0


def test_an_uncertain_send_writes_its_own_timeline_entry(db_session: Session) -> None:
    """A state nobody can see is a state nobody resolves."""
    account = connected_account(db_session)
    draft = _draft(db_session, account)
    db_session.commit()
    with pytest.raises(Conflict):
        send_service(db_session, _lost_answer(True)).send(draft.id, actor_for(account))
    db_session.rollback()

    kinds = db_session.execute(select(Activity.kind)).scalars().all()
    assert "gmail.invio_incerto" in kinds
    payloads = (
        db_session.execute(select(Activity.payload).where(Activity.kind == "gmail.invio_incerto"))
        .scalars()
        .all()
    )
    # The subject and our own id -- what a person needs to find the message in Sent --
    # and no recipient and no body.
    assert payloads[0]["message_id_header"] == draft.message_id_header
    assert "ada@acme.it" not in str(payloads[0])
    assert "allegato" not in str(payloads[0])


# --- the reconciliation ----------------------------------------------------------------


def test_reconciliation_finds_it_by_our_own_message_id_and_adopts_gmails(
    db_session: Session,
) -> None:
    account = connected_account(db_session)
    draft = _draft(db_session, account)
    db_session.commit()
    fake = _lost_answer(True)
    service = send_service(db_session, fake)
    with pytest.raises(Conflict):
        service.send(draft.id, actor_for(account))
    db_session.rollback()

    read = service.reconcile(draft.id, actor_for(account))
    db_session.commit()

    assert read.send_state == "inviato"
    assert read.sent_gmail_message_id
    assert read.last_error is None
    # The answer the Email tab's «Verifica» reads is built like every other draft read
    # (REB-415), attachments named and all.
    assert read.attachments == []
    outbound = (
        db_session.execute(select(GmailMessage).where(GmailMessage.direction == "outbound"))
        .scalars()
        .one()
    )
    assert outbound.message_id_header == draft.message_id_header
    assert outbound.gmail_message_id == read.sent_gmail_message_id
    # The lookup was by rfc822msgid, i.e. exact -- not a comparison of subject and time.
    lookups = [r for r in fake.requests if r.is_messages_list and "rfc822msgid" in (r.q or "")]
    assert len(lookups) == 1
    assert draft.message_id_header.strip("<>") in (lookups[0].q or "")


def test_a_double_send_cannot_happen(db_session: Session) -> None:
    """The whole point, in one test.

    The sequence is the one that produces two emails in the previous system: the send's outcome is
    unknown, the operator retries, and nothing stops them. Here the retry is refused, the
    reconciliation resolves the truth by asking Gmail, and exactly one message was ever
    sent.
    """
    account = connected_account(db_session)
    draft = _draft(db_session, account)
    db_session.commit()
    fake = _lost_answer(True)
    service = send_service(db_session, fake)

    with pytest.raises(Conflict):
        service.send(draft.id, actor_for(account))
    db_session.rollback()

    # The operator presses Invia again, exactly as they would after reading an error.
    with pytest.raises(Conflict) as caught:
        service.send(draft.id, actor_for(account))
    assert "verifica" in caught.value.message
    db_session.rollback()

    service.reconcile(draft.id, actor_for(account))
    db_session.commit()

    # One send call over the whole scenario. Not two, and not zero.
    assert len([r for r in fake.requests if r.is_messages_send]) == 1
    assert (
        db_session.execute(
            select(func.count())
            .select_from(GmailMessage)
            .where(GmailMessage.direction == "outbound")
        ).scalar_one()
        == 1
    )
    row = db_session.get(EmailDraft, draft.id)
    assert row is not None and row.send_state == "inviato"


def test_a_message_genuinely_absent_after_the_grace_window_becomes_failed(
    db_session: Session,
) -> None:
    """Spec 13, criterion 9, second half: the draft stays intact."""
    account = connected_account(db_session)
    draft = _draft(db_session, account)
    db_session.commit()
    fake = _lost_answer(False)  # it really did not leave
    service = send_service(db_session, fake)
    with pytest.raises(Conflict):
        service.send(draft.id, actor_for(account))
    db_session.rollback()
    _age(db_session, draft.id, minutes=16)

    read = service.reconcile(draft.id, actor_for(account))
    db_session.commit()

    assert read.send_state == "fallito"
    assert read.body_markdown == BODY
    assert read.to_addresses == ["ada@acme.it"]
    # The sentence names where to look, because the one thing that could make this
    # verdict wrong -- Gmail replacing our Message-ID -- is still unverified, and
    # «non è partito» about a message in the user's own Sent folder reads as
    # «send it again».
    assert "Posta inviata" in (read.last_error or "")
    assert db_session.execute(select(func.count()).select_from(GmailMessage)).scalar_one() == 0


def test_a_failed_reconciliation_leaves_a_draft_the_composer_can_reopen(
    db_session: Session,
) -> None:
    """`fallito` is editable (B2-3) and sendable again (B2-5). A verdict that froze the
    draft would leave retyping the message as the only recovery."""
    account = connected_account(db_session)
    draft = _draft(db_session, account)
    db_session.commit()
    with pytest.raises(Conflict):
        send_service(db_session, _lost_answer(False)).send(draft.id, actor_for(account))
    db_session.rollback()
    _age(db_session, draft.id, minutes=16)
    send_service(db_session, _lost_answer(False)).reconcile(draft.id, actor_for(account))
    db_session.commit()

    working = FakeGmail()
    read = send_service(db_session, working).send(draft.id, actor_for(account))
    assert read.send_state == "inviato"
    assert len([r for r in working.requests if r.is_messages_send]) == 1


def test_inside_the_grace_window_it_stays_uncertain_rather_than_guessing(
    db_session: Session,
) -> None:
    """Gmail's index is not instantaneous. Declaring failure at second zero would turn a
    slow index into a resend -- which is the very outcome this design exists to
    prevent."""
    account = connected_account(db_session)
    draft = _draft(db_session, account)
    db_session.commit()
    fake = _lost_answer(False)
    service = send_service(db_session, fake)
    with pytest.raises(Conflict):
        service.send(draft.id, actor_for(account))
    db_session.rollback()

    read = service.reconcile(draft.id, actor_for(account))
    assert read.send_state == "incerto"
    # It did ask, though. "Not yet" is an answer about the index, not a refusal to look.
    assert [r for r in fake.requests if r.is_messages_list and "rfc822msgid" in (r.q or "")]


def test_reconciliation_is_idempotent(db_session: Session) -> None:
    account = connected_account(db_session)
    draft = _draft(db_session, account)
    db_session.commit()
    fake = _lost_answer(True)
    service = send_service(db_session, fake)
    with pytest.raises(Conflict):
        service.send(draft.id, actor_for(account))
    db_session.rollback()
    service.reconcile(draft.id, actor_for(account))
    db_session.commit()
    lookups_after_one = len([r for r in fake.requests if r.is_messages_list])
    service.reconcile(draft.id, actor_for(account))
    db_session.commit()

    assert (
        db_session.execute(
            select(func.count())
            .select_from(GmailMessage)
            .where(GmailMessage.direction == "outbound")
        ).scalar_one()
        == 1
    )
    # And the second call asked Google nothing: a resolved draft has no outcome left to
    # look up, so a «verifica» pressed twice costs one request, not two.
    assert len([r for r in fake.requests if r.is_messages_list]) == lookups_after_one


def test_reconciling_a_draft_that_was_never_sent_does_nothing(db_session: Session) -> None:
    account = connected_account(db_session)
    draft = _draft(db_session, account)
    db_session.commit()
    fake = FakeGmail()

    read = send_service(db_session, fake).reconcile(draft.id, actor_for(account))

    assert read.send_state == "bozza"
    assert fake.requests == []


def test_a_readonly_actor_cannot_resolve_an_uncertain_outcome(db_session: Session) -> None:
    """«verifica» is the repair half of having pressed Invia, so it is gated like the
    send it repairs. Two reasons, and either alone would be enough: it writes
    `send_state` to a terminal value (`inviato` or `fallito`), and it spends the owner's
    Gmail quota under the owner's OAuth grant.

    Refused *before* any request goes out, which is the part worth asserting: a gate
    that fired after the lookup would still have spent the quota it exists to protect.
    """
    account = connected_account(db_session)
    draft = _draft(db_session, account)
    row = db_session.get(EmailDraft, draft.id)
    assert row is not None
    # Straight onto the row: `EmailDraftCreate` refuses `send_state` by design -- it is a
    # fact about what happened, never an input -- and the only writer is the send path.
    row.send_state = "incerto"
    db_session.commit()
    fake = FakeGmail()

    with pytest.raises(PermissionDenied):
        send_service(db_session, fake).reconcile(
            draft.id, Actor(id=account.user_id, type="user", role="readonly")
        )

    assert fake.requests == []
    db_session.refresh(row)
    assert row.send_state == "incerto"


def test_reconciling_a_draft_that_does_not_exist_is_a_not_found(db_session: Session) -> None:
    account = connected_account(db_session)
    db_session.commit()
    fake = FakeGmail()
    with pytest.raises(NotFound):
        send_service(db_session, fake).reconcile(uuid4(), actor_for(account))
    assert fake.requests == []


# --- the assumption nobody has verified -------------------------------------------------


def test_a_gmail_that_rewrote_our_message_id_still_does_not_report_a_delivered_mail_as_failed(
    db_session: Session,
) -> None:
    """The failure mode of the one UNVERIFIED assumption in this design.

    `FakeGmail(rewrites_message_id=True)` is the `NO` branch of
    `docs/superpowers/notes/2026-08-20-gmail-message-id-verification.md`: the message
    really is in the mailbox and `q=rfc822msgid:` cannot find it. If reconciliation
    stopped at the exact lookup, this draft would be marked `fallito` while the client
    already has the email -- and the person, reading «non risulta partito», would send
    it a second time. That is the defect this whole slice is named after, reintroduced
    through the back door of an assumption.

    So the approximate match of spec 6.3 runs *always*, not only when the setting is
    switched off, and this is the test that says so.
    """
    account = connected_account(db_session)
    draft = _draft(db_session, account)
    # The recipient has to be somebody the CRM knows, or the sweep of spec 4 would never
    # look for this conversation at all -- which is the fallback's real limit and not
    # something to paper over here.
    db_session.add(
        Person(customer_id=draft.entity_id, nome="Ada", cognome="Rossi", email="ada@acme.it")
    )
    db_session.commit()
    fake = _lost_answer(True, rewrites_message_id=True)
    service = send_service(db_session, fake)
    with pytest.raises(Conflict):
        service.send(draft.id, actor_for(account))
    db_session.rollback()

    # The sweep by address that spec 4 already performs is what puts this account's own
    # outgoing mail into `gmail_messages`; here it runs for real.
    sync_service(db_session, fake).sync(actor_for(account))
    db_session.commit()
    _age(db_session, draft.id, minutes=16)

    read = service.reconcile(draft.id, actor_for(account))
    db_session.commit()

    assert read.send_state == "inviato", (
        "a delivered message was reported as failed because Gmail did not keep our "
        "Message-ID -- the exact case the verification note says is unverified"
    )
    assert (
        db_session.execute(
            select(func.count())
            .select_from(GmailMessage)
            .where(GmailMessage.direction == "outbound")
        ).scalar_one()
        == 1
    ), "the adopted message must be the one already stored, not a second copy of it"


def test_the_approximate_match_will_not_steal_a_message_another_draft_already_claims(
    db_session: Session,
) -> None:
    """The declared weakness of the fallback is that two near-identical sends minutes
    apart are indistinguishable. It must not also be that the second one adopts the
    first one's email: that would make one message the record of two sends, and the
    second draft would read as delivered when nothing left."""
    account = connected_account(db_session)
    customer = _customer(db_session)
    already_sent = GmailMessage(
        google_account_id=account.id,
        gmail_message_id="sent-claimed",
        gmail_thread_id="t-claimed",
        message_id_header="<other.1@crm.example.it>",
        direction="outbound",
        from_address=account.email_address,
        to_addresses=["ada@acme.it"],
        subject=SUBJECT,
        internal_date=datetime.now(UTC),
    )
    db_session.add(already_sent)
    claimant = draft_service(db_session).create(
        EmailDraftCreate(
            entity_type="customer",
            entity_id=customer.id,
            to_addresses=["ada@acme.it"],
            subject=SUBJECT,
            body_markdown=BODY,
        ),
        actor_for(account),
    )
    claiming_row = db_session.get(EmailDraft, claimant.id)
    assert claiming_row is not None
    claiming_row.send_state = "inviato"
    claiming_row.sent_gmail_message_id = "sent-claimed"
    db_session.commit()

    draft = _draft(db_session, account)
    db_session.commit()
    # Nothing left, and Gmail rewrites ids, so only the approximation can answer -- and
    # the one candidate it finds belongs to somebody else's send.
    fake = _lost_answer(False, rewrites_message_id=True)
    service = send_service(db_session, fake)
    with pytest.raises(Conflict):
        service.send(draft.id, actor_for(account))
    db_session.rollback()
    _age(db_session, draft.id, minutes=16)

    read = service.reconcile(draft.id, actor_for(account))
    assert read.send_state == "fallito"
    assert read.sent_gmail_message_id is None


# --- an abandoned attempt is an unknown outcome too --------------------------------------


def test_a_send_abandoned_mid_flight_is_reconciled_rather_than_stranded(
    db_session: Session,
) -> None:
    """The window the three-transaction design pays for: the process dies between the
    claim's commit and the outcome's commit, and the draft is left `in_invio`. Nothing
    can edit it, send it or verify it, so it has to be resolvable the same way `incerto`
    is -- by asking."""
    account = connected_account(db_session)
    draft = _draft(db_session, account)
    db_session.commit()
    fake = _lost_answer(True)
    with pytest.raises(Conflict):
        send_service(db_session, fake).send(draft.id, actor_for(account))
    db_session.rollback()
    # Rewind to what a killed process leaves behind: claimed, and nothing recorded.
    stranded = db_session.get(EmailDraft, draft.id)
    assert stranded is not None
    stranded.send_state = "in_invio"
    stranded.last_error = None
    db_session.commit()
    _age(db_session, draft.id, minutes=16)

    read = send_service(db_session, fake).reconcile(draft.id, actor_for(account))
    db_session.commit()
    assert read.send_state == "inviato"


def test_a_send_still_in_flight_is_left_alone_and_asked_nothing(db_session: Session) -> None:
    """Inside the grace window `in_invio` means "another request is doing this right
    now". Touching it would race a send about to record its own outcome."""
    account = connected_account(db_session)
    draft = _draft(db_session, account)
    db_session.commit()
    row = db_session.get(EmailDraft, draft.id)
    assert row is not None
    row.send_state = "in_invio"
    row.send_attempted_at = datetime.now(UTC)
    db_session.commit()

    fake = FakeGmail()
    read = send_service(db_session, fake).reconcile(draft.id, actor_for(account))
    assert read.send_state == "in_invio"
    assert fake.requests == []


# --- and it runs by itself ---------------------------------------------------------------


def test_every_sync_reconciles_first(db_session: Session) -> None:
    """Spec 6.3 point 3: reconciliation runs at the start of every sync and on request. A
    state that only resolves when a human remembers to press a button is a state that
    stays wrong."""
    account = connected_account(db_session)
    draft = _draft(db_session, account)
    db_session.commit()
    fake = _lost_answer(True)
    with pytest.raises(Conflict):
        send_service(db_session, fake).send(draft.id, actor_for(account))
    db_session.rollback()

    report = sync_service(db_session, fake).sync(actor_for(account))
    db_session.commit()

    row = db_session.get(EmailDraft, draft.id)
    assert row is not None
    assert row.send_state == "inviato"
    assert report.reconciled == 1


def test_a_sync_with_nothing_to_reconcile_reports_zero_and_asks_nothing_extra(
    db_session: Session,
) -> None:
    """The counter has to be able to be wrong in the other direction too: a cycle that
    reconciled nothing must not report that it did, or the number means nothing."""
    account = connected_account(db_session)
    db_session.commit()
    fake = FakeGmail()

    report = sync_service(db_session, fake).sync(actor_for(account))

    assert report.reconciled == 0
    assert [r for r in fake.requests if "rfc822msgid" in (r.q or "")] == []


def test_verification_needs_the_read_scope_and_says_so(db_session: Session) -> None:
    """`users.messages.list` is authorised by `gmail.readonly`, and `gmail.send` does not
    grant it. Asking for the wrong scope here would turn a legible «manca
    l'autorizzazione» into a 403 from Google that nobody can act on."""
    from pigrocrm.core.gmail.errors import ScopeMissing

    account = connected_account(db_session, scopes=("openid", "email", SCOPE_SEND))
    draft = _draft(db_session, account)
    db_session.commit()
    row = db_session.get(EmailDraft, draft.id)
    assert row is not None
    row.send_state = "incerto"
    row.send_attempted_at = datetime.now(UTC)
    db_session.commit()

    fake = FakeGmail()
    with pytest.raises(ScopeMissing) as caught:
        send_service(db_session, fake).reconcile(draft.id, actor_for(account))
    assert "gmail.readonly" in caught.value.message
    assert fake.requests == []


def test_the_fallback_can_be_switched_off_and_the_exact_path_still_answers(
    db_session: Session,
) -> None:
    """`PIGROCRM_GMAIL_RECONCILE_BY_MESSAGE_ID=false` is the escape hatch the note
    prescribes if the check ever comes back `NO`. With it off, the exact lookup is not
    even attempted -- there is no point spending a request on an id Gmail threw away."""
    settings = gmail_settings(gmail_reconcile_by_message_id=False)
    account = connected_account(db_session)
    draft = _draft(
        db_session,
        account,
    )
    db_session.commit()
    fake = _lost_answer(True)
    service = send_service(db_session, fake, settings=settings)
    with pytest.raises(Conflict):
        service.send(draft.id, actor_for(account))
    db_session.rollback()

    service.reconcile(draft.id, actor_for(account))
    assert [r for r in fake.requests if "rfc822msgid" in (r.q or "")] == []


# --- whose send is it, anyway ---------------------------------------------------------


def test_one_users_sync_does_not_declare_another_users_delivered_mail_failed(
    db_session: Session,
) -> None:
    """The defect B2-6 could mitigate and not remove, now closed by a column.

    `reconcile_all` runs inside *each* user's sync cycle. Before
    `email_drafts.google_account_id` existed it walked every unresolved draft in the
    installation, so this cycle -- user A's -- would pick up user B's `incerto` send, look
    for it in A's mailbox, find nothing there because it never was there, and past the
    grace window write `fallito` on a message sitting in B's client's inbox.

    That verdict is the worst thing this slice can produce: it is not merely wrong, it
    reads as an instruction to send the message a second time. Delete the
    `google_account_id` predicate in `uncertain_draft_ids` and this test reports
    `fallito`.
    """
    mine = connected_account(db_session)
    theirs = connected_account(db_session, email_address="altro@example.it")
    draft = _draft(db_session, theirs)
    db_session.commit()

    # Their send: the answer never arrived, and the message really did leave.
    with pytest.raises(Conflict):
        send_service(db_session, _lost_answer(True)).send(draft.id, actor_for(theirs))
    db_session.rollback()
    _age(db_session, draft.id, minutes=16)

    stored = db_session.get(EmailDraft, draft.id)
    assert stored is not None
    assert stored.send_state == "incerto"
    assert stored.google_account_id == theirs.id, "the claim must record the sending mailbox"

    # My cycle, with a mailbox that has never seen their message.
    empty = FakeGmail()
    resolved = send_service(db_session, empty).reconcile_all(mine.id, actor_for(mine))
    db_session.commit()

    assert resolved == 0
    after = db_session.get(EmailDraft, draft.id)
    assert after is not None
    assert after.send_state == "incerto", "another mailbox's cycle rewrote this outcome"
    # And it did not even ask: their draft is none of this cycle's business, so no
    # lookup was spent on somebody else's correspondence.
    assert [request for request in empty.requests if "rfc822msgid" in (request.q or "")] == []


def test_verifying_someone_elses_send_by_hand_is_refused_rather_than_guessed(
    db_session: Session,
) -> None:
    """The endpoint is reachable per draft, so the scoping cannot live only in the sweep.
    Refused with a sentence about the mailbox -- never a subject, never a recipient."""
    mine = connected_account(db_session)
    theirs = connected_account(db_session, email_address="altro@example.it")
    draft = _draft(db_session, theirs)
    db_session.commit()
    with pytest.raises(Conflict):
        send_service(db_session, _lost_answer(True)).send(draft.id, actor_for(theirs))
    db_session.rollback()
    _age(db_session, draft.id, minutes=16)

    with pytest.raises(Conflict, match="un'altra casella"):
        send_service(db_session, FakeGmail()).reconcile(draft.id, actor_for(mine))

    after = db_session.get(EmailDraft, draft.id)
    assert after is not None
    assert after.send_state == "incerto"


def test_a_draft_that_never_left_carries_no_mailbox(db_session: Session) -> None:
    """`google_account_id` is written by the claim and by nothing else. A draft somebody
    is still writing has no mailbox involved yet, and recording one at creation would
    claim an outcome that does not exist."""
    account = connected_account(db_session)
    draft = _draft(db_session, account)
    db_session.commit()
    row = db_session.get(EmailDraft, draft.id)
    assert row is not None
    assert row.google_account_id is None
