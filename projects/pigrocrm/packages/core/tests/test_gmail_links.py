"""Where a synchronised message lands, and what the timeline says about it.

Two rules are under test here and they pull in opposite directions, which is why they
need each other:

* **A message is filed against every entity the conversation touches.** One email
  concerns Ada, the customer Ada works for and the deal that customer has open, all at
  once -- so `gmail_message_links` is many-to-many and the links are computed for the
  *thread*, not for each message in isolation.
* **The thread is what widens, never the roster.** A sibling message from somebody the
  CRM has never heard of is stored and linked through its conversation, and that
  person's address still does not become a reason to go looking for more mail
  (spec 4.3). `test_gmail_sync.py` owns the second half of that; this file owns the
  first.

Nothing asserted here prints a body. The activity payload deliberately carries the
subject and the sender -- that is what makes a timeline entry readable -- and a test
below pins the fact that it carries nothing more.
"""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from fakes.fake_gmail import FakeGmail, FakeMessage
from fakes.gmail_fixtures import MAILBOX, actor_for, connected_account, sync_service
from sqlalchemy import select
from sqlalchemy.orm import Session

from pigrocrm.core.activities.models import Activity
from pigrocrm.core.actor import Actor
from pigrocrm.core.customers.models import Customer
from pigrocrm.core.deals.models import Deal
from pigrocrm.core.gmail.models import GmailMessage, GmailMessageLink
from pigrocrm.core.gmail.repository import GmailRepository
from pigrocrm.core.gmail.roster import EntityRef
from pigrocrm.core.people.models import Person


def _ms(days_ago: float) -> int:
    """An `internalDate` inside the first cycle's 30-day window.

    Relative to now and never a literal date, for the reason `test_gmail_sync.py` states:
    a fixed 2023 timestamp falls outside the `after:` clause, the fake matches nothing,
    and every assertion below would then be made against an empty mailbox.
    """
    return int((datetime.now(UTC) - timedelta(days=days_ago)).timestamp() * 1000)


def _thread(fake: FakeGmail) -> None:
    """One conversation, two writers: a person the CRM knows, and a stranger."""
    fake.messages["m1"] = FakeMessage(
        id="m1",
        thread_id="t1",
        headers={
            "From": "ada@acme.it",
            "To": MAILBOX,
            "Subject": "Rinnovo",
            "Message-ID": "<m1@acme.it>",
        },
        body_text="corpo riservato uno",
        internal_date_ms=_ms(2),
    )
    fake.messages["m2"] = FakeMessage(
        id="m2",
        thread_id="t1",
        headers={
            "From": "stranger@elsewhere.com",
            "To": MAILBOX,
            "Subject": "Re: Rinnovo",
            "Message-ID": "<m2@elsewhere.com>",
        },
        body_text="corpo riservato due",
        internal_date_ms=_ms(1),
    )


def _acme(session: Session) -> Customer:
    customer = Customer(ragione_sociale="Acme", email=None)
    session.add(customer)
    session.flush()
    return customer


def _ada(session: Session, customer: Customer) -> Person:
    person = Person(nome="Ada", cognome="Byron", email="ada@acme.it", customer_id=customer.id)
    session.add(person)
    session.flush()
    return person


def _links_for(session: Session, gmail_id: str) -> set[tuple[str, UUID]]:
    message = (
        session.execute(select(GmailMessage).where(GmailMessage.gmail_message_id == gmail_id))
        .scalars()
        .one()
    )
    return {
        (row.entity_type, row.entity_id)
        for row in session.execute(
            select(GmailMessageLink).where(GmailMessageLink.gmail_message_id == message.id)
        )
        .scalars()
        .all()
    }


# --- linking -------------------------------------------------------------------------


def test_an_email_appears_on_the_person_and_on_her_customer(
    db_session: Session, seeded_open_stage_id: UUID
) -> None:
    """Spec 13, criterion 3. Three links for one message, because a single foreign key
    would force a choice the data does not support."""
    account = connected_account(db_session)
    customer = _acme(db_session)
    person = _ada(db_session, customer)
    deal = Deal(nome="Rinnovo", customer_id=customer.id, pipeline_stage_id=seeded_open_stage_id)
    db_session.add(deal)
    db_session.flush()

    fake = FakeGmail()
    _thread(fake)
    report = sync_service(db_session, fake).sync(actor_for(account))
    db_session.commit()

    assert _links_for(db_session, "m1") == {
        ("person", person.id),
        ("customer", customer.id),
        ("deal", deal.id),
    }
    # Three refs across the two messages of the thread, counted exactly: a `>=` here
    # would pass just as well if the counter were never decremented for a refused
    # duplicate, which is the number this field exists to report.
    assert report.links_created == 6


def test_a_sibling_message_from_a_stranger_is_stored_but_linked_through_the_thread(
    db_session: Session,
) -> None:
    """The stranger's own address resolves to nothing, so the link comes from the
    thread: the message belongs to the same conversation, and a conversation shown
    half-populated is the defect spec 4.3 exists to avoid."""
    account = connected_account(db_session)
    customer = _acme(db_session)
    person = _ada(db_session, customer)

    fake = FakeGmail()
    _thread(fake)
    sync_service(db_session, fake).sync(actor_for(account))
    db_session.commit()

    assert _links_for(db_session, "m2") == {
        ("person", person.id),
        ("customer", customer.id),
    }


def test_syncing_twice_does_not_duplicate_a_link(db_session: Session) -> None:
    account = connected_account(db_session)
    customer = _acme(db_session)
    _ada(db_session, customer)

    fake = FakeGmail()
    _thread(fake)
    # Inside the 24-hour overlap the second cycle lists from. At `_thread`'s own two days
    # and one day the second cycle listed nothing, and the count below held for an empty
    # run every time (REB-262).
    fake.messages["m1"].internal_date_ms = _ms(0.5)
    fake.messages["m2"].internal_date_ms = _ms(0.25)
    service = sync_service(db_session, fake)
    service.sync(actor_for(account))
    db_session.commit()
    before = len(db_session.execute(select(GmailMessageLink)).scalars().all())
    assert before == 4, "the first cycle linked nothing, so the second proves nothing"

    second = service.sync(actor_for(account))
    db_session.commit()
    assert (second.threads_fetched, second.messages_skipped) == (1, 2), (
        "the second cycle did not fetch the conversation again, so it proves nothing"
    )
    assert len(db_session.execute(select(GmailMessageLink)).scalars().all()) == before


def test_a_refused_link_does_not_discard_the_rest_of_the_cycle(db_session: Session) -> None:
    """The reason `add_link` owns a SAVEPOINT rather than catching the `IntegrityError`
    and calling `Session.rollback()`.

    A cycle commits once, at the end. A session-wide rollback would therefore throw away
    every message and every link written earlier in the same cycle -- and the loss would
    be invisible, because a duplicate link is the *expected* outcome of two overlapping
    cycles filing the same conversation. This test writes, then provokes the duplicate,
    then checks that what came before it is still there.
    """
    account = connected_account(db_session)
    customer = _acme(db_session)
    repo = GmailRepository(db_session)
    first = _row(account.id, "m1")
    second = _row(account.id, "m2")
    assert repo.add_message_if_absent(first) is True
    assert repo.add_message_if_absent(second) is True

    ref = EntityRef("customer", customer.id)
    assert repo.add_link(first.id, ref) is True
    assert repo.add_link(second.id, ref) is True
    assert repo.add_link(second.id, ref) is False, "the second insert of a triple is a duplicate"

    assert len(db_session.execute(select(GmailMessage)).scalars().all()) == 2
    assert len(db_session.execute(select(GmailMessageLink)).scalars().all()) == 2


def test_the_same_entity_on_two_messages_is_two_links(db_session: Session) -> None:
    """The unique constraint is on the triple, not on the pair: a customer is linked to
    every message of the conversation, or the timeline would show one email per thread."""
    account = connected_account(db_session)
    customer = _acme(db_session)
    repo = GmailRepository(db_session)
    first = _row(account.id, "m1")
    second = _row(account.id, "m2")
    repo.add_message_if_absent(first)
    repo.add_message_if_absent(second)
    ref = EntityRef("customer", customer.id)
    assert repo.add_link(first.id, ref) is True
    assert repo.add_link(second.id, ref) is True


# --- reading the links back ----------------------------------------------------------


def test_messages_for_entity_returns_the_conversation_and_honours_the_limit(
    db_session: Session,
) -> None:
    """What B1-13's customer page and B1-17's thread view both read."""
    account = connected_account(db_session)
    customer = _acme(db_session)
    _ada(db_session, customer)
    fake = FakeGmail()
    _thread(fake)
    sync_service(db_session, fake).sync(actor_for(account))
    db_session.commit()

    repo = GmailRepository(db_session)
    found = repo.messages_for_entity("customer", customer.id, limit=10)
    assert [row.gmail_message_id for row in found] == ["m1", "m2"]
    assert len(repo.messages_for_entity("customer", customer.id, limit=1)) == 1
    # An entity nobody wrote to is not an error, it is an empty conversation.
    assert repo.messages_for_entity("customer", uuid4(), limit=10) == []


def test_last_inbound_from_ignores_our_own_replies_and_anything_older(
    db_session: Session,
) -> None:
    """The signal 5B-2's reminder candidate list is built on: chasing a client who has
    already answered is the mistake a CRM that does not read email cannot notice."""
    account = connected_account(db_session)
    other = connected_account(db_session, email_address="altra@example.it")
    repo = GmailRepository(db_session)
    now = datetime.now(UTC)
    since = now - timedelta(days=7)

    repo.add_message_if_absent(
        _row(account.id, "vecchio", frm="ada@acme.it", when=now - timedelta(days=30))
    )
    repo.add_message_if_absent(
        _row(account.id, "nostro", frm=MAILBOX, direction="outbound", when=now - timedelta(days=1))
    )
    repo.add_message_if_absent(
        _row(account.id, "altrui", frm="estraneo@altrove.com", when=now - timedelta(hours=1))
    )
    repo.add_message_if_absent(
        _row(account.id, "atteso", frm="ada@acme.it", when=now - timedelta(days=2))
    )
    # Same address, same window, different mailbox: another user's correspondence must
    # not answer this user's question.
    repo.add_message_if_absent(
        _row(other.id, "estranea", frm="ada@acme.it", when=now - timedelta(minutes=5))
    )

    found = repo.last_inbound_from(account.id, ["Ada@Acme.it"], since)
    assert found is not None
    assert found.gmail_message_id == "atteso"
    assert repo.last_inbound_from(account.id, ["nessuno@altrove.com"], since) is None
    # No addresses at all must not become `IN ()`, which Postgres refuses outright.
    assert repo.last_inbound_from(account.id, [], since) is None


# --- the timeline --------------------------------------------------------------------


def test_a_received_message_writes_a_timeline_entry_on_the_linked_entity(
    db_session: Session,
) -> None:
    account = connected_account(db_session)
    customer = _acme(db_session)
    _ada(db_session, customer)
    fake = FakeGmail()
    _thread(fake)
    sync_service(db_session, fake).sync(actor_for(account))
    db_session.commit()

    rows = (
        db_session.execute(select(Activity).where(Activity.kind == "gmail.messaggio_ricevuto"))
        .scalars()
        .all()
    )
    assert rows, "an inbound message must be visible on the timeline it belongs to"
    assert {row.entity_type for row in rows} <= {"customer", "person", "deal"}
    # activities/sanitize.py's own docstring cites "recording, say, an inbound email's
    # subject line" as the reason a payload is sanitised rather than validated. The
    # extension point was written with this in mind.
    assert any("Rinnovo" in str(row.payload.get("subject", "")) for row in rows)


def test_a_message_we_sent_is_not_announced_as_received(db_session: Session) -> None:
    """`gmail.messaggio_ricevuto` is a claim about direction. A cycle that recorded one
    for every stored message would tell the user their own reply had arrived, and would
    do it on every outbound message in the thread."""
    account = connected_account(db_session)
    customer = _acme(db_session)
    _ada(db_session, customer)
    fake = FakeGmail()
    fake.messages["m1"] = FakeMessage(
        id="m1",
        thread_id="t1",
        headers={
            "From": MAILBOX,
            "To": "ada@acme.it",
            "Subject": "Rinnovo",
            "Message-ID": "<m1@example.it>",
        },
        body_text="corpo riservato",
        internal_date_ms=_ms(1),
    )
    sync_service(db_session, fake).sync(actor_for(account))
    db_session.commit()

    assert (
        db_session.execute(select(Activity).where(Activity.kind == "gmail.messaggio_ricevuto"))
        .scalars()
        .all()
        == []
    )
    # It is stored and linked all the same: what we wrote is half of the conversation.
    assert _links_for(db_session, "m1") == {
        ("person", _ada_id(db_session)),
        ("customer", customer.id),
    }


def test_no_timeline_entry_carries_a_message_body(db_session: Session) -> None:
    """A timeline entry is rendered in the UI, returned by the REST timeline route and
    handed to an agent by `get_timeline`. A body in it is the same disclosure as a body
    on a public page, only harder to find afterwards."""
    account = connected_account(db_session)
    customer = _acme(db_session)
    _ada(db_session, customer)
    fake = FakeGmail()
    _thread(fake)
    sync_service(db_session, fake).sync(actor_for(account))
    db_session.commit()

    rows = db_session.execute(select(Activity)).scalars().all()
    assert rows, "no activity was written at all, so this proves nothing about what one holds"
    printed = " ".join(str(row.payload) for row in rows)
    assert "corpo riservato" not in printed


def test_the_sync_itself_is_recorded_once_per_cycle(db_session: Session) -> None:
    account = connected_account(db_session)
    sync_service(db_session, FakeGmail()).sync(actor_for(account))
    db_session.commit()
    rows = (
        db_session.execute(select(Activity).where(Activity.kind == "gmail.sync_eseguito"))
        .scalars()
        .all()
    )
    assert len(rows) == 1
    assert rows[0].entity_type == "google_account"
    assert rows[0].entity_id == account.id


def test_an_mcp_actor_is_recorded_as_mcp(db_session: Session) -> None:
    account = connected_account(db_session)
    sync_service(db_session, FakeGmail()).sync(Actor(id=account.user_id, type="mcp", role="admin"))
    db_session.commit()
    row = (
        db_session.execute(select(Activity).where(Activity.kind == "gmail.sync_eseguito"))
        .scalars()
        .one()
    )
    assert row.actor_type == "mcp"


# --- helpers -------------------------------------------------------------------------


def _ada_id(session: Session) -> UUID:
    return session.execute(select(Person.id).where(Person.email == "ada@acme.it")).scalars().one()


def _row(
    account_id: UUID,
    gmail_id: str,
    *,
    thread: str = "t1",
    frm: str = "ada@acme.it",
    direction: str = "inbound",
    when: datetime | None = None,
) -> GmailMessage:
    return GmailMessage(
        google_account_id=account_id,
        gmail_message_id=gmail_id,
        gmail_thread_id=thread,
        direction=direction,
        from_address=frm,
        to_addresses=[MAILBOX],
        cc_addresses=[],
        subject="Oggetto",
        snippet="anteprima",
        internal_date=when or datetime.now(UTC),
        body_text="corpo riservato",
        attachments=[],
    )
