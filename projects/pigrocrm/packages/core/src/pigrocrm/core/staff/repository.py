"""Reads and writes of `staff_members` and `payroll_settings`, for `StaffService` alone."""

import datetime as dt
from collections.abc import Iterable
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from pigrocrm.core.auth.models import User
from pigrocrm.core.fiscal.models import FiscalProfile
from pigrocrm.core.staff.models import PayrollSettings, StaffMember
from pigrocrm.core.staff.schemas import StaffListQuery


class StaffRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, staff_member_id: UUID) -> StaffMember | None:
        return self.session.get(StaffMember, staff_member_id)

    def add(self, member: StaffMember) -> StaffMember:
        self.session.add(member)
        self.session.flush()
        return member

    def pack_id(self, legal_entity_id: UUID) -> str | None:
        """The fiscal pack the legal entity is on, `None` until it has a fiscal profile."""
        return self.session.execute(
            select(FiscalProfile.pack_id).where(FiscalProfile.legal_entity_id == legal_entity_id)
        ).scalar_one_or_none()

    def user_exists(self, user_id: UUID) -> bool:
        return self.session.get(User, user_id) is not None

    def member_of_user(self, user_id: UUID) -> UUID | None:
        return self.session.execute(
            select(StaffMember.id).where(StaffMember.user_id == user_id)
        ).scalar_one_or_none()

    def pay_day(self, legal_entity_id: UUID) -> PayrollSettings | None:
        return self.session.execute(
            select(PayrollSettings).where(PayrollSettings.legal_entity_id == legal_entity_id)
        ).scalar_one_or_none()

    def pay_days(self, legal_entity_ids: Iterable[UUID]) -> dict[UUID, PayrollSettings]:
        rows = self.session.execute(
            select(PayrollSettings).where(
                PayrollSettings.legal_entity_id.in_(set(legal_entity_ids))
            )
        ).scalars()
        return {row.legal_entity_id: row for row in rows}

    def add_pay_day(self, settings: PayrollSettings) -> PayrollSettings:
        self.session.add(settings)
        self.session.flush()
        return settings

    # `list_rows` stays the last method in this class -- the unconditional project rule (a
    # `def list(...)` would rebind `list` in the class namespace).
    def list_rows(self, query: StaffListQuery, *, today: dt.date) -> list[StaffMember]:
        """In id order (ids are time-ordered), one extra row to tell «there is more» from
        «that was everything»."""
        stmt = select(StaffMember)
        if query.legal_entity_id is not None:
            stmt = stmt.where(StaffMember.legal_entity_id == query.legal_entity_id)
        if query.kind is not None:
            stmt = stmt.where(StaffMember.kind == query.kind)
        if not query.include_ended:
            stmt = stmt.where(or_(StaffMember.end_date.is_(None), StaffMember.end_date >= today))
        if query.cursor is not None:
            stmt = stmt.where(StaffMember.id > query.cursor)
        stmt = stmt.order_by(StaffMember.id.asc()).limit(query.limit + 1)
        return list(self.session.execute(stmt).scalars())
