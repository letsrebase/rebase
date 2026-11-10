from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from pydantic import Field

from pigrocrm.core.expense_notes.schemas import (
    Bucket,
    ExpenseNoteCreate,
    ExpenseNoteListQuery,
    ExpenseNotePage,
    ExpenseNoteRead,
    ExpenseNoteReimburse,
    ExpenseNoteReject,
    ExpenseNoteSummary,
    NoteStatus,
)
from pigrocrm.core.expense_notes.service import ExpenseNoteService
from pigrocrm_api.deps import ActorDep, SessionDep, get_actor, module_guard
from pigrocrm_api.errors import PROBLEM_RESPONSES
from pigrocrm_api.query_params import LEGAL_ENTITY_QUERY_DESCRIPTION, KeysetParams

# An expense note becomes a purchase, so the surface needs both modules (design § 6): with
# `purchases` off «Note spese» is gone and «Persone dell'azienda» stays. The role rules are the
# service's: a staff member reads and submits their own, an administrator reads and decides.
router = APIRouter(
    prefix="/api/expense-notes",
    tags=["expense notes"],
    responses=PROBLEM_RESPONSES,
    dependencies=[Depends(get_actor), module_guard("staff", "purchases")],
)


class ExpenseNoteListParams(KeysetParams):
    legal_entity_id: UUID | None = Field(default=None, description=LEGAL_ENTITY_QUERY_DESCRIPTION)
    staff_member_id: UUID | None = Field(
        default=None, description="Only this staff member's notes (an administrator's filter)"
    )
    category_id: UUID | None = None
    status: NoteStatus | None = None
    bucket: Bucket | None = Field(
        default=None,
        description="`to_approve` (submitted), `to_reimburse` (approved) or `closed` "
        "(rejected or reimbursed)",
    )


@router.post("", response_model=ExpenseNoteRead, status_code=status.HTTP_201_CREATED)
def submit_expense_note(
    data: ExpenseNoteCreate, session: SessionDep, actor: ActorDep
) -> ExpenseNoteRead:
    """Submits an expense note: a receipt with its date, amount and category, or kilometres at a
    rate the person types. `staff_member_id` is who paid: left out, the caller's own staff
    member; an administrator names another's, a person who is not a user included. The
    receipt is an inbox item uploaded first (`POST /api/inbox/upload?read=false`)."""
    return ExpenseNoteService(session).submit_expense_note(data, actor)


@router.get("", response_model=ExpenseNotePage)
def list_expense_notes(
    session: SessionDep, actor: ActorDep, params: Annotated[ExpenseNoteListParams, Query()]
) -> ExpenseNotePage:
    """A staff member's own notes, or an administrator's every note in scope, newest first."""
    return ExpenseNoteService(session).list_expense_notes(
        params.to_query(ExpenseNoteListQuery), actor
    )


@router.get("/summary", response_model=ExpenseNoteSummary)
def summarize_expense_notes(session: SessionDep, actor: ActorDep) -> ExpenseNoteSummary:
    """Who the caller is among the staff, the count of each tab and the money still to be paid
    back. Declared before `/{note_id}`, which would take `summary` for an id."""
    return ExpenseNoteService(session).summarize_expense_notes(actor)


@router.get("/{note_id}", response_model=ExpenseNoteRead)
def get_expense_note(note_id: UUID, session: SessionDep, actor: ActorDep) -> ExpenseNoteRead:
    return ExpenseNoteService(session).get_expense_note(note_id, actor)


@router.post("/{note_id}/approve", response_model=ExpenseNoteRead)
def approve_expense_note(note_id: UUID, session: SessionDep, actor: ActorDep) -> ExpenseNoteRead:
    """Approves a submitted note: it becomes a purchase of kind `expense_note` and writes the
    due item to the person. An administrator other than the note's own, except the space's only
    one, whose approval is flagged `self_approved`."""
    return ExpenseNoteService(session).approve_expense_note(note_id, actor)


@router.post("/{note_id}/reject", response_model=ExpenseNoteRead)
def reject_expense_note(
    note_id: UUID, data: ExpenseNoteReject, session: SessionDep, actor: ActorDep
) -> ExpenseNoteRead:
    """Rejects a submitted note with the reason the person reads. Final: a corrected note is a
    new note."""
    return ExpenseNoteService(session).reject_expense_note(note_id, data, actor)


@router.post("/{note_id}/reimburse", response_model=ExpenseNoteRead)
def reimburse_expense_note(
    note_id: UUID, data: ExpenseNoteReimburse, session: SessionDep, actor: ActorDep
) -> ExpenseNoteRead:
    """Chooses how an approved note is paid back: a bank transfer on a date (`paid` when it
    already left) or the payslip of a month, which settles the note's due item and adds no
    second one. Reimbursing happens once."""
    return ExpenseNoteService(session).reimburse_expense_note(note_id, data, actor)
