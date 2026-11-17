"""Who is already committed, as the CRM says (REB-1120): the CRM's weeks summarised into this week
and its next change, read once for a whole list through the real `FreelancerDoor` and the CRM
fake, and a card the CRM does not answer for read as free."""

from datetime import date, timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from fakes_crm_door import FakeCrmDoor

from rebase_core.door_schemas import CrmWeek
from rebase_core.pigro_door import PigroUnavailable
from rebase_core.talent_availability import read_commitments, summarize

MONDAY = date(2026, 11, 16)


def _weeks(*statuses: str, days: str = "3") -> list[dict[str, Any]]:
    """One CRM week per status, from `MONDAY` on, `days` committed in each committed one."""
    out = []
    for index, status in enumerate(statuses):
        start = MONDAY + timedelta(weeks=index)
        out.append(
            {
                "week": f"{start.isocalendar().year}-W{start.isocalendar().week:02d}",
                "starts_on": start.isoformat(),
                "ends_on": (start + timedelta(days=6)).isoformat(),
                "status": status,
                "label": status,
                "committed_days": days if status == "committed" else "0",
                "free_days": None if status == "unknown" else "2",
                "engagement_ids": [],
            }
        )
    return out


def test_each_committed_card_reads_this_week_and_when_it_changes_in_one_call() -> None:
    busy, starting, unknown, idle = uuid4(), uuid4(), uuid4(), uuid4()
    crm = FakeCrmDoor(
        committed={
            busy: _weeks("committed", "committed", "free", "committed"),
            starting: _weeks("free", "free", "committed", "committed"),
            unknown: _weeks("unknown", "unknown", "committed", "committed"),
        }
    )

    commitments = read_commitments(crm.door())

    until = MONDAY + timedelta(weeks=4, days=-1)
    now = commitments.of(busy)
    assert (now.status, now.committed_days, now.free_from, now.committed_from) == (
        "committed",
        Decimal(3),
        MONDAY + timedelta(weeks=2),
        None,
    )
    soon = commitments.of(starting)
    assert (soon.status, soon.committed_from, soon.free_from) == (
        "free",
        MONDAY + timedelta(weeks=2),
        None,
    )
    # «Sconosciuta» is never free, and no free week in the window leaves nothing to promise.
    cannot_say = commitments.of(unknown)
    assert (cannot_say.status, cannot_say.free_from) == ("unknown", None)
    # A card the CRM holds nothing live for is free over the same window.
    free = commitments.of(idle)
    assert (free.status, free.committed_days, free.committed_from, free.until) == (
        "free",
        Decimal(0),
        None,
        until,
    )
    assert now.until == until
    assert crm.calls == [("GET", "/api/rebase/freelancers/commitments")]


def test_a_crm_that_cannot_answer_raises_for_the_caller_to_decide() -> None:
    crm = FakeCrmDoor()
    crm.down((503, b'{"detail": "no"}'), (503, b'{"detail": "no"}'))

    with pytest.raises(PigroUnavailable):
        read_commitments(crm.door())
    with pytest.raises(PigroUnavailable):
        read_commitments(crm.door(token=""))


def _week(index: int, status: str) -> CrmWeek:
    start = MONDAY + timedelta(weeks=index)
    return CrmWeek(
        week=f"W{index}",
        starts_on=start,
        ends_on=start + timedelta(days=6),
        status=status,
        label=status,
        committed_days=Decimal(3 if status == "committed" else 0),
        free_days=None,
    )


@pytest.mark.parametrize(
    ("statuses", "expected"),
    [
        # Nothing answered is free over the window asked.
        ((), ("free", None, None)),
        # A later unknown week is a change from free: it is not free.
        (("free", "free", "unknown"), ("free", 2, None)),
        # Unknown weeks are never the free week a committed one waits for.
        (("committed", "unknown", "free"), ("committed", None, 2)),
        # A status a later CRM may add reads as «cannot say», never as free.
        (("on_hold", "free"), ("unknown", None, 1)),
        (("committed", "committed"), ("committed", None, None)),
    ],
)
def test_the_summary_reads_this_week_and_its_next_change(
    statuses: tuple[str, ...], expected: tuple[str, int | None, int | None]
) -> None:
    until = MONDAY + timedelta(weeks=12, days=-1)
    status, committed_week, free_week = expected

    summary = summarize([_week(i, s) for i, s in enumerate(statuses)], until)

    assert summary.status == status and summary.until == until
    assert summary.committed_from == (
        None if committed_week is None else MONDAY + timedelta(weeks=committed_week)
    )
    assert summary.free_from == (None if free_week is None else MONDAY + timedelta(weeks=free_week))
