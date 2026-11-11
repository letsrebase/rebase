"""Expense notes: what a staff member spent for the company and asks to be paid back (spec
2026-11-09, staff, payroll and expenses, § 3 and § 4; brief 2026-11-10, «Note spese»).

A note belongs to a **staff member**, who is the person that paid. It is a receipt (an inbox
item, when there is one), a date, an amount and a cost category, or kilometres at a rate the
person types: the amount of a `mileage` note is the product of the two, written by the service
and never sent. A note that was not sent does not exist (there is no draft); it starts
`submitted`, and an administrator who is not its author either approves or rejects it:

- `submitted`: waits for an administrator;
- `rejected`: final, with the approver's reason; a corrected note is a new note;
- `approved`: the note became a **purchase** of kind `expense_note` (`purchase_id`), which owns
  the figure for cost, VAT and export, and wrote the one outgoing **due item** to the person
  (`due_item_id`), dated the day it was approved until the way of paying is chosen;
- `reimbursed`: the money reached the person. By `transfer` that is the due item's settlement,
  made like any other (a person's mark, or a bank movement); in a `payslip` it is the
  accepted payslip of `payroll_month`, which settles the same due item, so no second due item
  exists. `reimbursed_on` is the day it was settled.

Choosing the way (`reimbursement_method`, with its date) is its own act, after the approval: the
approval judges the expense, the reimbursement decides when money leaves.

The table carries the plain `legal_entity_scope` policy of every money table; who may read which
note (the staff member's own, an administrator's all) is a check in `ExpenseNoteService`.
"""

import datetime as dt
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from pigrocrm.core.db import Base, PrimaryKeyMixin, TimestampMixin

KINDS: tuple[str, ...] = ("expense", "mileage")
STATUSES: tuple[str, ...] = ("submitted", "approved", "rejected", "reimbursed")
METHODS: tuple[str, ...] = ("transfer", "payslip")

DESCRIPTION_MAX_LENGTH = 500
PLACE_MAX_LENGTH = 120
REASON_MAX_LENGTH = 255


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(value) for value in values)})"


class ExpenseNote(Base, PrimaryKeyMixin, TimestampMixin):
    __tablename__ = "expense_notes"
    __table_args__ = (
        CheckConstraint(_in("kind", KINDS), name="ck_expense_notes_kind"),
        CheckConstraint(_in("status", STATUSES), name="ck_expense_notes_status"),
        CheckConstraint("amount > 0", name="ck_expense_notes_amount"),
        # Kilometres and the rate go together and only on a mileage note, which has no receipt.
        CheckConstraint(
            "(kind = 'mileage' AND kilometres IS NOT NULL AND kilometres > 0 "
            "AND rate_per_km IS NOT NULL AND rate_per_km > 0 AND route_from IS NOT NULL "
            "AND route_to IS NOT NULL AND inbox_item_id IS NULL) OR "
            "(kind = 'expense' AND kilometres IS NULL AND rate_per_km IS NULL "
            "AND route_from IS NULL AND route_to IS NULL)",
            name="ck_expense_notes_mileage",
        ),
        # A decision names who took it and when; a refusal says why; an approval has the
        # purchase and the due item it wrote.
        CheckConstraint(
            "status = 'submitted' OR (decided_by_type IS NOT NULL AND decided_at IS NOT NULL)",
            name="ck_expense_notes_decided",
        ),
        CheckConstraint(
            "(status = 'rejected') = (rejection_reason IS NOT NULL)",
            name="ck_expense_notes_rejection_reason",
        ),
        CheckConstraint(
            "(status IN ('approved', 'reimbursed')) = (purchase_id IS NOT NULL AND due_item_id "
            "IS NOT NULL)",
            name="ck_expense_notes_approved_records",
        ),
        # The way of paying is chosen on an approved note and kept once it is paid; a payslip
        # names its month, a transfer its date.
        CheckConstraint(
            f"reimbursement_method IS NULL OR {_in('reimbursement_method', METHODS)}",
            name="ck_expense_notes_method",
        ),
        CheckConstraint(
            "reimbursement_method IS NULL OR status IN ('approved', 'reimbursed')",
            name="ck_expense_notes_method_status",
        ),
        CheckConstraint(
            "(reimbursement_method IS NULL AND reimbursement_date IS NULL "
            "AND payroll_month IS NULL) OR "
            "(reimbursement_method = 'transfer' AND reimbursement_date IS NOT NULL "
            "AND payroll_month IS NULL) OR "
            "(reimbursement_method = 'payslip' AND reimbursement_date IS NOT NULL "
            "AND payroll_month IS NOT NULL AND extract(day FROM payroll_month) = 1)",
            name="ck_expense_notes_way",
        ),
        CheckConstraint(
            "(status = 'reimbursed') = (reimbursed_on IS NOT NULL) "
            "AND (status <> 'reimbursed' OR reimbursement_method IS NOT NULL)",
            name="ck_expense_notes_reimbursed",
        ),
        CheckConstraint("NOT self_approved OR status <> 'submitted'", name="ck_expense_notes_self"),
        Index("ix_expense_notes_legal_entity_status", "legal_entity_id", "status"),
        Index("ix_expense_notes_staff_member_id", "staff_member_id"),
        Index("ix_expense_notes_purchase_id", "purchase_id"),
        Index("ix_expense_notes_due_item_id", "due_item_id"),
        # A receipt backs one live note: a rejected note frees it, since its correction is a
        # new note.
        Index(
            "uq_expense_notes_inbox_item_id",
            "inbox_item_id",
            unique=True,
            postgresql_where=text("inbox_item_id IS NOT NULL AND status <> 'rejected'"),
        ),
    )

    staff_member_id: Mapped[UUID] = mapped_column(ForeignKey("staff_members.id"), nullable=False)
    legal_entity_id: Mapped[UUID] = mapped_column(ForeignKey("legal_entities.id"), nullable=False)
    kind: Mapped[str] = mapped_column(String(10), nullable=False, default="expense")
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="submitted")
    date: Mapped[dt.date] = mapped_column(Date, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    category_id: Mapped[UUID] = mapped_column(ForeignKey("cost_categories.id"), nullable=False)
    # In euro, VAT included, as the receipt says; for a mileage note kilometres times the rate.
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    kilometres: Mapped[Decimal | None] = mapped_column(Numeric(8, 2), default=None)
    # The rate per kilometre the person types, until the ACI tables are verified (frame A5).
    rate_per_km: Mapped[Decimal | None] = mapped_column(Numeric(8, 4), default=None)
    route_from: Mapped[str | None] = mapped_column(String(PLACE_MAX_LENGTH), default=None)
    route_to: Mapped[str | None] = mapped_column(String(PLACE_MAX_LENGTH), default=None)
    inbox_item_id: Mapped[UUID | None] = mapped_column(ForeignKey("inbox_items.id"), default=None)
    # Who sent it, as the timeline stores an actor: a reference, never a foreign key.
    submitted_by_id: Mapped[UUID | None] = mapped_column(default=None)
    submitted_by_type: Mapped[str] = mapped_column(String(10), nullable=False)
    decided_by_id: Mapped[UUID | None] = mapped_column(default=None)
    decided_by_type: Mapped[str | None] = mapped_column(String(10), default=None)
    decided_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    rejection_reason: Mapped[str | None] = mapped_column(String(REASON_MAX_LENGTH), default=None)
    # The only administrator approved their own note (spec § 3): the export says so (C8).
    self_approved: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    purchase_id: Mapped[UUID | None] = mapped_column(ForeignKey("purchases.id"), default=None)
    due_item_id: Mapped[UUID | None] = mapped_column(ForeignKey("due_items.id"), default=None)
    reimbursement_method: Mapped[str | None] = mapped_column(String(10), default=None)
    # The day the transfer is for, or the pay day of the payslip's month.
    reimbursement_date: Mapped[dt.date | None] = mapped_column(Date, default=None)
    # The first day of the payroll month whose payslip pays the note.
    payroll_month: Mapped[dt.date | None] = mapped_column(Date, default=None)
    reimbursed_on: Mapped[dt.date | None] = mapped_column(Date, default=None)
