"""Freezing rates closes half of it. This is the other half: back-dating an entry into
a month somebody has already reported still moves that month's number. §6.4."""

from datetime import date
from uuid import UUID

import pytest
from sqlalchemy.orm import Session

from pigrocrm.core.activities.service import ActivityService
from pigrocrm.core.actor import Actor
from pigrocrm.core.errors import Conflict, NotFound, PermissionDenied
from pigrocrm.core.timetracking.locks import PERIOD_LOCK_TIMELINE_ID, PeriodLockService
from pigrocrm.core.timetracking.schemas import PeriodLockCreate

COLLABORATOR = Actor(id=None, type="user", role="collaborator")


def test_closing_a_month_records_who_and_when(db_session: Session, seeded_user_id: UUID) -> None:
    admin = Actor(id=seeded_user_id, type="user", role="admin")
    lock = PeriodLockService(db_session).close_period(PeriodLockCreate(year=2026, month=3), admin)
    assert (lock.year, lock.month, lock.closed_by) == (2026, 3, seeded_user_id)
    assert lock.closed_at is not None


def test_a_write_dated_inside_a_closed_month_is_refused_and_names_it(
    db_session: Session, seeded_user_id: UUID
) -> None:
    admin = Actor(id=seeded_user_id, type="user", role="admin")
    service = PeriodLockService(db_session)
    service.close_period(PeriodLockCreate(year=2026, month=3), admin)

    with pytest.raises(Conflict) as excinfo:
        service.assert_writable("time_entry", "data", date(2026, 3, 31))
    details = excinfo.value.details
    assert details["year"] == 2026 and details["month"] == 3
    assert details["closed_by"] == str(seeded_user_id)
    assert "marzo 2026" in excinfo.value.message


@pytest.mark.parametrize(
    "day", [date(2026, 2, 28), date(2026, 4, 1)], ids=["month-before", "month-after"]
)
def test_the_neighbouring_months_stay_writable(
    db_session: Session, seeded_user_id: UUID, day: date
) -> None:
    """The boundary, explicitly: a lock on 2026-03 must not leak onto 28 February or
    1 April. A month is closed, not a range."""
    admin = Actor(id=seeded_user_id, type="user", role="admin")
    service = PeriodLockService(db_session)
    service.close_period(PeriodLockCreate(year=2026, month=3), admin)
    service.assert_writable("time_entry", "data", day)  # must not raise


def test_moving_a_row_out_of_a_closed_month_is_still_a_write_into_it(
    db_session: Session, seeded_user_id: UUID
) -> None:
    """The reason `assert_writable` is variadic: an update supplies both the stored
    date and the new one, and either falling inside a closed month refuses the
    write."""
    admin = Actor(id=seeded_user_id, type="user", role="admin")
    service = PeriodLockService(db_session)
    service.close_period(PeriodLockCreate(year=2026, month=3), admin)
    with pytest.raises(Conflict):
        service.assert_writable("time_entry", "data", date(2026, 3, 15), date(2026, 4, 15))
    with pytest.raises(Conflict):
        service.assert_writable("time_entry", "data", date(2026, 4, 15), date(2026, 3, 15))


def test_none_days_are_skipped(db_session: Session, seeded_user_id: UUID) -> None:
    """An update that does not touch `data` passes `None` for the new value; that is
    "unchanged", not "the epoch"."""
    admin = Actor(id=seeded_user_id, type="user", role="admin")
    service = PeriodLockService(db_session)
    service.close_period(PeriodLockCreate(year=2026, month=3), admin)
    service.assert_writable("time_entry", "data", None, date(2026, 4, 1))


def test_reopening_is_admin_and_leaves_a_trace(db_session: Session, seeded_user_id: UUID) -> None:
    """A period is not reopened by accident and is not reopened in silence."""
    admin = Actor(id=seeded_user_id, type="user", role="admin")
    service = PeriodLockService(db_session)
    service.close_period(PeriodLockCreate(year=2026, month=3), admin)
    service.reopen_period(2026, 3, admin)
    assert service.is_closed(date(2026, 3, 15)) is None
    service.assert_writable("time_entry", "data", date(2026, 3, 15))

    with pytest.raises(PermissionDenied):
        service.reopen_period(2026, 3, COLLABORATOR)


def test_closing_twice_is_a_conflict_and_reopening_an_open_month_is_not_found(
    db_session: Session, seeded_user_id: UUID
) -> None:
    admin = Actor(id=seeded_user_id, type="user", role="admin")
    service = PeriodLockService(db_session)
    service.close_period(PeriodLockCreate(year=2026, month=3), admin)
    with pytest.raises(Conflict):
        service.close_period(PeriodLockCreate(year=2026, month=3), admin)
    with pytest.raises(NotFound):
        service.reopen_period(2026, 4, admin)


def test_closing_nothing_leaves_everything_writable(db_session: Session) -> None:
    """Closing is not mandatory: somebody who closes nothing gets the previous
    behaviour, and no screen demands a ritual before it works."""
    PeriodLockService(db_session).assert_writable("time_entry", "data", date(1999, 1, 1))


def test_the_timeline_carries_both_events(db_session: Session, seeded_user_id: UUID) -> None:
    admin = Actor(id=seeded_user_id, type="user", role="admin")
    service = PeriodLockService(db_session)
    service.close_period(PeriodLockCreate(year=2026, month=3), admin)
    service.reopen_period(2026, 3, admin)
    entries = ActivityService(db_session).timeline("period_lock", PERIOD_LOCK_TIMELINE_ID)
    assert [entry.kind for entry in entries] == ["reopened", "closed"]
    assert entries[0].payload == {"year": 2026, "month": 3}
