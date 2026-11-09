"""`deal_past_close` and `deal_stale` (spec 2026-10-26 §3.2, both «New» in the catalogue).

A deal counts while its pipeline stage is of type `open` and it is not archived: a won or
lost one is finished, and a closing date on it means nothing. Recipients are the deal's
owner and the owner of its customer (`links.py`).

**«Stale» is the absence of anything**: no mail linked to the deal, no task created or
completed on it and no hours booked to it for the threshold's days. The deal's own
creation counts as the first activity, so a deal born today is not stale tomorrow, and
editing its fields does not count: a deal somebody keeps retouching and never works is the
case the reminder is for.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import Date, Select, cast, func, literal, select
from sqlalchemy.sql import ColumnElement

from pigrocrm.core.deals.models import Deal
from pigrocrm.core.gmail.models import GmailMessage, GmailMessageLink
from pigrocrm.core.mail import day_short
from pigrocrm.core.organizations.models import Organization
from pigrocrm.core.pipeline.models import PipelineStage
from pigrocrm.core.reminders.catalogue import REMINDER_TYPES
from pigrocrm.core.reminders.detect import Detection, Detector, DetectorContext
from pigrocrm.core.reminders.detectors.text import format_days, format_days_ago
from pigrocrm.core.tasks.models import Task
from pigrocrm.core.timetracking.models import TimeEntry

PAST_CLOSE = REMINDER_TYPES["deal_past_close"]
STALE = REMINDER_TYPES["deal_stale"]

_NEVER = date(1970, 1, 1)


def _open_deals() -> Select[Deal, str | None, UUID | None]:
    return (
        select(Deal, Organization.legal_name, Organization.owner_id)
        .join(PipelineStage, PipelineStage.id == Deal.pipeline_stage_id)
        # Outer: a deal stays readable when its customer is not, which happens once a
        # customer moves to another legal entity and the deal's owner is scoped to the old one.
        .outerjoin(Organization, Organization.id == Deal.customer_id)
        .where(PipelineStage.type == "open", Deal.deleted_at.is_(None))
    )


def _detection(
    ctx: DetectorContext,
    deal: Deal,
    customer_name: str | None,
    customer_owner: UUID | None,
    *,
    type_key: str,
    measure: int,
    title: str,
    detail: str,
) -> Detection:
    return Detection(
        type=type_key,
        subject_id=deal.id,
        measure=float(measure),
        recipients=ctx.directory.recipients((deal.owner_id, customer_owner), deal.legal_entity_id),
        title=title,
        detail=f"{detail} · {customer_name}" if customer_name else detail,
        entity="deal",
        entity_id=deal.id,
        legal_entity_id=deal.legal_entity_id,
        customer_id=deal.customer_id,
    )


def detect_past_close(ctx: DetectorContext) -> list[Detection]:
    """Open deals past their `expected_close_date` by at least `bound` days, never
    fewer than one."""
    cutoff = ctx.today - timedelta(days=max(ctx.bound(PAST_CLOSE), 1))
    rows = ctx.session.execute(
        _open_deals()
        .where(Deal.expected_close_date.is_not(None), Deal.expected_close_date <= cutoff)
        .order_by(Deal.expected_close_date, Deal.id)
    )
    found: list[Detection] = []
    for deal, customer_name, customer_owner in rows:
        assert deal.expected_close_date is not None
        late = (ctx.today - deal.expected_close_date).days
        found.append(
            _detection(
                ctx,
                deal,
                customer_name,
                customer_owner,
                type_key="deal_past_close",
                measure=late,
                title=f"Deal oltre la chiusura prevista: {deal.name}",
                detail=(
                    f"Doveva chiudere {day_short(deal.expected_close_date)}, "
                    f"{format_days_ago(late)}"
                ),
            )
        )
    return found


def _local_day(column: Any, zone: str) -> ColumnElement[date]:
    """An instant as the calendar day it is in the installation's zone: the product's
    one clock (`db/clock.py`) and not the database's."""
    return cast(func.timezone(zone, column), Date)


def _last_activity(zone: str) -> ColumnElement[date]:
    """The most recent thing that happened on a deal: its creation, a mail linked to it,
    an task created or completed on it, hours booked to it."""
    mail = (
        select(func.max(_local_day(GmailMessage.internal_date, zone)))
        .select_from(GmailMessageLink)
        .join(GmailMessage, GmailMessage.id == GmailMessageLink.gmail_message_id)
        .where(GmailMessageLink.entity_type == "deal", GmailMessageLink.entity_id == Deal.id)
        .scalar_subquery()
    )
    todo = (
        select(
            func.max(
                func.greatest(
                    _local_day(Task.created_at, zone),
                    func.coalesce(Task.completed_at, literal(_NEVER)),
                )
            )
        )
        .where(Task.deal_id == Deal.id, Task.deleted_at.is_(None))
        .scalar_subquery()
    )
    hours = (
        select(func.max(TimeEntry.date))
        .where(TimeEntry.deal_id == Deal.id, TimeEntry.deleted_at.is_(None))
        .scalar_subquery()
    )
    return func.greatest(
        _local_day(Deal.created_at, zone),
        func.coalesce(mail, literal(_NEVER)),
        func.coalesce(todo, literal(_NEVER)),
        func.coalesce(hours, literal(_NEVER)),
    )


def detect_stale(ctx: DetectorContext) -> list[Detection]:
    """Open deals with nothing on them for at least `bound` days."""
    last = _last_activity(ctx.timezone).label("last_activity")
    cutoff = ctx.today - timedelta(days=max(ctx.bound(STALE), 1))
    rows = ctx.session.execute(
        _open_deals()
        .add_columns(last)
        .where(_last_activity(ctx.timezone) <= cutoff)
        .order_by(last, Deal.id)
    )
    found: list[Detection] = []
    for deal, customer_name, customer_owner, last_activity in rows:
        idle = (ctx.today - last_activity).days
        found.append(
            _detection(
                ctx,
                deal,
                customer_name,
                customer_owner,
                type_key="deal_stale",
                measure=idle,
                title=f"Deal fermo: {deal.name}",
                detail=f"Niente da {format_days(idle)} (ultima cosa: {day_short(last_activity)})",
            )
        )
    return found


DETECTORS = (Detector(PAST_CLOSE, detect_past_close), Detector(STALE, detect_stale))
