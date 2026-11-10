"""Who reads and who decides on an expense note (spec 2026-11-09, staff, § 3 and § 4), in one
place, because three services ask it: `ExpenseNoteService`, `PurchaseService` (the purchase an
approval wrote is the note's, so it is as private) and `DueItemService` (paying the note's due
item is reimbursing it).

A note is **own** when the staff member is the acting user, or the acting user typed it in. An
administrator never decides on an own note, except the space's only active administrator, who
is the only one who can; an agent never does. Imports the model and the repository only, so the
services above use it without a cycle.
"""

from uuid import UUID

from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.errors import AgentForbidden, OwnExpenseNote, PermissionDenied
from pigrocrm.core.expense_notes.models import ExpenseNote
from pigrocrm.core.expense_notes.repository import ExpenseNoteRepository
from pigrocrm.core.staff.models import StaffMember


def reads_all_notes(actor: Actor) -> bool:
    """Whether the actor reads and decides on every note in scope: an administrator, and an
    agent only with `full_access`. Everyone else sees their own."""
    try:
        actor.require_admin("read_all_expense_notes")
    except (PermissionDenied, AgentForbidden):
        return False
    return True


def own_staff_member_id(session: Session, actor: Actor) -> UUID | None:
    """The staff member the actor's user is, `None` when they are none."""
    if actor.id is None:
        return None
    member = ExpenseNoteRepository(session).staff_member_of_user(actor.id)
    return None if member is None else member.id


def is_own_note(note: ExpenseNote, member: StaffMember, actor: Actor) -> bool:
    return actor.id is not None and actor.id in (member.user_id, note.submitted_by_id)


def may_decide_on_own(session: Session, actor: Actor) -> bool:
    """The only active administrator, as a person, decides on their own note."""
    return (
        actor.type == "user"
        and actor.id is not None
        and ExpenseNoteRepository(session).only_administrator(actor.id)
    )


def require_may_decide(
    session: Session, note: ExpenseNote, member: StaffMember, actor: Actor, action: str
) -> bool:
    """Raises when the actor may not decide on this note; answers whether the actor is deciding
    on their own note (the only administrator's, which an approval flags)."""
    if not is_own_note(note, member, actor):
        return False
    if may_decide_on_own(session, actor):
        return True
    raise OwnExpenseNote(action)


def require_may_pay_item(session: Session, due_item_id: UUID, actor: Actor) -> None:
    """Paying the due item of an expense note is reimbursing it: the same rule as
    `reimburse_expense_note`, whichever door the payment comes through (a person's mark, a
    bank movement). An item that is no note's is nobody's concern here."""
    repo = ExpenseNoteRepository(session)
    note = repo.of_due_item(due_item_id)
    if note is None:
        return
    member = repo.staff_member(note.staff_member_id)
    if member is not None:
        require_may_decide(session, note, member, actor, "reimburse_expense_note")
