"""`deal_past_close` and `deal_stale` (spec 2026-10-26 §3.2, both «New» in the catalogue).

A deal counts while its pipeline stage is of type `open` and it is not archived: a won or
lost one is finished, and a closing date on it means nothing. Recipients are the deal's
owner and the owner of its customer (`links.py`).

**«Stale» is the absence of anything**: no mail linked to the deal, no attività created or
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

from pigrocrm.core.attivita.models import Attivita
from pigrocrm.core.customers.models import Customer
from pigrocrm.core.deals.models import Deal
from pigrocrm.core.gmail.models import GmailMessage, GmailMessageLink
from pigrocrm.core.mail import giorno_breve
from pigrocrm.core.pipeline.models import PipelineStage
from pigrocrm.core.reminders.catalogue import REMINDER_TYPES
from pigrocrm.core.reminders.detect import Detection, Detector, DetectorContext
from pigrocrm.core.timetracking.models import TimeEntry

PAST_CLOSE = REMINDER_TYPES["deal_past_close"]
STALE = REMINDER_TYPES["deal_stale"]

_NEVER = date(1970, 1, 1)


def _open_deals() -> Select[Deal, str, UUID | None]:
    return (
        select(Deal, Customer.ragione_sociale, Customer.owner_id)
        .join(PipelineStage, PipelineStage.id == Deal.pipeline_stage_id)
        .join(Customer, Customer.id == Deal.customer_id)
        .where(PipelineStage.tipo == "open", Deal.deleted_at.is_(None))
    )


def _detection(
    ctx: DetectorContext,
    deal: Deal,
    customer_name: str,
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
        recipients=ctx.directory.recipients((deal.owner_id, customer_owner), deal.azienda_id),
        title=title,
        detail=f"{detail} · {customer_name}",
        entity="deal",
        entity_id=deal.id,
        azienda_id=deal.azienda_id,
        customer_id=deal.customer_id,
    )


def detect_past_close(ctx: DetectorContext) -> list[Detection]:
    """Open deals past their `data_chiusura_prevista` by at least `bound` days, never
    fewer than one."""
    cutoff = ctx.today - timedelta(days=max(ctx.bound(PAST_CLOSE), 1))
    rows = ctx.session.execute(
        _open_deals()
        .where(Deal.data_chiusura_prevista.is_not(None), Deal.data_chiusura_prevista <= cutoff)
        .order_by(Deal.data_chiusura_prevista, Deal.id)
    )
    found: list[Detection] = []
    for deal, customer_name, customer_owner in rows:
        assert deal.data_chiusura_prevista is not None
        late = (ctx.today - deal.data_chiusura_prevista).days
        found.append(
            _detection(
                ctx,
                deal,
                customer_name,
                customer_owner,
                type_key="deal_past_close",
                measure=late,
                title=f"Deal oltre la chiusura prevista: {deal.nome}",
                detail=(
                    f"Doveva chiudere {giorno_breve(deal.data_chiusura_prevista)}, "
                    + ("ieri" if late == 1 else f"{late} giorni fa")
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
    an attività created or completed on it, hours booked to it."""
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
                    _local_day(Attivita.created_at, zone),
                    func.coalesce(Attivita.completata_il, literal(_NEVER)),
                )
            )
        )
        .where(Attivita.deal_id == Deal.id, Attivita.deleted_at.is_(None))
        .scalar_subquery()
    )
    hours = (
        select(func.max(TimeEntry.data))
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
                title=f"Deal fermo: {deal.nome}",
                detail=f"Niente da {idle} giorni (ultima cosa: {giorno_breve(last_activity)})",
            )
        )
    return found


DETECTORS = (Detector(PAST_CLOSE, detect_past_close), Detector(STALE, detect_stale))
