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

import datetime as dt
from collections.abc import Callable
from typing import Any
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from pigrocrm.core.activities.service import ActivityService
from pigrocrm.core.actor import Actor
from pigrocrm.core.auth.models import User
from pigrocrm.core.db import today_local
from pigrocrm.core.errors import Conflict, NotFound, ValidationFailed
from pigrocrm.core.expense_notes.payslips import settle_notes_in_payslip
from pigrocrm.core.legal_entities.models import LegalEntity
from pigrocrm.core.money import round_money
from pigrocrm.core.schemas import supplied_changes
from pigrocrm.core.staff.estimate import add_months, first_of
from pigrocrm.core.staff.models import (
    DIRECTOR_PACKS,
    EMPLOYEE_PACKS,
    PayrollMonthClosure,
    PayrollSettings,
    StaffMember,
    StaffPayslip,
)
from pigrocrm.core.staff.payroll import PayrollService
from pigrocrm.core.staff.repository import StaffRepository
from pigrocrm.core.staff.schemas import (
    DirectorCreate,
    EffectivePayDay,
    EmployeeCreate,
    HourlyCostAdopt,
    HourlyCostProposalRead,
    PayDayRead,
    PayDayWrite,
    PayrollClosureRead,
    PayrollMonthClose,
    PayslipCorrection,
    PayslipFigures,
    PayslipProposalFields,
    PayslipRead,
    StaffListQuery,
    StaffMemberRead,
    StaffMemberUpdate,
    StaffPage,
)
from pigrocrm.core.timetracking.locks import period_label
from pigrocrm.core.timetracking.schemas import UserRatesUpdate
from pigrocrm.core.timetracking.service import TimeEntryService

ENTITY = "staff_member"
MONTH_ENTITY = "payroll_month"
PAY_DAY_ENTITY = "payroll_settings"
USER_INDEX = "uq_staff_members_user_id"

EMPLOYEE_ONLY = frozenset(
    {
        "collective_agreement",
        "contract_level",
        "contract_type",
        "disabled_worker",
        "seasonal_contracts",
        "gross_annual_pay",
        "monthly_payments",
        "part_time_percent",
        "employer_inps_rate",
        "employer_inail_rate",
        "employee_inps_rate",
        "thirteenth_month",
        "fourteenth_month",
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
        self.payroll = PayrollService(session)

    def create_staff_member(
        self, data: EmployeeCreate | DirectorCreate, actor: Actor
    ) -> StaffMemberRead:
        actor.require_admin("create_staff_member")
        self._require_may_have(data.legal_entity_id, data.kind)
        if data.user_id is not None:
            self._require_free_user(data.user_id, None)
        if data.site_id is not None:
            self._require_site(data.legal_entity_id, data.site_id)
        member = self._flushed(
            lambda: self.repo.add(StaffMember(**data.model_dump())), data.user_id
        )
        # Recorded under `staff_member`, never on the legal entity's timeline, which every
        # member reads: the row policy lets only the entity's own scope see it, the feed
        # leaves it out for anyone but an administrator and while the module is off. It says
        # who did what, never which field moved nor to what (no `changed`).
        self.activities.record(ENTITY, member.id, "staff_member_created", actor)
        self.payroll.refresh_payroll_forecasts(member.legal_entity_id)
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
        self._normalise_workplace(member, changes)
        self._require_consistent(member, changes)
        for key, value in changes.items():
            setattr(member, key, value)
        self._flushed(self.session.flush, changes.get("user_id"))
        self.activities.record(ENTITY, member.id, "staff_member_updated", actor)
        self.payroll.refresh_payroll_forecasts(member.legal_entity_id)
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
        self.activities.record(PAY_DAY_ENTITY, found.id, "payroll_pay_day_set", actor)
        self.payroll.refresh_payroll_forecasts(legal_entity_id)
        self.session.commit()
        return PayDayRead(
            legal_entity_id=legal_entity_id,
            pay_day=data.pay_day,
            pay_month_offset=data.pay_month_offset,
        )

    def record_staff_payslip(
        self, staff_member_id: UUID, data: PayslipFigures, actor: Actor
    ) -> PayslipRead:
        """Records the figures of an accepted payslip: from now on the month it pays is those
        figures and not an estimate, and the net pay and the income-tax withholding of every
        other month are estimated from its ratios (`staff/estimate.py`). A person's act, as
        accepting anything is (frame § A4): `record_staff_payslip` is person-only in
        `agent_actions.py`, so no credential reaches it, whatever it carries. A month
        has one payslip, and a month a person closed takes none (409)."""
        actor.require_admin("record_staff_payslip")
        member = self._require(staff_member_id)
        payslip = self._write_payslip(member, data, actor)
        self.session.commit()
        self.session.refresh(payslip)
        return PayslipRead.model_validate(payslip)

    def file_proposed_payslip(
        self,
        fields: PayslipProposalFields,
        proposal_id: UUID,
        inbox_item_id: UUID | None,
        actor: Actor,
    ) -> UUID:
        """Writes the payslip a person accepted from the inbox's proposal: the same row, with
        the same due items and forecasts, as `record_staff_payslip` writes, plus the inbox item
        its PDF is. Flushes and never commits: `ProposalService.accept` owns the transaction
        and the proposal's own decision. Answers the payslip's id."""
        actor.require_admin("accept_payslip_proposal")
        member = self._require(fields.staff_member_id)
        payslip = self._write_payslip(
            member,
            PayslipFigures(
                period=fields.period,
                gross=fields.gross,
                net=fields.net,
                withholding=fields.withholding,
            ),
            actor,
            inbox_item_id=inbox_item_id,
            payload={"proposal_id": str(proposal_id)},
        )
        return payslip.id

    def _write_payslip(
        self,
        member: StaffMember,
        data: PayslipFigures,
        actor: Actor,
        *,
        inbox_item_id: UUID | None = None,
        payload: dict[str, Any] | None = None,
    ) -> StaffPayslip:
        """The one writer of a payslip row: the checks, the row, the timeline entry and the
        forecasts it moves. The legal entity's row is taken first, so a close of the month
        and a payslip for it queue, and never commits."""
        period = first_of(data.period)
        self.payroll.lock_legal_entity(member.legal_entity_id)
        self._require_open_month(member.legal_entity_id, period)
        if period < first_of(member.start_date) or (
            member.end_date is not None and period > first_of(member.end_date)
        ):
            raise ValidationFailed(
                "staff_payslip",
                "period",
                "il cedolino e' di un mese in cui la persona non era in servizio",
                expected="un mese tra l'inizio e la fine del rapporto",
            )
        if self.repo.payslip(member.id, period) is not None:
            raise Conflict(
                "staff_payslip",
                "il cedolino di questo mese e' gia' registrato",
                staff_member_id=str(member.id),
                period=period.isoformat(),
            )
        payslip = self.repo.add_payslip(
            StaffPayslip(
                staff_member_id=member.id,
                legal_entity_id=member.legal_entity_id,
                period=period,
                gross=data.gross,
                net=data.net,
                withholding=data.withholding,
                inbox_item_id=inbox_item_id,
            )
        )
        # The expense notes this payslip pays back are settled by it, on their own due items:
        # the payslip adds none (spec § 3).
        settle_notes_in_payslip(self.session, member.id, period, actor)
        self.activities.record(ENTITY, member.id, "staff_payslip_recorded", actor, payload)
        self.payroll.refresh_payroll_forecasts(member.legal_entity_id)
        return payslip

    def _require_open_month(self, legal_entity_id: UUID, month: dt.date) -> None:
        """A month a person closed takes no payslip and no correction: 409, with the month
        named so the page can say it."""
        if self.repo.closure(legal_entity_id, month) is not None:
            raise Conflict(
                "staff_payslip",
                f"il mese di paga {period_label(month.year, month.month)} e' chiuso: non si "
                "registra ne' si corregge un cedolino di quel mese",
                legal_entity_id=str(legal_entity_id),
                month=month.isoformat(),
            )

    def correct_staff_payslip(
        self, staff_member_id: UUID, payslip_id: UUID, data: PayslipCorrection, actor: Actor
    ) -> PayslipRead:
        """Corrects the figures of a recorded payslip: the month it pays, the net pay and the
        withholding items it wrote and every forecast the figures feed (the ratios of the other
        months, the INPS contributions) follow at once. A person's act, as recording is:
        `correct_staff_payslip` is person-only in `agent_actions.py`, so no credential reaches
        it, whatever it carries. The month never changes; an item of the payslip with a payment
        recorded that the figures would move is refused, and nothing is written (the payment is
        reversed first), and so is a payslip of a month a person closed (409). The timeline
        entry says who and for which month, never the figures. Correcting to the figures it
        already has writes nothing."""
        actor.require_admin("correct_staff_payslip")
        member = self._require(staff_member_id)
        self.payroll.lock_legal_entity(member.legal_entity_id)
        payslip = self.repo.payslip_locked(payslip_id)
        if payslip is None or payslip.staff_member_id != member.id:
            raise NotFound("staff_payslip", payslip_id)
        self._require_open_month(member.legal_entity_id, payslip.period)
        if (payslip.gross, payslip.net, payslip.withholding) == (
            data.gross,
            data.net,
            data.withholding,
        ):
            return PayslipRead.model_validate(payslip)
        with self.session.begin_nested():
            payslip.gross = data.gross
            payslip.net = data.net
            payslip.withholding = data.withholding
            self.session.flush()
            self.payroll.restate_payslip_items(member, payslip)
        self.activities.record(
            ENTITY,
            member.id,
            "staff_payslip_corrected",
            actor,
            {"period": payslip.period.isoformat()},
        )
        self.payroll.refresh_payroll_forecasts(member.legal_entity_id)
        self.session.commit()
        self.session.refresh(payslip)
        return PayslipRead.model_validate(payslip)

    def close_payroll_month(
        self, legal_entity_id: UUID, data: PayrollMonthClose, actor: Actor
    ) -> PayrollClosureRead:
        """Closes a payroll month of one legal entity (staff design § 4): its accepted payslips
        and its estimates are locked, which is to say that no payslip is recorded or corrected
        for it from now on and the month reads as it is now, whatever the pay inputs become.
        An administrator's act and, for an agent, a `full_access` token's. Only a month that
        has ended closes, and once: a closed month stays closed. A person still on an estimate
        is closed with it, as an estimate; the page says who before it asks."""
        actor.require_admin("close_payroll_month")
        self._require_legal_entity(legal_entity_id)
        month = first_of(data.month)
        if add_months(month, 1) > today_local():
            raise ValidationFailed(
                "payroll_month",
                "month",
                "il mese di paga non e' ancora finito: si chiude quando e' stato lavorato",
                expected="un mese gia' concluso",
            )
        self.payroll.lock_legal_entity(legal_entity_id)
        if self.repo.closure(legal_entity_id, month) is not None:
            raise Conflict(
                "payroll_month",
                f"il mese di paga {period_label(month.year, month.month)} e' gia' chiuso",
                legal_entity_id=str(legal_entity_id),
                month=month.isoformat(),
            )
        snapshot = self.payroll.live_month(legal_entity_id, month).model_dump(mode="json")
        closure = self.repo.add_closure(
            PayrollMonthClosure(
                legal_entity_id=legal_entity_id,
                month=month,
                closed_at=dt.datetime.now(dt.UTC),
                closed_by_id=actor.id,
                snapshot=snapshot,
            )
        )
        self.activities.record(
            MONTH_ENTITY, closure.id, "payroll_month_closed", actor, {"month": month.isoformat()}
        )
        self.session.commit()
        return self.payroll.closure_read(closure)

    def propose_staff_hourly_cost(
        self, staff_member_id: UUID, actor: Actor
    ) -> HourlyCostProposalRead:
        """What an hour of this person costs: a month's cost
        (`PayrollService.reference_monthly_cost`) over their working hours a month, for the
        linked user's `default_hourly_cost` (design § 5). Offered on request to an
        administrator and written nowhere; there is no agent tool, and a credential is
        refused (`agent_actions.py`)."""
        actor.require_person("propose_staff_hourly_cost")
        actor.require_unscoped_admin("propose_staff_hourly_cost")
        member = self._require(staff_member_id)
        if member.user_id is None:
            raise ValidationFailed(
                ENTITY,
                "user_id",
                "la persona non e' collegata a un utente: senza, il costo orario non ha dove "
                "andare",
                expected="una persona collegata a un utente",
            )
        if member.hours_per_month is None:
            raise ValidationFailed(
                ENTITY,
                "hours_per_month",
                "mancano le ore di lavoro al mese: il costo mensile si divide per quelle",
                expected="le ore di lavoro al mese",
            )
        reference = self.payroll.reference_monthly_cost(member, today_local())
        if reference is None:
            raise ValidationFailed(
                ENTITY,
                "gross_annual_pay" if member.kind == "employee" else "annual_fee",
                "non c'e' un mese intero di servizio con un costo da cui partire",
                expected="la retribuzione e un mese intero in servizio",
            )
        month, monthly = reference
        user = self.session.get(User, member.user_id)
        if user is None:
            raise NotFound("user", member.user_id)
        return HourlyCostProposalRead(
            staff_member_id=member.id,
            user_id=member.user_id,
            month=month,
            monthly_cost=monthly,
            hours_per_month=member.hours_per_month,
            hourly_cost=round_money(monthly / member.hours_per_month),
            current_hourly_cost=user.default_hourly_cost,
        )

    def adopt_staff_hourly_cost(
        self, staff_member_id: UUID, data: HourlyCostAdopt, actor: Actor
    ) -> HourlyCostProposalRead:
        """A person confirms the figure they were shown and it becomes the linked user's
        `default_hourly_cost`, from now on (hours already logged keep theirs). Refused when the
        cost no longer gives that figure, so what was adopted is what was seen. The write is
        `TimeEntryService.update_user_rates`, with its own timeline entry."""
        actor.require_person("adopt_staff_hourly_cost")
        actor.require_unscoped_admin("adopt_staff_hourly_cost")
        proposal = self.propose_staff_hourly_cost(staff_member_id, actor)
        if proposal.hourly_cost != data.hourly_cost:
            raise Conflict(
                ENTITY,
                "il costo orario non e' piu' quello proposto: va riletto prima di adottarlo",
                staff_member_id=str(staff_member_id),
                proposed=str(proposal.hourly_cost),
            )
        TimeEntryService(self.session).update_user_rates(
            proposal.user_id, UserRatesUpdate(default_hourly_cost=data.hourly_cost), actor
        )
        return self.propose_staff_hourly_cost(staff_member_id, actor)

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

    def _flushed[T](self, write: Callable[[], T], user_id: UUID | None) -> T:
        """Runs a write that flushes, inside a savepoint. `_require_free_user` reads under the
        row policy, so a user already staffed in a legal entity out of this actor's sight is
        not found there and the global unique index is what stops the link: that is the same
        «already linked» answer, and it does not say where."""
        try:
            with self.session.begin_nested():
                return write()
        except IntegrityError as exc:
            if user_id is not None and USER_INDEX in str(exc.orig):
                raise Conflict(
                    ENTITY,
                    "questo utente è già collegato a un'altra persona dell'azienda",
                    user_id=str(user_id),
                ) from exc
            raise

    def _normalise_workplace(self, member: StaffMember, changes: dict[str, Any]) -> None:
        """A person works at a site, at the registered office or nowhere stated: the write that
        names one place takes the person off the other, and `null` for the flag is `false`."""
        if "at_registered_office" in changes and changes["at_registered_office"] is None:
            changes["at_registered_office"] = False
        if changes.get("site_id") is not None:
            self._require_site(member.legal_entity_id, changes["site_id"])
            changes.setdefault("at_registered_office", False)
        if changes.get("at_registered_office") is True:
            changes.setdefault("site_id", None)

    def _require_site(self, legal_entity_id: UUID, site_id: UUID) -> None:
        if not self.repo.has_site(legal_entity_id, site_id):
            raise ValidationFailed(
                ENTITY,
                "site_id",
                "la sede non è di questa azienda",
                expected="una sede dell'azienda della persona",
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
        if value("fourteenth_month") is not None and value("monthly_payments") != 14:
            raise ValidationFailed(
                ENTITY,
                "fourteenth_month",
                "la quattordicesima c'e' solo con quattordici mensilita'",
                expected="quattordici mensilita', o nessun mese per la quattordicesima",
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


__all__ = ["ENTITY", "PAY_DAY_ENTITY", "StaffService"]
