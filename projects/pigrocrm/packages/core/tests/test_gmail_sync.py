"""The sync cycle, and the third guard of spec 4.1.

`gmail/query.py` refuses to *build* a listing URL without an address clause, and
`test_gmail_query.py` walks the AST of the repository so no caller can write such a URL
elsewhere. Both guards reason about code. This file adds the one that reasons about
behaviour: after a real sync against a mailbox of a thousand irrelevant messages, every
request the fake transport recorded is inspected, and every address any of them asked
Gmail about has to belong to the roster. A sync that quietly widened its own relevance
-- by asking about the connected mailbox itself, about a correspondent met inside a
thread, or about anything else -- passes the first two guards and fails this one.
"""

import json
import re
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier
from urllib.parse import parse_qs, urlparse
from uuid import UUID, uuid4

import pytest
from fakes.fake_gmail import FakeGmail, FakeMessage, RecordedRequest
from fakes.gmail_fixtures import (
    MAILBOX,
    REFRESH_TOKEN,
    TOKEN_KEY,
    actor_for,
    connected_account,
    gmail_settings,
    sync_service,
)
from sqlalchemy import Engine, delete, func, select
from sqlalchemy.orm import Session

from pigrocrm.core.activities.models import Activity
from pigrocrm.core.actor import Actor
from pigrocrm.core.auth.models import User
from pigrocrm.core.customers.models import Customer
from pigrocrm.core.db import session_factory
from pigrocrm.core.errors import Conflict, PermissionDenied
from pigrocrm.core.gmail.errors import GmailUnavailable
from pigrocrm.core.gmail.models import GmailMessage, GoogleOAuthState
from pigrocrm.core.gmail.query import ADDRESS_BATCH_DEFAULT
from pigrocrm.core.gmail.repository import GmailRepository
from pigrocrm.core.gmail.roster import AddressRoster
from pigrocrm.core.gmail.schemas import GmailMessageRead
from pigrocrm.core.people.models import Person

# Every address-bearing clause Gmail understands, as the query builder writes them. The
# guard below reads the requests with this and compares against the roster, so a clause
# naming an address nobody in the CRM owns fails whatever produced it.
_ADDRESS_CLAUSE = re.compile(r"\b(?:from|to|cc|bcc|rfc822msgid):([^\s)]+)")


def _ms(days_ago: float) -> int:
    """An `internalDate` inside the first cycle's 30-day window.

    Relative to now, never a literal date: a fixed 2023 timestamp would fall outside
    `after:` and the fake would match nothing, which is a test that passes by finding an
    empty mailbox.
    """
    return int((datetime.now(UTC) - timedelta(days=days_ago)).timestamp() * 1000)


def _message(index: int, *, frm: str, to: str, thread: str, days_ago: float = 1.0) -> FakeMessage:
    return FakeMessage(
        id=f"m{index}",
        thread_id=thread,
        headers={
            "From": frm,
            "To": to,
            "Subject": f"Oggetto {index}",
            "Message-ID": f"<msg{index}@example.it>",
        },
        body_text=f"corpo riservato {index}",
        internal_date_ms=_ms(days_ago),
    )


def _noise(fake: FakeGmail, count: int) -> None:
    """A realistic mailbox: newsletters, receipts, the children's school."""
    for index in range(count):
        fake.messages[f"n{index}"] = FakeMessage(
            id=f"n{index}",
            thread_id=f"nt{index}",
            headers={
                "From": f"newsletter{index}@spam.example",
                "To": MAILBOX,
                "Subject": "Offerta imperdibile",
                "Message-ID": f"<n{index}@spam.example>",
            },
            body_text="compra",
            internal_date_ms=_ms(2),
        )


def _customer(session: Session, email: str, ragione_sociale: str = "Acme") -> Customer:
    customer = Customer(ragione_sociale=ragione_sociale, email=email)
    session.add(customer)
    session.flush()
    return customer


def _listings(fake: FakeGmail) -> list[RecordedRequest]:
    return [request for request in fake.requests if request.is_messages_list]


def _stored_ids(session: Session) -> list[str]:
    return sorted(session.execute(select(GmailMessage.gmail_message_id)).scalars().all())


# --- the cycle -----------------------------------------------------------------------


def test_a_thousand_irrelevant_messages_and_two_known_addresses(db_session: Session) -> None:
    """Spec 13, criterion 1. The mailbox is overwhelmingly noise, as real mailboxes
    are, and what lands in the CRM is the two conversations it already had a reason to
    care about."""
    account = connected_account(db_session)
    customer = _customer(db_session, "info@acme.it")
    db_session.add(
        Person(nome="Ada", cognome="Byron", email="ada@acme.it", customer_id=customer.id)
    )
    db_session.flush()

    fake = FakeGmail()
    _noise(fake, 1_000)
    fake.messages["m1"] = _message(1, frm="ada@acme.it", to=MAILBOX, thread="t1")
    fake.messages["m2"] = _message(2, frm=MAILBOX, to="info@acme.it", thread="t2", days_ago=0.9)

    report = sync_service(db_session, fake).sync(actor_for(account))
    db_session.commit()

    assert _stored_ids(db_session) == ["m1", "m2"]
    assert report.messages_stored == 2
    assert report.threads_fetched == 2


def test_no_messages_list_is_ever_issued_without_an_address_filter(db_session: Session) -> None:
    """The adversarial test spec 4.1 asks for by name: it inspects the requests the fake
    transport *received*, rather than trusting a rule written in a comment.

    Guard one lives in `messages_list_url`, guard two in the AST sweep of
    `test_gmail_query.py`; this is guard three, and all of them are needed. The first
    two would both accept `messages_list_url("from:sconosciuto@altrove.com")` -- a
    well-formed clause naming somebody the CRM has never heard of. Only this one can
    tell that the address in the request belongs to the roster.
    """
    account = connected_account(db_session)
    customer = _customer(db_session, "info@acme.it")
    db_session.add(
        Person(nome="Ada", cognome="Byron", email="ada@acme.it", customer_id=customer.id)
    )
    db_session.flush()
    known = set(AddressRoster(db_session).known_addresses())
    assert known == {"info@acme.it", "ada@acme.it"}

    fake = FakeGmail()
    _noise(fake, 50)
    fake.messages["m1"] = _message(1, frm="ada@acme.it", to=MAILBOX, thread="t1")
    fake.messages["m2"] = _message(
        2, frm="estraneo@altrove.com", to=MAILBOX, thread="t1", days_ago=0.9
    )
    sync_service(db_session, fake).sync(actor_for(account))

    listings = _listings(fake)
    assert listings, "the sync issued no listing at all, so this test proves nothing"
    for request in listings:
        assert request.q, f"a messages.list with no q: {request.path}?{request.query}"
        asked = set(_ADDRESS_CLAUSE.findall(request.q))
        assert asked, f"a q with no address clause at all: {request.q}"
        assert asked <= known, (
            f"the sync asked Gmail about {sorted(asked - known)}, which nobody in the "
            f"CRM owns: {request.q}"
        )
    # Not even the connected mailbox itself, which is the widening that looks harmless.
    assert not any(MAILBOX in (request.q or "") for request in listings)
    # And nothing in the whole sync asked Gmail to send anything (spec 13, criterion 15).
    assert [request for request in fake.requests if request.is_messages_send] == []


def test_an_empty_roster_issues_no_request_at_all(db_session: Session) -> None:
    """Not one unfiltered listing: none. This is the shape of the failure spec 4 exists
    to prevent, so it gets its own test -- and it covers the token refresh too, because
    a cycle with nothing to ask must not even wake Google up."""
    account = connected_account(db_session)
    fake = FakeGmail()
    _noise(fake, 10)
    report = sync_service(db_session, fake).sync(actor_for(account))
    assert fake.requests == []
    assert report.queries_issued == 0
    assert report.messages_stored == 0


def test_addresses_are_asked_in_batches(db_session: Session) -> None:
    """Gmail's `q` has a practical length limit, so the roster is spent over several
    listings rather than one enormous one."""
    account = connected_account(db_session)
    for index in range(45):
        _customer(db_session, f"c{index}@acme.it", f"C{index}")

    fake = FakeGmail()
    sync_service(db_session, fake).sync(actor_for(account))
    listings = _listings(fake)
    assert len(listings) == 3
    for request in listings:
        assert (request.q or "").count("from:") <= ADDRESS_BATCH_DEFAULT
    # Every address is asked about exactly once: batching must split the roster, not
    # sample it.
    asked = [
        address for request in listings for address in _ADDRESS_CLAUSE.findall(request.q or "")
    ]
    assert sorted(set(asked)) == sorted(f"c{index}@acme.it" for index in range(45))


def test_the_listing_follows_gmail_pagination(db_session: Session) -> None:
    """A hundred results per page is Gmail's maximum, and a cycle that read only the
    first page would silently lose the older half of every busy conversation list."""
    account = connected_account(db_session)
    _customer(db_session, "info@acme.it")

    inner = FakeGmail()
    inner.messages["m1"] = _message(1, frm="info@acme.it", to=MAILBOX, thread="t1")
    inner.messages["m2"] = _message(2, frm="info@acme.it", to=MAILBOX, thread="t2", days_ago=0.9)
    report = sync_service(db_session, _Paged(inner)).sync(actor_for(account))
    db_session.commit()

    assert _stored_ids(db_session) == ["m1", "m2"]
    assert report.threads_fetched == 2
    page_tokens = [request.query.get("pageToken", []) for request in _listings(inner)]
    assert page_tokens == [[], ["pagina-2"]]


class _Paged:
    """A Gmail that answers a listing one message at a time, as the real one does once
    a query matches more than a page.

    A wrapper rather than a flag on `FakeGmail`: paging is a property of this one test,
    and the inner fake still records every request, so the guards above keep reading
    the requests that were actually made.
    """

    def __init__(self, inner: FakeGmail) -> None:
        self.inner = inner
        self.messages = inner.messages
        self.requests = inner.requests

    def __call__(
        self, method: str, url: str, headers: dict[str, str], body: bytes | None
    ) -> tuple[int, bytes, dict[str, str]]:
        status, payload, response_headers = self.inner(method, url, headers, body)
        parsed = urlparse(url)
        if status != 200 or method != "GET" or not parsed.path.endswith("/messages"):
            return status, payload, response_headers
        entries = json.loads(payload).get("messages") or []
        if "pageToken" in parse_qs(parsed.query):
            return 200, json.dumps({"messages": entries[1:]}).encode(), {}
        return (
            200,
            json.dumps({"messages": entries[:1], "nextPageToken": "pagina-2"}).encode(),
            {},
        )


# --- the whole conversation ----------------------------------------------------------


def test_a_whole_thread_is_stored_including_participants_we_do_not_know(
    db_session: Session,
) -> None:
    """Spec 4.3. A conversation read halfway is worse than one not read: if the client
    writes, a colleague replies in copy and the client confirms, keeping only the first
    and third produces a thread that lies."""
    account = connected_account(db_session)
    _customer(db_session, "info@acme.it")

    fake = FakeGmail()
    fake.messages["m1"] = _message(1, frm="info@acme.it", to=MAILBOX, thread="t1")
    fake.messages["m2"] = _message(2, frm="collega@acme.it", to=MAILBOX, thread="t1", days_ago=0.9)
    fake.messages["m3"] = _message(3, frm=MAILBOX, to="info@acme.it", thread="t1", days_ago=0.8)
    sync_service(db_session, fake).sync(actor_for(account))
    db_session.commit()

    assert _stored_ids(db_session) == ["m1", "m2", "m3"]


def test_an_unknown_sender_met_inside_a_thread_does_not_join_the_roster(
    db_session: Session,
) -> None:
    """Spec 4.3, the cost stated plainly. If those addresses entered the roster,
    relevance would widen by itself on every cycle -- exactly the failure mode of spec
    4."""
    account = connected_account(db_session)
    _customer(db_session, "info@acme.it")
    fake = FakeGmail()
    fake.messages["m1"] = _message(1, frm="info@acme.it", to=MAILBOX, thread="t1")
    fake.messages["m2"] = _message(
        2, frm="estraneo@altrove.com", to=MAILBOX, thread="t1", days_ago=0.9
    )
    sync_service(db_session, fake).sync(actor_for(account))
    db_session.commit()

    assert "estraneo@altrove.com" not in AddressRoster(db_session).known_addresses()
    # Stored, though: the message is part of the conversation. It is relevance that must
    # not widen, not the record of what was said.
    assert "m2" in _stored_ids(db_session)


def test_direction_is_recorded_from_the_connected_mailbox(db_session: Session) -> None:
    account = connected_account(db_session, email_address=MAILBOX)
    _customer(db_session, "info@acme.it")
    fake = FakeGmail()
    fake.messages["m1"] = _message(1, frm="info@acme.it", to=MAILBOX, thread="t1")
    fake.messages["m2"] = _message(2, frm=MAILBOX, to="info@acme.it", thread="t1", days_ago=0.9)
    sync_service(db_session, fake).sync(actor_for(account))
    db_session.commit()

    rows = {
        row.gmail_message_id: row.direction
        for row in db_session.execute(select(GmailMessage)).scalars().all()
    }
    assert rows == {"m1": "inbound", "m2": "outbound"}


def test_with_store_bodies_off_only_the_snippet_is_kept(db_session: Session) -> None:
    """The declared degradation of spec 5.4, not a missing value: the CRM shows who
    wrote, when and about what, and the text stays in Gmail."""
    account = connected_account(db_session)
    account.gmail_store_bodies = False
    _customer(db_session, "info@acme.it")
    fake = FakeGmail()
    fake.messages["m1"] = _message(1, frm="info@acme.it", to=MAILBOX, thread="t1")
    sync_service(db_session, fake).sync(actor_for(account))
    db_session.commit()
    row = db_session.execute(select(GmailMessage)).scalars().one()
    assert row.body_text == ""
    assert row.snippet != ""
    assert row.subject == "Oggetto 1"


def test_a_stored_message_prints_neither_its_body_nor_its_subject(db_session: Session) -> None:
    """`GmailMessage` has no `__repr__` on purpose. A generated one would print somebody
    else's correspondence into every traceback, every debugger frame and every pytest
    failure dump that happens to hold a row."""
    account = connected_account(db_session)
    _customer(db_session, "info@acme.it")
    fake = FakeGmail()
    fake.messages["m1"] = _message(1, frm="info@acme.it", to=MAILBOX, thread="t1")
    sync_service(db_session, fake).sync(actor_for(account))
    row = db_session.execute(select(GmailMessage)).scalars().one()
    printed = repr(row)
    assert "corpo riservato" not in printed
    assert "Oggetto 1" not in printed
    assert "info@acme.it" not in printed


def test_the_read_schema_carries_the_message_and_no_account_column(db_session: Session) -> None:
    account = connected_account(db_session)
    _customer(db_session, "info@acme.it")
    fake = FakeGmail()
    fake.messages["m1"] = _message(1, frm="info@acme.it", to=MAILBOX, thread="t1")
    sync_service(db_session, fake).sync(actor_for(account))
    row = db_session.execute(select(GmailMessage)).scalars().one()

    read = GmailMessageRead.model_validate(row)
    assert read.gmail_message_id == "m1"
    assert read.direction == "inbound"
    assert read.body_text == "corpo riservato 1"
    # No `google_account_id`: which credential fetched a message is not information the
    # reader of a conversation needs, and it is a join key into the credential table.
    assert "google_account_id" not in read.model_dump()


# --- idempotency ---------------------------------------------------------------------


def test_running_the_sync_twice_produces_the_same_rows(db_session: Session) -> None:
    """Spec 13, criterion 2. The `(google_account_id, gmail_message_id)` unique
    constraint is what makes the watermark's deliberate 24-hour overlap free.

    The message is half the overlap old, not `_message`'s default day (REB-262). A day
    old is the overlap's own edge: the second cycle asks `after:` the first cycle's start
    minus 24 hours, in whole seconds, so a message built one day before *now* was listed
    again only when no second ticked over between building it and that start. On a busy
    CI worker one did, now and then, and the second cycle found nothing to skip
    (`messages_skipped` 0, run 35865494773). Half the overlap puts it inside the window
    by twelve hours whatever the clock does.
    """
    account = connected_account(db_session)
    _customer(db_session, "info@acme.it")
    half_the_overlap_days = gmail_settings().gmail_watermark_overlap_hours / 2 / 24

    fake = FakeGmail()
    fake.messages["m1"] = _message(
        1, frm="info@acme.it", to=MAILBOX, thread="t1", days_ago=half_the_overlap_days
    )
    service = sync_service(db_session, fake)
    service.sync(actor_for(account))
    db_session.commit()
    first = _rows_of(db_session, account.id)
    assert first == 1

    second = service.sync(actor_for(account))
    db_session.commit()
    assert _rows_of(db_session, account.id) == first
    assert second.messages_skipped == 1
    assert second.messages_stored == 0


def _rows_of(session: Session, account_id: UUID) -> int:
    """This test's mailbox only, not the whole table: a row another test committed on this
    worker's database is not a row this sync wrote."""
    return session.execute(
        select(func.count())
        .select_from(GmailMessage)
        .where(GmailMessage.google_account_id == account_id)
    ).scalar_one()


def test_the_watermark_moves_forward_but_overlaps_by_a_day(db_session: Session) -> None:
    account = connected_account(db_session)
    _customer(db_session, "info@acme.it")
    fake = FakeGmail()
    fake.messages["m1"] = _message(1, frm="info@acme.it", to=MAILBOX, thread="t1")
    before = datetime.now(UTC)
    sync_service(db_session, fake).sync(actor_for(account))
    db_session.commit()
    db_session.refresh(account)

    assert account.last_sync_at is not None and account.last_sync_at >= before
    assert account.sync_watermark is not None
    # Deliberate overlap: it costs almost nothing and it absorbs a message that arrived
    # across the boundary of two runs. Bounded on both sides, so a watermark that never
    # moved at all would fail here too.
    overlap = gmail_settings().gmail_watermark_overlap_hours
    assert account.sync_watermark <= before - timedelta(hours=overlap - 1)
    assert account.sync_watermark >= before - timedelta(hours=overlap + 1)


def test_the_second_cycle_asks_only_for_what_happened_since_the_watermark(
    db_session: Session,
) -> None:
    """The point of storing a watermark at all. Without it every cycle would re-read the
    same thirty days, which is the cost spec 4 refuses to pay every hour."""
    account = connected_account(db_session)
    _customer(db_session, "info@acme.it")
    fake = FakeGmail()
    fake.messages["m1"] = _message(1, frm="info@acme.it", to=MAILBOX, thread="t1")
    service = sync_service(db_session, fake)
    service.sync(actor_for(account))
    service.sync(actor_for(account))

    afters = [
        int(re.search(r"after:(\d+)", request.q or "").group(1)) for request in _listings(fake)
    ]
    assert len(afters) == 2
    assert afters[1] > afters[0], "the second cycle re-read the whole first window"


def test_a_refused_duplicate_does_not_discard_the_rest_of_the_cycle(db_session: Session) -> None:
    """The reason `add_message_if_absent` owns a SAVEPOINT instead of catching the
    `IntegrityError` and calling `Session.rollback()`.

    A cycle commits once, at the end. A rollback on the session would therefore throw
    away every message stored earlier in the same cycle -- one duplicate in a thread of
    thirty would lose the twenty-nine before it -- and the loss would be invisible,
    because a duplicate is the *expected* outcome of the watermark's overlap.
    """
    account = connected_account(db_session)
    repo = GmailRepository(db_session)
    assert repo.add_message_if_absent(_row(account.id, "m0")) is True
    assert repo.add_message_if_absent(_row(account.id, "m1")) is True
    assert repo.add_message_if_absent(_row(account.id, "m1")) is False
    assert _stored_ids(db_session) == ["m0", "m1"]


def test_the_same_gmail_id_under_another_account_is_not_a_duplicate(db_session: Session) -> None:
    """The constraint is on the pair. Gmail's message ids are unique per mailbox, so
    scoping it to the id alone would let one mailbox's history hide another's."""
    first = connected_account(db_session)
    second = connected_account(db_session, email_address="altra@example.it")
    repo = GmailRepository(db_session)
    assert repo.add_message_if_absent(_row(first.id, "m1")) is True
    assert repo.add_message_if_absent(_row(second.id, "m1")) is True
    assert db_session.execute(select(func.count()).select_from(GmailMessage)).scalar_one() == 2


def test_a_message_is_looked_up_within_its_own_account_and_no_other(db_session: Session) -> None:
    """Gmail's ids are unique per mailbox, not globally, so the account is half of the
    question. B1-9 and B1-11 both reach for a single stored message by id; a lookup that
    forgot the account would hand one mailbox a row belonging to another."""
    first = connected_account(db_session)
    second = connected_account(db_session, email_address="altra@example.it")
    repo = GmailRepository(db_session)
    repo.add_message_if_absent(_row(first.id, "m1"))

    found = repo.message_by_gmail_id(first.id, "m1")
    assert found is not None
    assert found.gmail_message_id == "m1"
    assert repo.message_by_gmail_id(second.id, "m1") is None
    assert repo.message_by_gmail_id(first.id, "sconosciuto") is None


def test_asking_about_no_ids_at_all_asks_the_database_nothing(db_session: Session) -> None:
    """An empty thread would otherwise produce `IN ()`, which Postgres rejects."""
    account = connected_account(db_session)
    assert GmailRepository(db_session).message_ids_present(account.id, []) == set()


def _row(account_id: UUID, gmail_id: str, thread: str = "t1") -> GmailMessage:
    return GmailMessage(
        google_account_id=account_id,
        gmail_message_id=gmail_id,
        gmail_thread_id=thread,
        direction="inbound",
        from_address="info@acme.it",
        to_addresses=[MAILBOX],
        cc_addresses=[],
        subject="Oggetto",
        snippet="corpo",
        internal_date=datetime.now(UTC),
        body_text="corpo",
        attachments=[],
    )


# --- idempotency under real concurrency ----------------------------------------------
#
# "Idempotent" is a claim about two cycles overlapping, not about calling the function
# twice in a row: the second sequential call sees the first one's committed rows and
# skips them before it ever reaches the constraint. Only two connections at once
# exercise the guarantee, and the `db_session` fixture cannot host that -- it wraps
# every test in a transaction it rolls back, so the two connections could not see each
# other's rows. These two therefore write for real and clean up in a `finally`.


@pytest.fixture
def committed_account(db_engine: Engine) -> Iterator[tuple[UUID, UUID, str]]:
    """A user, a connected account and a customer, actually committed. Yields the user
    id, the account id and the customer's address."""
    factory = session_factory(db_engine)
    address = f"race-{uuid4().hex[:8]}@acme.it"
    with factory() as session:
        account = connected_account(session)
        customer = _customer(session, address, f"Race {uuid4().hex[:6]}")
        session.commit()
        ids = (account.user_id, account.id, customer.id)
    try:
        yield ids[0], ids[1], address
    finally:
        with factory() as session:
            # `google_accounts.user_id` and `gmail_messages.google_account_id` are both
            # ON DELETE CASCADE, so deleting the user takes the whole tree with it
            # whatever state the test left it in.
            session.execute(delete(User).where(User.id == ids[0]))
            session.execute(delete(Customer).where(Customer.email == address))
            # `activities` deliberately has no foreign key to anything -- `entity_id`
            # addresses whichever table `entity_type` names -- so nothing cascades, and
            # the cycles these two tests really commit would leave their timeline
            # entries behind in a database every other test shares. Alphabetical
            # collection order happened to hide it; that is not a property any test
            # should depend on.
            session.execute(delete(Activity).where(Activity.entity_id.in_(ids[1:])))
            session.commit()


def test_two_simultaneous_inserts_of_one_message_produce_exactly_one_row(
    db_engine: Engine, committed_account: tuple[UUID, UUID, str]
) -> None:
    """The constraint doing the arbitration, with two real connections and a barrier.

    Both callers pass the `message_ids_present` check -- neither can see the other's
    uncommitted row -- so the pre-check cannot be what makes this come out right. What
    makes it come out right is `uq_gmail_messages_account_message`: the loser's INSERT
    blocks on the index until the winner commits, then fails, and
    `add_message_if_absent` answers `False` from inside its SAVEPOINT instead of
    poisoning the session. Drop the constraint and this reports two rows; drop the
    SAVEPOINT and the loser raises instead of answering.
    """
    _, account_id, _ = committed_account
    factory = session_factory(db_engine)
    barrier = Barrier(2)

    def claim() -> bool:
        with factory() as session:
            row = _row(account_id, "contesa")
            barrier.wait(timeout=10)
            stored = GmailRepository(session).add_message_if_absent(row)
            session.commit()
            return stored

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = sorted(
            future.result(timeout=30) for future in [pool.submit(claim) for _ in range(2)]
        )

    assert outcomes == [False, True], f"the message was stored {sum(outcomes)} times"
    with factory() as session:
        stored = session.execute(
            select(func.count())
            .select_from(GmailMessage)
            .where(GmailMessage.google_account_id == account_id)
        ).scalar_one()
    assert stored == 1


def test_two_overlapping_syncs_store_each_message_once(
    db_engine: Engine, committed_account: tuple[UUID, UUID, str]
) -> None:
    """The same guarantee at the level a user can trigger it: two syncs started at once
    -- a button pressed twice, a cron run overlapping a manual one -- leave one row per
    message and neither of them fails. Task B1-10 turns the second one into an answer
    rather than a race; until then it must at least not corrupt anything."""
    user_id, account_id, address = committed_account
    factory = session_factory(db_engine)
    barrier = Barrier(2)
    actor = Actor(id=user_id, type="user", role="admin")

    def run() -> int:
        fake = FakeGmail()
        fake.messages["m1"] = _message(1, frm=address, to=MAILBOX, thread="t1")
        fake.messages["m2"] = _message(2, frm=MAILBOX, to=address, thread="t1", days_ago=0.9)
        with factory() as session:
            service = sync_service(session, fake)
            barrier.wait(timeout=10)
            return service.sync(actor).messages_stored

    with ThreadPoolExecutor(max_workers=2) as pool:
        stored = [future.result(timeout=60) for future in [pool.submit(run) for _ in range(2)]]

    assert sum(stored) == 2, f"two messages were stored {sum(stored)} times between the two cycles"
    with factory() as session:
        ids = (
            session.execute(
                select(GmailMessage.gmail_message_id).where(
                    GmailMessage.google_account_id == account_id
                )
            )
            .scalars()
            .all()
        )
    assert sorted(ids) == ["m1", "m2"]


# --- housekeeping and refusals -------------------------------------------------------


def test_the_cycle_prunes_the_expired_oauth_states(db_session: Session) -> None:
    """`refresh_tokens` has this same problem and, per residuo R8, no pruning at all.
    The sync is the only recurring job this system has, so it is where the housekeeping
    goes."""
    account = connected_account(db_session)
    db_session.add(
        GoogleOAuthState(
            jti=f"scaduto-{uuid4().hex[:8]}",
            code_verifier="v" * 43,
            user_id=account.user_id,
            expires_at=datetime.now(UTC) - timedelta(minutes=10),
        )
    )
    db_session.flush()

    report = sync_service(db_session, FakeGmail()).sync(actor_for(account))
    assert report.states_pruned == 1
    assert db_session.execute(select(func.count()).select_from(GoogleOAuthState)).scalar_one() == 0


def test_a_readonly_actor_cannot_sync(db_session: Session) -> None:
    account = connected_account(db_session)
    fake = FakeGmail()
    with pytest.raises(PermissionDenied):
        sync_service(db_session, fake).sync(Actor(id=account.user_id, type="user", role="readonly"))
    assert fake.requests == []


def test_syncing_without_a_connected_mailbox_is_a_refusal_not_a_silent_success(
    db_session: Session,
) -> None:
    user = User(
        email=f"solo-{uuid4().hex[:8]}@example.it",
        nome="Solo",
        password_hash="x",
        ruolo="admin",
        attivo=True,
    )
    db_session.add(user)
    db_session.flush()
    with pytest.raises(Conflict):
        sync_service(db_session, FakeGmail()).sync(Actor(id=user.id, type="user", role="admin"))


def test_an_unconfigured_installation_refuses_to_sync(db_session: Session) -> None:
    account = connected_account(db_session)
    settings = gmail_settings(google_client_id="")
    with pytest.raises(Conflict):
        sync_service(db_session, FakeGmail(), settings=settings).sync(actor_for(account))


def test_no_failure_of_the_cycle_carries_a_token(db_session: Session) -> None:
    """Every request in this cycle carries a bearer token, and the refresh token itself
    goes into the token endpoint's body. None of that may reach the sentence the user
    reads, the exception's arguments or its details -- the API renders `details` into
    the problem document and the MCP adapter into guidance, so anything in there is
    published."""
    account = connected_account(db_session)
    _customer(db_session, "info@acme.it")
    fake = FakeGmail(fail_with=[(500, b"{}", {})] * 8)

    with pytest.raises(GmailUnavailable) as failure:
        sync_service(db_session, fake).sync(actor_for(account))

    printed = f"{failure.value} {failure.value.args} {failure.value.details}"
    assert REFRESH_TOKEN not in printed
    assert fake.access_token not in printed
    assert TOKEN_KEY.decode() not in printed


def test_the_fake_refuses_the_query_real_gmail_answers_nothing_to() -> None:
    """`after:0` is the divergence that let a broken full backfill pass: the fake treated
    it as "since the epoch" while Gmail returns an empty page. A fake that is more
    lenient than the real thing turns a passing test into a false statement, so it now
    refuses the query the way spec 4.1's empty-`q` refusal already does."""
    from fakes.gmail_query import matches

    with pytest.raises(AssertionError):
        matches(_message(1, frm="ada@acme.it", to=MAILBOX, thread="t1"), "from:ada@acme.it after:0")
