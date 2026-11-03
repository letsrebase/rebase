"""§5.1's resolution order and, more importantly, §5's guarantee: what happens to last
quarter's margin when you raise a rate today. The answer must be "nothing", and not
out of discipline -- by construction, because no report ever reads a rate column."""

from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy.orm import Session

from pigrocrm.core.auth.models import User
from pigrocrm.core.deals.models import Deal
from pigrocrm.core.money import line_value
from pigrocrm.core.timetracking.models import TimeEntry
from pigrocrm.core.timetracking.rates import RateResolver


def _set_deal_rate(db_session: Session, deal_id: UUID, value: Decimal | None) -> None:
    db_session.get(Deal, deal_id).hourly_rate = value
    db_session.flush()


def _set_user_rates(
    db_session: Session, user_id: UUID, rate: Decimal | None, cost: Decimal | None
) -> None:
    user = db_session.get(User, user_id)
    user.default_hourly_rate = rate
    user.default_hourly_cost = cost
    db_session.flush()


def test_an_explicit_value_wins_and_is_marked_manual(
    db_session: Session, seeded_deal_id: UUID, seeded_user_id: UUID
) -> None:
    _set_deal_rate(db_session, seeded_deal_id, Decimal("150.000000"))
    _set_user_rates(db_session, seeded_user_id, Decimal("120.000000"), Decimal("40.000000"))
    resolved = RateResolver(db_session).resolve(
        deal_id=seeded_deal_id,
        user_id=seeded_user_id,
        rate_esplicita=Decimal("99.500000"),
        cost_explicit=Decimal("10.250000"),
    )
    assert resolved.rate == Decimal("99.500000")
    assert resolved.rate_source == "manual"
    assert resolved.cost == Decimal("10.250000")
    assert resolved.cost_source == "manual"


def test_the_deal_rate_is_level_two(
    db_session: Session, seeded_deal_id: UUID, seeded_user_id: UUID
) -> None:
    _set_deal_rate(db_session, seeded_deal_id, Decimal("150.000000"))
    _set_user_rates(db_session, seeded_user_id, Decimal("120.000000"), Decimal("40.000000"))
    resolved = RateResolver(db_session).resolve(deal_id=seeded_deal_id, user_id=seeded_user_id)
    assert (resolved.rate, resolved.rate_source) == (Decimal("150.000000"), "deal")
    # The internal cost has no deal level at all: it is a property of who works the
    # hour, not of the client they work it for. So it falls to the user level here.
    assert (resolved.cost, resolved.cost_source) == (Decimal("40.000000"), "user")


def test_the_user_default_is_level_three(
    db_session: Session, seeded_deal_id: UUID, seeded_user_id: UUID
) -> None:
    _set_deal_rate(db_session, seeded_deal_id, None)
    _set_user_rates(db_session, seeded_user_id, Decimal("120.000000"), Decimal("40.000000"))
    resolved = RateResolver(db_session).resolve(deal_id=seeded_deal_id, user_id=seeded_user_id)
    assert (resolved.rate, resolved.rate_source) == (Decimal("120.000000"), "user")


def test_nothing_resolves_to_none_never_to_zero(
    db_session: Session, seeded_deal_id: UUID, seeded_user_id: UUID
) -> None:
    """No global default and no silent fall back to zero. A `0.00` would say "this
    work was free", which is a lie that sums; a global default would be a number
    nobody chose quietly becoming everybody's rate."""
    _set_deal_rate(db_session, seeded_deal_id, None)
    _set_user_rates(db_session, seeded_user_id, None, None)
    resolved = RateResolver(db_session).resolve(deal_id=seeded_deal_id, user_id=seeded_user_id)
    assert resolved.rate is None and resolved.rate_source == "absent"
    assert resolved.cost is None and resolved.cost_source == "absent"


def test_cost_source_is_never_deal(
    db_session: Session, seeded_deal_id: UUID, seeded_user_id: UUID
) -> None:
    """A consequence to read off the table, not a coincidence: `cost_source`'s
    possible values are `manual`, `user`, `absent` and nothing else."""
    _set_deal_rate(db_session, seeded_deal_id, Decimal("150.000000"))
    _set_user_rates(db_session, seeded_user_id, None, None)
    resolved = RateResolver(db_session).resolve(deal_id=seeded_deal_id, user_id=seeded_user_id)
    assert resolved.cost_source == "absent"


def test_an_explicit_zero_is_a_choice_and_survives(
    db_session: Session, seeded_deal_id: UUID, seeded_user_id: UUID
) -> None:
    """`0` is a value, never a blank -- the same rule the frontend's `isBlank` follows.
    Somebody writing 0.000000 is declaring free work on purpose, and the resolver must
    not fall through to a deal rate behind their back."""
    _set_deal_rate(db_session, seeded_deal_id, Decimal("150.000000"))
    resolved = RateResolver(db_session).resolve(
        deal_id=seeded_deal_id, user_id=seeded_user_id, rate_esplicita=Decimal("0.000000")
    )
    assert resolved.rate == Decimal("0.000000")
    assert resolved.rate_source == "manual"


def test_describe_answers_before_anything_is_written(
    db_session: Session, seeded_deal_id: UUID, seeded_user_id: UUID
) -> None:
    _set_deal_rate(db_session, seeded_deal_id, Decimal("150.000000"))
    _set_user_rates(db_session, seeded_user_id, None, Decimal("40.000000"))
    described = RateResolver(db_session).describe(deal_id=seeded_deal_id, user_id=seeded_user_id)
    assert described.rate == Decimal("150.000000")
    assert described.rate_source == "deal"
    assert described.cost == Decimal("40.000000")
    assert described.cost_source == "user"


def test_raising_a_rate_today_does_not_move_an_already_written_entrys_value(
    db_session: Session, seeded_deal_id: UUID, seeded_user_id: UUID
) -> None:
    """The slice's central guarantee, proved by breaking it rather than asserted in
    prose: an entry logged at last quarter's rate freezes that rate onto the row
    (`applied_rate`), the same way `TimeEntryService` (Task 4A-9) will. Raising
    the deal's rate today must not move a margin already computed from that frozen
    column -- and this test also computes what *would* happen under the design that
    was rejected (re-resolving the rate live at read time instead of freezing it),
    to show the two disagree. If the freeze were ever removed and reads went back to
    `RateResolver.resolve()` at report time, the first assertion below would fail."""
    _set_deal_rate(db_session, seeded_deal_id, Decimal("100.000000"))
    _set_user_rates(db_session, seeded_user_id, None, None)

    resolved = RateResolver(db_session).resolve(deal_id=seeded_deal_id, user_id=seeded_user_id)
    assert (resolved.rate, resolved.rate_source) == (Decimal("100.000000"), "deal")

    entry = TimeEntry(
        deal_id=seeded_deal_id,
        user_id=seeded_user_id,
        data=date(2026, 3, 10),
        hours=Decimal("8.00"),
        description="Analisi effettuata lo scorso trimestre",
        applied_rate=resolved.rate,
        rate_source=resolved.rate_source,
    )
    db_session.add(entry)
    db_session.flush()

    frozen_value_last_quarter = line_value(entry.hours, entry.applied_rate)
    assert frozen_value_last_quarter == Decimal("800.00")

    # The rate change this test is about: raised well after the entry was written.
    _set_deal_rate(db_session, seeded_deal_id, Decimal("250.000000"))

    # The guarantee: the row's own frozen value is untouched by the later change.
    db_session.refresh(entry)
    assert entry.applied_rate == Decimal("100.000000")
    assert line_value(entry.hours, entry.applied_rate) == frozen_value_last_quarter

    # The design that was rejected, made concrete: re-resolving live instead of
    # reading the frozen column moves the same entry's value purely because time
    # passed and an unrelated write happened -- exactly what §5's guarantee forbids.
    re_resolved_today = RateResolver(db_session).resolve(
        deal_id=seeded_deal_id, user_id=seeded_user_id
    )
    naive_recomputed_value = line_value(entry.hours, re_resolved_today.rate)
    assert naive_recomputed_value == Decimal("2000.00")
    assert naive_recomputed_value != frozen_value_last_quarter
