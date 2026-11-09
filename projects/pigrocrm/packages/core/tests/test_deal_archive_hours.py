"""Archiving a deal cannot leave its hours behind in the backlog.

Slice 6 criterion 2: a card and the list behind it must agree. Two figures on the
operational dashboard are computed from the same hours by different routes --
`unbilled_backlog` sums `time_entries` and joins nothing, while
`count_won_deals_to_invoice` joins `deals` and filters `Deal.deleted_at IS NULL` -- so
soft-deleting a deal that still carries a billable, unbilled entry made them disagree.
The dashboard reported valore_maturato against a signal card reading zero, with no row
anywhere to explain the difference and no way to act on it.

**The cure is the guard, not the join, and the choice is the point of this file.** Adding
a `Deal` semi-join to `unbilled_backlog` would fix that one pair and break another:
`week_hours` and `TimeEntryRepository.list` today agree precisely because neither joins
`deals`, so a semi-join added to the aggregate alone would create a fresh criterion-2
violation, and one added to the list as well would make an archived deal's hours
unlistable -- a soft delete that hides a *sibling table's* rows, which is not a soft
delete. Refusing the archive instead establishes one invariant, **no live time entry hangs
off an archived deal**, and every aggregate over `time_entries` and every list beside it
becomes consistent at once without either of them learning about `deals`.

It is also the rule the sibling case already has: `OrganizationService.soft_delete` refuses
while `count_active_deals` is non-zero, and `DealService.restore` closes the back door that
would otherwise reintroduce the state from the other side. Both halves are mirrored here,
and the second is tested, because an invariant with a back door is a comment.
"""

import contextlib
from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.analytics.repository import AnalyticsRepository
from pigrocrm.core.deals.models import Deal
from pigrocrm.core.deals.service import DealService
from pigrocrm.core.errors import Conflict
from pigrocrm.core.organizations.models import Organization
from pigrocrm.core.pipeline.models import PipelineStage
from pigrocrm.core.timetracking.repository import TimeEntryRepository
from pigrocrm.core.timetracking.service import TimeEntryService

ADMIN = Actor(id=None, type="system", role="admin")

# The scenario from the finding, to the euro: eight billable hours at a hundred an hour on
# a deal that has been won and not yet invoiced. 800.00 of `accrued_value` and one
# `won_to_invoice` signal are the two numbers that have to move together.
_HOURS = "8.00"
_RATE = "100.000000"
_VALUE = Decimal("800.00")
ZERO = Decimal("0.00")


@pytest.fixture
def won_stage_id(db_session: Session) -> UUID:
    stage = PipelineStage(name=f"Vinto {uuid4()}", position=9, default_probability=100, type="won")
    db_session.add(stage)
    db_session.flush()
    return stage.id


@pytest.fixture
def won_deal_id(db_session: Session, won_stage_id: UUID) -> UUID:
    customer = Organization(legal_name=f"Cliente {uuid4()}")
    db_session.add(customer)
    db_session.flush()
    deal = Deal(
        name="Progetto vinto",
        customer_id=customer.id,
        pipeline_stage_id=won_stage_id,
        probability=100,
    )
    db_session.add(deal)
    db_session.flush()
    return deal.id


def _figures(session: Session) -> tuple[Decimal, Decimal, int]:
    """`(ore, valore, segnale)` -- the two backlog numbers and the signal card beside them.

    Read through the repositories the dashboard reads, not through `DashboardService`,
    which opens a repeatable-read snapshot and therefore needs a committed corpus. What is
    under test is whether the three figures describe the same rows, and that question does
    not need an isolation level.
    """
    hours, value, _without_rate, _entries = AnalyticsRepository(session).unbilled_backlog()
    return hours, value, TimeEntryRepository(session).count_won_deals_to_invoice()


def test_the_backlog_and_the_signal_agree_before_anything_is_archived(
    db_session: Session, won_deal_id: UUID, seeded_user_id: UUID, time_entry_factory
) -> None:  # type: ignore[no-untyped-def]
    """The precondition. Without it the tests below would pass on an empty backlog."""
    time_entry_factory(date=date(2026, 3, 10), hours=_HOURS, rate=_RATE, deal_id=won_deal_id)
    hours, value, signal = _figures(db_session)
    assert (hours, value, signal) == (Decimal(_HOURS), _VALUE, 1)


def test_archiving_a_deal_with_live_hours_is_refused(
    db_session: Session, won_deal_id: UUID, seeded_user_id: UUID, time_entry_factory
) -> None:  # type: ignore[no-untyped-def]
    """`Conflict`, and the same shape as the customer rule: it says what to do first.

    A refusal rather than a silent success is what leaves a row to explain the number. The
    alternative the dashboard had was two irreconcilable figures and no event anywhere.
    """
    time_entry_factory(date=date(2026, 3, 10), hours=_HOURS, rate=_RATE, deal_id=won_deal_id)
    with pytest.raises(Conflict) as caught:
        DealService(db_session).soft_delete(won_deal_id, ADMIN)
    assert caught.value.details["entity"] == "deal"
    assert caught.value.details["active_hours"] == 1


def test_the_two_figures_agree_whatever_the_archive_does(
    db_session: Session, won_deal_id: UUID, seeded_user_id: UUID, time_entry_factory
) -> None:  # type: ignore[no-untyped-def]
    """Criterion 2 as a property, deliberately indifferent to how it is achieved.

    The archive is *attempted* and its refusal swallowed rather than required, because what
    has to hold is the relationship between the two numbers and not the mechanism that
    keeps it: a cure that let the archive through while excluding the hours from the
    backlog would satisfy this too, and a cure that fixed only one of the two figures would
    not. It is the assertion the defect failed -- 800.00 of maturato against a signal card
    reading zero, two numbers on one screen that cannot be reconciled.

    Asserted non-vacuously: the entry exists, so "both zero" is only reachable through an
    archive that really did remove the work from both sides.
    """
    time_entry_factory(date=date(2026, 3, 10), hours=_HOURS, rate=_RATE, deal_id=won_deal_id)
    with contextlib.suppress(Conflict):
        DealService(db_session).soft_delete(won_deal_id, ADMIN)

    hours, value, signal = _figures(db_session)
    assert (hours, value, signal) in {(Decimal(_HOURS), _VALUE, 1), (Decimal("0.00"), ZERO, 0)}, (
        f"the backlog reports {value} of work to invoice over {hours} hours while the card "
        f"that would let anyone act on it reads {signal}; that is the pair of numbers this "
        "file exists to keep together"
    )


def test_a_non_billable_hour_still_blocks_the_archive(
    db_session: Session, won_deal_id: UUID, seeded_user_id: UUID, time_entry_factory
) -> None:  # type: ignore[no-untyped-def]
    """The guard is on *live* entries, not on billable unbilled ones, and that is
    deliberate.

    A narrower guard would fix `unbilled_backlog` and leave `week_hours` -- which sums every
    entry in the window, billable or not -- still reporting hours against a deal that no
    longer exists to anyone looking at the deal list. One invariant that holds for every
    aggregate is worth more than three guards that each hold for one.
    """
    time_entry_factory(
        date=date(2026, 3, 10), hours=_HOURS, rate=None, billable=False, deal_id=won_deal_id
    )
    with pytest.raises(Conflict):
        DealService(db_session).soft_delete(won_deal_id, ADMIN)


def test_a_deal_whose_hours_are_archived_can_be_archived(
    db_session: Session, won_deal_id: UUID, seeded_user_id: UUID, time_entry_factory
) -> None:  # type: ignore[no-untyped-def]
    """The rule has to be satisfiable, and satisfying it has to clear the figures.

    Archive the hour, then the deal: both aggregates fall to zero because the entry is
    soft-deleted, which is the state every one of them already reads correctly.
    """
    entry = time_entry_factory(
        date=date(2026, 3, 10), hours=_HOURS, rate=_RATE, deal_id=won_deal_id
    )
    TimeEntryService(db_session).soft_delete(entry.id, ADMIN)
    DealService(db_session).soft_delete(won_deal_id, ADMIN)

    hours, value, signal = _figures(db_session)
    assert (hours, value, signal) == (ZERO, ZERO, 0)


def test_a_deal_with_no_hours_at_all_is_archived_unchanged(
    db_session: Session, won_deal_id: UUID
) -> None:
    """The ordinary case must not have become harder. A guard that also refused the empty
    deal would be a regression dressed as a fix."""
    DealService(db_session).soft_delete(won_deal_id, ADMIN)
    assert db_session.get(Deal, won_deal_id).deleted_at is not None  # type: ignore[union-attr]


def test_restoring_an_hour_onto_an_archived_deal_is_refused(
    db_session: Session, won_deal_id: UUID, seeded_user_id: UUID, time_entry_factory
) -> None:  # type: ignore[no-untyped-def]
    """The back door, closed the same way `DealService.restore` closes its own.

    Archive the hour, archive the now-empty deal, restore the hour -- and the exact state
    the guard exists to prevent is back, with the backlog counting an entry whose deal the
    signal card cannot see. `DealService.restore` documents this pattern for the
    customer/deal pair; it is the same pattern one level down.
    """
    entry = time_entry_factory(
        date=date(2026, 3, 10), hours=_HOURS, rate=_RATE, deal_id=won_deal_id
    )
    TimeEntryService(db_session).soft_delete(entry.id, ADMIN)
    DealService(db_session).soft_delete(won_deal_id, ADMIN)

    with pytest.raises(Conflict) as caught:
        TimeEntryService(db_session).restore(entry.id, ADMIN)
    assert caught.value.details["deal_id"] == str(won_deal_id)

    hours, value, signal = _figures(db_session)
    assert (hours, value, signal) == (ZERO, ZERO, 0)


def test_restoring_an_hour_on_a_live_deal_still_works(
    db_session: Session, won_deal_id: UUID, seeded_user_id: UUID, time_entry_factory
) -> None:  # type: ignore[no-untyped-def]
    """The guard is about the deal's state, not about restoring."""
    entry = time_entry_factory(
        date=date(2026, 3, 10), hours=_HOURS, rate=_RATE, deal_id=won_deal_id
    )
    TimeEntryService(db_session).soft_delete(entry.id, ADMIN)
    restored = TimeEntryService(db_session).restore(entry.id, ADMIN)
    assert restored.id == entry.id

    hours, value, signal = _figures(db_session)
    assert (hours, value, signal) == (Decimal(_HOURS), _VALUE, 1)
