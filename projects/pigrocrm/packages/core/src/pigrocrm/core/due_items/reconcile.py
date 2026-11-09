"""The boot reconciliation of the invoices written by the previous release (spec 2026-11-09,
due items, § 1.1, «Rollout»).

`migrate` runs before the new API is up and the old API keeps serving, so an invoice issued
or imported meanwhile has no due item, and every reader of the receivables would skip it.
`pigrocrm ensure-space-defaults` calls this after the migrations: each issued invoice without
an item gets its item, settled in full when the invoice was marked paid. It touches nothing
that already has an item, so a second boot adds nothing.
"""

from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from pigrocrm.core.due_items.models import DueItem
from pigrocrm.core.due_items.sales import SALES_INVOICE
from pigrocrm.core.due_items.service import DueItemService
from pigrocrm.core.invoices.models import Invoice


def reconcile_sales_invoices(session: Session) -> int:
    """Gives every issued invoice with something to collect its due item. Commits, and
    answers how many items it created. On a session bound to the system actor's scope."""
    service = DueItemService(session)
    missing = (
        select(Invoice)
        .where(
            Invoice.type == "invoice",
            Invoice.status == "issued",
            Invoice.deleted_at.is_(None),
            ~exists().where(DueItem.source_kind == SALES_INVOICE, DueItem.source_id == Invoice.id),
        )
        .order_by(Invoice.issue_date, Invoice.id)
    )
    created = 0
    for invoice in session.scalars(missing).all():
        if service.reconcile_invoice(invoice) is not None:
            created += 1
    session.commit()
    return created
