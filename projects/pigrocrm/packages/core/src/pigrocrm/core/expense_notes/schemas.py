"""Every Pydantic shape of an expense note (spec 2026-11-09, staff, payroll and expenses, § 3).

`max_length` mirrors every `String(n)` and `max_digits`/`decimal_places` every `Numeric(p, s)`,
so an over-long value is a 422 naming the field and never a raw `DataError`. A mileage note
sends the kilometres and the rate the person types (the ACI tables are not verified yet, frame
A5) and never the amount: the service writes the product. An expense note sends the amount
and never the kilometres.
"""

import datetime as dt
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from pigrocrm.core.expense_notes.models import (
    DESCRIPTION_MAX_LENGTH,
    PLACE_MAX_LENGTH,
    REASON_MAX_LENGTH,
)
from pigrocrm.core.validation import SafeStr

NoteKind = Literal["expense", "mileage"]
NoteStatus = Literal["submitted", "approved", "rejected", "reimbursed"]
ReimbursementMethod = Literal["transfer", "payslip"]
# The three tabs of the page: what waits for a decision, what is approved and not yet paid
# back, and what is over.
Bucket = Literal["to_approve", "to_reimburse", "closed"]

_Money = Annotated[Decimal, Field(gt=0, max_digits=12, decimal_places=2)]
_Kilometres = Annotated[Decimal, Field(gt=0, max_digits=8, decimal_places=2)]
_Rate = Annotated[Decimal, Field(gt=0, max_digits=8, decimal_places=4)]


class ExpenseNoteCreate(BaseModel):
    """A note to submit. `staff_member_id` is who paid: left out, the caller's own staff
    member (a user linked to one); an administrator names another's."""

    model_config = ConfigDict(extra="forbid")

    staff_member_id: UUID | None = None
    kind: NoteKind = "expense"
    date: dt.date
    description: SafeStr = Field(min_length=1, max_length=DESCRIPTION_MAX_LENGTH)
    category_id: UUID
    amount: _Money | None = None
    kilometres: _Kilometres | None = None
    rate_per_km: _Rate | None = None
    route_from: SafeStr | None = Field(default=None, min_length=1, max_length=PLACE_MAX_LENGTH)
    route_to: SafeStr | None = Field(default=None, min_length=1, max_length=PLACE_MAX_LENGTH)
    inbox_item_id: UUID | None = None

    @model_validator(mode="after")
    def _by_kind(self) -> Self:
        mileage = (self.kilometres, self.rate_per_km, self.route_from, self.route_to)
        if self.kind == "expense":
            if self.amount is None:
                raise ValueError("una spesa ha l'importo")
            if any(value is not None for value in mileage):
                raise ValueError("chilometri, tariffa e tragitto sono di una nota chilometrica")
            return self
        if self.amount is not None:
            raise ValueError("l'importo di una nota chilometrica e' chilometri per tariffa")
        if any(value is None for value in mileage):
            raise ValueError("una nota chilometrica ha chilometri, tariffa, partenza e arrivo")
        if self.inbox_item_id is not None:
            raise ValueError("una nota chilometrica non ha uno scontrino")
        return self


class ExpenseNoteReject(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: SafeStr = Field(min_length=1, max_length=REASON_MAX_LENGTH)


class ExpenseNoteReimburse(BaseModel):
    """The way of paying an approved note back. By `transfer`, `date` is the day the transfer
    is for, and `paid` says it already left (the date is then a past or present day and the
    due item is settled on it). In a `payslip`, `payroll_month` (any day of it) is the month
    whose payslip pays it, left out: the person's next month with no payslip yet."""

    model_config = ConfigDict(extra="forbid")

    method: ReimbursementMethod
    date: dt.date | None = None
    paid: bool = False
    payroll_month: dt.date | None = None

    @model_validator(mode="after")
    def _by_method(self) -> Self:
        if self.method == "transfer":
            if self.date is None:
                raise ValueError("un bonifico ha la sua data")
            if self.payroll_month is not None:
                raise ValueError("il mese di paga e' di un rimborso in busta paga")
            return self
        if self.date is not None or self.paid:
            raise ValueError("in busta paga la data e' quella della paga e non si segna pagata")
        return self


class ExpenseNoteRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    staff_member_id: UUID
    staff_member_name: str
    legal_entity_id: UUID
    kind: NoteKind
    status: NoteStatus
    date: dt.date
    description: str
    category_id: UUID
    category_name: str
    amount: Decimal
    kilometres: Decimal | None
    rate_per_km: Decimal | None
    route_from: str | None
    route_to: str | None
    inbox_item_id: UUID | None
    # The receipt's file name, until the build draws the file itself (brief, «Note spese»).
    receipt_name: str | None
    submitted_by_id: UUID | None
    submitted_by_type: str
    decided_by_id: UUID | None
    decided_by_type: str | None
    decided_by_name: str | None
    decided_at: datetime | None
    rejection_reason: str | None
    self_approved: bool
    purchase_id: UUID | None
    due_item_id: UUID | None
    reimbursement_method: ReimbursementMethod | None
    reimbursement_date: dt.date | None
    payroll_month: dt.date | None
    reimbursed_on: dt.date | None
    # What the caller may do with it now, so a screen draws the action it can take and a
    # sentence where it cannot (the approver's own note).
    own: bool
    can_decide: bool
    created_at: datetime
    updated_at: datetime


class ExpenseNoteListQuery(BaseModel):
    legal_entity_id: UUID | None = None
    staff_member_id: UUID | None = None
    category_id: UUID | None = None
    status: NoteStatus | None = None
    bucket: Bucket | None = None
    limit: int = Field(default=50, ge=1, le=200)
    cursor: UUID | None = None


class ExpenseNotePage(BaseModel):
    items: list[ExpenseNoteRead]
    next_cursor: UUID | None
    # What the filters select as a whole, apart from the page and the cursor.
    total_count: int
    total_amount: Decimal


class ExpenseNoteSummary(BaseModel):
    """What the page needs before it lists anything: whether the caller is a staff member
    (a user linked to one, which `GET /api/staff` would not tell a non-administrator), the
    count of each tab and the money waiting to be paid back, and whether the caller is the
    space's only administrator (their own note is then approved by them and flagged)."""

    staff_member_id: UUID | None
    staff_member_name: str | None
    staff_member_legal_entity_id: UUID | None
    can_submit: bool
    to_approve: int
    to_reimburse: int
    closed: int
    # The approved notes' total still to be paid back, in euro.
    awaiting_reimbursement: Decimal
    only_administrator: bool
