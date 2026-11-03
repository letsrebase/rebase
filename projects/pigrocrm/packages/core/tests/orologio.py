"""One instant at which Italy and the process disagree about what day it is.

`db/clock.py` exists because `date.today()` reads the *process's* system timezone and this
project does not control that end to end -- `Dockerfile.api` pins no `TZ`, so the API
runs in UTC. A test that only checks "the service called `today_local`" proves
nothing about behaviour: it passes just as happily against an implementation that reads
the right clock and then does the wrong thing with it. What proves the rule is holding
is an assertion taken at a moment where the two clocks give *different answers*, so a
regression to `date.today()` changes the observable result and the test goes red.

Italy is ahead of UTC (CET is +01:00, CEST +02:00), so a UTC process is still on
*yesterday* for the first hour of every Italian day. 00:30 in Rome on 1 January 2026 is
23:30 UTC on 31 December 2025: the two clocks disagree about the day, the month and the
year at once, which is the same shape as the previous system's defect `db/clock.py`'s docstring
describes -- an instant projected into the wrong zone landing a document in the wrong
fiscal year.

`congela` freezes that instant on both sides of the disagreement:

  * `pigrocrm.core.db.clock._now` is frozen, so `today_local()` answers `TODAY_IN_ITALY`
    (1 January 2026): the real function, real `ZoneInfo` conversion, only the instant
    supplied.
  * each production module named by the caller gets a `date` whose `today()` answers
    `TODAY_OF_PROCESSO` (31 December 2025), which is what the standard library would
    genuinely return for that instant on a host running in UTC.

There is exactly one place to freeze, `db.clock._now`, because there is exactly one place
that reads the wall clock; a helper that had to patch two would be evidence the
consolidation had not actually happened.

Patching a module attribute rather than the whole interpreter is deliberate: it is the
same `monkeypatch.setattr(module, "datetime", ...)` shape `test_auth_tokens` and
`test_refresh_tokens` already use to freeze token issuance, it needs no `TZ`/`tzset`
games that would leak into other tests through the C library, and the per-module `date`
is patched with `raising=False` because a module that has been fixed correctly no longer
imports `date` at all -- the absence of the name is not a reason for the test to error.
"""

from datetime import UTC, date, datetime

import pytest

from pigrocrm.core.db import clock as db_clock
from pigrocrm.core.db.clock import ITALY_TZ

# 00:30 on 1 January in Rome. In UTC this instant is still 23:30 on 31 December.
ISTANTE = datetime(2026, 1, 1, 0, 30, tzinfo=ITALY_TZ)

TODAY_IN_ITALY: date = ISTANTE.astimezone(ITALY_TZ).date()
TODAY_OF_PROCESSO: date = ISTANTE.astimezone(UTC).date()

assert TODAY_IN_ITALY != TODAY_OF_PROCESSO, "the whole point is that these two differ"


class _DataDiSistema(date):
    """`date` whose `today()` answers as the standard library would on a UTC host."""

    @classmethod
    def today(cls) -> date:
        return TODAY_OF_PROCESSO


def congela(monkeypatch: pytest.MonkeyPatch, *moduli: object) -> None:
    """Freeze `ISTANTE`, and make `date.today()` inside `moduli` lie the way UTC does."""
    monkeypatch.setattr(db_clock, "_now", lambda: ISTANTE.astimezone(UTC))
    for modulo in moduli:
        monkeypatch.setattr(modulo, "date", _DataDiSistema, raising=False)


def fissa_today(monkeypatch: pytest.MonkeyPatch, day: date) -> None:
    """Pin the product clock to midday in Rome on `day`, the day a module computed once
    at import (`TODAY = today_local()`): a run that crosses midnight would otherwise compare
    the service's new day with the old one."""
    istante = datetime(day.year, day.month, day.day, 12, 0, tzinfo=ITALY_TZ)
    monkeypatch.setattr(db_clock, "_now", lambda: istante.astimezone(UTC))


__all__ = ["ISTANTE", "fissa_today", "TODAY_OF_PROCESSO", "TODAY_IN_ITALY", "congela"]
