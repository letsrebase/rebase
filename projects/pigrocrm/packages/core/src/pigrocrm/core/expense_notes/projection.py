"""What an expense note reads from its due item: the only place that moves a note between
`approved` and `reimbursed` once the way of paying was chosen.

The due item an approval wrote is settled like any other (a person marks it paid, a bank
movement settles it, an accepted payslip settles it), and `DueItemService` calls this after
each of those, as it projects an invoice's payment state from its item. It imports the model
only, so `due_items` can use it without a cycle.
"""

import datetime as dt

from sqlalchemy import select
from sqlalchemy.orm import Session

from pigrocrm.core.due_items.models import DueItem
from pigrocrm.core.expense_notes.models import ExpenseNote


def project_reimbursement(session: Session, item: DueItem, settled_on: dt.date | None) -> None:
    """The note whose due item this is becomes `reimbursed` when the item is settled in full,
    dated by its latest payment, and goes back to `approved` when a reversal reopens it. A note
    paid before anyone chose its way was paid by transfer: the way and its date are recorded
    then, and stay when a reversal reopens the item. Flushes."""
    note = session.execute(
        select(ExpenseNote).where(ExpenseNote.due_item_id == item.id)
    ).scalar_one_or_none()
    if note is None or item.state == "dropped":
        return
    if item.state == "settled" and note.status == "approved":
        if note.reimbursement_method is None:
            note.reimbursement_method = "transfer"
            note.reimbursement_date = settled_on
        note.status = "reimbursed"
        note.reimbursed_on = settled_on
        session.flush()
    elif item.state != "settled" and note.status == "reimbursed":
        note.status = "approved"
        note.reimbursed_on = None
        session.flush()
