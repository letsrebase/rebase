"""`email_drafts`: the text is on disk before anything else happens to it.

Two of these tests commit for real and clean up in a `finally`, for the reason
`test_gmail_lock.py` states at length: the `db_session` fixture wraps each test in a
transaction it rolls back, so a second connection inside it could never see the first
one's rows -- and "durable" and "unique across concurrent writers" are precisely claims
about what a *second connection* sees. Calling a function twice in sequence proves
neither.
"""

import inspect
import re
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from fakes.gmail_fixtures import actor_for, connected_account, gmail_settings
from pydantic import ValidationError
from sqlalchemy import Engine, delete, select
from sqlalchemy.orm import Session

from pigrocrm.core.auth.models import User
from pigrocrm.core.customers.models import Customer
from pigrocrm.core.db import session_factory
from pigrocrm.core.documents.models import Document, DocumentVersion
from pigrocrm.core.errors import Conflict, NotFound, ValidationFailed
from pigrocrm.core.gmail import drafts as drafts_module
from pigrocrm.core.gmail.drafts import EmailDraftService
from pigrocrm.core.gmail.models import EmailDraft
from pigrocrm.core.gmail.query import rfc822msgid_query
from pigrocrm.core.gmail.schemas import (
    EmailDraftCreate,
    EmailDraftListQuery,
    EmailDraftUpdate,
)
from pigrocrm.core.people.models import Person

BODY = "Gentile Ada,\n\nin allegato l'offerta."


def _customer(session: Session) -> Customer:
    customer = Customer(ragione_sociale="Acme", email="info@acme.it")
    session.add(customer)
    session.flush()
    return customer


def _payload(customer: Customer, **overrides: object) -> EmailDraftCreate:
    base: dict[str, object] = {
        "entity_type": "customer",
        "entity_id": customer.id,
        "to_addresses": ["ada@acme.it"],
        "subject": "Offerta",
        "body_markdown": BODY,
    }
    return EmailDraftCreate(**{**base, **overrides})  # type: ignore[arg-type]


def _service(session: Session) -> EmailDraftService:
    return EmailDraftService(session, settings=gmail_settings())


# --- the draft exists, and it is ours ------------------------------------------------


def test_a_draft_is_written_to_the_database_before_anything_else(db_session: Session) -> None:
    """Losing a hand-written email to an HTTP error is unforgivable, and a composer that
    keeps the text only in React state loses it on the first refresh."""
    account = connected_account(db_session)
    customer = _customer(db_session)
    read = _service(db_session).create(_payload(customer), actor_for(account))

    stored = db_session.get(EmailDraft, read.id)
    assert stored is not None
    assert stored.send_state == "bozza"
    assert stored.body_markdown == BODY
    assert stored.to_addresses == ["ada@acme.it"]
    assert stored.sent_gmail_message_id is None


def test_a_message_id_is_minted_at_creation_not_at_send(db_session: Session) -> None:
    """Spec 6.2: the Message-ID exists *before* Gmail is called. That is what makes an
    unknown outcome resolvable by lookup instead of by guessing."""
    account = connected_account(db_session)
    customer = _customer(db_session)
    read = _service(db_session).create(_payload(customer), actor_for(account))

    assert read.message_id_header.startswith("<")
    assert read.message_id_header.endswith(">")
    # The domain is the installation's own, from `public_url`, so a recipient's client
    # and a spam filter both see an id that belongs to whoever sent it.
    assert read.message_id_header.endswith("@crm.example.it>")
    # And the id the reconciliation of B2-6 will hand to `rfc822msgid:` is this one,
    # accepted by the query builder's own validation rather than merely well-formed to
    # the eye.
    assert rfc822msgid_query(read.message_id_header).endswith(read.message_id_header.strip("<>"))


def test_two_drafts_never_share_a_message_id(db_session: Session) -> None:
    account = connected_account(db_session)
    customer = _customer(db_session)
    service = _service(db_session)
    ids = {
        service.create(_payload(customer), actor_for(account)).message_id_header for _ in range(10)
    }
    assert len(ids) == 10


def test_the_caller_cannot_choose_the_send_state_or_the_gmail_id(db_session: Session) -> None:
    """`send_state` is a fact about what happened, not an input. A schema that accepted
    it would let a composer -- or an agent -- declare a message sent that never left."""
    for hostile in ("send_state", "sent_gmail_message_id", "message_id_header"):
        with pytest.raises(ValidationError):
            EmailDraftCreate(
                entity_type="customer",
                entity_id=uuid4(),
                to_addresses=["a@b.it"],
                subject="x",
                body_markdown="y",
                **{hostile: "inviato"},  # type: ignore[arg-type]
            )


# --- editing -------------------------------------------------------------------------


def test_editing_a_draft_keeps_its_message_id(db_session: Session) -> None:
    """Changing the id on every keystroke would make the reconciliation look for a
    message that was never sent under that name."""
    account = connected_account(db_session)
    customer = _customer(db_session)
    service = _service(db_session)
    created = service.create(_payload(customer), actor_for(account))
    updated = service.update(
        created.id, EmailDraftUpdate(body_markdown="Testo nuovo"), actor_for(account)
    )
    assert updated.message_id_header == created.message_id_header
    assert updated.body_markdown == "Testo nuovo"
    # An update that names one field must not blank the others.
    assert updated.subject == "Offerta"
    assert updated.to_addresses == ["ada@acme.it"]


def test_a_draft_being_sent_or_already_sent_can_no_longer_be_edited(
    db_session: Session,
) -> None:
    """Three states, and each for its own reason: `in_invio` is a message in flight, so
    an edit would change what is being sent underneath the sender; `inviato` has left;
    `incerto` may have left, and editing it would make the reconciliation of spec 6.3
    compare a Gmail message against text that is no longer the text that was sent."""
    account = connected_account(db_session)
    customer = _customer(db_session)
    service = _service(db_session)
    for state in ("in_invio", "inviato", "incerto"):
        created = service.create(_payload(customer), actor_for(account))
        draft = service.repo_draft(created.id)
        draft.send_state = state
        db_session.flush()
        with pytest.raises(Conflict, match="già inviata"):
            service.update(created.id, EmailDraftUpdate(subject="Altro"), actor_for(account))


def test_a_failed_draft_is_editable_again_and_loses_its_stale_error(
    db_session: Session,
) -> None:
    """Spec 6.3(a): Gmail refused, nothing left, «la bozza resta intatta con l'errore
    accanto, il composer si riapre con il testo dentro». Refusing the edit would leave
    the only recovery as "write it again", which is the failure the durable draft exists
    to prevent. The error goes when the text changes: an error sentence next to text it
    no longer describes is worse than none."""
    account = connected_account(db_session)
    customer = _customer(db_session)
    service = _service(db_session)
    created = service.create(_payload(customer), actor_for(account))
    draft = service.repo_draft(created.id)
    draft.send_state = "fallito"
    draft.last_error = "Gmail ha rifiutato il destinatario"
    db_session.flush()

    updated = service.update(
        created.id, EmailDraftUpdate(to_addresses=["ada.rossi@acme.it"]), actor_for(account)
    )
    assert updated.send_state == "bozza"
    assert updated.last_error is None
    assert updated.to_addresses == ["ada.rossi@acme.it"]


def test_deleting_a_draft_in_flight_is_refused(db_session: Session) -> None:
    """Deleting the row while the send is running would leave the outcome with nothing
    to be recorded against -- which is the previous system's `404 'Offerta non trovata per
    registrare l'invio email.'` with the mail already delivered."""
    account = connected_account(db_session)
    customer = _customer(db_session)
    service = _service(db_session)
    created = service.create(_payload(customer), actor_for(account))
    draft = service.repo_draft(created.id)
    draft.send_state = "in_invio"
    db_session.flush()
    with pytest.raises(Conflict):
        service.delete(created.id, actor_for(account))

    draft.send_state = "bozza"
    db_session.flush()
    service.delete(created.id, actor_for(account))
    with pytest.raises(NotFound):
        service.get(created.id, actor_for(account))


# --- what a draft may point at -------------------------------------------------------


def test_an_unknown_entity_is_a_not_found_not_a_foreign_key_error(db_session: Session) -> None:
    account = connected_account(db_session)
    customer = _customer(db_session)
    with pytest.raises(NotFound):
        _service(db_session).create(_payload(customer, entity_id=uuid4()), actor_for(account))


def test_each_entity_type_is_checked_against_its_own_table(db_session: Session) -> None:
    """A person's id passed as a customer must not resolve. `entity_type` decides the
    table, and checking only "some row with this id exists somewhere" would file the
    draft against a row of the wrong kind."""
    account = connected_account(db_session)
    customer = _customer(db_session)
    person = Person(nome="Ada", cognome="Rossi", email="ada@acme.it", customer_id=customer.id)
    db_session.add(person)
    db_session.flush()
    service = _service(db_session)

    assert (
        service.create(
            _payload(customer, entity_type="person", entity_id=person.id), actor_for(account)
        ).entity_type
        == "person"
    )
    with pytest.raises(NotFound):
        service.create(
            _payload(customer, entity_type="deal", entity_id=person.id), actor_for(account)
        )


def test_a_soft_deleted_entity_is_not_a_valid_target(db_session: Session) -> None:
    """An archived customer is not somewhere a new email belongs, and the check that
    ignored `deleted_at` would be green against a row nothing in the CRM will show."""
    from datetime import UTC, datetime

    account = connected_account(db_session)
    customer = _customer(db_session)
    customer.deleted_at = datetime.now(UTC)
    db_session.flush()
    with pytest.raises(NotFound):
        _service(db_session).create(_payload(customer), actor_for(account))


def test_an_attachment_that_is_not_a_document_version_is_refused(db_session: Session) -> None:
    """Spec 6.4: attachments come only from `document_versions`. A UUID that is merely
    well-formed must become a `NotFound` here rather than an `IntegrityError` -- or,
    worse, a send that discovers at Gmail's door that there is nothing to attach."""
    account = connected_account(db_session)
    customer = _customer(db_session)
    with pytest.raises(NotFound):
        _service(db_session).create(
            _payload(customer, attachment_version_ids=[uuid4()]), actor_for(account)
        )


def test_a_reply_to_an_unknown_message_is_refused(db_session: Session) -> None:
    account = connected_account(db_session)
    customer = _customer(db_session)
    with pytest.raises(NotFound):
        _service(db_session).create(
            _payload(customer, in_reply_to_message_id=uuid4()), actor_for(account)
        )


def test_a_draft_with_no_recipients_is_refused_at_creation(db_session: Session) -> None:
    account = connected_account(db_session)
    customer = _customer(db_session)
    with pytest.raises(ValidationFailed):
        _service(db_session).create(_payload(customer, to_addresses=[]), actor_for(account))


def test_an_update_may_not_empty_the_recipients_either(db_session: Session) -> None:
    account = connected_account(db_session)
    customer = _customer(db_session)
    service = _service(db_session)
    created = service.create(_payload(customer), actor_for(account))
    with pytest.raises(ValidationFailed):
        service.update(created.id, EmailDraftUpdate(to_addresses=[]), actor_for(account))


# --- the schema, not Postgres --------------------------------------------------------


def test_an_over_long_subject_is_rejected_by_the_schema_not_by_postgres() -> None:
    with pytest.raises(ValidationError) as caught:
        EmailDraftCreate(
            entity_type="customer",
            entity_id=uuid4(),
            to_addresses=["a@b.it"],
            subject="x" * 999,
            body_markdown="y",
        )
    assert "998" in str(caught.value) or "at most" in str(caught.value)


def test_a_nul_byte_in_the_body_is_rejected_and_not_stripped() -> None:
    with pytest.raises(ValidationError):
        EmailDraftCreate(
            entity_type="customer",
            entity_id=uuid4(),
            to_addresses=["a@b.it"],
            subject="x",
            body_markdown="prima\x00dopo",
        )


def test_an_over_long_address_is_rejected_by_the_schema() -> None:
    with pytest.raises(ValidationError):
        EmailDraftCreate(
            entity_type="customer",
            entity_id=uuid4(),
            to_addresses=["a" * 320 + "@b.it"],
            subject="x",
            body_markdown="y",
        )


# --- privacy -------------------------------------------------------------------------


def test_no_error_this_service_raises_carries_the_body_or_a_recipient(
    db_session: Session,
) -> None:
    """A body and a recipient list are somebody's private correspondence. They may not
    reach an exception message, its `details`, or the pytest dump of either -- which is
    where a `NotFound(entity, payload)` written in a hurry would put them."""
    account = connected_account(db_session)
    customer = _customer(db_session)
    secret_body = "il prezzo riservato è 12.000 €"
    secret_address = "segreta@altrove.it"
    with pytest.raises((NotFound, ValidationFailed, Conflict)) as caught:
        _service(db_session).create(
            _payload(
                customer,
                entity_id=uuid4(),
                to_addresses=[secret_address],
                body_markdown=secret_body,
            ),
            actor_for(account),
        )
    rendered = f"{caught.value} {caught.value.details}"
    assert secret_body not in rendered
    assert secret_address not in rendered


# --- reading -------------------------------------------------------------------------


def test_list_returns_the_drafts_of_one_entity_and_filters_by_state(
    db_session: Session,
) -> None:
    account = connected_account(db_session)
    customer = _customer(db_session)
    other = Customer(ragione_sociale="Beta", email="info@beta.it")
    db_session.add(other)
    db_session.flush()
    service = _service(db_session)

    mine = service.create(_payload(customer), actor_for(account))
    service.create(_payload(customer, entity_id=other.id), actor_for(account))
    sent = service.create(_payload(customer, subject="Inviata"), actor_for(account))
    service.repo_draft(sent.id).send_state = "inviato"
    db_session.flush()

    page = service.list(
        EmailDraftListQuery(entity_type="customer", entity_id=customer.id), actor_for(account)
    )
    assert page.total == 2
    assert {item.id for item in page.items} == {mine.id, sent.id}

    only_drafts = service.list(
        EmailDraftListQuery(entity_type="customer", entity_id=customer.id, send_state="bozza"),
        actor_for(account),
    )
    assert [item.id for item in only_drafts.items] == [mine.id]

    # REB-415: what the Email tab asks for. Every state but `inviato`, counted the same
    # way, so a page of sent drafts can never push an unsent one out of the list.
    uncertain = service.create(_payload(customer, subject="Incerta"), actor_for(account))
    service.repo_draft(uncertain.id).send_state = "incerto"
    db_session.flush()
    unsent = service.list(
        EmailDraftListQuery(entity_type="customer", entity_id=customer.id, unsent=True, limit=1),
        actor_for(account),
    )
    assert unsent.total == 2
    unsent_ids = {
        item.id
        for item in service.list(
            EmailDraftListQuery(entity_type="customer", entity_id=customer.id, unsent=True),
            actor_for(account),
        ).items
    }
    assert unsent_ids == {mine.id, uncertain.id}


def test_every_read_names_the_attachments_the_send_will_carry(db_session: Session) -> None:
    """REB-415: the Email tab shows a draft to the person who is about to send it, and an
    attachment shown as a version id tells them nothing about what their client will
    receive. `get`, `list` and the create that hands the row back all name the file the
    way the send will (`attach.attachment_filename`), in the order the draft keeps."""
    account = connected_account(db_session)
    customer = _customer(db_session)
    document = Document(customer_id=customer.id, tipo="offerta", titolo="Offerta Q1")
    db_session.add(document)
    db_session.flush()
    versions = [
        DocumentVersion(
            document_id=document.id,
            numero=numero,
            storage_key=f"acme/offerta-v{numero}-{uuid4().hex}.pdf",
            content_type="application/pdf",
            dimensione=1024 * numero,
            hash_sha256="0" * 64,
        )
        for numero in (1, 2)
    ]
    db_session.add_all(versions)
    db_session.flush()
    service = _service(db_session)

    created = service.create(
        _payload(customer, attachment_version_ids=[versions[1].id, versions[0].id]),
        actor_for(account),
    )
    listed = service.list(
        EmailDraftListQuery(entity_type="customer", entity_id=customer.id), actor_for(account)
    ).items[0]
    read = service.get(created.id, actor_for(account))

    for draft in (created, listed, read):
        assert [a.filename for a in draft.attachments] == [
            "offerta-q1-v2.pdf",
            "offerta-q1-v1.pdf",
        ]
        assert [a.version_id for a in draft.attachments] == draft.attachment_version_ids
        assert [a.dimensione for a in draft.attachments] == [2048, 1024]

    plain = service.create(_payload(customer), actor_for(account))
    assert plain.attachments == []


def test_list_is_the_last_method_defined_on_the_service() -> None:
    """A method named `list` rebinds the builtin in the class namespace, so a later
    method annotated `-> list[...]` fails at import on Python 3.13. The rule is
    unconditional, so it is asserted rather than remembered."""
    source = inspect.getsource(EmailDraftService)
    names = [match.group(1) for match in re.finditer(r"\n    def ([a-zA-Z_]\w*)\(", source)]
    assert names[-1] == "list", names


# --- the two claims that need a second connection ------------------------------------


def _cleanup(engine: Engine, *, draft_ids: list[UUID], customer_id: UUID, user_id: UUID) -> None:
    """Everything these two tests committed, removed in the order the foreign keys
    allow. `google_accounts` cascades from `users`, so deleting the user is enough."""
    with session_factory(engine)() as session:
        if draft_ids:
            session.execute(delete(EmailDraft).where(EmailDraft.id.in_(draft_ids)))
        session.execute(delete(Customer).where(Customer.id == customer_id))
        session.execute(delete(User).where(User.id == user_id))
        session.commit()


def test_the_draft_is_on_disk_before_the_caller_does_anything_else(db_engine: Engine) -> None:
    """The ordering claim, proved the only way it can be: a **second connection** sees
    the draft without the caller ever having committed.

    If `create` merely flushed and left the commit to whoever called it, this reads
    nothing -- and that is exactly the shape in which the text is lost when the request
    that was going to commit dies instead. Committed for real, so cleaned up in a
    `finally`.
    """
    factory = session_factory(db_engine)
    draft_id: UUID | None = None
    customer_id: UUID | None = None
    user_id: UUID | None = None
    try:
        with factory() as writer:
            account = connected_account(writer)
            user_id = account.user_id
            customer = _customer(writer)
            customer_id = customer.id
            writer.commit()
            read = EmailDraftService(writer, settings=gmail_settings()).create(
                _payload(customer), actor_for(account)
            )
            draft_id = read.id

        # A different connection entirely, opened after the fact: it can only see rows
        # that are committed.
        with factory() as reader:
            found = reader.get(EmailDraft, draft_id)
            assert found is not None, "the draft was never committed: an HTTP error loses it"
            assert found.body_markdown == BODY
            assert found.send_state == "bozza"
            assert found.message_id_header.endswith("@crm.example.it>")
    finally:
        if customer_id is not None and user_id is not None:
            _cleanup(
                db_engine,
                draft_ids=[draft_id] if draft_id else [],
                customer_id=customer_id,
                user_id=user_id,
            )


def test_two_concurrent_creates_can_never_share_a_message_id(
    db_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The uniqueness is the database's, not uuid7's good luck.

    Both threads are forced onto the same `Message-ID` and made to overlap on a barrier
    placed between the mint and the commit, so neither can lose merely because the other
    had already finished. Exactly one row may survive: two drafts sharing one id would
    make the reconciliation lookup of spec 6.3 ambiguous at the one moment it matters.
    Without `uq_email_drafts_message_id` this returns two successes instead of one.
    """
    collision = f"<collision.{uuid4().hex}@crm.example.it>"
    monkeypatch.setattr(drafts_module, "new_message_id", lambda domain: collision)

    factory = session_factory(db_engine)
    customer_id: UUID | None = None
    user_id: UUID | None = None
    try:
        with factory() as setup:
            account = connected_account(setup)
            user_id = account.user_id
            customer = _customer(setup)
            customer_id = customer.id
            setup.commit()
            actor = actor_for(account)
            payload = _payload(customer)

        both_minted = Barrier(2)

        def create_one() -> bool:
            with factory() as session:
                service = EmailDraftService(session, settings=gmail_settings())
                both_minted.wait(timeout=30)
                try:
                    service.create(payload, actor)
                except Exception:
                    return False
                return True

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = sorted(
                future.result(timeout=60) for future in [pool.submit(create_one) for _ in range(2)]
            )
        assert outcomes == [False, True], "two drafts were stored under one Message-ID"

        with factory() as reader:
            surviving = (
                reader.execute(
                    select(EmailDraft.id).where(EmailDraft.message_id_header == collision)
                )
                .scalars()
                .all()
            )
        assert len(surviving) == 1
    finally:
        with factory() as cleaner:
            stored = (
                cleaner.execute(
                    select(EmailDraft.id).where(EmailDraft.message_id_header == collision)
                )
                .scalars()
                .all()
            )
            cleaner.commit()
        if customer_id is not None and user_id is not None:
            _cleanup(db_engine, draft_ids=list(stored), customer_id=customer_id, user_id=user_id)
