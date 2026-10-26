"""The money that slips, part one: `invoice_overdue`, `invoice_not_transmitted`,
`proforma_not_invoiced` and `hours_unbilled` (spec 2026-10-26 §3.3, REB-767). The
contract-side types of the same group are in `billing_contracts.py`.

Recipients are the owner of the invoice's customer and, when it has one, of its deal
(`links.py`); for `hours_unbilled` the deal's owner and its customer's. Nothing is stored:
each type is recomputed on every pass, so a reminder goes when the invoice is paid, the
file is sent, the proforma is invoiced or the hours land on an issued invoice.

**`invoice_overdue`** is the receivables page's «scaduto» (`invoices/repository.py`
`_overdue_predicate`): an issued fattura, not paid, `data_scadenza` strictly before today.
Due today is not late. The measure is the days past the due date.

**`invoice_not_transmitted`** applies where the space exports FatturaPA: at least one
azienda whose fiscal profile is not the `non-it` pack. It leaves out what this CRM never
produced an XML for, the invoices of an azienda that was abroad when they were issued (the
frozen snapshot's `fiscale.pack_id`, as `InvoiceService._frozen_abroad` reads it) and the
imported ones (`importata_da`). The measure is the days since `data_emissione`, and what
clears it is `trasmessa_esternamente_il`.

**`proforma_not_invoiced`**: a confirmed proforma (a converted one is `consumata`, a
discarded one is soft-deleted). The model keeps no «confirmed on» column, so the day is
the one the activity log recorded `confirmed` for it (latest, in the installation's zone),
and the day it was created when the log has none.

**`hours_unbilled`**: billable hours on no line of an issued invoice, the definition of
`AnalyticsRepository.unbilled_backlog` (hours on a draft's lines are still unbilled). The
subject is the **deal**: hours are logged on a deal, its owner is who bills them, and one
reminder per deal is what a person can act on, where one per entry would be a flood and one
per customer would hide which deal. The measure is the age in days of the deal's oldest
unbilled entry.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import ColumnElement, Date, cast, exists, func, select

from pigrocrm.core.activities.models import Activity
from pigrocrm.core.deals.models import Deal
from pigrocrm.core.fiscal.models import FiscalProfile
from pigrocrm.core.fiscal.pack import PACK_NON_IT
from pigrocrm.core.invoices.models import Invoice, InvoiceLine
from pigrocrm.core.invoices.naming import numero_completo
from pigrocrm.core.mail import euro, giorno_breve
from pigrocrm.core.reminders.catalogue import REMINDER_TYPES
from pigrocrm.core.reminders.detect import Detection, Detector, DetectorContext
from pigrocrm.core.reminders.detectors.links import LinkResolver, Links
from pigrocrm.core.timetracking.models import TimeEntry

OVERDUE = REMINDER_TYPES["invoice_overdue"]
NOT_TRANSMITTED = REMINDER_TYPES["invoice_not_transmitted"]
PROFORMA = REMINDER_TYPES["proforma_not_invoiced"]
HOURS = REMINDER_TYPES["hours_unbilled"]


def _issued() -> tuple[ColumnElement[bool], ...]:
    """An issued, live fattura: the register's own rows."""
    return (Invoice.deleted_at.is_(None), Invoice.tipo == "fattura", Invoice.stato == "emessa")


def _local_day(column: Any, zone: str) -> ColumnElement[date]:
    """An instant as the day it is in the installation's zone (`db/clock.py`)."""
    return cast(func.timezone(zone, column), Date)


def _label(invoice: Invoice) -> str:
    if invoice.anno is not None and invoice.numero is not None:
        return f"fattura {numero_completo(invoice.anno, invoice.numero)}"
    return "fattura"


def _links(ctx: DetectorContext, invoices: Iterable[Invoice]) -> dict[UUID, Links]:
    rows = list(invoices)
    resolver = LinkResolver(ctx.session)
    resolver.preload(invoices=(row.id for row in rows))
    return {row.id: resolver.resolve(invoice_id=row.id) for row in rows}


def _who(links: Links) -> str:
    return f" · {links.customer_name}" if links.customer_name else ""


def detect_overdue(ctx: DetectorContext) -> list[Detection]:
    """Issued, unpaid invoices past their due date by at least `bound` days, never fewer
    than one: due today is not overdue."""
    cutoff = ctx.today - timedelta(days=max(ctx.bound(OVERDUE), 1))
    rows = list(
        ctx.session.scalars(
            select(Invoice)
            .where(
                *_issued(),
                Invoice.stato_pagamento == "da_incassare",
                Invoice.data_scadenza.is_not(None),
                Invoice.data_scadenza <= cutoff,
            )
            .order_by(Invoice.data_scadenza, Invoice.id)
        )
    )
    links = _links(ctx, rows)
    found: list[Detection] = []
    for invoice in rows:
        assert invoice.data_scadenza is not None
        late = (ctx.today - invoice.data_scadenza).days
        link = links[invoice.id]
        found.append(
            Detection(
                type="invoice_overdue",
                subject_id=invoice.id,
                measure=float(late),
                recipients=ctx.directory.recipients(link.owners, invoice.azienda_id),
                title=f"Incasso in ritardo: {_label(invoice)}",
                detail=(
                    f"{euro(invoice.totale)} dovuti da {giorno_breve(invoice.data_scadenza)}, "
                    + ("ieri" if late == 1 else f"{late} giorni fa")
                    + _who(link)
                ),
                entity="invoice",
                entity_id=invoice.id,
                azienda_id=invoice.azienda_id,
                customer_id=invoice.customer_id,
            )
        )
    return found


def _exports_fatturapa(ctx: DetectorContext) -> bool:
    """Whether any azienda of the space issues under an Italian pack."""
    return bool(ctx.session.scalar(select(exists().where(FiscalProfile.pack_id != PACK_NON_IT))))


def detect_not_transmitted(ctx: DetectorContext) -> list[Detection]:
    """Issued invoices this CRM produced an XML for, not marked transmitted `bound` days
    after their issue date, never fewer than one."""
    cutoff = ctx.today - timedelta(days=max(ctx.bound(NOT_TRANSMITTED), 1))
    abroad = func.coalesce(Invoice.snapshot["fiscale"]["pack_id"].as_string(), "") == PACK_NON_IT
    rows = list(
        ctx.session.scalars(
            select(Invoice)
            .where(
                *_issued(),
                Invoice.trasmessa_esternamente_il.is_(None),
                Invoice.importata_da.is_(None),
                ~abroad,
                Invoice.data_emissione.is_not(None),
                Invoice.data_emissione <= cutoff,
            )
            .order_by(Invoice.data_emissione, Invoice.id)
        )
    )
    links = _links(ctx, rows)
    found: list[Detection] = []
    for invoice in rows:
        assert invoice.data_emissione is not None
        age = (ctx.today - invoice.data_emissione).days
        link = links[invoice.id]
        found.append(
            Detection(
                type="invoice_not_transmitted",
                subject_id=invoice.id,
                measure=float(age),
                recipients=ctx.directory.recipients(link.owners, invoice.azienda_id),
                title=f"Fattura non trasmessa: {_label(invoice)}",
                detail=(
                    f"Emessa {giorno_breve(invoice.data_emissione)}, da {age} giorni "
                    f"il file non risulta consegnato all'intermediario{_who(link)}"
                ),
                entity="invoice",
                entity_id=invoice.id,
                azienda_id=invoice.azienda_id,
                customer_id=invoice.customer_id,
            )
        )
    return found


def detect_proforma(ctx: DetectorContext) -> list[Detection]:
    """Confirmed proformas `bound` days after the day they were confirmed (created, when
    the activity log has no confirmation), never fewer than one."""
    cutoff = ctx.today - timedelta(days=max(ctx.bound(PROFORMA), 1))
    confirmed = (
        select(func.max(Activity.occurred_at))
        .where(
            Activity.entity_type == "invoice",
            Activity.entity_id == Invoice.id,
            Activity.kind == "confirmed",
        )
        .scalar_subquery()
    )
    since = _local_day(func.coalesce(confirmed, Invoice.created_at), ctx.timezone)
    rows = ctx.session.execute(
        select(Invoice, since.label("since"))
        .where(
            Invoice.deleted_at.is_(None), Invoice.tipo == "proforma", Invoice.stato == "confermata"
        )
        .where(since <= cutoff)
        .order_by(since, Invoice.id)
    ).all()
    links = _links(ctx, (row[0] for row in rows))
    found: list[Detection] = []
    for invoice, day in rows:
        age = (ctx.today - day).days
        link = links[invoice.id]
        what = f"proforma {invoice.riferimento}" if invoice.riferimento else "proforma"
        found.append(
            Detection(
                type="proforma_not_invoiced",
                subject_id=invoice.id,
                measure=float(age),
                recipients=ctx.directory.recipients(link.owners, invoice.azienda_id),
                title=f"Proforma da fatturare: {what}",
                detail=(
                    f"{euro(invoice.totale)}, confermata {giorno_breve(day)}: "
                    f"{age} giorni e non è ancora diventata una fattura{_who(link)}"
                ),
                entity="proforma",
                entity_id=invoice.id,
                azienda_id=invoice.azienda_id,
                customer_id=invoice.customer_id,
            )
        )
    return found


def detect_hours_unbilled(ctx: DetectorContext) -> list[Detection]:
    """Deals whose oldest billable entry on no issued invoice is at least `bound` days
    old, never fewer than one."""
    cutoff = ctx.today - timedelta(days=max(ctx.bound(HOURS), 1))
    on_issued = (
        select(InvoiceLine.id)
        .join(Invoice, Invoice.id == InvoiceLine.invoice_id)
        .where(InvoiceLine.id == TimeEntry.invoice_line_id, *_issued())
        .exists()
    )
    oldest = func.min(TimeEntry.data)
    rows = ctx.session.execute(
        select(Deal, oldest, func.sum(TimeEntry.ore), func.count(TimeEntry.id))
        .join(Deal, Deal.id == TimeEntry.deal_id)
        .where(
            TimeEntry.deleted_at.is_(None),
            TimeEntry.fatturabile.is_(True),
            Deal.deleted_at.is_(None),
            ~on_issued,
        )
        .group_by(Deal.id)
        .having(oldest <= cutoff)
        .order_by(oldest, Deal.id)
    ).all()
    resolver = LinkResolver(ctx.session)
    resolver.preload(deals=(deal.id for deal, *_ in rows))
    found: list[Detection] = []
    for deal, first, hours, entries in rows:
        age = (ctx.today - first).days
        link = resolver.resolve(deal_id=deal.id)
        total = Decimal(hours).normalize()
        found.append(
            Detection(
                type="hours_unbilled",
                subject_id=deal.id,
                measure=float(age),
                recipients=ctx.directory.recipients(link.owners, deal.azienda_id),
                title=f"Ore da fatturare: {deal.nome}",
                detail=(
                    f"{total:f} ore in {entries} voci non sono su una fattura emessa, "
                    f"la più vecchia è del {giorno_breve(first)} ({age} giorni){_who(link)}"
                ),
                entity="deal",
                entity_id=deal.id,
                azienda_id=deal.azienda_id,
                customer_id=deal.customer_id,
            )
        )
    return found


DETECTORS = (
    Detector(OVERDUE, detect_overdue),
    Detector(NOT_TRANSMITTED, detect_not_transmitted, applies=_exports_fatturapa),
    Detector(PROFORMA, detect_proforma),
    Detector(HOURS, detect_hours_unbilled),
)
