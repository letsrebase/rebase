"""`timer_running_long` (spec 2026-10-26 §3.2, «New» in the catalogue).

A person has at most one timer (`time_timers`, unique on the user), and one left running
overnight is how a day's hours end up capped at 24 (`timer.elapsed_hours`). The measure is
whole hours since it started, so the ladder reads in the unit the spec uses: `serious` at
10 hours, `critical` at 24.

Recipients are the person whose timer it is and, when it is booked to a deal, the deal's
owner and its customer's (`links.py`): somebody has to be able to stop it.
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select

from pigrocrm.core.reminders.catalogue import REMINDER_TYPES
from pigrocrm.core.reminders.detect import Detection, Detector, DetectorContext
from pigrocrm.core.reminders.detectors.links import LinkResolver
from pigrocrm.core.timetracking.models import TimeTimer

LONG = REMINDER_TYPES["timer_running_long"]


def detect_long_running(ctx: DetectorContext) -> list[Detection]:
    """Timers running for at least `bound` hours, never fewer than one."""
    cutoff = ctx.now - timedelta(hours=max(ctx.bound(LONG), 1))
    rows = list(
        ctx.session.scalars(
            select(TimeTimer).where(TimeTimer.started_at <= cutoff).order_by(TimeTimer.started_at)
        )
    )
    resolver = LinkResolver(ctx.session)
    resolver.preload(deals=(row.deal_id for row in rows))
    found: list[Detection] = []
    for row in rows:
        links = resolver.resolve(deal_id=row.deal_id)
        hours = int((ctx.now - row.started_at).total_seconds() // 3600)
        owner = ctx.directory.people.get(row.user_id)
        who = f"{owner.nome}: " if owner is not None else ""
        what = f" ({row.descrizione})" if row.descrizione else ""
        found.append(
            Detection(
                type="timer_running_long",
                subject_id=row.id,
                measure=float(hours),
                recipients=ctx.directory.recipients((row.user_id, *links.owners), links.azienda_id),
                title=f"Timer acceso da {hours} ore",
                detail=f"{who}il timer{what} è in corso da {hours} ore: fermalo se hai finito",
                entity="timer",
                entity_id=row.id,
                azienda_id=links.azienda_id,
                customer_id=links.customer_id,
            )
        )
    return found


DETECTORS = (Detector(LONG, detect_long_running),)
