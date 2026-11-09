"""The SQL every reader of the receivables shares: an issued invoice joined to its due item
(spec 2026-11-09, due items, § 1.1).

The due item is the truth about what is still owed and when: its remaining amount is the
receivable and its due date is the date it falls due, so the «Da incassare» card, the six
buckets, the payment reminders and the digest read one definition and cannot disagree on a
partly paid invoice. The invoice's own `payment_status` stays a projection for the readers
that want only the state (the list filter, the calendar, the year CSV).

Reads only: `DueItemService` is the sole writer.
"""

from decimal import Decimal

from sqlalchemy import ColumnElement, Select, and_, func, select

from pigrocrm.core.due_items.models import DueItem, DueItemSettlement
from pigrocrm.core.invoices.models import Invoice

SALES_INVOICE = "sales_invoice"


def settled_amount() -> ColumnElement[Decimal]:
    """What the live settlements of the enclosing `DueItem` add up to, zero when none."""
    return (
        select(func.coalesce(func.sum(DueItemSettlement.amount), 0))
        .where(
            DueItemSettlement.due_item_id == DueItem.id,
            DueItemSettlement.reversed_at.is_(None),
        )
        .correlate(DueItem)
        .scalar_subquery()
    )


def remaining_amount() -> ColumnElement[Decimal]:
    """What is still owed on the enclosing `DueItem`: its amount less what was settled."""
    return DueItem.amount - settled_amount()


def _sales_item_of_invoice() -> ColumnElement[bool]:
    return and_(DueItem.source_kind == SALES_INVOICE, DueItem.source_id == Invoice.id)


def join_sales_item[*Columns](stmt: Select[*Columns]) -> Select[*Columns]:
    """`stmt` over `Invoice`, joined to each invoice's due item."""
    return stmt.select_from(Invoice).join(DueItem, _sales_item_of_invoice())


def still_owed() -> ColumnElement[bool]:
    """The item is open: a partly paid item stays `expected` with what remains, a paid one
    reads `settled` and an annulled invoice's `dropped`."""
    return DueItem.state == "expected"
