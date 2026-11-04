"""Spec 4.4: what happens to the mail that arrived *before* the CRM knew the address.

Two mechanisms, deliberately different in kind:

* **Automatic, once, and bounded.** An address the roster gained since the last cycle is
  looked for over `gmail_backfill_days` instead of only since the watermark. It happens
  on the next cycle rather than the instant the person was saved, because `PersonService`
  is not made to learn what Gmail is -- `packages/core` services do not call each other
  for network side effects -- and the interface says so rather than leaving the user to
  discover it.
* **Explicit, human-initiated, unbounded in time only.** `backfill(..., full=True)` drops
  the horizon on one entity. Not a default: on a ten-year mailbox it is slow and nobody
  wants it by accident.

The third guard of spec 4.1 applies to both and is asserted here in the same shape
`test_gmail_sync.py` uses: every address in every recorded listing has to be one the CRM
owns. "No horizon" is about time, never about relevance.
"""

import re
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fakes.contacts import points
from fakes.fake_gmail import FakeGmail, FakeMessage, RecordedRequest
from fakes.gmail_fixtures import MAILBOX, actor_for, connected_account, sync_service
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from pigrocrm.core.activities.models import Activity
from pigrocrm.core.actor import Actor
from pigrocrm.core.customers.models import Customer
from pigrocrm.core.errors import Conflict, NotFound, PermissionDenied, ValidationFailed
from pigrocrm.core.gmail.models import GmailKnownAddress, GmailMessage
from pigrocrm.core.gmail.repository import GmailRepository
from pigrocrm.core.people.models import Person

_ADDRESS_CLAUSE = re.compile(r"\b(?:from|to|cc|bcc|rfc822msgid):([^\s)]+)")
_AFTER = re.compile(r"\bafter:(\d+)")

# 2017. Far outside every horizon this slice has, and a literal is right *here*
# precisely because the assertion about it is "never fetched, whatever today is".
ANCIENT_MS = 1_500_000_000_000


def _ms(days_ago: float) -> int:
    """An `internalDate` relative to now, never a literal date.

    The interesting value in this file is 45 days: inside `gmail_backfill_days` (90) and
    outside the first cycle's default window (30). A message there is stored only if the
    backfill horizon is genuinely being used, which is the whole claim. A fixed
    timestamp would drift out of both windows and the tests would then pass by finding
    an empty mailbox.
    """
    return int((datetime.now(UTC) - timedelta(days=days_ago)).timestamp() * 1000)


def _message(mid: str, *, frm: str, when_ms: int, to: str = MAILBOX) -> FakeMessage:
    return FakeMessage(
        id=mid,
        thread_id=f"t-{mid}",
        headers={
            "From": frm,
            "To": to,
            "Subject": f"Oggetto {mid}",
            "Message-ID": f"<{mid}@example.it>",
        },
        body_text=f"corpo riservato {mid}",
        internal_date_ms=when_ms,
    )


def _listings(fake: FakeGmail) -> list[RecordedRequest]:
    return [request for request in fake.requests if request.is_messages_list]


def _assert_every_listing_is_filtered(fake: FakeGmail, known: set[str]) -> None:
    """The third guard of spec 4.1, over the requests that were actually sent."""
    listings = _listings(fake)
    assert listings, "nothing was listed at all, so this guard proved nothing"
    for request in listings:
        asked = set(_ADDRESS_CLAUSE.findall(request.q or ""))
        assert asked, f"a q with no address clause at all: {request.q}"
        assert asked <= known, (
            f"the backfill asked Gmail about {sorted(asked - known)}, which nobody in "
            f"the CRM owns: {request.q}"
        )
    # Not even the connected mailbox itself, which is the widening that looks harmless.
    assert not any(MAILBOX in (request.q or "") for request in listings)


def _stored(session: Session) -> set[str]:
    return set(session.execute(select(GmailMessage.gmail_message_id)).scalars().all())


def _person_of_new_customer(session: Session, email: str) -> Person:
    customer = Customer(legal_name=f"Acme {uuid4().hex[:6]}", contact_points=points(email=None))
    session.add(customer)
    session.flush()
    person = Person(
        first_name="Ada",
        last_name="Byron",
        contact_points=points(email=email),
        customer_id=customer.id,
    )
    session.add(person)
    session.flush()
    return person


# --- the automatic backfill, on the next cycle ---------------------------------------


def test_a_newly_added_address_is_backfilled_over_ninety_days(db_session: Session) -> None:
    """Spec 4.4 row 2. The address is new to this mailbox, so the cycle looks back
    `gmail_backfill_days` rather than only to the watermark -- and not to the beginning
    of time.

    The 45-day message is the discriminating one: a cycle that used only its ordinary
    window (30 days on a mailbox that has never synced) would leave it behind.
    """
    account = connected_account(db_session)
    _person_of_new_customer(db_session, "ada@acme.it")

    fake = FakeGmail()
    fake.messages["recente"] = _message("recente", frm="ada@acme.it", when_ms=_ms(45))
    fake.messages["antico"] = _message("antico", frm="ada@acme.it", when_ms=ANCIENT_MS)
    report = sync_service(db_session, fake).sync(actor_for(account))

    assert report.messages_stored == 1
    assert _stored(db_session) == {"recente"}
    _assert_every_listing_is_filtered(fake, {"ada@acme.it"})

    # A backfill is a wider and slower cycle than the user asked for, so it is recorded:
    # "nothing appeared" and "we looked back three months and nothing was there" are two
    # different answers.
    kinds = db_session.execute(select(Activity.kind).where(Activity.kind.like("gmail.%"))).scalars()
    assert "gmail.backfill_run" in set(kinds)

    remembered = GmailRepository(db_session).seen_addresses(account.id)
    assert remembered == {"ada@acme.it"}


def test_the_second_cycle_asks_only_from_the_watermark(db_session: Session) -> None:
    """The backfill happens once. A cycle that re-derived "new" from the roster alone
    would re-read three months of mail every fifteen minutes, forever."""
    account = connected_account(db_session)
    db_session.add(Customer(legal_name="Acme", contact_points=points(email="info@acme.it")))
    db_session.flush()

    fake = FakeGmail()
    service = sync_service(db_session, fake)
    service.sync(actor_for(account))
    first_after = [int(_AFTER.search(r.q or "").group(1)) for r in _listings(fake)]  # type: ignore[union-attr]
    fake.requests.clear()

    service.sync(actor_for(account))
    second_after = [int(_AFTER.search(r.q or "").group(1)) for r in _listings(fake)]  # type: ignore[union-attr]

    assert len(first_after) == 1
    assert len(second_after) == 1
    # The real assertion: the window narrowed. The first cycle reached back three
    # months, the second only to the watermark, which is minutes ago less the overlap.
    assert second_after[0] > first_after[0]
    horizon = datetime.now(UTC) - timedelta(days=90)
    assert abs(first_after[0] - horizon.timestamp()) < 120

    entries = (
        db_session.execute(select(Activity).where(Activity.kind == "gmail.backfill_run"))
        .scalars()
        .all()
    )
    assert len(entries) == 1, "the second cycle backfilled again"


def test_an_address_added_later_is_backfilled_while_the_others_are_not(
    db_session: Session,
) -> None:
    """The two windows in one cycle, which is the case the split exists for: an
    established correspondent must not be re-read over three months just because a
    colleague was added to the CRM this morning."""
    account = connected_account(db_session)
    db_session.add(Customer(legal_name="Acme", contact_points=points(email="info@acme.it")))
    db_session.flush()

    fake = FakeGmail()
    service = sync_service(db_session, fake)
    service.sync(actor_for(account))
    fake.requests.clear()

    _person_of_new_customer(db_session, "ada@acme.it")
    fake.messages["ada45"] = _message("ada45", frm="ada@acme.it", when_ms=_ms(45))
    fake.messages["info45"] = _message("info45", frm="info@acme.it", when_ms=_ms(45))
    report = service.sync(actor_for(account))

    # Ada's is reachable; the established address's 45-day-old message is not, because
    # its window is the watermark and the watermark is minutes old.
    assert _stored(db_session) == {"ada45"}
    assert report.queries_issued == 2
    _assert_every_listing_is_filtered(fake, {"ada@acme.it", "info@acme.it"})

    by_address = {
        _ADDRESS_CLAUSE.findall(r.q or "")[0]: int(_AFTER.search(r.q or "").group(1))  # type: ignore[union-attr]
        for r in _listings(fake)
    }
    assert by_address["ada@acme.it"] < by_address["info@acme.it"]


def test_remembering_an_address_twice_keeps_the_rest_of_the_cycle(db_session: Session) -> None:
    """The register absorbs a duplicate without discarding the cycle around it.

    This is the shape that has already gone wrong twice in this slice: a
    `Session.rollback()` reached for to swallow an expected `IntegrityError`. The cycle
    commits once at the end, so a session-wide rollback here would throw away every
    message stored before it -- on the *expected* outcome of an address that was already
    known. A message is stored first precisely so that a rollback has something visible
    to destroy.
    """
    account = connected_account(db_session)
    repo = GmailRepository(db_session)
    row = GmailMessage(
        google_account_id=account.id,
        gmail_message_id="before",
        gmail_thread_id="t1",
        direction="inbound",
        from_address="ada@acme.it",
        to_addresses=[MAILBOX],
        cc_addresses=[],
        subject="Oggetto",
        snippet="body",
        internal_date=datetime.now(UTC),
        body_text="body",
        attachments=[],
    )
    assert repo.add_message_if_absent(row) is True

    assert repo.remember_addresses(account.id, ["ada@acme.it", "info@acme.it"]) == 2
    assert repo.remember_addresses(account.id, ["ada@acme.it", "nuovo@acme.it"]) == 1
    db_session.flush()

    assert repo.seen_addresses(account.id) == {"ada@acme.it", "info@acme.it", "nuovo@acme.it"}
    assert _stored(db_session) == {"before"}


def test_the_register_is_per_mailbox(db_session: Session) -> None:
    """Two users may both correspond with the same client, and each mailbox has to be
    backfilled for it once on its own account."""
    first = connected_account(db_session)
    second = connected_account(db_session, email_address="due@example.it")
    repo = GmailRepository(db_session)
    assert repo.remember_addresses(first.id, ["ada@acme.it"]) == 1
    assert repo.remember_addresses(second.id, ["ada@acme.it"]) == 1
    assert repo.seen_addresses(first.id) == {"ada@acme.it"}
    assert repo.seen_addresses(second.id) == {"ada@acme.it"}
    assert (
        db_session.execute(select(GmailKnownAddress.address)).scalars().all().count("ada@acme.it")
        == 2
    )


# --- the explicit action -------------------------------------------------------------


def test_full_history_has_no_horizon_and_keeps_the_address_filter(db_session: Session) -> None:
    """Spec 4.4 row 3. Explicit and human-initiated, because on a ten-year mailbox it is
    slow and nobody wants it by accident."""
    account = connected_account(db_session)
    person = _person_of_new_customer(db_session, "ada@acme.it")

    fake = FakeGmail()
    fake.messages["antico"] = _message("antico", frm="ada@acme.it", when_ms=ANCIENT_MS)
    report = sync_service(db_session, fake).backfill(
        "person", person.id, full=True, actor=actor_for(account)
    )

    assert report.messages_stored == 1
    assert _stored(db_session) == {"antico"}
    listings = _listings(fake)
    assert len(listings) == 1
    # No `after:` clause at all -- not `after:0`. Gmail answers an empty page to a
    # literal `after:0` (found in production, see `test_gmail_query.py`), so "no
    # horizon" has to be spelled by leaving the clause out.
    assert "after:" not in (listings[0].q or "")
    assert "from:ada@acme.it" in (listings[0].q or "")
    # "No horizon" is about time, never about relevance.
    _assert_every_listing_is_filtered(fake, {"ada@acme.it"})


def test_a_bounded_backfill_reaches_the_horizon_and_no_further(db_session: Session) -> None:
    """`full=False` is the same action with the automatic cycle's own horizon: a user
    who asks for the recent history of one client should not have to fetch ten years to
    get it."""
    account = connected_account(db_session)
    person = _person_of_new_customer(db_session, "ada@acme.it")

    fake = FakeGmail()
    fake.messages["recente"] = _message("recente", frm="ada@acme.it", when_ms=_ms(45))
    fake.messages["antico"] = _message("antico", frm="ada@acme.it", when_ms=ANCIENT_MS)
    report = sync_service(db_session, fake).backfill(
        "person", person.id, full=False, actor=actor_for(account)
    )

    assert report.messages_stored == 1
    assert _stored(db_session) == {"recente"}


def test_a_customer_backfill_covers_the_people_who_work_there(db_session: Session) -> None:
    """A client is an organisation. Backfilling "Acme" and getting only the address on
    the company record would silently miss every message from the person one actually
    writes to."""
    account = connected_account(db_session)
    customer = Customer(legal_name="Acme", contact_points=points(email="info@acme.it"))
    db_session.add(customer)
    db_session.flush()
    db_session.add(
        Person(
            first_name="Ada",
            last_name="Byron",
            contact_points=points(email="ada@acme.it"),
            customer_id=customer.id,
        )
    )
    db_session.add(
        Person(
            first_name="Grace",
            last_name="Hopper",
            contact_points=points(email="grace@acme.it"),
            customer_id=customer.id,
            deleted_at=datetime.now(UTC),
        )
    )
    db_session.flush()

    fake = FakeGmail()
    fake.messages["daAda"] = _message("daAda", frm="ada@acme.it", when_ms=_ms(45))
    fake.messages["daInfo"] = _message("daInfo", frm="info@acme.it", when_ms=_ms(45))
    report = sync_service(db_session, fake).backfill(
        "customer", customer.id, full=False, actor=actor_for(account)
    )

    assert report.messages_stored == 2
    assert _stored(db_session) == {"daAda", "daInfo"}
    # The archived colleague is not asked about: an archived person is not a reason to
    # go and read three months of somebody's mail.
    _assert_every_listing_is_filtered(fake, {"ada@acme.it", "info@acme.it"})


def test_a_backfill_does_not_move_the_watermark(db_session: Session) -> None:
    """A backfill reads *backwards*. If it advanced the watermark, every message that
    arrived while it was running would be skipped by the next ordinary cycle and never
    seen again -- a silent hole, in the one direction nobody would think to look."""
    account = connected_account(db_session)
    person = _person_of_new_customer(db_session, "ada@acme.it")
    sync_service(db_session, FakeGmail()).sync(actor_for(account))
    watermark = account.sync_watermark
    last_sync = account.last_sync_at
    assert watermark is not None

    fake = FakeGmail()
    fake.messages["antico"] = _message("antico", frm="ada@acme.it", when_ms=ANCIENT_MS)
    sync_service(db_session, fake).backfill(
        "person", person.id, full=True, actor=actor_for(account)
    )

    db_session.refresh(account)
    assert account.sync_watermark == watermark
    assert account.last_sync_at == last_sync


def test_a_backfill_that_meets_a_running_cycle_answers(
    db_session: Session, db_engine: Engine
) -> None:
    """The same answer B1-10 gives the sync button, for the same reason: a backfill
    against a mailbox already being read is not an error, and it must not double the
    requests. A second connection holds the lock, which is what a cron run is."""
    account = connected_account(db_session)
    person = _person_of_new_customer(db_session, "ada@acme.it")

    fake = FakeGmail()
    fake.messages["antico"] = _message("antico", frm="ada@acme.it", when_ms=ANCIENT_MS)

    with Session(db_engine) as holder:
        assert GmailRepository(holder).try_sync_lock(account.id) is True
        report = sync_service(db_session, fake).backfill(
            "person", person.id, full=True, actor=actor_for(account)
        )
        assert report.already_running is True
        assert report.messages_stored == 0
        assert fake.requests == []
        GmailRepository(holder).release_sync_lock(account.id)


# --- refusals ------------------------------------------------------------------------


def test_backfill_on_an_entity_with_no_address_refuses_clearly(db_session: Session) -> None:
    """Silence would be the worst answer: the user pressed a button, waited, and got a
    report of zero because there was never an address to look for."""
    account = connected_account(db_session)
    customer = Customer(legal_name="Acme", contact_points=points(email=None))
    db_session.add(customer)
    db_session.flush()
    with pytest.raises(Conflict, match="nessun indirizzo"):
        sync_service(db_session, FakeGmail()).backfill(
            "customer", customer.id, full=False, actor=actor_for(account)
        )


def test_backfill_on_an_unknown_entity_is_a_not_found(db_session: Session) -> None:
    account = connected_account(db_session)
    db_session.flush()
    with pytest.raises(NotFound):
        sync_service(db_session, FakeGmail()).backfill(
            "customer", uuid4(), full=False, actor=actor_for(account)
        )


def test_backfill_on_an_archived_entity_is_a_not_found(db_session: Session) -> None:
    """An archived person is not a reason to go and read a mailbox. A soft delete that
    only hid the row from lists would leave this door open."""
    account = connected_account(db_session)
    person = _person_of_new_customer(db_session, "ada@acme.it")
    person.deleted_at = datetime.now(UTC)
    db_session.flush()
    with pytest.raises(NotFound):
        sync_service(db_session, FakeGmail()).backfill(
            "person", person.id, full=True, actor=actor_for(account)
        )


def test_backfill_refuses_an_entity_type_it_does_not_handle(db_session: Session) -> None:
    """A deal has no address of its own -- it borrows its customer's -- so asking for one
    has to say which types exist rather than answering zero."""
    account = connected_account(db_session)
    db_session.flush()
    with pytest.raises(ValidationFailed, match="entity_type"):
        sync_service(db_session, FakeGmail()).backfill(
            "deal", uuid4(), full=True, actor=actor_for(account)
        )


def test_a_readonly_actor_cannot_backfill(db_session: Session) -> None:
    """Reading somebody's mailbox on their Google quota is a write, whatever it is
    called."""
    account = connected_account(db_session)
    person = _person_of_new_customer(db_session, "ada@acme.it")
    reader = Actor(id=account.user_id, type="user", role="readonly")
    with pytest.raises(PermissionDenied):
        sync_service(db_session, FakeGmail()).backfill("person", person.id, full=True, actor=reader)
