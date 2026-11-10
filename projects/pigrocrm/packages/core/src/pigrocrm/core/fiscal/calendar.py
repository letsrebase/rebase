"""The tax calendar of a calendar year, read from a pack (REB-1004, taxes design § 2).

The pack holds the nominal days (`TaxCalendarLaw`); this module turns them into the dates of
one year. A deadline that falls on a Saturday, a Sunday or a national holiday moves to the
next working day (art. 7, comma 1, lett. h, decreto-legge 70/2011; A5 verified), and the
national holidays are those of legge 260/1949 art. 2 as amended by legge 54/1977, DPR
792/1985 and legge 336/2000 (A5 verified): 1 and 6 January, Easter Monday, 25 April, 1 May,
2 June, 15 August, 1 November, 8 and 25 and 26 December. A patron saint's day is local and is
not a holiday here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from pigrocrm.core.fiscal.law import Cadence, DateKind, TaxName
from pigrocrm.core.fiscal.pack import FiscalPack

# (month, day) of the fixed national holidays.
_FIXED_HOLIDAYS = frozenset(
    {(1, 1), (1, 6), (4, 25), (5, 1), (6, 2), (8, 15), (11, 1), (12, 8), (12, 25), (12, 26)}
)


@dataclass(frozen=True)
class CalendarDate:
    """One date of the calendar: the nominal day the pack states and the day it is due once
    a non-working day is moved forward."""

    nominal_date: date
    due_date: date
    tax: TaxName
    kind: DateKind
    cadence: Cadence | None
    covers: str
    source: str


def easter(year: int) -> date:
    """Easter Sunday of `year` (the anonymous Gregorian algorithm)."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    weekday_offset = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * weekday_offset) // 451
    month, day = divmod(h + weekday_offset - 7 * m + 114, 31)
    return date(year, month, day + 1)


def is_non_working_day(day: date) -> bool:
    return (
        day.weekday() >= 5  # noqa: PLR2004 -- Saturday and Sunday
        or (day.month, day.day) in _FIXED_HOLIDAYS
        or day == easter(day.year) + timedelta(days=1)
    )


def next_working_day(day: date) -> date:
    """`day` itself when it is a working day, otherwise the first working day after it."""
    while is_non_working_day(day):
        day += timedelta(days=1)
    return day


def tax_calendar(
    pack: FiscalPack, year: int, *, calendar_year_end: bool = True
) -> list[CalendarDate] | None:
    """Every date of `pack`'s calendar that falls in calendar year `year`, oldest first;
    `None` when the pack carries no dates for that year (never a guess).

    `calendar_year_end` says whether the legal entity's fiscal year ends on 31 December. A rule
    marked `calendar_year_only` is left out for any other year end: its dates would be wrong, and
    the pack does not state the ones that are right. Any pack's calendar is read through this
    one function, by the tax-calendar read and by whatever writes due items on its dates."""
    law = pack.calendar_for(year)
    if law is None:
        return None
    dates = [
        CalendarDate(
            nominal_date=nominal,
            due_date=next_working_day(nominal),
            tax=rule.tax,
            kind=rule.kind,
            cadence=rule.cadence,
            covers=rule.covers,
            source=rule.source,
        )
        for rule in law.rules
        if calendar_year_end or not rule.calendar_year_only
        for nominal in (date(year, point.month, point.day) for point in rule.dates)
    ]
    return sorted(dates, key=lambda entry: (entry.nominal_date, entry.tax, entry.kind))


__all__ = [
    "CalendarDate",
    "easter",
    "is_non_working_day",
    "next_working_day",
    "tax_calendar",
]
