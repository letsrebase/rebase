"""The reads of the bundle's parts. A repository never commits."""

import datetime as dt
from collections import defaultdict
from decimal import Decimal
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from pigrocrm.core.due_items.models import DueItem, DueItemSettlement
from pigrocrm.core.due_items.sales import SALES_INVOICE
from pigrocrm.core.invoices.models import Invoice, InvoiceLine, InvoiceRegisterGap
from pigrocrm.core.organizations.models import Organization


class ExportRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def numbered_invoices(
        self, legal_entity_id: UUID, date_from: dt.date, date_to: dt.date
    ) -> list[Invoice]:
        """The legal entity's numbered invoices, issued and annulled, dated in the period by
        the date printed on the document, in register order. A proforma and a draft have no
        number and never reach the register."""
        return list(
            self.session.scalars(
                select(Invoice)
                .where(
                    Invoice.legal_entity_id == legal_entity_id,
                    Invoice.type == "invoice",
                    Invoice.status.in_(("issued", "cancelled")),
                    Invoice.deleted_at.is_(None),
                    Invoice.issue_date >= date_from,
                    Invoice.issue_date <= date_to,
                )
                .order_by(Invoice.year, Invoice.number)
            )
        )

    def lines(self, invoice_ids: list[UUID]) -> dict[UUID, list[InvoiceLine]]:
        grouped: dict[UUID, list[InvoiceLine]] = defaultdict(list)
        if not invoice_ids:
            return grouped
        for line in self.session.scalars(
            select(InvoiceLine)
            .where(InvoiceLine.invoice_id.in_(invoice_ids))
            .order_by(InvoiceLine.invoice_id, InvoiceLine.line_number)
        ):
            grouped[line.invoice_id].append(line)
        return grouped

    def declared_gaps(
        self, legal_entity_id: UUID, year: int, first: int, last: int
    ) -> list[InvoiceRegisterGap]:
        """The numbers of `year` declared as never used, between two numbers the period
        carries."""
        return list(
            self.session.scalars(
                select(InvoiceRegisterGap)
                .where(
                    InvoiceRegisterGap.legal_entity_id == legal_entity_id,
                    InvoiceRegisterGap.year == year,
                    InvoiceRegisterGap.number >= first,
                    InvoiceRegisterGap.number <= last,
                )
                .order_by(InvoiceRegisterGap.number)
            )
        )

    def open_due_items(
        self, legal_entity_id: UUID, date_to: dt.date
    ) -> list[tuple[DueItem, str | None, Decimal]]:
        """The documented amounts that were not settled in full at the end of `date_to`:
        `(item, counterparty name, settled by then)`.

        An item born after that day is not there yet; a settlement counts when it was booked
        by that day and not reversed by then, so an item paid and later reversed, or paid
        after the period, was open at the period's end. A forecast has no document and is a
        projection, not an open item; a dropped one is no longer owed.
        """
        end_of_day = dt.datetime.combine(date_to + dt.timedelta(days=1), dt.time.min, dt.UTC)
        items = self.session.execute(
            select(DueItem, Organization.legal_name)
            .outerjoin(Organization, Organization.id == DueItem.organization_id)
            .outerjoin(
                Invoice,
                and_(DueItem.source_kind == SALES_INVOICE, Invoice.id == DueItem.source_id),
            )
            .where(
                DueItem.legal_entity_id == legal_entity_id,
                DueItem.state.in_(("expected", "settled")),
                # Born by the end of the period: a sale by the date of its invoice, every
                # other item by the day it was written.
                or_(
                    and_(Invoice.id.is_not(None), Invoice.issue_date <= date_to),
                    and_(Invoice.id.is_(None), DueItem.created_at < end_of_day),
                ),
            )
            .order_by(DueItem.due_date.asc().nulls_last(), DueItem.created_at)
        ).all()
        settled: dict[UUID, Decimal] = defaultdict(lambda: Decimal("0.00"))
        ids = [item.id for item, _ in items]
        if ids:
            for settlement in self.session.scalars(
                select(DueItemSettlement).where(
                    DueItemSettlement.due_item_id.in_(ids),
                    DueItemSettlement.settled_on <= date_to,
                    or_(
                        DueItemSettlement.reversed_at.is_(None),
                        DueItemSettlement.reversed_at >= end_of_day,
                    ),
                )
            ):
                settled[settlement.due_item_id] += settlement.amount
        return [(item, name, settled[item.id]) for item, name in items]

    def settlements(
        self, legal_entity_id: UUID, date_from: dt.date, date_to: dt.date
    ) -> list[tuple[DueItemSettlement, DueItem, str | None]]:
        """The payments booked in the period that still stand, with what each settled."""
        rows = self.session.execute(
            select(DueItemSettlement, DueItem, Organization.legal_name)
            .join(DueItem, DueItem.id == DueItemSettlement.due_item_id)
            .outerjoin(Organization, Organization.id == DueItem.organization_id)
            .where(
                DueItemSettlement.legal_entity_id == legal_entity_id,
                DueItemSettlement.reversed_at.is_(None),
                DueItemSettlement.settled_on >= date_from,
                DueItemSettlement.settled_on <= date_to,
            )
            .order_by(DueItemSettlement.settled_on, DueItemSettlement.created_at)
        ).all()
        return [(settlement, item, name) for settlement, item, name in rows]
