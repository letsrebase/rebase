"""One mailbox, one cycle at a time -- and the second caller gets an answer.

A cron every fifteen minutes and a human pressing the button is not a hypothetical, so
"already running" has to be a *result* rather than an exception, a wait, or a second
pass over the same thousand messages.

Two of the tests here hold the lock from a second connection, which is a faithful
reproduction rather than an approximation: Postgres advisory locks are per session, so
a second `Session` on its own connection is exactly what a concurrent cron run is. The
two threaded tests go further and overlap for real, in the shape this slice has settled
on for concurrency claims (`test_gmail_models.py`'s single-use race,
`test_gmail_sync.py`'s two overlapping cycles): the `db_session` fixture wraps each
test in a transaction it rolls back, so two connections inside it could never see each
other's rows, and anything that has to race therefore commits for real and cleans up in
a `finally`.
"""

from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from threading import Barrier, Event
from time import monotonic, sleep
from typing import Any
from uuid import UUID, uuid4

import pytest
from fakes.contacts import points
from fakes.fake_gmail import FakeGmail, FakeMessage, RecordedRequest
from fakes.gmail_fixtures import MAILBOX, actor_for, connected_account, sync_service
from sqlalchemy import Connection, Engine, delete, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from pigrocrm.core.activities.models import Activity
from pigrocrm.core.actor import Actor
from pigrocrm.core.auth.models import User
from pigrocrm.core.contact_points.models import ContactPoint
from pigrocrm.core.db import session_factory
from pigrocrm.core.gmail.errors import CredentialRevoked
from pigrocrm.core.gmail.models import GmailMessage
from pigrocrm.core.gmail.repository import GmailRepository
from pigrocrm.core.gmail.schemas import SyncReport
from pigrocrm.core.organizations.models import Organization


def _ms(days_ago: float) -> int:
    """An `internalDate` inside the first cycle's 30-day window. Relative to now and
    never a literal date: a fixed timestamp would fall outside `after:`, the fake would
    match nothing, and the test would pass by finding an empty mailbox."""
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


# --- the second caller answers -------------------------------------------------------


def test_a_second_overlapping_sync_answers_instead_of_failing(
    db_session: Session, db_engine: Engine
) -> None:
    """Spec 13, criterion 4. A user who presses the button twice must see a response,
    not an error -- and the second press must not double the requests."""
    account = connected_account(db_session)
    db_session.add(Organization(legal_name="Acme", contact_points=points(email="info@acme.it")))
    db_session.flush()
    fake = FakeGmail()
    fake.messages["m1"] = _message(1, frm="info@acme.it", to=MAILBOX, thread="t1")

    with Session(db_engine) as holder:
        assert GmailRepository(holder).try_sync_lock(account.id) is True
        report = sync_service(db_session, fake).sync(actor_for(account))

        assert report.already_running is True
        assert report.messages_stored == 0
        # Not one request: the point of the lock is that the work is not done twice.
        # Not even the token refresh, which is the first thing a cycle does.
        assert fake.requests == []
        GmailRepository(holder).release_sync_lock(account.id)

    # And the same call, against the same mailbox, once the lock is free -- so the
    # assertions above are about the lock and not about a mailbox that had nothing in
    # it or a sync that was never going to work.
    second = sync_service(db_session, fake).sync(actor_for(account))
    assert second.already_running is False
    assert second.messages_stored == 1


def test_the_reported_start_time_is_the_last_sync_that_actually_ran(
    db_session: Session, db_engine: Engine
) -> None:
    """`running_since` is what turns "in corso" into something a person can act on: a
    run that started forty seconds ago is a reason to wait, one that started at dawn is
    a reason to look for a stuck job.

    It is the last *committed* cycle's start, and it can only be that: a run in flight
    has not published its own `last_sync_at` to any other transaction yet.
    """
    account = connected_account(db_session)
    started = datetime.now(UTC).replace(microsecond=0) - timedelta(minutes=20)
    account.last_sync_at = started
    db_session.flush()

    with Session(db_engine) as holder:
        GmailRepository(holder).try_sync_lock(account.id)
        report = sync_service(db_session, FakeGmail()).sync(actor_for(account))
        assert report.running_since == started
        GmailRepository(holder).release_sync_lock(account.id)


def test_a_mailbox_that_has_never_synced_says_so_rather_than_inventing_a_time(
    db_session: Session, db_engine: Engine
) -> None:
    """`running_since` is nullable for a real state: the very first cycle overlapping
    the button press has no previous run to name. `None` is honest; `started_at` would
    claim this call was the one already running."""
    account = connected_account(db_session)
    db_session.flush()
    assert account.last_sync_at is None

    with Session(db_engine) as holder:
        GmailRepository(holder).try_sync_lock(account.id)
        report = sync_service(db_session, FakeGmail()).sync(actor_for(account))
        assert report.already_running is True
        assert report.running_since is None
        GmailRepository(holder).release_sync_lock(account.id)


# --- the lock is handed back ---------------------------------------------------------


def test_a_completed_cycle_leaves_the_lock_free(db_session: Session, db_engine: Engine) -> None:
    """The happy path releases too. A lock kept after a successful run would make the
    *next* cycle answer "already running" forever, which is the same undiagnosable
    screen as a hung job."""
    account = connected_account(db_session)
    db_session.add(Organization(legal_name="Acme", contact_points=points(email="info@acme.it")))
    db_session.flush()
    fake = FakeGmail()
    fake.messages["m1"] = _message(1, frm="info@acme.it", to=MAILBOX, thread="t1")

    report = sync_service(db_session, fake).sync(actor_for(account))
    assert report.already_running is False

    with Session(db_engine) as other:
        assert GmailRepository(other).try_sync_lock(account.id) is True
        GmailRepository(other).release_sync_lock(account.id)


def test_the_lock_is_released_even_when_the_cycle_raises(
    db_session: Session, db_engine: Engine
) -> None:
    """A lock leaked by an exception makes every later sync answer 'already running'
    forever, which looks exactly like a hung job and is impossible to diagnose.

    The raise is asserted, not swallowed: a bare `except Exception` would let a typo
    that failed *before* the lock was ever taken produce a passing test. `CredentialRevoked`
    can only come out of the token refresh, which lives inside the cycle, so seeing it
    is proof the lock was held and then given back.
    """
    account = connected_account(db_session)
    db_session.add(Organization(legal_name="Acme", contact_points=points(email="info@acme.it")))
    db_session.flush()

    fake = FakeGmail(revoked=True)  # the refresh fails, so the cycle raises
    with pytest.raises(CredentialRevoked):
        sync_service(db_session, fake).sync(actor_for(account))
    # After a rollback as well as before one: `pg_advisory_unlock` is not transactional,
    # so a release that had been left to the transaction would show up here.
    db_session.rollback()

    with Session(db_engine) as other:
        assert GmailRepository(other).try_sync_lock(account.id) is True
        GmailRepository(other).release_sync_lock(account.id)


def test_the_lock_is_released_even_when_the_transaction_is_beyond_saving(
    db_session: Session, db_engine: Engine
) -> None:
    """The lock does not live on the caller's transaction, so a transaction Postgres has
    aborted (every statement refused until a rollback) cannot stop it being handed back:
    the unlock runs on the lock's own connection, which nothing else ever touches. A
    release that depended on the aborted transaction would raise from inside the
    `finally`, mask the original failure, and leave the account unable to sync again for
    the life of the process."""
    account = connected_account(db_session)
    db_session.flush()
    repo = GmailRepository(db_session)
    assert repo.try_sync_lock(account.id) is True

    with pytest.raises(DBAPIError):
        # Division by zero in Postgres, not in Python: the point is a statement the
        # server rejects, which is what leaves the transaction unusable.
        db_session.execute(text("SELECT 1 / 0"))

    repo.release_sync_lock(account.id)
    with Session(db_engine) as other:
        assert GmailRepository(other).try_sync_lock(account.id) is True
        GmailRepository(other).release_sync_lock(account.id)


def test_the_lock_is_released_after_the_session_gave_its_connection_back_to_the_pool(
    db_engine: Engine, committed_account: tuple[UUID, UUID, str]
) -> None:
    """A `Session` returns its connection to the pool at every commit and checks out the
    next one in line for its following statement, so with several idle connections the
    unlock used to run on a *different* backend, answer `false`, and leave the lock on
    the first until the process restarted. The lock now lives on a connection of its own,
    and the unlock goes to that one whatever the pool does in between.

    Built to bite: the pool is first filled with idle connections, which is the state in
    which the old code lost the lock."""
    _, account_id, _ = committed_account
    idle = [db_engine.connect() for _ in range(3)]
    for connection in idle:
        connection.close()

    with Session(db_engine) as syncing:
        repo = GmailRepository(syncing)
        assert repo.try_sync_lock(account_id) is True
        syncing.execute(text("SELECT 1"))
        syncing.commit()  # the session's connection goes back to the pool here
        repo.release_sync_lock(account_id)

    with Session(db_engine) as other:
        assert GmailRepository(other).try_sync_lock(account_id) is True
        GmailRepository(other).release_sync_lock(account_id)


def test_a_connection_that_cannot_unlock_is_invalidated_and_never_raises(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the unlock itself fails the connection is broken, and a broken connection that
    goes back to the pool is one that may still hold the lock. It is invalidated instead,
    which closes it for good (Postgres drops a session's advisory locks when the session
    ends), and the failure is not raised: this runs in a `finally`, over whatever failed
    first.

    The failure is injected at the one statement that matters, on a fake of the driver's
    answer, and the invalidation is observed rather than assumed."""
    account = connected_account(db_session)
    db_session.flush()
    repo = GmailRepository(db_session)
    assert repo.try_sync_lock(account.id) is True
    events: list[str] = []
    real_execute, real_invalidate = Connection.execute, Connection.invalidate

    def execute(self: Connection, statement: Any, *args: Any, **kwargs: Any) -> Any:
        if "pg_advisory_unlock" in str(statement):
            events.append("unlock failed")
            raise DBAPIError("SELECT pg_advisory_unlock", {}, Exception("server closed"))
        return real_execute(self, statement, *args, **kwargs)

    def invalidate(self: Connection, *args: Any, **kwargs: Any) -> None:
        events.append("invalidated")
        real_invalidate(self, *args, **kwargs)

    monkeypatch.setattr(Connection, "execute", execute)
    monkeypatch.setattr(Connection, "invalidate", invalidate)

    repo.release_sync_lock(account.id)  # must not raise

    assert events == ["unlock failed", "invalidated"]
    monkeypatch.undo()
    # Invalidated, not unlocked: the backend is gone and with it the lock. The server frees
    # the lock when it *notices* the closed connection, which is its own moment and not the
    # client's close(), so the test waits for it, bounded, instead of asking once.
    deadline = monotonic() + 10
    with Session(db_session.get_bind().engine) as other:
        while not GmailRepository(other).try_sync_lock(account.id):
            assert monotonic() < deadline, (
                "the server never freed the lock of the closed connection"
            )
            sleep(0.05)
        GmailRepository(other).release_sync_lock(account.id)


def test_two_different_accounts_do_not_block_each_other(
    db_session: Session, db_engine: Engine
) -> None:
    """The lock is per mailbox. A shared key would mean one user's fifteen-minute cron
    silently suppressing every other user's sync, which no screen would ever show.

    Sharper than it looks, and it has already caught the real bug: every primary key
    here is a **uuid7**, so two accounts created in the same test share their leading
    bytes. A key folded from the front of the id gave both mailboxes the same lock and
    this failed on the first run.
    """
    first = connected_account(db_session)
    second = connected_account(db_session, email_address="due@example.it")
    db_session.flush()
    with Session(db_engine) as holder:
        assert GmailRepository(holder).try_sync_lock(first.id) is True
        with Session(db_engine) as other:
            assert GmailRepository(other).try_sync_lock(second.id) is True
            GmailRepository(other).release_sync_lock(second.id)
        GmailRepository(holder).release_sync_lock(first.id)


# --- and now for real ----------------------------------------------------------------
#
# Everything above holds the lock from a second connection, which proves the *answer*
# but not the arbitration: the holder wins by construction. These two race.


@pytest.fixture
def committed_account(db_engine: Engine) -> Iterator[tuple[UUID, UUID, str]]:
    """A user, a connected account and a customer, actually committed. Yields the user
    id, the account id and the customer's address.

    A twin of `test_gmail_sync.py`'s fixture of the same name, and deliberately its own
    copy: this repository's three test roots have no importable `conftest`, and a race
    fixture is not worth making a shared module for at the cost of one more ambiguous
    top-level name.
    """
    factory = session_factory(db_engine)
    address = f"lock-{uuid4().hex[:8]}@acme.it"
    with factory() as session:
        account = connected_account(session)
        customer = Organization(
            legal_name=f"Lock {uuid4().hex[:6]}", contact_points=points(email=address)
        )
        session.add(customer)
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
            session.execute(
                delete(Organization).where(
                    Organization.id.in_(
                        select(ContactPoint.customer_id).where(ContactPoint.normalized == address)
                    )
                )
            )
            # `activities` deliberately has no foreign key to anything -- `entity_id`
            # addresses whichever table `entity_type` names -- so nothing cascades and
            # the cycles these tests really committed would leave their timeline entries
            # in a database every other test shares. That is not hypothetical: it makes
            # `test_gmail_links.py`'s "recorded once per cycle" assertions depend on
            # pytest's collection order, which is not a property any test should have.
            session.execute(delete(Activity).where(Activity.entity_id.in_(ids[1:])))
            session.commit()


def test_two_threads_racing_for_the_lock_produce_exactly_one_holder(
    db_engine: Engine, committed_account: tuple[UUID, UUID, str]
) -> None:
    """Two real connections asking at once, with a barrier so neither releases before
    the other has asked. Exactly one `True`.

    Drop the mutual exclusion -- return `True` unconditionally, or take the lock in a
    transaction that has already ended -- and this reports `[True, True]`.
    """
    _, account_id, _ = committed_account
    factory = session_factory(db_engine)
    both_asked = Barrier(2)

    def claim() -> bool:
        with factory() as session:
            repo = GmailRepository(session)
            got = repo.try_sync_lock(account_id)
            # The barrier sits *between* the attempt and the release, so the loser
            # cannot have lost merely because the winner had already finished.
            both_asked.wait(timeout=30)
            if got:
                repo.release_sync_lock(account_id)
            return got

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = sorted(
            future.result(timeout=60) for future in [pool.submit(claim) for _ in range(2)]
        )
    assert outcomes == [False, True], f"the lock was held {sum(outcomes)} times at once"


def _noop() -> None:
    return None


@dataclass
class _GatedGmail(FakeGmail):
    """A `FakeGmail` that calls `gate` before every request it serves.

    That hook is what makes the overlap below deterministic rather than a sleep: the
    winner of the lock stops inside its own cycle, still holding the lock, until the
    other cycle has answered.
    """

    gate: Callable[[], None] = field(default=_noop)

    def __call__(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        body: bytes | None,
        *,
        max_bytes: int | None = None,
    ) -> tuple[int, bytes, dict[str, str]]:
        self.gate()
        return super().__call__(method, url, headers, body, max_bytes=max_bytes)


def test_two_overlapping_syncs_leave_exactly_one_doing_the_work(
    db_engine: Engine, committed_account: tuple[UUID, UUID, str]
) -> None:
    """The claim in the task's title, at the level a user can trigger it: a cron run and
    a button press, genuinely at the same time.

    Deterministic, and not by luck. The winner blocks inside the cycle at its first
    request -- the token refresh -- while still holding the lock, so the loser cannot
    possibly find the lock free; the loser answers, sets the event, and the winner
    finishes. Whichever thread wins, the shape is the same.

    B1-8 already proved this cannot corrupt anything. What is new is that the second
    cycle now costs nothing at all: no token refresh, no listing, no thread fetch.
    """
    user_id, account_id, address = committed_account
    factory = session_factory(db_engine)
    both_started = Barrier(2)
    someone_answered = Event()
    actor = Actor(id=user_id, type="user", role="admin")

    def run() -> tuple[SyncReport, list[RecordedRequest]]:
        held = False

        def gate() -> None:
            nonlocal held
            if held:
                return
            held = True
            assert someone_answered.wait(timeout=60), "the other cycle never answered"

        fake = _GatedGmail(gate=gate)
        fake.messages["m1"] = _message(1, frm=address, to=MAILBOX, thread="t1")
        fake.messages["m2"] = _message(2, frm=MAILBOX, to=address, thread="t1", days_ago=0.9)
        with factory() as session:
            service = sync_service(session, fake)
            both_started.wait(timeout=30)
            try:
                return service.sync(actor), list(fake.requests)
            finally:
                someone_answered.set()

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = [future.result(timeout=120) for future in [pool.submit(run) for _ in range(2)]]

    refused = [(report, requests) for report, requests in outcomes if report.already_running]
    ran = [(report, requests) for report, requests in outcomes if not report.already_running]
    assert len(refused) == 1, "both cycles believed they were alone"
    assert len(ran) == 1

    assert refused[0][1] == [], "the refused cycle still asked Google something"
    assert refused[0][0].messages_stored == 0
    assert ran[0][0].messages_stored == 2
    assert ran[0][1] != []

    with factory() as session:
        stored = sorted(
            session.execute(
                select(GmailMessage.gmail_message_id).where(
                    GmailMessage.google_account_id == account_id
                )
            )
            .scalars()
            .all()
        )
    assert stored == ["m1", "m2"]
