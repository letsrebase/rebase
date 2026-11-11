"""Every issued invoice paid in full, for a test of an estimate (REB-1071).

The flat-rate regime and the ordinary person are taxed on what was collected in the year
(`fiscal/basis.py`), so an invoice nobody paid is no revenue of any year. A test whose fixture
issues invoices and then asks for an estimate collects them with this, on the day each was issued.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.due_items.sales import SALES_INVOICE
from pigrocrm.core.due_items.schemas import SettlementCreate
from pigrocrm.core.due_items.service import DueItemService
from pigrocrm.core.invoices.models import Invoice

ADMIN = Actor(id=None, type="system", role="admin")


def collect_every_issued_invoice(session: Session) -> None:
    service = DueItemService(session)
    for invoice in session.scalars(
        select(Invoice).where(Invoice.type == "invoice", Invoice.status == "issued")
    ):
        item = service.repo.by_source(SALES_INVOICE, invoice.id)
        assert item is not None and invoice.issue_date is not None
        service.settle(
            item.id, SettlementCreate(amount=item.amount, settled_on=invoice.issue_date), ADMIN
        )
