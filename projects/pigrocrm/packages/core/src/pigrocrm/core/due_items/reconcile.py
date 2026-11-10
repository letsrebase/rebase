"""The reconciliation of the invoices written by the previous release (spec 2026-11-09,
due items, § 1.1, «Rollout»).

`migrate` runs before the new API is up and the old API keeps serving, so an invoice issued,
imported, marked paid or annulled meanwhile has no item, or an item that disagrees with it,
and every reader of the receivables would skip or misread it. This step converges them:

* an issued invoice without an item gets its item, settled in full when it was paid;
* an issued invoice whose item disagrees with its `payment_status` and `payment_date` is
  settled or reversed to match (`DueItemService.repair_invoice`), and an annulled invoice's
  item is dropped.

It touches nothing that already agrees, so a second run changes nothing. It runs from
`pigrocrm ensure-space-defaults` after the migrations, and again from `pigrocrm
reconcile-due-items` (the api container runs it as it starts, after the old API is gone, and
cron may run it): that is what closes the window between the first run and the old API
stopping.
"""

from typing import NamedTuple

from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.orm import Session

from pigrocrm.core.due_items.models import OPEN_STATES, DueItem, DueItemSettlement
from pigrocrm.core.due_items.sales import SALES_INVOICE
from pigrocrm.core.due_items.service import DueItemService
from pigrocrm.core.invoices.models import Invoice


class Reconciled(NamedTuple):
    created: int
    repaired: int


def reconcile_sales_invoices(session: Session) -> Reconciled:
    """Gives every invoice its due item and brings a disagreeing one back in step. Commits,
    and answers how many items it created and how many it repaired. On a session bound to the
    system actor's scope."""
    service = DueItemService(session)
    created = 0
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
    for invoice in session.scalars(missing).all():
        if service.reconcile_invoice(invoice) is not None:
            created += 1

    last_settled = (
        select(func.max(DueItemSettlement.settled_on))
        .where(DueItemSettlement.due_item_id == DueItem.id, DueItemSettlement.reversed_at.is_(None))
        .correlate(DueItem)
        .scalar_subquery()
    )
    paid = Invoice.payment_status == "paid"
    disagrees = or_(
        # Annulled with the item still owed or settled.
        and_(Invoice.status == "cancelled", DueItem.state.in_((*OPEN_STATES, "settled"))),
        and_(
            Invoice.status == "issued",
            DueItem.state != "dropped",
            or_(
                and_(paid, DueItem.state != "settled"),
                and_(~paid, DueItem.state == "settled"),
                and_(
                    paid,
                    last_settled != func.coalesce(Invoice.payment_date, Invoice.issue_date),
                ),
            ),
        ),
    )
    stale = (
        select(Invoice)
        .join(DueItem, and_(DueItem.source_kind == SALES_INVOICE, DueItem.source_id == Invoice.id))
        .where(Invoice.type == "invoice", Invoice.deleted_at.is_(None), disagrees)
        .order_by(Invoice.issue_date, Invoice.id)
    )
    repaired = sum(1 for invoice in session.scalars(stale).all() if service.repair_invoice(invoice))
    session.commit()
    return Reconciled(created, repaired)
