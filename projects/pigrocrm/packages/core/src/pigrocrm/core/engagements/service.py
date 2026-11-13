"""What reads a freelancer's space through the registry: the report of a hub-era
engagement's deal hours with the invoices they sit on (spec 2026-09-25 § 2.4), which the
collaborators' days report still calls, and the space and lock helpers the leads and
own-contract services borrow. The hub no longer creates engagements through this module
(REB-1108); the CRM's own collaborators flow writes the contract (`collaborators/door.py`).

On the registry's engine, never on a space's: the registry says which space, and each
space is opened for the length of one step. Everything read in a space is read as
`Actor.rebase()`, so the space's own history says who did it.

Imported by its full path, never through `pigrocrm.core.engagements` (see that
package's docstring: this module imports `tenants.service`, which imports
`tenants.database`, which imports this package's `models`).
"""

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import Connection, Engine, create_engine, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.config import Settings
from pigrocrm.core.db import session_factory, today_local
from pigrocrm.core.db.scope import bind_scope
from pigrocrm.core.deals.schemas import DealRead
from pigrocrm.core.deals.service import DealService
from pigrocrm.core.engagements.models import RebaseEngagement
from pigrocrm.core.engagements.schemas import (
    REPORT_MAX_DAYS,
    EngagementReport,
    ReportDeal,
    ReportEntry,
    ReportInvoice,
    ReportWorkUnit,
)
from pigrocrm.core.errors import Conflict, NotFound, ValidationFailed
from pigrocrm.core.invoices.models import Invoice, InvoiceLine
from pigrocrm.core.money import sum_hours
from pigrocrm.core.tenants.database import tenant_database_name, tenant_database_url
from pigrocrm.core.tenants.models import Tenant
from pigrocrm.core.timetracking.schemas import TimeEntryListQuery, TimeEntryRead
from pigrocrm.core.timetracking.service import TimeEntryService, billed_entry_ids
from pigrocrm.core.work_units.models import WorkUnit

logger = logging.getLogger(__name__)

ENTITY = "engagement"
LOCK = text("SELECT pg_advisory_lock(hashtext(:email))")
UNLOCK = text("SELECT pg_advisory_unlock(hashtext(:email))")
GONE = "Il deal di questa lettera è stato eliminato nello spazio."
REVERSED = "La data di fine precede quella di inizio."
# The report reads the deal's hours through `TimeEntryService.list`, this many a page
# (its own cap), until the cursor runs out.
REPORT_PAGE_SIZE = 200


class SpaceUnreachable(Exception):
    """A database error inside a space: its database missing (a provisioning a restart
    cut short after the registry row), unreachable, or not migrated yet. Carries the
    tenant's id, the one name of the space a log line may hold (never its slug, which
    is the freelancer's name, nor the address); the database error is its cause."""

    def __init__(self, tenant_id: UUID) -> None:
        super().__init__(f"space {tenant_id} unreachable")
        self.tenant_id = tenant_id


def _origin(settings: Settings) -> str:
    return settings.public_url.strip().rstrip("/")


def space_url(settings: Settings, slug: str) -> str:
    """The space's home, as the hub links it."""
    return f"{_origin(settings)}/{slug}/app/"


def deal_url(settings: Settings, slug: str, deal_id: UUID) -> str:
    """The deal's own page, the SPA's `/<slug>/app/deal/<id>` route."""
    return f"{space_url(settings, slug)}deal/{deal_id}"


class EngagementService:
    """On the registry's engine: `report` opens its own sessions on it, so a caller hands
    over the engine and nothing that is already in a transaction. The leads and own-contract
    services borrow its `_space_session`, `_require_public_url` and `_unlock`."""

    def __init__(self, registry_engine: Engine, settings: Settings) -> None:
        self.registry_engine = registry_engine
        self.settings = settings

    def report(
        self, match_id: UUID, date_from: date | None = None, date_to: date | None = None
    ) -> EngagementReport:
        """The hours on the match's deal from `da` to `a`, both included (spec § 2.4):
        one row per time entry with the invoice its line belongs to, the CRM's own split
        of billed hours, and one row per invoice with the hours of this report on it.
        Nothing else of the space: not its organizations, not its other deals, not an
        invoice these hours do not sit on.

        `da` defaults to the day the deal was created and `a` to today, both in the
        CRM's zone; a period that ends before it starts, or spans over
        `REPORT_MAX_DAYS`, is refused, each with its own sentence. No row for the
        match, or a row a call left before its deal existed, is `NotFound`; a deal
        deleted in the space is a `Conflict`."""
        self._require_public_url()
        with session_factory(self.registry_engine)() as registry:
            row = self._row(registry, match_id)
            if row is None or row.deal_id is None:
                raise NotFound(ENTITY, str(match_id))
            tenant = self._tenant(registry, row)
            deal_id = row.deal_id
            contract_id = row.contract_id

        with self._space_session(tenant) as space:
            deal = self._live_deal(space, deal_id, match_id)
            first, last = self._period(deal, date_from, date_to)
            times = TimeEntryService(space)
            entries = self._entries(times, deal_id, first, last)
            status = times.deal_summary(deal_id, Actor.rebase()).status
            billed = billed_entry_ids(space, entries)
            invoice_of_line, invoices = self._invoices(space, entries)
            work_units = (
                self._work_units(space, contract_id, first, last) if contract_id is not None else []
            )

        on_invoice: dict[UUID, list[Decimal]] = {}
        days = []
        for entry in entries:
            invoice_id = (
                invoice_of_line.get(entry.invoice_line_id)
                if entry.invoice_line_id is not None
                else None
            )
            if invoice_id is not None:
                on_invoice.setdefault(invoice_id, []).append(entry.hours)
            days.append(
                ReportEntry(
                    date=entry.date,
                    hours=entry.hours,
                    description=entry.description,
                    billable=entry.billable,
                    invoice=invoices[invoice_id][0] if invoice_id is not None else None,
                )
            )
        total = sum_hours(entry.hours for entry in entries)
        invoiced = sum_hours(entry.hours for entry in entries if entry.id in billed)
        # Newest first by the date it was issued, the undated (a draft, a proforma) last;
        # among equals the one created last first, then by id, so the order is stable.
        newest_first = sorted(
            invoices.values(),
            key=lambda pair: (
                pair[0].date is not None,
                pair[0].date or date.min,
                pair[1],
                pair[0].id,
            ),
            reverse=True,
        )
        return EngagementReport(
            slug=tenant.slug,
            deal_url=deal_url(self.settings, tenant.slug, deal_id),
            deal=ReportDeal(
                id=deal.id,
                name=deal.name,
                hourly_rate=deal.hourly_rate,
                budgeted_hours=deal.budgeted_hours,
                status=status,
            ),
            days=days,
            total_hours=total,
            billed_hours=invoiced,
            unbilled_hours=total - invoiced,
            invoices=[
                invoice.model_copy(update={"hours": sum_hours(on_invoice[invoice.id])})
                for invoice, _ in newest_first
            ],
            work_units=work_units,
        )

    @staticmethod
    def _work_units(
        space: Session, contract_id: UUID, date_from: date, date_to: date
    ) -> list[ReportWorkUnit]:
        """The days of the contract in `[da, a]`, oldest first. A revoked or rejected day is
        left out: it frees its date and the ledger no longer counts it."""
        rows = space.scalars(
            select(WorkUnit)
            .where(
                WorkUnit.contract_id == contract_id,
                WorkUnit.date >= date_from,
                WorkUnit.date <= date_to,
                WorkUnit.status.not_in(("revoked", "rejected")),
            )
            .order_by(WorkUnit.date, WorkUnit.created_at, WorkUnit.id)
        )
        return [
            ReportWorkUnit(
                date=day.date,
                quantity=day.quantity,
                status=day.status,
                description=day.description,
                billed=day.invoice_line_id is not None,
            )
            for day in rows
        ]

    def _require_public_url(self) -> None:
        """The report and the leads route carry links built from `PIGROCRM_PUBLIC_URL`:
        without it the door is not configured."""
        if not _origin(self.settings):
            raise ValidationFailed(ENTITY, "public_url", "PIGROCRM_PUBLIC_URL non configurato")

    def _period(
        self, deal: DealRead, date_from: date | None, date_to: date | None
    ) -> tuple[date, date]:
        """`da` or the day the deal was created, `a` or today, both in the CRM's zone:
        `created_at` is an instant, and 22:30 UTC on 1 October is the 2nd in Rome. At
        most `REPORT_MAX_DAYS` from one to the other, so the hub's windows of exactly
        that many days, bounds touching, all pass."""
        first = (
            date_from
            if date_from is not None
            else deal.created_at.astimezone(ZoneInfo(self.settings.timezone)).date()
        )
        last = date_to if date_to is not None else today_local(self.settings)
        if last < first:
            raise ValidationFailed(ENTITY, "period", REVERSED)
        if (last - first).days > REPORT_MAX_DAYS:
            raise ValidationFailed(ENTITY, "period", f"al massimo {REPORT_MAX_DAYS} giorni")
        return first, last

    @staticmethod
    def _entries(
        times: TimeEntryService, deal_id: UUID, date_from: date, date_to: date
    ) -> list[TimeEntryRead]:
        """Every live entry of the deal in `[da, a]`, through the service's own list and
        its cursor to the end, sorted by day and then by when it was logged (the list
        itself runs newest first)."""
        entries: list[TimeEntryRead] = []
        cursor: UUID | None = None
        while True:
            page = times.list(
                TimeEntryListQuery(
                    deal_id=deal_id,
                    date_from=date_from,
                    date_to=date_to,
                    limit=REPORT_PAGE_SIZE,
                    cursor=cursor,
                ),
                Actor.rebase(),
            )
            entries.extend(page.items)
            if page.next_cursor is None:
                return sorted(entries, key=lambda e: (e.date, e.created_at, e.id))
            cursor = page.next_cursor

    @staticmethod
    def _invoices(
        space: Session, entries: list[TimeEntryRead]
    ) -> tuple[dict[UUID, UUID], dict[UUID, tuple[ReportInvoice, datetime]]]:
        """The invoice of each entry's line, in one query for the whole report: which
        invoice each line is on, and each invoice once, with its `created_at` for the
        order. Any state (a draft, a proforma, an annulled invoice all still show), but
        not an invoice deleted since: the CRM no longer shows it, and neither does the
        door."""
        line_ids = {e.invoice_line_id for e in entries if e.invoice_line_id is not None}
        if not line_ids:
            return {}, {}
        rows = space.execute(
            select(
                InvoiceLine.id,
                Invoice.id,
                Invoice.type,
                Invoice.year,
                Invoice.number,
                Invoice.status,
                Invoice.payment_status,
                Invoice.issue_date,
                Invoice.created_at,
            )
            .join(Invoice, Invoice.id == InvoiceLine.invoice_id)
            .where(InvoiceLine.id.in_(line_ids), Invoice.deleted_at.is_(None))
        ).all()
        invoice_of_line: dict[UUID, UUID] = {}
        invoices: dict[UUID, tuple[ReportInvoice, datetime]] = {}
        for line_id, invoice_id, type, year, number, status, payment, issue, created in rows:
            invoice_of_line[line_id] = invoice_id
            invoices[invoice_id] = (
                ReportInvoice(
                    id=invoice_id,
                    type=type,
                    year=year,
                    number=number,
                    status=status,
                    payment_status=payment,
                    date=issue,
                ),
                created,
            )
        return invoice_of_line, invoices

    @staticmethod
    def _unlock(lock: Connection, email: str) -> None:
        """Releases the address. If the unlock itself fails, the connection is
        invalidated instead of going back to the pool holding the lock: closing it is
        what releases a session lock. Not re-raised, so the call's own answer or error
        is the one that surfaces; logged by type only, since SQLAlchemy's message
        carries the statement's parameters, and the address is one of them."""
        try:
            lock.execute(UNLOCK, {"email": email})
            lock.commit()
        except Exception as exc:
            lock.invalidate()
            logger.warning(
                "engagements: pg_advisory_unlock failed (%s); connection dropped",
                type(exc).__name__,
            )

    @staticmethod
    def _row(registry: Session, match_id: UUID) -> RebaseEngagement | None:
        return registry.scalar(
            select(RebaseEngagement).where(RebaseEngagement.match_id == match_id)
        )

    @staticmethod
    def _tenant(registry: Session, row: RebaseEngagement) -> Tenant:
        tenant = registry.get(Tenant, row.tenant_id)
        if tenant is None:  # pragma: no cover - a foreign key holds it
            raise NotFound("tenant", row.tenant_id)
        return tenant

    @staticmethod
    def _live_deal(space: Session, deal_id: UUID, match_id: UUID) -> DealRead:
        """The row's deal in its space; soft-deleted or missing, the `409` the report
        answers."""
        try:
            return DealService(space).get(deal_id, Actor.rebase())
        except NotFound as exc:
            raise Conflict(ENTITY, GONE, match_id=str(match_id)) from exc

    @contextmanager
    def _space_session(self, tenant: Tenant) -> Iterator[Session]:
        """A session on the space's own database for one step. A database error in it
        leaves as `SpaceUnreachable`, so the router's `503` can say which space."""
        tenant_id = tenant.id
        engine = create_engine(
            tenant_database_url(self.settings, tenant_database_name(tenant.slug)), future=True
        )
        try:
            with session_factory(engine)() as space:
                # The door is unscoped by design (spec 2026-10-03 §4): it opens the space
                # as the application role, and without this it would find no marker
                # deal and be refused its own customer (REB-634).
                bind_scope(space, Actor.rebase())
                yield space
        except SQLAlchemyError as exc:
            raise SpaceUnreachable(tenant_id) from exc
        finally:
            engine.dispose()
