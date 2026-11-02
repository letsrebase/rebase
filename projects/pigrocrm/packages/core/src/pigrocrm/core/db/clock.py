"""The project's only clock.

Nothing anywhere in `packages/core`, `apps/api` or `apps/mcp` may call `date.today()` or
`datetime.now(UTC).date()` to obtain a calendar day. Both answer the *UTC* day (or, for
`date.today()`, the day of whatever timezone the host happens to run in -- `Dockerfile.api`
pins no `TZ`, so the API image runs in UTC), and every `Date` column in this product is a
day in the emitter's zone: at 00:30 on 1 April in Rome it is still 31 March in UTC, so a
deal won just after midnight would land in the previous month's conversion rate, and an
invoice issued on 31 December at 23:30 CET would land in the previous fiscal year -- the
defect slice 3 §6.2 names by name. `packages/core/tests/test_clock.py` enforces that ban
with an AST scan over all three source trees, because commit 875a1f9 had to fix three
sites that were written after the rule was already documented.

`datetime.now(UTC)` for a *timestamp* is still correct and still required: `created_at`,
`updated_at`, `deleted_at` and `occurred_at` are instants, and an instant has no zone
problem. This module is about the other kind of column.

`_now` is a module-level function rather than an inline call so a test can freeze it
without patching the standard library. It is the single freeze point for the whole
product: `pigrocrm.core.db.clock.today_local()` -- which predates this module and has
eight fiscal callers -- is now a thin alias over `today_local()`, so freezing `_now`
freezes both. That delegation is the point of the exercise. Two functions each doing
their own `datetime.now(...).date()` would be two clocks, and two clocks in one product
is a bug that surfaces on 31 December.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from pigrocrm.core.config import Settings, get_settings
from pigrocrm.core.errors import ValidationFailed

# The issuer's own calendar, pinned to Rome and not read from `Settings.timezone`: it
# projects an instant that was already stored (`local_date`), and an invoice's civil date
# must not move because somebody set `PIGROCRM_TIMEZONE`.
ITALY_TZ = ZoneInfo("Europe/Rome")

# Days per month, non-leap. February is corrected in `month_bounds`.
_DAYS_IN_MONTH = (31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)


def _now() -> datetime:
    return datetime.now(UTC)


def today_local(settings: Settings | None = None) -> date:
    """Today, in the emitter's zone.

    `settings` is optional so a repository three layers down does not have to thread it
    through; passing it explicitly is what makes the tests able to check two zones without
    touching the environment.
    """
    resolved = settings if settings is not None else get_settings()
    return _now().astimezone(ZoneInfo(resolved.timezone)).date()


def local_date(instant: datetime) -> date:
    """The civil date, in Rome, of an instant already stored (an invoice's `created_at`).
    A projection of a known instant, not a reading of the clock."""
    return instant.astimezone(ITALY_TZ).date()


def month_bounds(anno: int, mese: int) -> tuple[date, date]:
    """The first and last day of a month, both inclusive.

    Inclusive at both ends because every period filter in this slice and in slice 4 is
    `BETWEEN da AND a` over a `Date` column. A half-open convention would be defensible
    and would also mean two conventions in one product, which is how a December figure
    ends up counted twice.

    Computed rather than taken from `calendar.monthrange`: the arithmetic is four lines,
    and this way the leap rule is visible next to the only place that depends on it. The
    rule is the full Gregorian one -- divisible by 4, except centuries, except those
    divisible by 400 -- and not the `% 4` shortcut, which is right for every year this
    product will plausibly see and wrong for 1900 and 2100. A backfilled historical date
    is not a hypothetical in a CRM that imports.
    """
    if not 1 <= mese <= 12:
        raise ValidationFailed("periodo", "mese", "mese fuori intervallo", expected="1-12")
    last = _DAYS_IN_MONTH[mese - 1]
    if mese == 2 and (anno % 4 == 0 and (anno % 100 != 0 or anno % 400 == 0)):
        last = 29
    return date(anno, mese, 1), date(anno, mese, last)


def window_from(start: date, days: int) -> tuple[date, date]:
    """`(start, start + days)`, both inclusive.

    Here and not in `core/dashboard/` because that package is forbidden from containing any
    arithmetic at all -- no `*`, `/` or `-` BinOp, and no `Decimal` import outside
    `schemas.py` -- so that it provably cannot invent a figure (spec §3, and
    `packages/core/tests/test_dashboard_no_arithmetic.py`). A date offset is harmless in
    itself; the rule has no exceptions precisely so that nobody has to judge which
    arithmetic is harmless.

    Inclusive at both ends, like `month_bounds` above and for the same reason: every period
    filter in slices 4 and 6 is `BETWEEN da AND a` over a `Date` column, and two conventions
    in one product is how a day gets counted twice.
    """
    return start, start + timedelta(days=days)


def current_week(settings: Settings | None = None) -> tuple[date, date]:
    """Monday to Sunday, both inclusive, containing today in the emitter's zone.

    Monday and not Sunday: an Italian working week starts on Monday, and "which days did I
    not log" is a working-week question. Under a Sunday-first convention every Sunday would
    open a new week, and the six days just worked would drop off the chart on the one day
    somebody is most likely to be catching up on them.

    `isoweekday()` returns 1 for Monday through 7 for Sunday, so the offset back to Monday
    is `isoweekday() - 1` -- zero on a Monday, six on a Sunday. `weekday()` would do as
    well and is off by one from the name of the standard; the ISO spelling is the one whose
    numbering can be checked against a calendar without running it.

    Arithmetic on `date` and not on `datetime`, deliberately: the week containing the
    March DST transition is 167 hours long and still seven days, and any duration-based
    derivation loses its last day once a year. It is also why this lives here rather than
    in `core/dashboard/`, which may contain no arithmetic at all (spec §3).

    Inclusive at both ends, like `month_bounds` and `window_from` above: every period
    filter in slices 4 and 6 is `BETWEEN da AND a` over a `Date` column.
    """
    today = today_local(settings)
    monday = today - timedelta(days=today.isoweekday() - 1)
    return monday, monday + timedelta(days=6)


__all__ = ["current_week", "month_bounds", "today_local", "window_from"]
