"""What staff cost and when it leaves, read and planned (spec 2026-11-09, staff, payroll and
expenses, § 2 and the C4 amendment of § 4; REB-1008).

`PayrollService` is the one place the pure estimate (`staff/estimate.py`) meets the database.
It has three jobs:

* **`get_payroll_month`**: the estimate of one legal entity's month, per staff member, cost and
  cash apart. An administrator's read and, for an agent, a `full_access` token's: nothing
  about pay is learnt before the role is asked, as in `StaffService`.
* **`refresh_payroll_forecasts`**: the cash half of the estimate written as due items of the
  `payroll` source, so the schedule shows each amount in its month. The writer's half: no
  actor, no commit, called by `StaffService` inside its own transaction after it has done its
  authorisation. A **forecast** is an estimate and never outlives its date: items due today or
  later are kept in step with the inputs (an unchanged one is left alone, a changed one is
  dropped and written again, one no longer estimated is dropped), and a forecast whose date
  passed is dropped, since only a document can settle money and a forecast cannot be settled.
  A month with an accepted payslip is not a forecast any more for what the payslip prints: its
  net pay and its withholding are written as `expected` items with the payslip's own figures,
  once, and the forecasts of those two are dropped. What the payslip does not print stays an
  estimate: the INPS contributions (the typed rates on the payslip's gross) and INAIL's yearly
  premium.
* **`personnel_cost_in_range`**: the personnel line of the period P&L, the estimate's cost, or
  an accepted payslip's gross where there is one, with no person behind it. The caller decides
  who may read it. `personnel_cost_by_kind` is the same read told apart by employees and
  directors, with the members it could not cost (`fiscal/basis.py`, REB-1071).

Refreshes happen when the inputs, the pay day or a payslip change, and every day in the
automations pass (`AutomationPass._roll_payroll`), for the months from two back to twelve ahead
of that day. The aggregate leaves out a forecast whose date passed, in case a day's pass is late.
A recorded payslip is corrected by `StaffService.correct_staff_payslip` (a person's act): its
figures are rewritten in place and `restate_payslip_items` brings the net pay and the
withholding items it wrote to them, while nothing was paid on the item (a payment is reversed
first, a person's decision, and an item an administrator dropped stays dropped). The refresh
that follows rewrites every forecast the new figures move.

Every payroll due item is of the restricted source kind `payroll`, so the schedule shows it to
administrators and everyone else sees the aggregate «Stipendi» line
(`DueItemService.list_payroll_totals`).
"""

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.auth.models import User
from pigrocrm.core.db import today_local
from pigrocrm.core.due_items.models import PAYROLL_SOURCE_KIND, DueItem
from pigrocrm.core.due_items.schemas import DueItemCreate
from pigrocrm.core.due_items.service import DueItemService
from pigrocrm.core.errors import NotFound
from pigrocrm.core.fiscal.irap_sites import SiteFacts, StaffPay
from pigrocrm.core.fiscal.law import PayrollLaw
from pigrocrm.core.fiscal.pack import resolve_pack
from pigrocrm.core.legal_entities.models import LegalEntity
from pigrocrm.core.money import ZERO_MONEY, round_money
from pigrocrm.core.proposals.access import visible_to as proposals_visible_to
from pigrocrm.core.proposals.models import Proposal
from pigrocrm.core.staff.estimate import (
    CashItem,
    CashKind,
    MonthEstimate,
    PayDay,
    Ratios,
    add_months,
    days_in_service,
    estimate_month,
    first_of,
    ratios_of,
)
from pigrocrm.core.staff.models import (
    PayrollMonthClosure,
    PayrollSettings,
    StaffMember,
    StaffPayslip,
)
from pigrocrm.core.staff.repository import StaffRepository
from pigrocrm.core.staff.schemas import (
    MemberMonthRead,
    PayrollCashItemRead,
    PayrollClosureRead,
    PayrollCostRead,
    PayrollMonthRead,
    PayslipProposalRead,
    PayslipRead,
)

# Months looked back and ahead from today when forecasts are refreshed: back, because a
# payroll month's F24 falls one or two months after it, ahead, as far as a plan reads.
LOOKBACK_MONTHS = 2
HORIZON_MONTHS = 12

# The tax a payroll F24 line is for, as the due item's counterparty (the tax office and the
# tax); net pay and the 13th and 14th payments are owed to the person instead.
TAX_OF: dict[CashKind, str] = {
    "withholding": "payroll_withholding",
    "inps_contributions": "payroll_inps",
    "inail_premium": "payroll_inail",
}
DESCRIPTION_OF: dict[CashKind, str] = {
    "net_pay": "Stipendio netto",
    "extra_pay": "Mensilita' aggiuntiva netta",
    "withholding": "Ritenute IRPEF sugli stipendi",
    "inps_contributions": "Contributi INPS sugli stipendi",
    "inail_premium": "Premio INAIL",
}
# What a payslip prints and so fixes: the net pay and the income tax withheld. The INPS
# contributions and INAIL's yearly premium are not on it.
PAYSLIP_KINDS: frozenset[CashKind] = frozenset({"net_pay", "withholding"})
# Why `restate_payslip_items` drops an item the corrected figures no longer owe.
CORRECTION_DROP_REASON = "cedolino corretto"


@dataclass(frozen=True)
class PersonnelCost:
    """What a window's personnel cost is made of (`personnel_cost_by_kind`): the employees'
    cost, the directors' fees, and how many members were in service in it with no cost
    estimated because their pay inputs are missing."""

    employee: Decimal
    director: Decimal
    not_estimated_members: int
    # Each employee with a cost in the window, by person (REB-1081): what IRAP's deductions
    # of art. 11 need, the cost told apart by contract type and the days served.
    employees: tuple["EmployeeCost", ...] = ()

    def by_contract_type(self) -> dict[str | None, Decimal]:
        """The employees' cost by contract type; `None` collects the ones with no type stated."""
        totals: dict[str | None, Decimal] = {}
        for row in self.employees:
            totals[row.contract_type] = totals.get(row.contract_type, ZERO_MONEY) + row.cost
        return {key: round_money(value) for key, value in totals.items()}


@dataclass(frozen=True)
class EmployeeCost:
    """One employee's cost over a window and the days of it the person was in service."""

    staff_member_id: UUID
    name: str
    contract_type: str | None
    cost: Decimal
    days_in_service: int
    # The share of a full-time week, in percent (100 when the member's record states none).
    part_time_percent: Decimal = Decimal(100)
    # Disabled in the sense of L. 68/1999 or L. 482/1968; `None` is «not stated» (REB-1091).
    disabled_worker: bool | None = None
    # The INAIL premium inside `cost`, from the person's typed rates (zero when no INAIL rate is
    # stated): art. 11 c. 4-octies leaves it out of the 70% of a seasonal worker (REB-1090).
    inail: Decimal = ZERO_MONEY
    # The contract history of a seasonal worker, as stored (`StaffMember.seasonal_contracts`).
    seasonal_contracts: tuple[dict[str, Any], ...] | None = None


def _inail_in(member: StaffMember, cost: Decimal) -> Decimal:
    """The employer's INAIL inside `cost`: the cost is the gross times one plus the employer's
    INPS and INAIL rates, so the INAIL is its rate's share of that sum. Exact on an estimated
    month, approximate where an accepted payslip replaced the estimate."""
    inail = member.employer_inail_rate or Decimal(0)
    if inail == 0:
        return ZERO_MONEY
    return round_money(cost * inail / (Decimal(100) + (member.employer_inps_rate or 0) + inail))


def _key(item: CashItem) -> str:
    """The identity of a payroll cash item: what it is, the month it belongs to and the day it
    leaves (a 13th paid in another month than the regular pay is withheld on its own date).
    It is the forecast's `source_rule`."""
    return f"payroll_{item.kind}:{item.payroll_month:%Y-%m}:{item.due_date:%Y-%m-%d}"


class PayrollService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.repo = StaffRepository(session)
        self.dues = DueItemService(session)

    # ---- the read ---------------------------------------------------------------------------

    def get_payroll_month(
        self, legal_entity_id: UUID, month: dt.date, actor: Actor
    ) -> PayrollMonthRead:
        """The estimate of `month` (any day of it) for every staff member of the legal entity:
        cost and cash apart, each cash item with its date, a month an accepted payslip fixed
        marked `payslip`, and a person whose payslip the inbox read and nobody accepted marked
        `received`. A month a person closed is read as it was closed (`closed` says when and
        by whom), whatever the pay inputs are now."""
        actor.require_admin("get_payroll_month")
        if self.session.get(LegalEntity, legal_entity_id) is None:
            raise NotFound("legal_entity", legal_entity_id)
        month = first_of(month)
        closure = self.repo.closure(legal_entity_id, month)
        if closure is not None:
            return self._closed_read(closure)
        read = self.live_month(legal_entity_id, month)
        waiting = self.repo.pending_payslip_proposals(
            legal_entity_id, month, proposals_visible_to(actor)
        )
        for row in read.members:
            proposal = waiting.get(row.staff_member_id)
            if proposal is not None:
                row.received = _received(proposal)
        return read

    def live_month(self, legal_entity_id: UUID, month: dt.date) -> PayrollMonthRead:
        """The month as the inputs and the accepted payslips say now, with no role check and no
        waiting proposals: what a closing freezes, and what `get_payroll_month` starts from."""
        members = self.repo.members_of(legal_entity_id)
        context = _Context.load(self.repo, legal_entity_id, members)
        rows: list[MemberMonthRead] = []
        for member in members:
            estimate = context.estimate(member, month)
            if estimate.empty and not estimate.not_estimated:
                continue
            slip = context.payslip(member, month)
            rows.append(_read(member, estimate, slip))
        return PayrollMonthRead(
            legal_entity_id=legal_entity_id,
            month=month,
            members=rows,
            cost_total=sum((r.cost.total for r in rows if r.cost is not None), ZERO_MONEY),
            cash_total=sum((i.amount for r in rows for i in r.cash), ZERO_MONEY),
        )

    def _closed_read(self, closure: PayrollMonthClosure) -> PayrollMonthRead:
        read = PayrollMonthRead.model_validate(closure.snapshot)
        read.closed = self.closure_read(closure)
        return read

    def closure_read(self, closure: PayrollMonthClosure) -> PayrollClosureRead:
        user = (
            None if closure.closed_by_id is None else self.session.get(User, closure.closed_by_id)
        )
        return PayrollClosureRead(
            legal_entity_id=closure.legal_entity_id,
            month=closure.month,
            closed_at=closure.closed_at,
            closed_by=None if user is None else (user.name or user.email),
        )

    def reference_monthly_cost(
        self, member: StaffMember, today: dt.date
    ) -> tuple[dt.date, Decimal] | None:
        """The month's cost an hourly cost is taken from: the accrual of the current month,
        moved to the nearest month the person was in service for the whole of it (a part
        month's cost is prorated and would understate the hour). The accrual, never a
        payslip's gross: a payslip prints one payment, with no share of the 13th and 14th,
        so the hour would move with whether one was typed in and not with what the person
        costs. `None` when the pay inputs give no cost there, or no whole month was served."""
        first = first_of(member.start_date)
        low = first if member.start_date == first else add_months(first, 1)
        month = max(first_of(today), low)
        if member.end_date is not None:
            end_first = first_of(member.end_date)
            ends_on_last = add_months(end_first, 1) - dt.timedelta(days=1) == member.end_date
            high = end_first if ends_on_last else add_months(end_first, -1)
            month = min(month, high)
        if month < low:
            return None
        cost = estimate_month(member, month, pay_day=None, law=None, ratios=None, payslip=None).cost
        return None if cost is None else (month, cost.total)

    def personnel_cost_in_range(
        self, legal_entity_id: UUID | None, date_from: dt.date, date_to: dt.date
    ) -> Decimal:
        """The personnel cost of the window: each month's cost, in proportion to the days of
        the member's service in that month that the window covers. A month a person closed
        counts as it was closed. No actor: whoever calls it has decided the reader may know the
        figure (`analytics/pnl.py`), and it carries no person."""
        totals, _, _, _ = self._personnel_cost(legal_entity_id, date_from, date_to)
        return round_money(sum(totals.values(), ZERO_MONEY))

    def personnel_cost_by_kind(
        self, legal_entity_id: UUID | None, date_from: dt.date, date_to: dt.date
    ) -> PersonnelCost:
        """`personnel_cost_in_range` told apart by kind of member (REB-1071): the employees' cost
        and the directors' fees, which IRAP reads as different lines, and how many members were
        in service in the window with no cost to speak of (their pay inputs are missing), since
        a total that silently leaves a person out would read as a cost of zero. Same arithmetic
        and the same rules: no actor, a closed month counts as it was closed."""
        totals, not_estimated, per_member, _ = self._personnel_cost(
            legal_entity_id, date_from, date_to
        )
        employees = tuple(
            EmployeeCost(
                member.id,
                member.name,
                member.contract_type,
                round_money(per_member[member.id]),
                days_in_service(member, date_from, date_to),
                member.part_time_percent or Decimal(100),
                member.disabled_worker,
                _inail_in(member, round_money(per_member[member.id])),
                tuple(member.seasonal_contracts) if member.seasonal_contracts is not None else None,
            )
            for member in self.repo.members_of(legal_entity_id)
            if member.kind == "employee" and member.id in per_member
        )
        return PersonnelCost(
            round_money(totals["employee"]),
            round_money(totals["director"]),
            len(not_estimated),
            employees,
        )

    def staff_pay_in_range(
        self, legal_entity_id: UUID, date_from: dt.date, date_to: dt.date
    ) -> SiteFacts:
        """What each person is paid in the window and where they work, for the IRAP split between
        regions (REB-1092, `fiscal/irap_sites.py`): the gross pay the estimate accrues, employees
        and directors alike, over the days of the window the person was in service, and the
        people in service whose pay the estimate cannot state (`unpriced`). The legal entity's
        sites are the caller's to add. No actor, as `personnel_cost_in_range` has none."""
        _, not_estimated, _, gross = self._personnel_cost(legal_entity_id, date_from, date_to)
        members = self.repo.members_of(legal_entity_id)
        staff = tuple(
            StaffPay(
                staff_member_id=member.id,
                name=member.name,
                pay=round_money(gross[member.id]),
                service_from=max(member.start_date, date_from),
                service_to=min(member.end_date or date_to, date_to),
                site_id=member.site_id,
                at_registered_office=member.at_registered_office,
            )
            for member in members
            if member.id in gross
        )
        return SiteFacts(
            sites=(),
            staff=staff,
            unpriced=tuple(member.name for member in members if member.id in not_estimated),
        )

    def _personnel_cost(
        self, legal_entity_id: UUID | None, date_from: dt.date, date_to: dt.date
    ) -> tuple[dict[str, Decimal], set[UUID], dict[UUID, Decimal], dict[UUID, Decimal]]:
        """The cost by kind, the members in service with no cost estimated, each member's cost
        and each member's gross pay (the part of the cost that is pay), over the window."""
        totals: dict[str, Decimal] = {"employee": ZERO_MONEY, "director": ZERO_MONEY}
        not_estimated: set[UUID] = set()
        per_member: dict[UUID, Decimal] = {}
        gross_per_member: dict[UUID, Decimal] = {}
        members = self.repo.members_of(legal_entity_id)
        if not members:
            return totals, not_estimated, per_member, gross_per_member
        context = _Context.load(self.repo, legal_entity_id, members)
        closed = self.repo.closed_months(legal_entity_id)
        month = first_of(date_from)
        while month <= date_to:
            last = add_months(month, 1) - dt.timedelta(days=1)
            for member in members:
                closure = closed.get((member.legal_entity_id, month))
                if closure is not None:
                    cost, gross = _closed_cost(closure, member.id)
                else:
                    estimated = context.estimate(member, month, cost_only=True).cost
                    cost = None if estimated is None else estimated.total
                    gross = None if estimated is None else estimated.gross
                served = days_in_service(member, month, last)
                covered = days_in_service(member, max(month, date_from), min(last, date_to))
                if cost is None:
                    if covered > 0:
                        not_estimated.add(member.id)
                    continue
                if served == 0:
                    continue
                share = cost * covered / served
                totals[member.kind] += share
                per_member[member.id] = per_member.get(member.id, ZERO_MONEY) + share
                if gross is not None:
                    gross_per_member[member.id] = (
                        gross_per_member.get(member.id, ZERO_MONEY) + gross * covered / served
                    )
            month = add_months(month, 1)
        return totals, not_estimated, per_member, gross_per_member

    # ---- the writer's half: no actor, no commit ---------------------------------------------

    def lock_legal_entity(self, legal_entity_id: UUID) -> None:
        """Takes the legal entity's row, so two writers of its payroll items queue."""
        self.session.execute(
            select(LegalEntity.id).where(LegalEntity.id == legal_entity_id).with_for_update()
        ).first()

    def restate_payslip_items(self, member: StaffMember, slip: StaffPayslip) -> None:
        """After a payslip's figures changed: the net pay and the withholding items it wrote
        owe what it prints now, the date each was written with. A figure the payslip no longer
        owes drops its item, and a later correction that owes it again reopens the item this
        dropped (by `CORRECTION_DROP_REASON`, never one an administrator dropped, which stays
        dropped). An item with a payment recorded that would change is refused by the due
        items' own rule: the payment is reversed first. A figure that was zero and is owed now
        has no item yet, and the refresh writes it. Flushes, never commits."""
        self.lock_legal_entity(member.legal_entity_id)
        owed = {"net_pay": slip.net, TAX_OF["withholding"]: slip.withholding}
        for listed in self.dues.repo.items_of_source(PAYROLL_SOURCE_KIND, slip.id):
            # Locked and read afresh before it is judged: an administrator's drop that committed
            # since the list was read must be seen, not reopened.
            item = self.dues.repo.get_locked(listed.id)
            if item is None:
                continue
            amount = owed[_identity(item)]
            if item.state == "dropped":
                if item.dropped_reason != CORRECTION_DROP_REASON or amount == 0:
                    continue
            elif amount == 0:
                self.dues.drop(item.id, CORRECTION_DROP_REASON)
                continue
            elif item.amount == amount:
                continue
            assert item.own_date is not None  # written from a cash item, which has a date
            self.dues.restate(item.id, amount=amount, own_date=item.own_date)

    def refresh_payroll_forecasts(self, legal_entity_id: UUID) -> None:
        """Brings the payroll due items of the legal entity in step with the inputs and the
        accepted payslips, as the module docstring says. Flushes, never commits. The legal
        entity's row is locked first, so two refreshes of one entity queue instead of both
        writing the same expected items."""
        self.lock_legal_entity(legal_entity_id)
        today = today_local()
        members = self.repo.members_of(legal_entity_id)
        if not members:
            return
        context = _Context.load(self.repo, legal_entity_id, members)
        # A month a person closed keeps the forecasts it was closed with (`_sync_forecasts`).
        closed = {month for (_, month) in self.repo.closed_months(legal_entity_id)}
        start = add_months(first_of(today), -LOOKBACK_MONTHS)
        months = [add_months(start, i) for i in range(LOOKBACK_MONTHS + HORIZON_MONTHS + 1)]
        for member in members:
            slips = context.payslips.get(member.id, [])
            for slip in slips:
                self._write_payslip_items(member, slip, context)
            fixed = {slip.period for slip in slips}
            desired: dict[str, CashItem] = {}
            for month in months:
                if month in closed:
                    continue
                for item in context.estimate(member, month).cash:
                    if month in fixed and item.kind in PAYSLIP_KINDS:
                        continue
                    if item.due_date >= today:
                        desired[_key(item)] = item
            self._sync_forecasts(member, desired, closed, today)

    def _write_payslip_items(
        self, member: StaffMember, slip: StaffPayslip, context: "_Context"
    ) -> None:
        # Every state counts as written: an item an administrator dropped stays dropped.
        written = {
            _identity(item) for item in self.dues.repo.items_of_source(PAYROLL_SOURCE_KIND, slip.id)
        }
        for item in context.estimate(member, slip.period).cash:
            if item.kind not in PAYSLIP_KINDS or _kind_identity(item.kind) in written:
                continue
            self.dues.create(self._data(member, item, state="expected", source_id=slip.id))

    def _sync_forecasts(
        self,
        member: StaffMember,
        desired: dict[str, CashItem],
        closed: set[dt.date],
        today: dt.date,
    ) -> None:
        kept: set[str] = set()
        for existing in self.dues.repo.items_of_source(PAYROLL_SOURCE_KIND, member.id):
            if existing.state != "forecast" or existing.source_rule is None:
                continue
            if (
                _rule_month(existing.source_rule) in closed
                and existing.own_date is not None
                and existing.own_date >= today
            ):
                # The month was closed with this forecast: the pay inputs moving later do not
                # rewrite it. Once its date passes it goes like any forecast.
                continue
            item = desired.get(existing.source_rule)
            unchanged = (
                item is not None
                and existing.amount == item.amount
                and existing.own_date == item.due_date
                and existing.source_rule not in kept
            )
            if unchanged:
                kept.add(existing.source_rule)
                continue
            self.dues.drop(existing.id, "stima aggiornata")
        for key, item in desired.items():
            if key in kept:
                continue
            self.dues.create(
                self._data(member, item, state="forecast", source_id=member.id, rule=key)
            )

    @staticmethod
    def _data(
        member: StaffMember,
        item: CashItem,
        *,
        state: str,
        source_id: UUID,
        rule: str | None = None,
    ) -> DueItemCreate:
        to_person = item.kind in ("net_pay", "extra_pay")
        return DueItemCreate.model_validate(
            {
                "legal_entity_id": member.legal_entity_id,
                "direction": "out",
                "counterparty_type": "staff_member" if to_person else "tax_office",
                "staff_member_id": member.id if to_person else None,
                "tax": None if to_person else TAX_OF[item.kind],
                "amount": item.amount,
                "description": DESCRIPTION_OF[item.kind],
                "state": state,
                "source_kind": PAYROLL_SOURCE_KIND,
                "source_id": source_id,
                "source_rule": rule,
                "own_date": item.due_date,
            }
        )


def _identity(item: DueItem) -> str:
    """What a payroll item written for a payslip is, read from its counterparty."""
    return item.tax if item.tax is not None else "net_pay"


def _kind_identity(kind: CashKind) -> str:
    return TAX_OF.get(kind, "net_pay")


def _rule_month(rule: str) -> dt.date | None:
    """The payroll month a forecast's `source_rule` (`_key`) belongs to."""
    try:
        return dt.date.fromisoformat(f"{rule.split(':')[1]}-01")
    except (IndexError, ValueError):
        return None


def _closed_cost(
    closure: PayrollMonthClosure, member_id: UUID
) -> tuple[Decimal | None, Decimal | None]:
    """What a month cost one person as it was closed and the gross pay in it, `None` for a person
    it had no cost for."""
    for row in closure.snapshot.get("members", []):
        if row.get("staff_member_id") == str(member_id):
            cost = row.get("cost")
            if cost is None:
                return None, None
            return Decimal(str(cost["total"])), Decimal(str(cost["gross"]))
    return None, None


def _received(proposal: Proposal) -> PayslipProposalRead:
    fields = proposal.proposed_fields
    return PayslipProposalRead(
        proposal_id=proposal.id,
        inbox_item_id=proposal.inbox_item_id,
        gross=Decimal(str(fields["gross"])),
        net=Decimal(str(fields["net"])),
        withholding=Decimal(str(fields["withholding"])),
        confidence=proposal.confidence,
        confidence_reason=proposal.confidence_reason,
        created_at=proposal.created_at,
    )


class _Context:
    """What the estimate reads besides the member, loaded once for a legal entity or for all
    the legal entities the caller sees."""

    def __init__(
        self,
        pay_days: dict[UUID, PayrollSettings],
        packs: dict[UUID, tuple[str, str] | None],
        payslips: dict[UUID, list[StaffPayslip]],
    ) -> None:
        self.pay_days = pay_days
        self.packs = packs
        self.payslips = payslips

    @classmethod
    def load(
        cls, repo: StaffRepository, legal_entity_id: UUID | None, members: list[StaffMember]
    ) -> "_Context":
        entities = {member.legal_entity_id for member in members}
        if legal_entity_id is not None:
            entities.add(legal_entity_id)
        return cls(
            repo.pay_days(entities),
            {entity: repo.pack(entity) for entity in entities},
            repo.payslips_of(member.id for member in members),
        )

    def _law(self, member: StaffMember, year: int) -> PayrollLaw | None:
        pack = self.packs.get(member.legal_entity_id)
        if pack is None:
            return None
        try:
            law = resolve_pack(*pack).law_for(year)
        except KeyError:
            # A pack this release does not ship (the company's, until the taxes design adds
            # it): no dates, so no F24 line, and the estimate says `payroll_law`.
            return None
        return None if law is None else law.payroll

    def _pay_day(self, member: StaffMember) -> PayDay | None:
        if member.pay_day is not None and member.pay_month_offset is not None:
            return PayDay(member.pay_day, member.pay_month_offset)
        found = self.pay_days.get(member.legal_entity_id)
        return None if found is None else PayDay(found.pay_day, found.pay_month_offset)

    def _ratios(self, member: StaffMember) -> Ratios | None:
        slips = self.payslips.get(member.id)
        if not slips:
            return None
        last = slips[-1]
        return ratios_of(last.gross, last.net, last.withholding)

    def payslip(self, member: StaffMember, month: dt.date) -> StaffPayslip | None:
        """The member's accepted payslip of `month` (its first day), when there is one."""
        return next(
            (slip for slip in self.payslips.get(member.id, []) if slip.period == month), None
        )

    def estimate(
        self, member: StaffMember, month: dt.date, *, cost_only: bool = False
    ) -> MonthEstimate:
        month = first_of(month)
        payslip = self.payslip(member, month)
        if cost_only:
            return estimate_month(
                member, month, pay_day=None, law=None, ratios=None, payslip=payslip
            )
        return estimate_month(
            member,
            month,
            pay_day=self._pay_day(member),
            law=self._law(member, month.year),
            ratios=self._ratios(member),
            payslip=payslip,
        )


def _read(
    member: StaffMember, estimate: MonthEstimate, payslip: StaffPayslip | None
) -> MemberMonthRead:
    cost = estimate.cost
    return MemberMonthRead(
        staff_member_id=member.id,
        name=member.name,
        kind=member.kind,  # type: ignore[arg-type]
        source=estimate.source,
        cost=None
        if cost is None
        else PayrollCostRead(
            gross=cost.gross,
            employer_inps=cost.employer_inps,
            employer_inail=cost.employer_inail,
            total=cost.total,
        ),
        cash=[
            PayrollCashItemRead(
                kind=item.kind,
                amount=item.amount,
                due_date=item.due_date,
                payroll_month=item.payroll_month,
            )
            for item in sorted(estimate.cash, key=lambda i: (i.due_date, i.kind))
        ],
        not_estimated=list(estimate.not_estimated),
        payslip=None if payslip is None else PayslipRead.model_validate(payslip),
    )


__all__ = ["HORIZON_MONTHS", "LOOKBACK_MONTHS", "PayrollService", "PersonnelCost"]
