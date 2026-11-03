"""The project's only clock.

`datetime.now(UTC).date()` is wrong for every figure in this slice, and wrong in a way
that is invisible for twenty-three hours a day: at 00:30 on 1 April in Rome it is still
31 March in UTC, so a deal won just after midnight lands in the previous month's
conversion rate. `deals.closed_at`, `documents.status_since`, `invoices.issue_date`,
`costs.date` and `time_entries.date` are all calendar dates in the *emitter's* day, not
instants, and slice 3 §6.2 already fixed that rule -- this module is the mechanism.

Two clocks in one product is a bug that shows up on 31 December, which is the worst
possible day to find it. That sentence is why this file does more than the brief asked
for. `pigrocrm.core.db.clock.today_local()` is the one function that answers "what day is
it", and the former `pigrocrm.core.clock.today_in_italy()`, a second name for it, is gone
(B-29): a second day function beside it would have been the very defect the module is named
after, and commit 875a1f9 had already fixed three production sites that read the wrong day.

The AST scan at the bottom is the other half. The brief's own interface section says
"nothing else in the project may call `date.today()` or `datetime.now(UTC).date()`" and
defers the enforcement to Task B9 -- whose brief turns out to enforce something else
entirely (the dashboard's arithmetic). A rule stated in a docstring and enforced
nowhere is a rule that lasts until the next contributor, which is exactly how the three
sites 875a1f9 fixed came to exist. The scan is modelled on
`apps/mcp/tests/test_mcp_invoice_ban.py`, the repository's precedent for this shape.
"""

import ast
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from pigrocrm.core.config import Settings
from pigrocrm.core.db import month_bounds, today_local

REPO_ROOT = Path(__file__).resolve().parents[3]
SOURCE_ROOTS = (
    REPO_ROOT / "packages" / "core" / "src",
    REPO_ROOT / "apps" / "api" / "src",
    REPO_ROOT / "apps" / "mcp" / "src",
)

# `db/clock.py` is the one module allowed to ask the standard library what time it is.
# Nothing else needs to, because `today_local()` is one import away.
CLOCK_MODULE = REPO_ROOT / "packages" / "core" / "src" / "pigrocrm" / "core" / "db" / "clock.py"


def test_the_default_timezone_is_rome() -> None:
    assert Settings().timezone == "Europe/Rome"


def test_an_unknown_timezone_is_refused_at_construction() -> None:
    """A typo in an environment variable must fail at start-up, not silently fall back to
    UTC and shift every date by an hour for the life of the deployment."""
    with pytest.raises(ValidationError):
        Settings(timezone="Europe/Atlantis")


def test_today_local_is_the_legal_entity_day_not_the_utc_day(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """00:30 on 1 April in Rome is 22:30 on 31 March in UTC. The clock must answer
    1 April."""
    import pigrocrm.core.db.clock as clock

    frozen = datetime(2026, 3, 31, 22, 30, tzinfo=UTC)
    monkeypatch.setattr(clock, "_now", lambda: frozen)

    assert today_local(Settings(timezone="Europe/Rome")) == date(2026, 4, 1)
    assert today_local(Settings(timezone="UTC")) == date(2026, 3, 31)


def test_today_local_handles_the_dst_transition(monkeypatch: pytest.MonkeyPatch) -> None:
    """The 2026 spring-forward in Rome is 29 March. 00:30 UTC on that day is 01:30 CET,
    still 29 March -- the date must not jump."""
    import pigrocrm.core.db.clock as clock

    monkeypatch.setattr(clock, "_now", lambda: datetime(2026, 3, 29, 0, 30, tzinfo=UTC))
    assert today_local(Settings(timezone="Europe/Rome")) == date(2026, 3, 29)


def test_the_offset_is_the_one_in_force_at_that_instant_not_a_fixed_hour(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """22:30 UTC is *the same wall time* in both halves of the year and lands on two
    different Italian days.

    The brief's own DST case above asserts a date that UTC and Rome agree on, so it
    passes just as happily against `return _now().date()`. This one does not: in July
    Rome is CEST (+02:00) and 22:30 UTC is already tomorrow; in January it is CET
    (+01:00) and 22:30 UTC is still today. An implementation that hard-codes a single
    offset -- or skips the conversion -- gets exactly one of these two wrong.
    """
    import pigrocrm.core.db.clock as clock

    monkeypatch.setattr(clock, "_now", lambda: datetime(2026, 7, 15, 22, 30, tzinfo=UTC))
    assert today_local(Settings(timezone="Europe/Rome")) == date(2026, 7, 16)

    monkeypatch.setattr(clock, "_now", lambda: datetime(2026, 1, 15, 22, 30, tzinfo=UTC))
    assert today_local(Settings(timezone="Europe/Rome")) == date(2026, 1, 15)


def test_today_local_with_no_argument_uses_the_cached_settings() -> None:
    """Callers deep in a repository should not have to thread `Settings` through four
    layers to learn what day it is."""
    assert isinstance(today_local(), date)


def test_month_bounds_is_inclusive_at_both_ends() -> None:
    assert month_bounds(2026, 2) == (date(2026, 2, 1), date(2026, 2, 28))
    assert month_bounds(2024, 2) == (date(2024, 2, 1), date(2024, 2, 29))
    assert month_bounds(2026, 12) == (date(2026, 12, 1), date(2026, 12, 31))


def test_month_bounds_applies_the_full_gregorian_leap_rule() -> None:
    """1900 is not a leap year and 2000 is. A `anno % 4 == 0` shortcut passes every
    assertion above and fails the first of these two."""
    assert month_bounds(1900, 2)[1] == date(1900, 2, 28)
    assert month_bounds(2000, 2)[1] == date(2000, 2, 29)
    assert month_bounds(2100, 2)[1] == date(2100, 2, 28)


def test_window_from_is_inclusive_at_both_ends() -> None:
    """The date arithmetic `core/dashboard/` is forbidden to contain (slice 6 §3) lives
    here instead. Inclusive at both ends, like `month_bounds`: every period filter in
    slices 4 and 6 is `BETWEEN date_from AND date_to`, and a second convention is how a day
    gets counted twice."""
    from pigrocrm.core.db import window_from

    assert window_from(date(2026, 1, 31), 30) == (date(2026, 1, 31), date(2026, 3, 2))
    # Across a month boundary and a leap day, because "plus thirty days" is not "next
    # month" and the two differ by up to three days.
    assert window_from(date(2024, 2, 1), 30) == (date(2024, 2, 1), date(2024, 3, 2))
    assert window_from(date(2026, 5, 4), 0) == (date(2026, 5, 4), date(2026, 5, 4))


@pytest.mark.parametrize("month", [0, 13, -1])
def test_month_bounds_refuses_a_month_outside_one_to_twelve(month: int) -> None:
    from pigrocrm.core.errors import ValidationFailed

    with pytest.raises(ValidationFailed) as caught:
        month_bounds(2026, month)
    assert caught.value.details["field"] == "month"


# --- The mechanical guard -------------------------------------------------------------


def _is_process_clock_day(node: ast.AST) -> str | None:
    """Name the banned shape at `node`, or `None`.

    Two shapes, both of which answer the *process's* day rather than the emitter's:

      * `date.today()` / `datetime.today()` -- reads the host's system timezone, which
        this project does not control (`Dockerfile.api` pins no `TZ`, so the API image
        runs in UTC);
      * `datetime.now(...).date()` -- projects an instant onto a calendar, correct only
        by accident of which zone was passed, and the accident is invisible for
        twenty-three hours a day.

    `datetime.now(UTC)` on its own is deliberately *not* banned: `created_at`,
    `updated_at`, `deleted_at` and `occurred_at` are instants and an instant has no zone
    problem. This guard is about the other kind of column.
    """
    if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
        return None
    outer = node.func
    if (
        outer.attr == "today"
        and isinstance(outer.value, ast.Name)
        and outer.value.id in ("date", "datetime")
    ):
        return f"{outer.value.id}.today()"
    if outer.attr == "date" and isinstance(outer.value, ast.Call):
        inner = outer.value.func
        if (
            isinstance(inner, ast.Attribute)
            and inner.attr == "now"
            and isinstance(inner.value, ast.Name)
            and inner.value.id == "datetime"
        ):
            return "datetime.now(...).date()"
    return None


def _scan(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return [
        f"{path.relative_to(REPO_ROOT)}:{node.lineno}: {shape}"
        for node in ast.walk(tree)
        if (shape := _is_process_clock_day(node)) is not None
    ]


def _modules() -> list[Path]:
    return sorted(p for root in SOURCE_ROOTS for p in root.rglob("*.py"))


def test_the_scan_actually_reads_the_whole_product() -> None:
    """A guard over an empty file list passes forever and proves nothing."""
    modules = _modules()
    assert len(modules) > 100, len(modules)
    for root in SOURCE_ROOTS:
        assert root.is_dir(), root


def test_the_guard_recognises_both_banned_shapes() -> None:
    """The scanner is itself tested, because a scanner that matches nothing is
    indistinguishable from a clean tree."""
    banned = ast.parse(
        "a = date.today()\n"
        "b = datetime.today()\n"
        "c = datetime.now(UTC).date()\n"
        "d = datetime.now(ITALY_TZ).date()\n"
    )
    hits = [s for n in ast.walk(banned) if (s := _is_process_clock_day(n)) is not None]
    assert len(hits) == 4, hits

    allowed = ast.parse(
        "a = datetime.now(UTC)\n"
        "b = today_local()\n"
        "c = _now().astimezone(zone).date()\n"
        "d = local_date(invoice.created_at)\n"
    )
    assert [s for n in ast.walk(allowed) if (s := _is_process_clock_day(n)) is not None] == []


def test_no_module_in_src_reads_a_calendar_day_from_the_process_clock() -> None:
    offenders = [hit for path in _modules() if path != CLOCK_MODULE for hit in _scan(path)]
    assert not offenders, (
        "a calendar day was read from the process clock instead of `today_local()`. "
        "Every `Date` column in this product is a day in the emitter's zone; the API "
        "image runs in UTC, an hour behind Italy, so these are wrong between midnight "
        "and 01:00 CET -- and wrong about the *year* on 31 December. Commit 875a1f9 "
        f"fixed three of these; use `pigrocrm.core.db.today_local()`. Offenders: {offenders}"
    )
