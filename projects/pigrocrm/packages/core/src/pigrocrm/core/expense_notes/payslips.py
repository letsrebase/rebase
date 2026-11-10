"""The expense notes an accepted payslip pays back (spec 2026-11-09, staff, § 3).

A note whose way was chosen as «in a payslip» waits, approved, for the payslip of its month.
Recording that payslip (`StaffService.record_staff_payslip`, a person's act) settles the note's
own due item and nothing else: the payslip writes the net pay and the withholding, the note's
item was written by its approval, so **no second due item exists** for the reimbursement. The
settlement is dated by the pay day and the projection (`projection.py`) turns the note
`reimbursed`.

A function and not a service method: `StaffService` has asked for an administrator already, and
this is the writer's half of the note, with no door of its own.
"""

import datetime as dt
from uuid import UUID

from sqlalchemy.orm import Session

from pigrocrm.core.activities.service import ActivityService
from pigrocrm.core.actor import Actor
from pigrocrm.core.due_items.service import DueItemService
from pigrocrm.core.expense_notes.repository import ExpenseNoteRepository

ENTITY = "expense_note"


def settle_notes_in_payslip(
    session: Session, staff_member_id: UUID, period: dt.date, actor: Actor
) -> int:
    """Settles the due item of every approved note of the person that the payslip of `period`
    pays (the month chosen for it, or an earlier one), and answers how many. Flushes, never
    commits: it runs in the payslip's own transaction."""
    notes = ExpenseNoteRepository(session).to_pay_with_payslip(staff_member_id, period)
    dues = DueItemService(session)
    activities = ActivityService(session)
    for note in notes:
        assert note.due_item_id is not None and note.reimbursement_date is not None
        dues.settle_remaining(
            note.due_item_id,
            note.reimbursement_date,
            f"Cedolino di {period:%m/%Y}",
            actor,
        )
        activities.record(
            ENTITY,
            note.id,
            "expense_note_reimbursed",
            actor,
            {"method": "payslip", "payroll_month": period.isoformat()},
        )
    return len(notes)
