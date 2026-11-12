"""Reads and writes of `staff_members` and `payroll_settings`, for `StaffService` alone."""

import datetime as dt
from collections.abc import Iterable
from uuid import UUID

from sqlalchemy import ColumnElement, or_, select
from sqlalchemy.orm import Session

from pigrocrm.core.auth.models import User
from pigrocrm.core.fiscal.models import FiscalProfile
from pigrocrm.core.legal_entities.models import LegalEntitySite
from pigrocrm.core.proposals.models import Proposal
from pigrocrm.core.staff.models import (
    PayrollMonthClosure,
    PayrollSettings,
    StaffMember,
    StaffPayslip,
)
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

    def has_site(self, legal_entity_id: UUID, site_id: UUID) -> bool:
        """Whether `site_id` is a site of the legal entity."""
        return (
            self.session.execute(
                select(LegalEntitySite.id).where(
                    LegalEntitySite.id == site_id,
                    LegalEntitySite.legal_entity_id == legal_entity_id,
                )
            ).first()
            is not None
        )

    def pack_id(self, legal_entity_id: UUID) -> str | None:
        """The fiscal pack the legal entity is on, `None` until it has a fiscal profile."""
        return self.session.execute(
            select(FiscalProfile.pack_id).where(FiscalProfile.legal_entity_id == legal_entity_id)
        ).scalar_one_or_none()

    def pack(self, legal_entity_id: UUID) -> tuple[str, str] | None:
        """The pack id and version the legal entity is on, `None` until it has a profile."""
        row = self.session.execute(
            select(FiscalProfile.pack_id, FiscalProfile.pack_version).where(
                FiscalProfile.legal_entity_id == legal_entity_id
            )
        ).one_or_none()
        return None if row is None else (row[0], row[1])

    def members_of(self, legal_entity_id: UUID | None) -> list[StaffMember]:
        """Every staff member, ended ones too, of one legal entity or of all the caller sees."""
        stmt = select(StaffMember).order_by(StaffMember.name.asc(), StaffMember.id.asc())
        if legal_entity_id is not None:
            stmt = stmt.where(StaffMember.legal_entity_id == legal_entity_id)
        return list(self.session.execute(stmt).scalars())

    def payslips_of(self, member_ids: Iterable[UUID]) -> dict[UUID, list[StaffPayslip]]:
        """The accepted payslips of each member, the earliest period first."""
        out: dict[UUID, list[StaffPayslip]] = {}
        rows = self.session.execute(
            select(StaffPayslip)
            .where(StaffPayslip.staff_member_id.in_(set(member_ids)))
            .order_by(StaffPayslip.period.asc())
        ).scalars()
        for row in rows:
            out.setdefault(row.staff_member_id, []).append(row)
        return out

    def payslip(self, staff_member_id: UUID, period: dt.date) -> StaffPayslip | None:
        return self.session.execute(
            select(StaffPayslip).where(
                StaffPayslip.staff_member_id == staff_member_id, StaffPayslip.period == period
            )
        ).scalar_one_or_none()

    def payslip_locked(self, payslip_id: UUID) -> StaffPayslip | None:
        """The payslip under its row lock, read afresh: two corrections of one payslip queue,
        and the second sees what the first wrote."""
        return self.session.execute(
            select(StaffPayslip)
            .where(StaffPayslip.id == payslip_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).scalar_one_or_none()

    def add_payslip(self, payslip: StaffPayslip) -> StaffPayslip:
        self.session.add(payslip)
        self.session.flush()
        return payslip

    def pending_payslip_proposals(
        self, legal_entity_id: UUID, month: dt.date, visible: ColumnElement[bool]
    ) -> dict[UUID, Proposal]:
        """The payslip proposals waiting for a person for `month`, by the staff member they are
        for (the oldest first, so a second one for the same person does not hide the first).
        Only members of the legal entity count: a proposal's `staff_member_id` is checked against
        the entity's own people when it is made, and read under the row policy here."""
        ids = {
            str(row)
            for row in self.session.execute(
                select(StaffMember.id).where(StaffMember.legal_entity_id == legal_entity_id)
            ).scalars()
        }
        out: dict[UUID, Proposal] = {}
        if not ids:
            return out
        rows = self.session.execute(
            select(Proposal)
            .where(
                Proposal.target_type == "payslip",
                Proposal.status == "pending",
                Proposal.proposed_fields["period"].astext == month.isoformat(),
                Proposal.proposed_fields["staff_member_id"].astext.in_(ids),
                visible,
            )
            .order_by(Proposal.created_at.asc(), Proposal.id.asc())
        ).scalars()
        for row in rows:
            out.setdefault(UUID(str(row.proposed_fields["staff_member_id"])), row)
        return out

    def closure(self, legal_entity_id: UUID, month: dt.date) -> PayrollMonthClosure | None:
        return self.session.execute(
            select(PayrollMonthClosure).where(
                PayrollMonthClosure.legal_entity_id == legal_entity_id,
                PayrollMonthClosure.month == month,
            )
        ).scalar_one_or_none()

    def closed_months(
        self, legal_entity_id: UUID | None
    ) -> dict[tuple[UUID, dt.date], PayrollMonthClosure]:
        """Every closed month of one legal entity, or of all the caller sees."""
        stmt = select(PayrollMonthClosure)
        if legal_entity_id is not None:
            stmt = stmt.where(PayrollMonthClosure.legal_entity_id == legal_entity_id)
        return {
            (row.legal_entity_id, row.month): row for row in self.session.execute(stmt).scalars()
        }

    def add_closure(self, closure: PayrollMonthClosure) -> PayrollMonthClosure:
        self.session.add(closure)
        self.session.flush()
        return closure

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
