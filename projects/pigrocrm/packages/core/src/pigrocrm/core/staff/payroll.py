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
  who may read it.

Refreshes happen when the inputs, the pay day or a payslip change, for the months from two back
to twelve ahead of that day. Nothing moves the window on by itself yet: a space whose staff
nobody edits for a year sees its horizon shrink, and the aggregate leaves out a forecast whose
date passed. A scheduled refresh is a later step.

Every payroll due item is of the restricted source kind `payroll`, so the schedule shows it to
administrators and everyone else sees the aggregate «Stipendi» line
(`DueItemService.list_payroll_totals`).
"""

import datetime as dt
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.db import today_local
from pigrocrm.core.due_items.models import PAYROLL_SOURCE_KIND, DueItem
from pigrocrm.core.due_items.schemas import DueItemCreate
from pigrocrm.core.due_items.service import DueItemService
from pigrocrm.core.errors import NotFound
from pigrocrm.core.fiscal.law import PayrollLaw
from pigrocrm.core.fiscal.pack import resolve_pack
from pigrocrm.core.legal_entities.models import LegalEntity
from pigrocrm.core.money import ZERO_MONEY, round_money
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
from pigrocrm.core.staff.models import PayrollSettings, StaffMember, StaffPayslip
from pigrocrm.core.staff.repository import StaffRepository
from pigrocrm.core.staff.schemas import (
    MemberMonthRead,
    PayrollCashItemRead,
    PayrollCostRead,
    PayrollMonthRead,
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
        marked `payslip`."""
        actor.require_admin("get_payroll_month")
        if self.session.get(LegalEntity, legal_entity_id) is None:
            raise NotFound("legal_entity", legal_entity_id)
        month = first_of(month)
        members = self.repo.members_of(legal_entity_id)
        context = _Context.load(self.repo, legal_entity_id, members)
        rows: list[MemberMonthRead] = []
        for member in members:
            estimate = context.estimate(member, month)
            if estimate.empty and not estimate.not_estimated:
                continue
            rows.append(_read(member, estimate))
        return PayrollMonthRead(
            legal_entity_id=legal_entity_id,
            month=month,
            members=rows,
            cost_total=sum((r.cost.total for r in rows if r.cost is not None), ZERO_MONEY),
            cash_total=sum((i.amount for r in rows for i in r.cash), ZERO_MONEY),
        )

    def personnel_cost_in_range(
        self, legal_entity_id: UUID | None, date_from: dt.date, date_to: dt.date
    ) -> Decimal:
        """The personnel cost of the window: each month's cost, in proportion to the days of
        the member's service in that month that the window covers. No actor: whoever calls it
        has decided the reader may know the figure (`analytics/pnl.py`), and it carries no
        person."""
        members = self.repo.members_of(legal_entity_id)
        if not members:
            return ZERO_MONEY
        context = _Context.load(self.repo, legal_entity_id, members)
        total = ZERO_MONEY
        month = first_of(date_from)
        while month <= date_to:
            last = add_months(month, 1) - dt.timedelta(days=1)
            for member in members:
                cost = context.estimate(member, month, cost_only=True).cost
                served = days_in_service(member, month, last)
                if cost is None or served == 0:
                    continue
                covered = days_in_service(member, max(month, date_from), min(last, date_to))
                total += cost.total * covered / served
            month = add_months(month, 1)
        return round_money(total)

    # ---- the writer's half: no actor, no commit ---------------------------------------------

    def refresh_payroll_forecasts(self, legal_entity_id: UUID) -> None:
        """Brings the payroll due items of the legal entity in step with the inputs and the
        accepted payslips, as the module docstring says. Flushes, never commits. The legal
        entity's row is locked first, so two refreshes of one entity queue instead of both
        writing the same expected items."""
        self.session.execute(
            select(LegalEntity.id).where(LegalEntity.id == legal_entity_id).with_for_update()
        ).first()
        today = today_local()
        members = self.repo.members_of(legal_entity_id)
        if not members:
            return
        context = _Context.load(self.repo, legal_entity_id, members)
        start = add_months(first_of(today), -LOOKBACK_MONTHS)
        months = [add_months(start, i) for i in range(LOOKBACK_MONTHS + HORIZON_MONTHS + 1)]
        for member in members:
            slips = context.payslips.get(member.id, [])
            for slip in slips:
                self._write_payslip_items(member, slip, context)
            fixed = {slip.period for slip in slips}
            desired: dict[str, CashItem] = {}
            for month in months:
                for item in context.estimate(member, month).cash:
                    if month in fixed and item.kind in PAYSLIP_KINDS:
                        continue
                    if item.due_date >= today:
                        desired[_key(item)] = item
            self._sync_forecasts(member, desired)

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

    def _sync_forecasts(self, member: StaffMember, desired: dict[str, CashItem]) -> None:
        kept: set[str] = set()
        for existing in self.dues.repo.items_of_source(PAYROLL_SOURCE_KIND, member.id):
            if existing.state != "forecast" or existing.source_rule is None:
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

    def estimate(
        self, member: StaffMember, month: dt.date, *, cost_only: bool = False
    ) -> MonthEstimate:
        month = first_of(month)
        payslip = next(
            (slip for slip in self.payslips.get(member.id, []) if slip.period == month), None
        )
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


def _read(member: StaffMember, estimate: MonthEstimate) -> MemberMonthRead:
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
    )


__all__ = ["HORIZON_MONTHS", "LOOKBACK_MONTHS", "PayrollService"]
