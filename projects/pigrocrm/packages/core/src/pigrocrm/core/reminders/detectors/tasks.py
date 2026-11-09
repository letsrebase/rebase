"""`task_due` and `task_overdue` (spec 2026-10-26 §3.2).

An task with no `due_date` is not late and not due, ever: «a NULL is not zero days»
(`attivita/models.py`), so both detectors read only the dated ones. Only an **open** one
counts: a completed or cancelled task is done with, and an archived one was a mistake.

Recipients are the task' assignee, plus the owners of the deal and the customer it
hangs on (`links.py`); an task linked to nothing and assigned to nobody goes to the
space's owner (`Directory.recipients`). The legal entity it is read under is its record's, so a
scoped member is told only about what their scope covers.
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import Select, select

from pigrocrm.core.mail import day_short
from pigrocrm.core.reminders.catalogue import REMINDER_TYPES
from pigrocrm.core.reminders.detect import Detection, Detector, DetectorContext
from pigrocrm.core.reminders.detectors.links import LinkResolver
from pigrocrm.core.tasks.models import Task

DUE = REMINDER_TYPES["task_due"]
OVERDUE = REMINDER_TYPES["task_overdue"]


def _detections(ctx: DetectorContext, rows: list[Task], type_key: str) -> list[Detection]:
    resolver = LinkResolver(ctx.session)
    resolver.preload(
        organizations=(row.customer_id for row in rows),
        people=(row.person_id for row in rows),
        deals=(row.deal_id for row in rows),
        invoices=(row.invoice_id for row in rows),
    )
    found: list[Detection] = []
    for row in rows:
        assert row.due_date is not None  # the query asked for dated ones only
        links = resolver.resolve(
            customer_id=row.customer_id,
            person_id=row.person_id,
            deal_id=row.deal_id,
            invoice_id=row.invoice_id,
        )
        days = (row.due_date - ctx.today).days
        where = f" · {links.customer_name}" if links.customer_name else ""
        if type_key == "task_due":
            when = (
                "Scade oggi"
                if days == 0
                else "Scade domani"
                if days == 1
                else f"Scade fra {days} giorni ({day_short(row.due_date)})"
            )
            title, measure = f"Attività in scadenza: {row.title}", float(days)
        else:
            late = -days
            when = (
                f"Scaduta ieri ({day_short(row.due_date)})"
                if late == 1
                else f"Scaduta da {late} giorni (era per {day_short(row.due_date)})"
            )
            title, measure = f"Attività scaduta: {row.title}", float(late)
        found.append(
            Detection(
                type=type_key,
                subject_id=row.id,
                measure=measure,
                recipients=ctx.directory.recipients(
                    (row.assigned_to, *links.owners), links.legal_entity_id
                ),
                title=title,
                detail=f"{when}{where}",
                entity="task",
                entity_id=row.id,
                legal_entity_id=links.legal_entity_id,
                customer_id=links.customer_id,
            )
        )
    return found


def _open_dated() -> Select[Task]:
    return select(Task).where(
        Task.status == "open",
        Task.deleted_at.is_(None),
        Task.due_date.is_not(None),
    )


def detect_due(ctx: DetectorContext) -> list[Detection]:
    """Open task falling due from today to `bound` days ahead. Today is the lowest
    measure: yesterday's is `task_overdue`'s."""
    horizon = ctx.today + timedelta(days=max(ctx.bound(DUE), 0))
    rows = list(
        ctx.session.scalars(
            _open_dated()
            .where(Task.due_date >= ctx.today, Task.due_date <= horizon)
            .order_by(Task.due_date, Task.id)
        )
    )
    return _detections(ctx, rows, "task_due")


def detect_overdue(ctx: DetectorContext) -> list[Detection]:
    """Open task past their `due_date` by at least `bound` days, never fewer than one:
    on the day itself an task is due, not late."""
    cutoff = ctx.today - timedelta(days=max(ctx.bound(OVERDUE), 1))
    rows = list(
        ctx.session.scalars(
            _open_dated().where(Task.due_date <= cutoff).order_by(Task.due_date, Task.id)
        )
    )
    return _detections(ctx, rows, "task_overdue")


DETECTORS = (Detector(DUE, detect_due), Detector(OVERDUE, detect_overdue))
