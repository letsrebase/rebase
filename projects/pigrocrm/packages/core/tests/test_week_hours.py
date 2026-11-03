"""§6's first two rows: hours per day in the current week, and the days with none.

The second one is the whole task. Slice 4 §13 refuses a stopwatch and names the real
failure it leaves open -- *"non ho mai inserito martedì"* -- and this is the figure that
attacks it. It is also the only figure in this slice with a direct agentic counterpart,
the `hours-to-log` prompt of §10.

**A series that silently omits its empty days is a chart that lies about its own shape.**
A week with three worked days and four blank ones must not render as three consecutive
bars, so `days` is asserted here as the *full dense range* -- seven entries, in order,
zeros included -- and not as a set of the days that happen to have hours. Every test that
touches the shape checks both ends of the window, because a fill loop is exactly the kind
of code that is right in the middle and wrong on the last day.

The set difference lives in `TimeEntryRepository` rather than in the browser or in
`DashboardService`: deriving it client-side would put business logic in the frontend, and
`DashboardService` may contain no arithmetic at all (§3, and
`test_dashboard_no_arithmetic.py`).
"""

from collections.abc import Callable
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import pigrocrm.core.db.clock as clock
from pigrocrm.core.config import Settings
from pigrocrm.core.db import current_week
from pigrocrm.core.timetracking.models import TimeEntry
from pigrocrm.core.timetracking.repository import TimeEntryRepository

ROMA = Settings(timezone="Europe/Rome")

# Monday 16 March 2026 to Sunday 22 March 2026. A week with no month boundary and no DST
# transition in it, so the fixtures below are testing the fill and nothing else; the two
# tests that *do* want a boundary say so.
_LUN = date(2026, 3, 16)
_DOM = date(2026, 3, 22)
_WEEK = [date(2026, 3, day) for day in range(16, 23)]

Factory = Callable[..., TimeEntry]


def _frozen(monkeypatch: pytest.MonkeyPatch, day: date) -> None:
    """Freeze the project's one clock at midday on `day`, UTC.

    Midday and not midnight: at 00:30 UTC it is already the next day in Rome, and a test
    that froze there would be measuring the zone conversion rather than the week
    arithmetic. `test_clock.py` owns the zone conversion and proves it across a DST
    transition.
    """
    monkeypatch.setattr(
        clock, "_now", lambda: datetime(day.year, day.month, day.day, 12, 0, tzinfo=UTC)
    )


# --- current_week -------------------------------------------------------------------


def test_current_week_runs_monday_to_sunday(monkeypatch: pytest.MonkeyPatch) -> None:
    """Monday to Sunday, not Sunday to Saturday: Italian working weeks start on Monday,
    and "the days I did not log" is a working-week question."""
    _frozen(monkeypatch, date(2026, 3, 18))  # a Wednesday
    assert current_week(ROMA) == (_LUN, _DOM)


def test_current_week_on_a_monday_starts_that_day(monkeypatch: pytest.MonkeyPatch) -> None:
    """The first of the two ends. An offset written as `weekday() + 1` or `isoweekday()`
    puts Monday in the week before its own."""
    _frozen(monkeypatch, _LUN)
    assert current_week(ROMA) == (_LUN, _DOM)


def test_current_week_on_a_sunday_ends_that_day(monkeypatch: pytest.MonkeyPatch) -> None:
    """The other end, and the one a Sunday-first convention gets wrong: under it this
    Sunday would open a *new* week and the six days just worked would drop off the chart."""
    _frozen(monkeypatch, _DOM)
    assert current_week(ROMA) == (_LUN, _DOM)


def test_current_week_spans_a_month_and_a_year_boundary(monkeypatch: pytest.MonkeyPatch) -> None:
    """Thursday 31 December 2026 belongs to a week that starts in December and ends in
    January. A week is not a slice of a month, and any implementation that reached for
    `month_bounds` or built the day from `date(anno, mese, giorno + n)` fails here."""
    _frozen(monkeypatch, date(2026, 12, 31))
    assert current_week(ROMA) == (date(2026, 12, 28), date(2027, 1, 3))


def test_current_week_is_seven_days_across_the_dst_transition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """29 March 2026 is the Sunday Italy loses an hour. The week is still seven calendar
    days -- which is why this arithmetic is on `date` and not on `datetime`, where the
    week would be 167 hours long and a naive division would lose the Sunday."""
    _frozen(monkeypatch, date(2026, 3, 26))
    date_from, date_to = current_week(ROMA)
    assert (date_from, date_to) == (date(2026, 3, 23), date(2026, 3, 29))


def test_current_week_uses_the_legal_entity_day_and_not_the_utc_day(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """23:30 UTC on Sunday 22 March is already Monday 23 March in Rome, so the week is the
    *next* one. The whole reason this lives beside `today_local` rather than being a
    `date.today()` two lines long."""
    monkeypatch.setattr(clock, "_now", lambda: datetime(2026, 3, 22, 23, 30, tzinfo=UTC))
    assert current_week(ROMA) == (date(2026, 3, 23), date(2026, 3, 29))


# --- the dense range ------------------------------------------------------------------


def test_hours_by_day_sums_per_day(db_session: Session, time_entry_factory: Factory) -> None:
    time_entry_factory(date=_LUN, hours="4.00")
    time_entry_factory(date=_LUN, hours="3.50")
    time_entry_factory(date=date(2026, 3, 18), hours="8.00")

    week = TimeEntryRepository(db_session).week_hours(_LUN, _DOM)
    by_day = {row.day: row.hours for row in week.days}
    assert by_day[_LUN] == Decimal("7.50")
    assert by_day[date(2026, 3, 18)] == Decimal("8.00")
    assert week.total_hours == Decimal("15.50")


def test_every_day_of_the_week_is_present_in_order_even_with_no_hours(
    db_session: Session, time_entry_factory: Factory
) -> None:
    """The core of the task. One worked day out of seven, and the series still has seven
    points in calendar order: a sparkline drawn from the grouped rows alone would show a
    single bar and say nothing about the six empty days around it.

    Asserted as a **list**, not a set and not a length: the order is what a chart reads off
    the x-axis, and a fill that appended the missing days after the present ones would pass
    every weaker form of this assertion.
    """
    time_entry_factory(date=date(2026, 3, 18), hours="8.00")
    week = TimeEntryRepository(db_session).week_hours(_LUN, _DOM)
    assert [row.day for row in week.days] == _WEEK
    assert [row.hours for row in week.days] == [
        Decimal("0.00"),
        Decimal("0.00"),
        Decimal("8.00"),
        Decimal("0.00"),
        Decimal("0.00"),
        Decimal("0.00"),
        Decimal("0.00"),
    ]
    assert all(isinstance(row.hours, Decimal) for row in week.days)


def test_an_empty_week_returns_seven_zero_days_and_not_an_empty_list(
    db_session: Session,
) -> None:
    """The degenerate case, and the one an implementation built from `GROUP BY` alone gets
    catastrophically wrong: it returns nothing at all, and the page renders as though the
    week had not happened."""
    week = TimeEntryRepository(db_session).week_hours(_LUN, _DOM)
    assert [row.day for row in week.days] == _WEEK
    assert week.total_hours == Decimal("0.00")
    assert week.days_without_hours == _WEEK


def test_both_ends_of_the_window_are_inside_it(
    db_session: Session, time_entry_factory: Factory
) -> None:
    """Hours on the first day and on the last day both count, and neither day is reported
    as unlogged. `>=`/`<=`, not `>`/`<`: an exclusive bound loses the Monday and the Sunday,
    which are the two days a week is most likely to be missing anyway."""
    time_entry_factory(date=_LUN, hours="1.00")
    time_entry_factory(date=_DOM, hours="2.00")
    week = TimeEntryRepository(db_session).week_hours(_LUN, _DOM)
    by_day = {row.day: row.hours for row in week.days}
    assert by_day[_LUN] == Decimal("1.00")
    assert by_day[_DOM] == Decimal("2.00")
    assert _LUN not in week.days_without_hours
    assert _DOM not in week.days_without_hours
    assert week.total_hours == Decimal("3.00")


def test_hours_outside_the_window_are_excluded(
    db_session: Session, time_entry_factory: Factory
) -> None:
    """One day either side, which is where an off-by-one lands. Neither day appears in the
    series at all -- the range is the window that was asked for, not the range of the data
    that came back."""
    time_entry_factory(date=date(2026, 3, 15), hours="8.00")
    time_entry_factory(date=date(2026, 3, 23), hours="8.00")
    week = TimeEntryRepository(db_session).week_hours(_LUN, _DOM)
    assert [row.day for row in week.days] == _WEEK
    assert week.total_hours == Decimal("0.00")
    assert week.days_without_hours == _WEEK


def test_the_window_is_echoed_back(db_session: Session) -> None:
    """A series with no stated window is a chart with no x-axis: the caller must be able to
    label the first and last point without recomputing them."""
    week = TimeEntryRepository(db_session).week_hours(_LUN, _DOM)
    assert (week.date_from, week.date_to) == (_LUN, _DOM)


def test_a_window_crossing_a_month_boundary_is_still_dense(
    db_session: Session, time_entry_factory: Factory
) -> None:
    """The week of 28 December 2026 runs into January. The fill is day arithmetic, not
    month arithmetic, and this is the window where the difference shows."""
    time_entry_factory(date=date(2027, 1, 1), hours="5.00")
    week = TimeEntryRepository(db_session).week_hours(date(2026, 12, 28), date(2027, 1, 3))
    assert [row.day for row in week.days] == [
        date(2026, 12, 28),
        date(2026, 12, 29),
        date(2026, 12, 30),
        date(2026, 12, 31),
        date(2027, 1, 1),
        date(2027, 1, 2),
        date(2027, 1, 3),
    ]
    assert week.total_hours == Decimal("5.00")


def test_a_single_day_window_is_one_point_and_not_zero(
    db_session: Session, time_entry_factory: Factory
) -> None:
    """`da == a` is inclusive at both ends, like every other period in slices 4 and 6. A
    span computed as `(a - da).days` without the `+ 1` returns an empty series here."""
    time_entry_factory(date=_LUN, hours="6.00")
    week = TimeEntryRepository(db_session).week_hours(_LUN, _LUN)
    assert [row.day for row in week.days] == [_LUN]
    assert week.total_hours == Decimal("6.00")
    assert week.days_without_hours == []


# --- the days with none ---------------------------------------------------------------


def test_the_days_without_hours_are_listed_by_the_repository(
    db_session: Session, time_entry_factory: Factory
) -> None:
    """Not derived in the browser: that would be a set difference in the frontend, and
    `DashboardService` -- the only other place it could go -- may contain no arithmetic at
    all (§3). Listed in calendar order, and the two logged days are the first and the
    fifth, so a list built by slicing rather than by filtering is visible here."""
    time_entry_factory(date=_LUN, hours="8.00")
    time_entry_factory(date=date(2026, 3, 20), hours="8.00")
    week = TimeEntryRepository(db_session).week_hours(_LUN, _DOM)
    assert week.days_without_hours == [
        date(2026, 3, 17),
        date(2026, 3, 18),
        date(2026, 3, 19),
        date(2026, 3, 21),
        date(2026, 3, 22),
    ]


def test_the_two_lists_agree_with_each_other(
    db_session: Session, time_entry_factory: Factory
) -> None:
    """`days_without_hours` is exactly the days of `days` showing zero, in the same order.

    Two fields describing one fact can disagree, and a card saying "hai saltato martedì"
    beside a bar showing Tuesday's hours is the shape of that defect. The invariant holds
    because `ore > 0` is a table constraint -- see the test below -- so a day with rows can
    never total zero; if that constraint were ever relaxed the two lists would part company
    here, deliberately, and this assertion is where it would be noticed.
    """
    time_entry_factory(date=date(2026, 3, 18), hours="8.00")
    time_entry_factory(date=date(2026, 3, 19), hours="1.50")
    week = TimeEntryRepository(db_session).week_hours(_LUN, _DOM)
    zero_days = [row.day for row in week.days if row.hours == Decimal("0.00")]
    assert zero_days == week.days_without_hours
    assert zero_days == [
        date(2026, 3, 16),
        date(2026, 3, 17),
        date(2026, 3, 20),
        date(2026, 3, 21),
        date(2026, 3, 22),
    ]


def test_the_database_refuses_a_zero_hour_entry(
    db_session: Session, time_entry_factory: Factory
) -> None:
    """**A day logged with zero hours is not a storable state in this product.**

    `ck_time_entries_hours_range` is `hours > 0 AND hours <= 24`, so the scenario the frontend's
    "`0` is a value, never a blank" rule protects -- somebody entering a zero-hour day and
    being told they forgot -- cannot arise from the hours table. It is pinned here rather
    than left implicit because `week_hours` decides membership of `days_without_hours` by the
    *presence of a row* and not by the total being zero, and under this constraint the two
    readings are indistinguishable: no test can tell them apart, so the choice is recorded
    in the repository's docstring and its consequence is recorded here. Presence is the one
    to keep, because it is the reading that stays correct if the constraint is ever
    loosened.
    """
    with pytest.raises(IntegrityError) as caught:
        time_entry_factory(date=date(2026, 3, 17), hours="0.00")
    assert "ck_time_entries_hours_range" in str(caught.value)


def test_the_smallest_storable_day_still_counts_as_logged(
    db_session: Session, time_entry_factory: Factory
) -> None:
    """`0.01` hours -- half a minute, and the least a day can carry and still exist. It is a
    day somebody made a statement about, so it is not in `days_without_hours`, and it appears
    in the series at its own value rather than rounded away to a zero that would read as
    "never entered"."""
    time_entry_factory(date=date(2026, 3, 17), hours="0.01")
    week = TimeEntryRepository(db_session).week_hours(_LUN, _DOM)
    assert date(2026, 3, 17) not in week.days_without_hours
    by_day = {row.day: row.hours for row in week.days}
    assert by_day[date(2026, 3, 17)] == Decimal("0.01")
    assert week.total_hours == Decimal("0.01")


def test_a_soft_deleted_entry_does_not_make_a_day_count_as_logged(
    db_session: Session, time_entry_factory: Factory
) -> None:
    """The soft-delete filter reaches both lists, not only the total: a deleted entry that
    still kept its day out of `days_without_hours` would leave the user with a day they
    cannot see and are never told about."""
    entry = time_entry_factory(date=date(2026, 3, 17), hours="8.00")
    entry.deleted_at = datetime.now(UTC)
    db_session.flush()
    week = TimeEntryRepository(db_session).week_hours(_LUN, _DOM)
    assert date(2026, 3, 17) in week.days_without_hours
    assert week.total_hours == Decimal("0.00")


def test_hours_are_counted_whoever_and_whatever_they_belong_to(
    db_session: Session, time_entry_factory: Factory
) -> None:
    """§6 is the operator's own week, and there is one operator: a non-billable hour and an
    unpriced one are still hours worked. Filtering on `billable` here would answer a
    different question -- and one the economic dashboard already answers."""
    time_entry_factory(date=_LUN, hours="3.00", billable=False)
    time_entry_factory(date=_LUN, hours="2.00", rate=None)
    week = TimeEntryRepository(db_session).week_hours(_LUN, _DOM)
    assert week.total_hours == Decimal("5.00")
    assert _LUN not in week.days_without_hours


def test_the_total_is_the_sum_of_the_visible_column(
    db_session: Session, time_entry_factory: Factory
) -> None:
    """Criterion 1 in miniature: the printed total must be the sum of the printed rows, at
    two decimal places, as a `Decimal`. Values chosen to be exact in decimal and not in
    binary -- `0.1 + 0.2` is where a float total stops matching its own column."""
    time_entry_factory(date=_LUN, hours="0.10")
    time_entry_factory(date=date(2026, 3, 17), hours="0.20")
    time_entry_factory(date=date(2026, 3, 18), hours="7.05")
    week = TimeEntryRepository(db_session).week_hours(_LUN, _DOM)
    assert week.total_hours == Decimal("7.35")
    assert str(week.total_hours) == "7.35"
    assert isinstance(week.total_hours, Decimal)
