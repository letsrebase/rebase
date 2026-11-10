"""The due item of an invoice a test builds as a row (REB-968, spec 2026-11-09, § 1.1).

Emission, import and `set_invoice_payment_state` write an invoice's due item through
`DueItemService`; a test that builds an issued `Invoice` row by hand, or flips its
`payment_status` on the row, goes round them. About seventy tests do, to get an invoice in a
state without the Typst renderer, so this hook plays the part the boot reconciliation
plays in production for a row the service did not write: an issued invoice gets its item,
settled in full when the row says it is paid, and a row whose `payment_status` is changed
afterwards has its item follow. It is plain Core SQL on the flush's own connection, after the
row is written, and does nothing for a row whose item already says what the row says, which is
every row `DueItemService` wrote.

`suspended()` turns it off, for the tests of the reconciliation itself: they need an invoice
as the previous release left it, with no item.

Only an inserted row gets an item; an update never creates one.

Skipped for a row with `imported_from` set: `InvoiceImporter` writes its own item, with the
payment, and an item written first here would keep the importer from settling it.
"""

import datetime as dt
from collections.abc import Iterator
from contextlib import contextmanager
from decimal import Decimal

from sqlalchemy import Connection, event, func, select, update
from sqlalchemy.orm import Mapper

from pigrocrm.core.due_items.models import DueItem, DueItemSettlement
from pigrocrm.core.invoices.models import Invoice

_ENABLED = True


@contextmanager
def suspended() -> Iterator[None]:
    global _ENABLED  # noqa: PLW0603 - a test-wide switch, restored on the way out
    previous, _ENABLED = _ENABLED, False
    try:
        yield
    finally:
        _ENABLED = previous


def install() -> None:
    event.listen(Invoice, "after_insert", _after_insert)
    event.listen(Invoice, "after_update", _after_update)


def _after_insert(mapper: Mapper[Invoice], connection: Connection, invoice: Invoice) -> None:
    _sync(connection, invoice, create=True)


def _after_update(mapper: Mapper[Invoice], connection: Connection, invoice: Invoice) -> None:
    """Only a row that already has its item: an invoice becomes issued by an update when
    `InvoiceService.issue` writes it, and the service writes the item then, with the due date
    it has not computed yet when the first flush of that update happens."""
    _sync(connection, invoice, create=False)


def _sync(connection: Connection, invoice: Invoice, *, create: bool) -> None:
    if not _ENABLED or invoice.type != "invoice" or invoice.status != "issued":
        return
    if invoice.imported_from is not None or invoice.net_receivable == 0:
        return
    item = connection.execute(
        select(DueItem.id, DueItem.state, DueItem.amount, DueItem.due_date).where(
            DueItem.source_kind == "sales_invoice", DueItem.source_id == invoice.id
        )
    ).first()
    if item is None:
        if not create:
            return
        item_id = connection.execute(
            DueItem.__table__.insert()
            .values(
                legal_entity_id=invoice.legal_entity_id,
                direction="in",
                state="expected",
                counterparty_type="organization",
                organization_id=invoice.customer_id,
                amount=invoice.net_receivable,
                currency=invoice.currency or "EUR",
                source_kind="sales_invoice",
                source_id=invoice.id,
                own_date=invoice.due_date,
                due_date=invoice.due_date,
                rule_kind="fixed",
                rule_offset_days=0,
                due_date_estimated=False,
            )
            .returning(DueItem.id)
        ).scalar_one()
        state, amount = "expected", invoice.net_receivable
    else:
        item_id, state, amount, due_date = item
        if due_date != invoice.due_date or amount != invoice.net_receivable:
            # A column the row's owner edited on the row (a service never does: the date is
            # frozen at issue): the item follows it.
            connection.execute(
                update(DueItem)
                .where(DueItem.id == item_id)
                .values(
                    due_date=invoice.due_date,
                    own_date=invoice.due_date,
                    amount=invoice.net_receivable,
                )
            )
            amount = invoice.net_receivable
    paid = invoice.payment_status == "paid"
    if paid and state == "expected":
        settled = connection.execute(_live_total(item_id)).scalar_one()
        connection.execute(
            DueItemSettlement.__table__.insert().values(
                due_item_id=item_id,
                legal_entity_id=invoice.legal_entity_id,
                amount=amount - Decimal(settled),
                settled_on=invoice.payment_date or invoice.issue_date,
                recorded_by_type="system",
            )
        )
        connection.execute(update(DueItem).where(DueItem.id == item_id).values(state="settled"))
    elif not paid and state == "settled":
        connection.execute(
            update(DueItemSettlement)
            .where(
                DueItemSettlement.due_item_id == item_id, DueItemSettlement.reversed_at.is_(None)
            )
            .values(reversed_at=dt.datetime.now(dt.UTC), reversed_by_type="system")
        )
        connection.execute(update(DueItem).where(DueItem.id == item_id).values(state="expected"))


def _live_total(item_id: object):  # type: ignore[no-untyped-def]
    return select(func.coalesce(func.sum(DueItemSettlement.amount), 0)).where(
        DueItemSettlement.due_item_id == item_id, DueItemSettlement.reversed_at.is_(None)
    )
