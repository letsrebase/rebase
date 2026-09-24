"""The one send path, and the claim that it cannot run twice.

The concurrency test at the bottom commits for real and cleans up in a `finally`, for
the reason `test_email_drafts.py` and `test_gmail_lock.py` both state at length: the
`db_session` fixture wraps each test in a transaction it rolls back, so a second
connection inside it could never see the first one's rows -- and "two concurrent sends
produce one email" is precisely a claim about what a *second connection* sees. Calling
`send` twice in sequence proves nothing about it.
"""

import base64
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from threading import Barrier
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
)
from sqlalchemy import Engine, delete, func, select
from sqlalchemy.orm import Session

from pigrocrm.core.activities.models import Activity
from pigrocrm.core.actor import Actor
from pigrocrm.core.auth.models import User
from pigrocrm.core.customers.models import Customer
from pigrocrm.core.db import session_factory
from pigrocrm.core.documents.models import Document, DocumentVersion
from pigrocrm.core.emitter.models import EmitterProfile
from pigrocrm.core.errors import Conflict, PermissionDenied, ValidationFailed
from pigrocrm.core.gmail.errors import CredentialRevoked, ScopeMissing
from pigrocrm.core.gmail.models import EmailDraft, GmailMessage, GmailMessageLink, GoogleAccount
from pigrocrm.core.gmail.parse import parse_message
from pigrocrm.core.gmail.query import message_get_url
from pigrocrm.core.gmail.repository import GmailRepository
from pigrocrm.core.gmail.schemas import SCOPE_READONLY, EmailDraftCreate, EmailDraftRead
from pigrocrm.core.people.models import Person
from pigrocrm.core.storage.local import LocalFileStorage

BODY = "Gentile Ada,\n\nè già pronta l’offerta."
SUBJECT = "Offerta — così"
MB = 1024 * 1024


def _customer(session: Session) -> Customer:
    customer = Customer(ragione_sociale="Acme", email="info@acme.it")
    session.add(customer)
    session.flush()
    return customer


def _draft(
    session: Session, account: GoogleAccount, customer: Customer | None = None, **overrides: Any
) -> EmailDraftRead:
    owner = customer if customer is not None else _customer(session)
    payload = EmailDraftCreate(
        entity_type="customer",
        entity_id=owner.id,
        to_addresses=["ada@acme.it"],
        subject=SUBJECT,
        body_markdown=BODY,
        **overrides,
    )
    return draft_service(session).create(payload, actor_for(account))


def _sent_raw(fake: FakeGmail) -> str:
    """The RFC822 that actually went out, decoded from the `raw` field of the one
    `messages.send` call."""
    sends = [request for request in fake.requests if request.is_messages_send]
    assert len(sends) == 1, f"expected exactly one send, got {len(sends)}"
    raw = json.loads(sends[0].body or b"{}")["raw"]
    return base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)).decode()


# --- the happy path -------------------------------------------------------------------


def test_a_successful_send_records_the_message_and_the_timeline_entry(
    db_session: Session,
) -> None:
    account = connected_account(db_session)
    draft = _draft(db_session, account)
    db_session.commit()
    fake = FakeGmail()

    read = send_service(db_session, fake).send(draft.id, actor_for(account))
    db_session.commit()

    assert read.send_state == "inviato"
    assert read.sent_gmail_message_id
    assert read.last_error is None
    # The outbound row is written *after* Gmail answered, never before.
    outbound = (
        db_session.execute(select(GmailMessage).where(GmailMessage.direction == "outbound"))
        .scalars()
        .one()
    )
    assert outbound.gmail_message_id == read.sent_gmail_message_id
    assert outbound.message_id_header == draft.message_id_header
    assert outbound.from_address == account.email_address
    kinds = db_session.execute(select(Activity.kind)).scalars().all()
    assert "gmail.messaggio_inviato" in kinds


def test_the_sent_draft_names_the_file_that_left_with_it(
    db_session: Session, tmp_path: Path
) -> None:
    """REB-415: the Email tab shows a draft's attachments by name before «Invia», and the
    send's own answer is built the same way. The name it reports is the name in the MIME
    part that actually left, which is the whole point of reading one before pressing."""
    account = connected_account(db_session)
    customer = _customer(db_session)
    document = Document(customer_id=customer.id, tipo="offerta", titolo="Offerta Q1")
    db_session.add(document)
    db_session.flush()
    pdf = b"%PDF-1.7\nfinto\n"
    version = DocumentVersion(
        document_id=document.id,
        numero=2,
        storage_key=f"acme/offerta-{uuid4().hex}.pdf",
        content_type="application/pdf",
        dimensione=len(pdf),
        hash_sha256="0" * 64,
    )
    db_session.add(version)
    db_session.flush()
    storage = LocalFileStorage(tmp_path)
    storage.put(version.storage_key, pdf, "application/pdf")
    draft = _draft(db_session, account, customer, attachment_version_ids=[version.id])
    db_session.commit()
    fake = FakeGmail()

    read = send_service(db_session, fake, storage=storage).send(draft.id, actor_for(account))
    db_session.commit()

    assert read.send_state == "inviato"
    assert [a.filename for a in read.attachments] == ["offerta-q1-v2.pdf"]
    assert 'filename="offerta-q1-v2.pdf"' in _sent_raw(fake)


def test_the_message_that_left_carries_our_own_message_id(db_session: Session) -> None:
    account = connected_account(db_session)
    draft = _draft(db_session, account)
    db_session.commit()
    fake = FakeGmail()
    send_service(db_session, fake).send(draft.id, actor_for(account))

    decoded = _sent_raw(fake)
    assert draft.message_id_header in decoded
    # And no 7bit anywhere in what actually went out: the body is Italian and a client
    # that takes a `7bit` declaration literally would mangle it.
    assert "7bit" not in decoded


def test_what_left_parses_back_into_the_message_we_meant(db_session: Session) -> None:
    """The round trip B2-2 introduced, now over the *send path*: builder -> the send
    endpoint -> `format=full` -> `parse_message`.

    A send proved only against the fake's happy path is proved against nothing. This is
    the assertion that the subject with its em dash, the body with its accents and the
    id we minted all survive being turned into wire bytes and read back the way Gmail
    would file them in Sent -- rather than agreeing only with themselves.
    """
    account = connected_account(db_session)
    draft = _draft(db_session, account)
    db_session.commit()
    fake = FakeGmail()

    read = send_service(db_session, fake).send(draft.id, actor_for(account))

    status, body, _ = fake("GET", message_get_url(read.sent_gmail_message_id or ""), {}, None)
    assert status == 200
    parsed = parse_message(json.loads(body), body_max_bytes=262_144, store_bodies=True)
    assert parsed.message_id_header == draft.message_id_header
    assert parsed.subject == SUBJECT
    assert parsed.body_text.replace("\r\n", "\n").rstrip("\n") == BODY
    assert parsed.to_addresses == ["ada@acme.it"]


def test_the_from_header_is_the_issuer_s_name_and_a_comma_in_it_adds_no_recipient(
    db_session: Session,
) -> None:
    """`emitter_profile.ragione_sociale` is somebody's own business name, and «Bianchi,
    Rossi e Associati» is an ordinary Italian one. Unquoted it parses as **two**
    addresses -- a bare `Bianchi`, then the real one -- because the comma is the
    address-list separator, which is a malformed `From` on every message the
    installation ever sends."""
    from email import message_from_string
    from email.policy import default as default_policy
    from email.utils import getaddresses

    db_session.add(
        EmitterProfile(
            ragione_sociale="Bianchi, Rossi e Associati",
            partita_iva="12345678901",
            codice_fiscale="BNCRSS80A01H501U",
            regime_fiscale="RF19",
        )
    )
    account = connected_account(db_session)
    draft = _draft(db_session, account)
    db_session.commit()
    fake = FakeGmail()

    send_service(db_session, fake).send(draft.id, actor_for(account))

    header = message_from_string(_sent_raw(fake), policy=default_policy)["From"]
    assert getaddresses([str(header)]) == [("Bianchi, Rossi e Associati", account.email_address)]


def test_a_reminder_inside_a_thread_carries_in_reply_to_and_references(
    db_session: Session,
) -> None:
    """Spec 13, criterion 13. A reminder that drops the chain arrives detached, and the
    first thing the recipient does is ask for the invoice again."""
    account = connected_account(db_session)
    original = GmailMessage(
        google_account_id=account.id,
        gmail_message_id="m-original",
        gmail_thread_id="t-1",
        message_id_header="<original.1@crm.example.it>",
        references="<root@acme.it>",
        direction="outbound",
        from_address="io@example.it",
        to_addresses=["ada@acme.it"],
        subject="Fattura 2026/14",
        internal_date=datetime(2026, 8, 1, tzinfo=UTC),
    )
    db_session.add(original)
    db_session.flush()
    draft = _draft(db_session, account, in_reply_to_message_id=original.id)
    db_session.commit()

    fake = FakeGmail()
    send_service(db_session, fake).send(draft.id, actor_for(account))

    decoded = _sent_raw(fake)
    assert "In-Reply-To: <original.1@crm.example.it>" in decoded
    # The whole chain, in order: the root the parent already carried, then the parent.
    references = decoded.split("References:")[1].split("\n\n")[0]
    assert "<root@acme.it>" in references
    assert "<original.1@crm.example.it>" in references


def test_the_sent_message_is_filed_against_the_people_it_actually_concerns(
    db_session: Session,
) -> None:
    """The same rule as an inbound message: one email concerns the person written to and
    the customer they belong to, and a sent message visible on the person but not on the
    customer is the conversation that lies, in the other direction."""
    account = connected_account(db_session)
    customer = _customer(db_session)
    person = Person(customer_id=customer.id, nome="Ada", cognome="Rossi", email="ada@acme.it")
    db_session.add(person)
    db_session.flush()
    draft = _draft(db_session, account, customer)
    db_session.commit()

    send_service(db_session, FakeGmail()).send(draft.id, actor_for(account))
    db_session.commit()

    filed = set(
        db_session.execute(select(GmailMessageLink.entity_type, GmailMessageLink.entity_id)).all()
    )
    assert ("customer", customer.id) in filed
    assert ("person", person.id) in filed


# --- refusals before anything is spent ------------------------------------------------


def test_a_revoked_credential_fails_before_composing_and_makes_no_http_call(
    db_session: Session,
) -> None:
    """Spec 13, criterion 5 (d). The gate is called before the attachments are read and
    before the draft is claimed, so nothing is half-done and nothing is spent."""
    account = connected_account(db_session, status="revoked")
    draft = _draft(db_session, account)
    db_session.commit()
    fake = FakeGmail()

    with pytest.raises(CredentialRevoked):
        send_service(db_session, fake).send(draft.id, actor_for(account))
    assert fake.requests == []
    db_session.rollback()
    row = db_session.get(EmailDraft, draft.id)
    assert row is not None
    # Not even claimed: a refusal before the gate must not consume the draft.
    assert row.send_state == "bozza"


def test_a_grant_without_gmail_send_refuses_by_naming_the_scope(db_session: Session) -> None:
    account = connected_account(db_session, scopes=("openid", "email", SCOPE_READONLY))
    draft = _draft(db_session, account)
    db_session.commit()
    fake = FakeGmail()

    with pytest.raises(ScopeMissing) as caught:
        send_service(db_session, fake).send(draft.id, actor_for(account))
    assert "gmail.send" in caught.value.message
    assert fake.requests == []
    db_session.rollback()
    row = db_session.get(EmailDraft, draft.id)
    assert row is not None and row.send_state == "bozza"


def test_a_readonly_actor_cannot_send(db_session: Session) -> None:
    account = connected_account(db_session)
    draft = _draft(db_session, account)
    db_session.commit()
    fake = FakeGmail()
    readonly = Actor(id=account.user_id, type="user", role="readonly")

    with pytest.raises(PermissionDenied):
        send_service(db_session, fake).send(draft.id, readonly)
    assert fake.requests == []


def test_an_oversized_attachment_is_refused_and_the_draft_stays_editable(
    db_session: Session, tmp_path: Path
) -> None:
    """B2-4's cap, on the path it exists for. Nothing reaches Gmail: the refusal happens
    while the message is being composed, and the draft ends up in a state the composer
    can reopen rather than frozen in `in_invio`."""
    account = connected_account(db_session)
    customer = _customer(db_session)
    document = Document(customer_id=customer.id, tipo="offerta", titolo="Offerta")
    db_session.add(document)
    db_session.flush()
    version = DocumentVersion(
        document_id=document.id,
        numero=1,
        storage_key=f"acme/offerta-{uuid4().hex}.pdf",
        content_type="application/pdf",
        dimensione=21 * MB,
        hash_sha256="0" * 64,
    )
    db_session.add(version)
    db_session.flush()
    draft = _draft(db_session, account, customer, attachment_version_ids=[version.id])
    db_session.commit()
    fake = FakeGmail()

    with pytest.raises(ValidationFailed):
        send_service(db_session, fake, storage=LocalFileStorage(tmp_path)).send(
            draft.id, actor_for(account)
        )
    assert [r for r in fake.requests if r.is_messages_send] == []
    db_session.rollback()

    row = db_session.get(EmailDraft, draft.id)
    assert row is not None
    assert row.send_state == "fallito", "a draft stranded in `in_invio` can never be fixed"
    assert row.body_markdown == BODY
    assert row.last_error


# --- what Gmail says ------------------------------------------------------------------


def test_gmail_refusing_leaves_the_draft_intact_and_editable(db_session: Session) -> None:
    """Spec 6.3 (a), the clean case: nothing left, so the composer reopens with the text
    inside and the error beside it."""
    account = connected_account(db_session)
    draft = _draft(db_session, account)
    db_session.commit()
    # `refuse_send_with` and never `fail_with`: that queue is consumed by whichever call
    # comes first, and the first call of a send is the token refresh -- so the refusal
    # would land on the refresh, the send would never happen, and this test would pass
    # while proving nothing about what a refusal from Gmail does to the draft.
    fake = FakeGmail(refuse_send_with=(400, b'{"error":{"status":"INVALID_ARGUMENT"}}'))

    with pytest.raises(Conflict):
        send_service(db_session, fake).send(draft.id, actor_for(account))
    db_session.rollback()

    row = db_session.get(EmailDraft, draft.id)
    assert row is not None
    assert row.send_state == "fallito"
    assert row.last_error
    assert row.body_markdown == BODY
    assert row.to_addresses == ["ada@acme.it"]
    assert db_session.execute(select(func.count()).select_from(GmailMessage)).scalar_one() == 0


def test_a_definite_refusal_is_not_retried_over_the_network(db_session: Session) -> None:
    """A 400 will not become a 200 by being asked again, and every retry is one more
    chance for the message to actually leave. One HTTP call, then the refusal."""
    account = connected_account(db_session)
    draft = _draft(db_session, account)
    db_session.commit()
    fake = FakeGmail(refuse_send_with=(400, b'{"error":{"status":"INVALID_ARGUMENT"}}'))

    with pytest.raises(Conflict):
        send_service(db_session, fake).send(draft.id, actor_for(account))
    assert len([r for r in fake.requests if r.is_messages_send]) == 1


def test_a_transient_failure_is_not_retried_either_because_a_send_is_not_idempotent(
    db_session: Session,
) -> None:
    """The one place in this slice where the transport's retry-with-backoff would be a
    defect rather than a kindness.

    A 503 on a listing costs a second listing. A 503 on a *send* may be a message that
    left and an answer that did not, and Gmail offers no idempotency key -- so the
    transport's four attempts are up to four copies in a client's inbox. `deliver_on_
    timeout` makes the fake behave the way that really goes wrong: every attempt that
    reaches it delivers. Drop `retry=False` from the send call and this reports four.
    """
    account = connected_account(db_session)
    draft = _draft(db_session, account)
    db_session.commit()
    fake = FakeGmail(timeout_on_send=True, deliver_on_timeout=True)

    with pytest.raises(Conflict, match="da verificare"):
        send_service(db_session, fake).send(draft.id, actor_for(account))
    assert len([r for r in fake.requests if r.is_messages_send]) == 1
    assert len(fake.messages) == 1, "a retried send is a second copy of somebody's email"
    db_session.rollback()
    row = db_session.get(EmailDraft, draft.id)
    assert row is not None and row.send_state == "incerto"


def test_a_failed_draft_can_be_retried(db_session: Session) -> None:
    """`fallito` is not terminal: nothing left, so trying again is correct. Only
    `inviato`, `in_invio` and `incerto` are refused."""
    account = connected_account(db_session)
    draft = _draft(db_session, account)
    db_session.commit()
    failing = FakeGmail(refuse_send_with=(400, b"{}"))
    with pytest.raises(Conflict):
        send_service(db_session, failing).send(draft.id, actor_for(account))
    db_session.rollback()

    working = FakeGmail()
    read = send_service(db_session, working).send(draft.id, actor_for(account))
    assert read.send_state == "inviato"
    # And the stale error is gone: an error sentence next to a message that has now
    # left describes a failure that is no longer true.
    assert read.last_error is None


def test_the_retried_send_reuses_the_message_id_minted_at_creation(
    db_session: Session,
) -> None:
    """Minting a new one on the retry would make the reconciliation of spec 6.3 look for
    a message that was never sent under that name."""
    account = connected_account(db_session)
    draft = _draft(db_session, account)
    db_session.commit()
    with pytest.raises(Conflict):
        send_service(db_session, FakeGmail(refuse_send_with=(400, b"{}"))).send(
            draft.id, actor_for(account)
        )
    db_session.rollback()

    working = FakeGmail()
    send_service(db_session, working).send(draft.id, actor_for(account))
    assert draft.message_id_header in _sent_raw(working)


def test_sending_the_same_draft_twice_is_refused(db_session: Session) -> None:
    account = connected_account(db_session)
    draft = _draft(db_session, account)
    db_session.commit()
    fake = FakeGmail()
    service = send_service(db_session, fake)
    service.send(draft.id, actor_for(account))
    db_session.commit()

    with pytest.raises(Conflict) as caught:
        service.send(draft.id, actor_for(account))
    assert "inviat" in caught.value.message
    # One email, one send call. A double click or a proxy retry cannot spend twice.
    assert len([r for r in fake.requests if r.is_messages_send]) == 1


def test_the_error_the_user_reads_carries_no_recipient_and_no_body(
    db_session: Session,
) -> None:
    """`last_error` is a stored column that the composer, the draft list and the REST
    layer all render. A recipient address or a line of the message in it would put
    somebody's private correspondence into every one of those surfaces, and into every
    test failure dump that prints the row."""
    account = connected_account(db_session)
    draft = _draft(db_session, account)
    db_session.commit()
    fake = FakeGmail(refuse_send_with=(403, b'{"error":{"status":"PERMISSION_DENIED"}}'))

    with pytest.raises(Conflict) as caught:
        send_service(db_session, fake).send(draft.id, actor_for(account))
    db_session.rollback()
    row = db_session.get(EmailDraft, draft.id)
    assert row is not None

    for rendered in (row.last_error or "", caught.value.message, str(caught.value.details)):
        assert "ada@acme.it" not in rendered
        assert "offerta" not in rendered.lower()
        assert "ya29" not in rendered, "the bearer token must never reach a message"


def test_a_deleted_draft_cannot_be_sent(db_session: Session) -> None:
    account = connected_account(db_session)
    fake = FakeGmail()
    from pigrocrm.core.errors import NotFound

    with pytest.raises(NotFound):
        send_service(db_session, fake).send(uuid4(), actor_for(account))
    assert fake.requests == []


# --- the claim, proved on two connections ---------------------------------------------


def _cleanup(engine: Engine, *, customer_id: UUID, user_id: UUID, account_id: UUID) -> None:
    """Everything the concurrency test committed, in the order the foreign keys allow.
    `google_accounts` and `gmail_messages` cascade from `users`; `email_drafts` and
    `activities` carry no FK to either, so they go explicitly."""
    with session_factory(engine)() as session:
        session.execute(delete(EmailDraft).where(EmailDraft.entity_id == customer_id))
        session.execute(delete(Activity).where(Activity.entity_id.in_([customer_id, account_id])))
        session.execute(delete(Customer).where(Customer.id == customer_id))
        session.execute(delete(User).where(User.id == user_id))
        session.commit()


def test_two_concurrent_sends_produce_one_email_one_row_and_one_conflict(
    db_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Spec 13, criterion 10. Real threads on real connections, meeting at a barrier
    placed between the pre-check and the claim, so that **both** have already read the
    draft as sendable before either tries to take it.

    That barrier is the whole test. Without it one thread routinely finishes before the
    other starts and the pre-check alone would look sufficient; with it, the pre-check is
    passed by both and only the conditional UPDATE can arbitrate. Replace
    `claim_draft_for_send`'s `WHERE send_state IN (...)` with a read-then-set and this
    reports `["sent", "sent"]`, two outbound rows and two emails in a client's inbox.
    """
    factory = session_factory(db_engine)
    customer_id: UUID | None = None
    user_id: UUID | None = None
    account_id: UUID | None = None
    try:
        with factory() as setup:
            account = connected_account(setup)
            user_id, account_id = account.user_id, account.id
            customer = _customer(setup)
            customer_id = customer.id
            draft = _draft(setup, account, customer)
            setup.commit()
            actor = actor_for(account)
            draft_id = draft.id

        fake = FakeGmail()
        both_ready = Barrier(2, timeout=30)
        original = GmailRepository.claim_draft_for_send

        def claim_at_the_same_moment(
            self: GmailRepository, target: UUID, now: datetime, mailbox: UUID
        ) -> bool:
            both_ready.wait()
            return original(self, target, now, mailbox)

        monkeypatch.setattr(GmailRepository, "claim_draft_for_send", claim_at_the_same_moment)

        def attempt(_: int) -> str:
            with factory() as session:
                try:
                    send_service(session, fake, settings=gmail_settings()).send(draft_id, actor)
                except Conflict:
                    return "conflict"
                return "sent"

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = sorted(
                future.result(timeout=60) for future in [pool.submit(attempt, i) for i in range(2)]
            )

        assert outcomes == ["conflict", "sent"], "the same draft was sent twice"
        assert len([r for r in fake.requests if r.is_messages_send]) == 1
        with factory() as check:
            assert (
                check.execute(
                    select(func.count())
                    .select_from(GmailMessage)
                    .where(GmailMessage.direction == "outbound")
                ).scalar_one()
                == 1
            )
            row = check.get(EmailDraft, draft_id)
            assert row is not None and row.send_state == "inviato"
    finally:
        if customer_id is not None and user_id is not None and account_id is not None:
            _cleanup(db_engine, customer_id=customer_id, user_id=user_id, account_id=account_id)
