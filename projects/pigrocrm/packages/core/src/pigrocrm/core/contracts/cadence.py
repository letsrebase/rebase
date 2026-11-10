"""A contract's billing periods: the calendar month, quarter or year.

`Contract.billing_cadence` is free text (no value set is pinned, `contracts/models.py`), so the
three periodic cadences are the words the rate cards already use for a period (`monthly`,
`quarterly`, `annual`, any case) and every other value is read as billing on completion. A
period is the calendar month, quarter or year, as in Mastro (`currentPeriodStart`), not one
counted from the contract's `start`.

Shared by the reminders that read what a contract has not billed and by the forecasts of money
in, so both cut a contract's time the same way.
"""

from datetime import date

from pigrocrm.core.db.clock import month_bounds

# The months of a period, by the words a contract's cadence uses for it.
PERIOD_MONTHS = {"monthly": 1, "quarterly": 3, "annual": 12}


def period_months(cadence: str) -> int | None:
    """The months in one period of `cadence`, or `None` for a cadence billed on completion."""
    return PERIOD_MONTHS.get(cadence.strip().lower())


def period_start(months: int, day: date) -> date:
    """The first day of the calendar month, quarter or year `day` falls in."""
    first = ((day.month - 1) // months) * months + 1
    return date(day.year, first, 1)


def period_end(months: int, day: date) -> date:
    """The last day of the calendar month, quarter or year `day` falls in."""
    last = ((day.month - 1) // months) * months + months
    return month_bounds(day.year, last)[1]
