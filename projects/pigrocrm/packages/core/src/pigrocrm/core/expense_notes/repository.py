"""Reads and writes of `expense_notes`, for `ExpenseNoteService` and the projection alone.
Never commits: the service method is the one transaction."""

import datetime as dt
from collections.abc import Iterable
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from pigrocrm.core.auth.models import User
from pigrocrm.core.expense_notes.models import ExpenseNote
from pigrocrm.core.expense_notes.schemas import ExpenseNoteListQuery
from pigrocrm.core.inbox.models import InboxItem
from pigrocrm.core.money import ZERO_MONEY
from pigrocrm.core.staff.models import StaffMember
from pigrocrm.core.timetracking.models import CostCategory

# What each tab of the page holds.
BUCKET_STATUSES: dict[str, tuple[str, ...]] = {
    "to_approve": ("submitted",),
    "to_reimburse": ("approved",),
    "closed": ("rejected", "reimbursed"),
}
# Money a staff member has sent and not received yet.
AWAITING_STATUSES: tuple[str, ...] = ("submitted", "approved")


class ExpenseNoteRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, note_id: UUID) -> ExpenseNote | None:
        return self.session.get(ExpenseNote, note_id)

    def get_locked(self, note_id: UUID) -> ExpenseNote | None:
        """The note under its row lock, read afresh: two decisions on one note queue, and the
        second sees what the first wrote."""
        return self.session.execute(
            select(ExpenseNote)
            .where(ExpenseNote.id == note_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).scalar_one_or_none()

    def add(self, note: ExpenseNote) -> ExpenseNote:
        self.session.add(note)
        self.session.flush()
        return note

    def staff_member(self, staff_member_id: UUID) -> StaffMember | None:
        return self.session.get(StaffMember, staff_member_id)

    def staff_member_of_user(self, user_id: UUID) -> StaffMember | None:
        return self.session.execute(
            select(StaffMember).where(StaffMember.user_id == user_id)
        ).scalar_one_or_none()

    def only_administrator(self, user_id: UUID) -> bool:
        """Whether this user is the space's one active administrator."""
        ids = self.session.execute(
            select(User.id).where(User.role == "admin", User.active.is_(True))
        ).scalars()
        return list(ids) == [user_id]

    def noted_item(self, inbox_item_id: UUID) -> ExpenseNote | None:
        """The live note a receipt already backs."""
        return self.session.execute(
            select(ExpenseNote).where(
                ExpenseNote.inbox_item_id == inbox_item_id, ExpenseNote.status != "rejected"
            )
        ).scalar_one_or_none()

    def of_due_item(self, due_item_id: UUID) -> ExpenseNote | None:
        return self.session.execute(
            select(ExpenseNote).where(ExpenseNote.due_item_id == due_item_id)
        ).scalar_one_or_none()

    def to_pay_with_payslip(self, staff_member_id: UUID, period: dt.date) -> list[ExpenseNote]:
        """The approved notes of a person that a payslip of `period` pays: the ones whose chosen
        month is that month or an earlier one nobody recorded a payslip for."""
        return list(
            self.session.execute(
                select(ExpenseNote)
                .where(
                    ExpenseNote.staff_member_id == staff_member_id,
                    ExpenseNote.status == "approved",
                    ExpenseNote.reimbursement_method == "payslip",
                    ExpenseNote.payroll_month <= period,
                )
                .order_by(ExpenseNote.id.asc())
                .with_for_update()
                .execution_options(populate_existing=True)
            ).scalars()
        )

    def names(
        self,
        member_ids: Iterable[UUID],
        category_ids: Iterable[UUID],
        item_ids: Iterable[UUID],
        user_ids: Iterable[UUID],
    ) -> tuple[dict[UUID, str], dict[UUID, str], dict[UUID, str], dict[UUID, str]]:
        """The staff members', categories', receipts' and deciders' names, one query each."""
        members = {
            id_: name
            for id_, name in self.session.execute(
                select(StaffMember.id, StaffMember.name).where(StaffMember.id.in_(set(member_ids)))
            )
        }
        categories = {
            id_: name
            for id_, name in self.session.execute(
                select(CostCategory.id, CostCategory.name).where(
                    CostCategory.id.in_(set(category_ids))
                )
            )
        }
        items = {
            id_: name
            for id_, name in self.session.execute(
                select(InboxItem.id, InboxItem.original_name).where(InboxItem.id.in_(set(item_ids)))
            )
        }
        users = {
            id_: name
            for id_, name in self.session.execute(
                select(User.id, User.name).where(User.id.in_(set(user_ids)))
            )
        }
        return members, categories, items, users

    @staticmethod
    def _filtered(query: ExpenseNoteListQuery, own_member_id: UUID | None) -> Select[Any]:
        stmt = select(ExpenseNote)
        if own_member_id is not None:
            stmt = stmt.where(ExpenseNote.staff_member_id == own_member_id)
        if query.legal_entity_id is not None:
            stmt = stmt.where(ExpenseNote.legal_entity_id == query.legal_entity_id)
        if query.staff_member_id is not None:
            stmt = stmt.where(ExpenseNote.staff_member_id == query.staff_member_id)
        if query.category_id is not None:
            stmt = stmt.where(ExpenseNote.category_id == query.category_id)
        if query.status is not None:
            stmt = stmt.where(ExpenseNote.status == query.status)
        if query.bucket is not None:
            stmt = stmt.where(ExpenseNote.status.in_(BUCKET_STATUSES[query.bucket]))
        return stmt

    def count_and_total(
        self, query: ExpenseNoteListQuery, own_member_id: UUID | None
    ) -> tuple[int, Decimal]:
        subquery = self._filtered(query, own_member_id).subquery()
        count, total = self.session.execute(
            select(func.count(), func.coalesce(func.sum(subquery.c.amount), ZERO_MONEY))
        ).one()
        return int(count), Decimal(total)

    def bucket_counts(self, own_member_id: UUID | None) -> dict[str, int]:
        stmt = select(ExpenseNote.status, func.count()).group_by(ExpenseNote.status)
        if own_member_id is not None:
            stmt = stmt.where(ExpenseNote.staff_member_id == own_member_id)
        per_status = {status: int(count) for status, count in self.session.execute(stmt)}
        return {
            bucket: sum(per_status.get(status, 0) for status in statuses)
            for bucket, statuses in BUCKET_STATUSES.items()
        }

    def awaiting_total(self, own_member_id: UUID | None) -> Decimal:
        stmt = select(func.coalesce(func.sum(ExpenseNote.amount), ZERO_MONEY)).where(
            ExpenseNote.status.in_(AWAITING_STATUSES)
        )
        if own_member_id is not None:
            stmt = stmt.where(ExpenseNote.staff_member_id == own_member_id)
        return Decimal(self.session.execute(stmt).scalar_one())

    # `list_rows` stays the last method in this class -- the unconditional project rule (a
    # `def list(...)` would rebind `list` in the class namespace).
    def list_rows(
        self, query: ExpenseNoteListQuery, own_member_id: UUID | None
    ) -> list[ExpenseNote]:
        """Newest first by id (ids are time-ordered), one extra row to tell «there is more» from
        «that was everything». `own_member_id` restricts the list to one staff member's."""
        stmt = self._filtered(query, own_member_id)
        if query.cursor is not None:
            stmt = stmt.where(ExpenseNote.id < query.cursor)
        stmt = stmt.order_by(ExpenseNote.id.desc()).limit(query.limit + 1)
        return list(self.session.execute(stmt).scalars())
