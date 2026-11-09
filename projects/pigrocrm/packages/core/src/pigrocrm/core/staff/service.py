"""The only writer of `staff_members` and `payroll_settings` (spec 2026-11-09, staff, payroll
and expenses, § 1 and § 4).

**Pay is the most personal data the product holds.** Every method here asks for an
administrator first, before it looks anything up, so a collaborator or a readonly member
learns neither pay nor whether a staff member exists; an agent credential passes only with
`full_access` for the four tools of the design's table (`create_staff_member`,
`update_staff_member`, `get_staff_member`, `list_staff`) and never for `set_payroll_pay_day`
(`agent_actions.py`). Which legal entities an administrator reaches is the row policy's
business (`legal_entity_scope`, the same as every money table): outside its scope a row is
simply not there.

**Which legal entities may have staff.** The fiscal pack decides (`EMPLOYEE_PACKS`,
`DIRECTOR_PACKS`): a legal entity with no fiscal profile cannot be classified and takes
nobody. Checked on creation; a change of pack later leaves the people already recorded.

Method names are unique across the services on purpose: `FORBIDDEN_SERVICE_CALLS` of the MCP
ban test finds the privileged tools by name, and a bare `get` or `list` would match every
other service.
"""

from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from pigrocrm.core.activities.service import ActivityService
from pigrocrm.core.actor import Actor
from pigrocrm.core.db import today_local
from pigrocrm.core.errors import Conflict, NotFound, ValidationFailed
from pigrocrm.core.legal_entities.models import LegalEntity
from pigrocrm.core.schemas import supplied_changes
from pigrocrm.core.staff.models import (
    DIRECTOR_PACKS,
    EMPLOYEE_PACKS,
    PayrollSettings,
    StaffMember,
)
from pigrocrm.core.staff.repository import StaffRepository
from pigrocrm.core.staff.schemas import (
    DirectorCreate,
    EffectivePayDay,
    EmployeeCreate,
    PayDayRead,
    PayDayWrite,
    StaffListQuery,
    StaffMemberRead,
    StaffMemberUpdate,
    StaffPage,
)

ENTITY = "staff_member"

EMPLOYEE_ONLY = frozenset(
    {
        "collective_agreement",
        "contract_level",
        "gross_annual_pay",
        "monthly_payments",
        "part_time_percent",
        "employer_inps_rate",
        "employer_inail_rate",
    }
)
DIRECTOR_ONLY = frozenset(
    {"annual_fee", "fee_payment", "accrues_tfm", "tfm_annual_amount", "inps_company_share"}
)
# What an update cannot empty: the pay inputs the kind needs, and the columns that are
# required whatever the kind.
REQUIRED: dict[str, frozenset[str]] = {
    "employee": frozenset({"gross_annual_pay", "monthly_payments", "part_time_percent"}),
    "director": frozenset({"annual_fee", "fee_payment", "accrues_tfm"}),
}
ALWAYS_REQUIRED = frozenset({"name", "start_date"})


class StaffService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.repo = StaffRepository(session)
        self.activities = ActivityService(session)

    def create_staff_member(
        self, data: EmployeeCreate | DirectorCreate, actor: Actor
    ) -> StaffMemberRead:
        actor.require_admin("create_staff_member")
        self._require_may_have(data.legal_entity_id, data.kind)
        if data.user_id is not None:
            self._require_free_user(data.user_id, None)
        member = self.repo.add(StaffMember(**data.model_dump()))
        self.activities.record(
            "legal_entity",
            member.legal_entity_id,
            "staff_member_created",
            actor,
            {"staff_member_id": str(member.id), "kind": member.kind},
        )
        self.session.commit()
        self.session.refresh(member)
        return self._read(member)

    def update_staff_member(
        self, staff_member_id: UUID, data: StaffMemberUpdate, actor: Actor
    ) -> StaffMemberRead:
        actor.require_admin("update_staff_member")
        member = self._require(staff_member_id)
        changes = supplied_changes(data)
        foreign = (DIRECTOR_ONLY if member.kind == "employee" else EMPLOYEE_ONLY) & changes.keys()
        if foreign:
            field = sorted(foreign)[0]
            raise ValidationFailed(
                ENTITY,
                field,
                "il campo non riguarda "
                + ("un dipendente" if member.kind == "employee" else "un amministratore"),
                expected="un campo del suo tipo",
            )
        required = REQUIRED[member.kind] | ALWAYS_REQUIRED
        for field in sorted(required & changes.keys()):
            if changes[field] is None:
                raise ValidationFailed(
                    ENTITY, field, "il campo non può essere svuotato", expected="un valore"
                )
        if changes.get("user_id") is not None:
            self._require_free_user(changes["user_id"], member.id)
        self._require_consistent(member, changes)
        for key, value in changes.items():
            setattr(member, key, value)
        self.session.flush()
        self.activities.record(
            "legal_entity",
            member.legal_entity_id,
            "staff_member_updated",
            actor,
            {"staff_member_id": str(member.id), "changed": sorted(changes)},
        )
        self.session.commit()
        self.session.refresh(member)
        return self._read(member)

    def get_staff_member(self, staff_member_id: UUID, actor: Actor) -> StaffMemberRead:
        actor.require_admin("get_staff_member")
        return self._read(self._require(staff_member_id))

    def get_payroll_pay_day(self, legal_entity_id: UUID, actor: Actor) -> PayDayRead:
        """The legal entity's own pay day; read with the staff list's permission."""
        actor.require_admin("list_staff")
        self._require_legal_entity(legal_entity_id)
        found = self.repo.pay_day(legal_entity_id)
        return PayDayRead(
            legal_entity_id=legal_entity_id,
            pay_day=found.pay_day if found else None,
            pay_month_offset=found.pay_month_offset if found else None,
        )

    def set_payroll_pay_day(
        self, legal_entity_id: UUID, data: PayDayWrite, actor: Actor
    ) -> PayDayRead:
        """Sets the legal entity's pay day, which every staff member without an override
        follows. Only a person does it: no agent credential, whatever it carries."""
        actor.require_admin("set_payroll_pay_day")
        self._require_may_have(legal_entity_id, "employee")
        found = self.repo.pay_day(legal_entity_id)
        if found is None:
            found = self.repo.add_pay_day(
                PayrollSettings(
                    legal_entity_id=legal_entity_id,
                    pay_day=data.pay_day,
                    pay_month_offset=data.pay_month_offset,
                )
            )
        else:
            found.pay_day = data.pay_day
            found.pay_month_offset = data.pay_month_offset
        self.activities.record(
            "legal_entity", legal_entity_id, "payroll_pay_day_set", actor, {"pay_day": data.pay_day}
        )
        self.session.commit()
        return PayDayRead(
            legal_entity_id=legal_entity_id,
            pay_day=data.pay_day,
            pay_month_offset=data.pay_month_offset,
        )

    # `list_staff` stays the last method in this class -- the unconditional project rule.
    def list_staff(self, query: StaffListQuery, actor: Actor) -> StaffPage:
        actor.require_admin("list_staff")
        rows = self.repo.list_rows(query, today=today_local())
        page = rows[: query.limit]
        defaults = self.repo.pay_days(member.legal_entity_id for member in page)
        return StaffPage(
            items=[self._read(member, defaults) for member in page],
            next_cursor=page[-1].id if len(rows) > query.limit and page else None,
        )

    # ---- internals ------------------------------------------------------------------------

    def _require(self, staff_member_id: UUID) -> StaffMember:
        member = self.repo.get(staff_member_id)
        if member is None:
            raise NotFound(ENTITY, staff_member_id)
        return member

    def _require_legal_entity(self, legal_entity_id: UUID) -> None:
        if self.session.get(LegalEntity, legal_entity_id) is None:
            raise NotFound("legal_entity", legal_entity_id)

    def _require_may_have(self, legal_entity_id: UUID, kind: str) -> None:
        self._require_legal_entity(legal_entity_id)
        pack = self.repo.pack_id(legal_entity_id)
        if pack is None:
            raise ValidationFailed(
                ENTITY,
                "legal_entity_id",
                "l'azienda non ha ancora un profilo fiscale: senza non si sa se può avere "
                "personale",
                expected="un'azienda con il profilo fiscale compilato",
            )
        if kind == "director":
            if pack not in DIRECTOR_PACKS:
                raise ValidationFailed(
                    ENTITY,
                    "kind",
                    "un amministratore con compenso si registra solo su una società di capitali: "
                    "questa azienda non lo è",
                    expected="un'azienda con regime di società di capitali",
                )
        elif pack not in EMPLOYEE_PACKS:
            raise ValidationFailed(
                ENTITY,
                "legal_entity_id",
                "un'azienda estera non può avere dipendenti: non ha le regole della busta "
                "paga italiana",
                expected="un'azienda italiana",
            )

    def _require_free_user(self, user_id: UUID, own_id: UUID | None) -> None:
        if not self.repo.user_exists(user_id):
            raise NotFound("user", user_id)
        owner = self.repo.member_of_user(user_id)
        if owner is not None and owner != own_id:
            raise Conflict(
                ENTITY,
                "questo utente è già collegato a un'altra persona dell'azienda",
                user_id=str(user_id),
            )

    def _require_consistent(self, member: StaffMember, changes: dict[str, Any]) -> None:
        """What the create schemas' own validators say, for the fields an update touches
        together with the ones it leaves."""

        def value(field: str) -> Any:
            return changes[field] if field in changes else getattr(member, field)

        if value("end_date") is not None and value("end_date") < value("start_date"):
            raise ValidationFailed(
                ENTITY, "end_date", "la fine non può precedere l'inizio", expected="dopo l'inizio"
            )
        if (value("pay_day") is None) != (value("pay_month_offset") is None):
            raise ValidationFailed(
                ENTITY,
                "pay_day",
                "il giorno di paga e il mese di paga vanno indicati insieme",
                expected="entrambi, o nessuno dei due per seguire l'azienda",
            )
        if member.kind == "director" and value("accrues_tfm") != (
            value("tfm_annual_amount") is not None
        ):
            raise ValidationFailed(
                ENTITY,
                "tfm_annual_amount",
                "l'importo annuo del TFM si indica se e solo se matura un TFM",
                expected="l'importo con il TFM acceso, nessuno con il TFM spento",
            )

    def _read(
        self, member: StaffMember, defaults: dict[UUID, PayrollSettings] | None = None
    ) -> StaffMemberRead:
        read = StaffMemberRead.model_validate(member)
        if member.pay_day is not None and member.pay_month_offset is not None:
            read.effective_pay_day = EffectivePayDay(
                pay_day=member.pay_day,
                pay_month_offset=member.pay_month_offset,
                source="staff_member",
            )
            return read
        if defaults is not None:
            found = defaults.get(member.legal_entity_id)
        else:
            found = self.repo.pay_day(member.legal_entity_id)
        if found is not None:
            read.effective_pay_day = EffectivePayDay(
                pay_day=found.pay_day,
                pay_month_offset=found.pay_month_offset,
                source="legal_entity",
            )
        return read


__all__ = ["ENTITY", "StaffService"]
