"""The monthly estimate of what a staff member costs and when that money leaves (spec
2026-11-09, staff, payroll and expenses, § 2; REB-1008).

Pure: no session, no actor. `PayrollService` feeds it a `StaffMember`, the legal entity's pay
day, the pack's `PayrollLaw` and the last accepted payslip's two ratios, and it answers a
`MonthEstimate` for one **payroll month** (the month the work belongs to). PigroCRM estimates and
plans; the payroll consultant computes (decision 1): no withholding table is built here, and
what cannot be said from the inputs is not said.

**Cost and cash are two figures.** The cost of a month is its accrual: the gross pay spread
over twelve months (the 13th and 14th payments accrue in every month, they are not a cost of the
month they are paid in) plus the employer's INPS and INAIL at the typed rates. The cash is what
leaves, each item with its date: net pay on the pay day, the 13th and 14th payments in their
typed months, the F24 lines on the dates the law gives them (`PayrollLaw`).

**Which parts need what.**

* Cost needs only the typed inputs (the employer's rates are cost when stated).
* Net pay and the income-tax withholding exist only from the last accepted payslip's ratios,
  net to gross and withholding to gross. A member with no accepted payslip has neither, and
  **no F24 line of any kind either**: INPS and INAIL wait with them, so no partial F24
  forecast appears (spec § 2).
* A part whose input is missing is not estimated and named in `not_estimated`, never defaulted:
  the pay day, the employer's and the employee's INPS rate, the INAIL rate, the months of the
  13th and 14th, the pack's payroll law. The F24 lines of an employee (withholding, INPS,
  INAIL) depend on the pay day, the pack's dates, the ratios and all three rates together, so
  a missing one takes all of them away, never one; the net pay stands.

**What a director has.** Cost from the annual fee spread over twelve months and, from the
ratios, the net fee on the pay day. **No F24 line**: the income-tax withholding on a fee and the
INPS «gestione separata» are «to verify» (A5, REB-975), so `not_estimated` says `fee_withholding`
and `gestione_separata`; the TFM accrual is its own card.

**When the 13th and 14th leave.** In the month typed for each, on the pay day's day of that
month, whatever the pay day's month offset says of the regular payment. Their withholding is
due the 16th after *their* payment; their INPS follows the month they are paid in.

**Simplifications, stated.** A month part in service counts by calendar days. The 16th is not
moved to the next working day: it is the legal date, the tolerance is the payer's. The 13th and
14th mature over the twelve months that end with the month they are paid in. INAIL's yearly
settlement assumes the advance paid last February was a full year's for a member employed then
(a member who left shows the difference as a credit), and it is dated to the 16th of the month
the pack's law names.
"""

import calendar
import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pigrocrm.core.fiscal.law import PayrollLaw
from pigrocrm.core.money import ZERO_MONEY, round_money
from pigrocrm.core.staff.models import StaffMember, StaffPayslip

HUNDRED = Decimal(100)
TWELVE = Decimal(12)

CashKind = Literal["net_pay", "extra_pay", "withholding", "inps_contributions", "inail_premium"]
# What a part that was not estimated is missing, one word each.
NotEstimated = Literal[
    "accepted_payslip",
    "pay_day",
    "payroll_law",
    "employer_inps_rate",
    "employee_inps_rate",
    "employer_inail_rate",
    "thirteenth_month",
    "fourteenth_month",
    "fee_schedule",
    "fee_withholding",
    "gestione_separata",
]


@dataclass(frozen=True)
class Ratios:
    """What the last accepted payslip says of a person's pay: how much of the gross is net,
    and how much is withheld as income tax."""

    net_to_gross: Decimal
    withholding_to_gross: Decimal


@dataclass(frozen=True)
class PayDay:
    """The day of the month net pay leaves, in the month worked (0) or the one after (1)."""

    day: int
    month_offset: int


@dataclass(frozen=True)
class CostLine:
    """The accrual of one month: gross pay, then the employer's contributions that could be
    estimated. `employer_inps` and `employer_inail` are `None` when the rate is not stated."""

    gross: Decimal
    employer_inps: Decimal | None
    employer_inail: Decimal | None

    @property
    def total(self) -> Decimal:
        return self.gross + (self.employer_inps or ZERO_MONEY) + (self.employer_inail or ZERO_MONEY)


@dataclass(frozen=True)
class CashItem:
    """One amount that leaves, out, on `due_date`. `payroll_month` is the month it belongs to
    (the month worked; the INAIL settlement belongs to the month it is paid in)."""

    kind: CashKind
    amount: Decimal
    due_date: dt.date
    payroll_month: dt.date


@dataclass(frozen=True)
class MonthEstimate:
    staff_member_id: UUID
    month: dt.date
    cost: CostLine | None
    cash: tuple[CashItem, ...]
    # `payslip` when an accepted payslip fixed the month, `estimate` otherwise.
    source: Literal["estimate", "payslip"] = "estimate"
    not_estimated: tuple[NotEstimated, ...] = field(default=())

    @property
    def empty(self) -> bool:
        return self.cost is None and not self.cash


@dataclass(frozen=True)
class _Payment:
    """One payment of the month, in gross, net and income tax withheld, on the day it is paid."""

    kind: Literal["net_pay", "extra_pay"]
    gross: Decimal
    net: Decimal
    withholding: Decimal
    paid_on: dt.date


def first_of(day: dt.date) -> dt.date:
    return day.replace(day=1)


def add_months(month: dt.date, count: int) -> dt.date:
    """The first of the month `count` months after `month`'s (negative: before)."""
    index = month.year * 12 + (month.month - 1) + count
    return dt.date(index // 12, index % 12 + 1, 1)


def _last_of(month: dt.date) -> dt.date:
    return month.replace(day=calendar.monthrange(month.year, month.month)[1])


def days_in_service(member: StaffMember, first: dt.date, last: dt.date) -> int:
    """Calendar days of `first..last` the member is in service."""
    start = max(first, member.start_date)
    end = last if member.end_date is None else min(last, member.end_date)
    return max((end - start).days + 1, 0)


def _share(member: StaffMember, month: dt.date) -> Decimal:
    """The part of `month` the member is in service, 0 to 1."""
    last = _last_of(month)
    return Decimal(days_in_service(member, month, last)) / Decimal(last.day)


def _annual_gross(member: StaffMember) -> Decimal | None:
    """What the member earns in a full year: the RAL less the part-time share for an
    employee, the fee for a director. `None` for a row with neither (never on a valid row)."""
    if member.kind == "employee":
        if member.gross_annual_pay is None or member.part_time_percent is None:
            return None
        return member.gross_annual_pay * member.part_time_percent / HUNDRED
    return member.annual_fee


def _payment_date(month: dt.date, pay_day: PayDay) -> dt.date:
    return add_months(month, pay_day.month_offset).replace(day=pay_day.day)


def _percent(amount: Decimal, rate: Decimal | None) -> Decimal | None:
    return None if rate is None else round_money(amount * rate / HUNDRED)


def ratios_of(gross: Decimal, net: Decimal, withholding: Decimal) -> Ratios:
    return Ratios(net / gross, withholding / gross)


def estimate_month(
    member: StaffMember,
    month: dt.date,
    *,
    pay_day: PayDay | None,
    law: PayrollLaw | None,
    ratios: Ratios | None,
    payslip: StaffPayslip | None = None,
) -> MonthEstimate:
    """The estimate of `month` (any day of it) for `member`. `payslip` is the member's accepted
    payslip of that month, when there is one: it replaces the estimate of the month with its
    own figures (the cost is its gross, the cash its net and its withholding), and `ratios`
    still come from the last payslip, which may be that one."""
    month = first_of(month)
    employee = member.kind == "employee"
    annual = _annual_gross(member)
    share = _share(member, month)
    in_service = share > 0
    missing: list[NotEstimated] = []
    cash: list[CashItem] = []

    # ---- cost: the accrual, from the typed inputs alone -------------------------------------
    gross_accrued: Decimal | None = None
    if payslip is not None:
        gross_accrued = payslip.gross
    elif annual is not None and in_service:
        gross_accrued = round_money(annual / TWELVE * share)
    cost = (
        None
        if gross_accrued is None
        else CostLine(
            gross=gross_accrued,
            employer_inps=_percent(gross_accrued, member.employer_inps_rate),
            employer_inail=_percent(gross_accrued, member.employer_inail_rate),
        )
    )

    # ---- what the cash needs -----------------------------------------------------------------
    # A month has cash to speak of when the member is paid in it or, for an employee, when it is
    # the month INAIL's yearly premium falls in (the employee having started by then).
    inail_month = employee and _inail_month(law, month) and member.start_date <= _last_of(month)
    pays = in_service or payslip is not None
    if ratios is None and (pays or inail_month):
        missing.append("accepted_payslip")
    if pay_day is None and (pays or inail_month):
        missing.append("pay_day")
    if law is None and (pays or inail_month):
        missing.append("payroll_law")
    if employee and (pays or inail_month):
        if member.employer_inps_rate is None:
            missing.append("employer_inps_rate")
        if member.employee_inps_rate is None:
            missing.append("employee_inps_rate")
        if member.employer_inail_rate is None:
            missing.append("employer_inail_rate")
    if not employee and pays:
        # What is «to verify» is said and not computed: the withholding on a fee and the INPS
        # «gestione separata» (A5, REB-975), so a director has no F24 line at all.
        missing += ["fee_withholding", "gestione_separata"]
        if member.fee_payment != "monthly":
            missing.append("fee_schedule")

    payments: list[_Payment] = []
    if ratios is not None and pay_day is not None:
        if payslip is not None:
            payments.append(
                _Payment(
                    "net_pay",
                    payslip.gross,
                    payslip.net,
                    payslip.withholding,
                    _payment_date(month, pay_day),
                )
            )
        else:
            payments += _estimated_payments(member, month, annual, pay_day, ratios, missing)
        for payment in payments:
            if payment.net > 0:
                cash.append(CashItem(payment.kind, payment.net, payment.paid_on, month))

    # ---- the F24 lines come together or not at all (spec § 2: no partial F24) ---------------
    # They need the pay day, the pack's dates, the ratios and, for an employee, the three typed
    # rates: the INPS line is the employer's share and the employee's together, and INAIL's
    # premium is paid in the same F24 as the month's. A director has none (see above).
    if (
        employee
        and law is not None
        and pay_day is not None
        and ratios is not None
        and member.employer_inps_rate is not None
        and member.employee_inps_rate is not None
        and member.employer_inail_rate is not None
    ):
        withheld: dict[dt.date, Decimal] = {}
        for payment in payments:
            due = add_months(payment.paid_on, 1).replace(day=law.withholding_day)
            withheld[due] = withheld.get(due, ZERO_MONEY) + payment.withholding
        for due, amount in sorted(withheld.items()):
            if amount > 0:
                cash.append(CashItem("withholding", amount, due, month))
        paid = sum((payment.gross for payment in payments), ZERO_MONEY)
        contributions = round_money(
            paid * (member.employer_inps_rate + member.employee_inps_rate) / HUNDRED
        )
        if contributions > 0:
            cash.append(
                CashItem(
                    "inps_contributions",
                    contributions,
                    add_months(month, 1).replace(day=law.contributions_day),
                    month,
                )
            )
        if inail_month:
            premium = _inail_settlement(member, month.year, law, member.employer_inail_rate)
            if premium != 0:
                due_on = law.inail_settlement
                cash.append(
                    CashItem(
                        "inail_premium",
                        premium,
                        dt.date(month.year, due_on.month, due_on.day),
                        month,
                    )
                )

    return MonthEstimate(
        staff_member_id=member.id,
        month=month,
        cost=cost,
        cash=tuple(cash),
        source="payslip" if payslip is not None else "estimate",
        not_estimated=tuple(dict.fromkeys(missing)),
    )


def _estimated_payments(
    member: StaffMember,
    month: dt.date,
    annual: Decimal | None,
    pay_day: PayDay,
    ratios: Ratios,
    missing: list[NotEstimated],
) -> list[_Payment]:
    """The month's payments from the typed inputs and the ratios: the regular one (a payment's
    share of the year, by days in service) on the pay day, and the 13th and 14th, paid in the
    month typed for them on the pay day's day of it."""
    payments: list[_Payment] = []
    if annual is None:
        return payments
    paid_regularly = member.kind == "employee" or member.fee_payment == "monthly"
    share = _share(member, month)
    if paid_regularly and share > 0:
        gross = round_money(annual / _payments(member) * share)
        payments.append(_payment("net_pay", gross, ratios, _payment_date(month, pay_day)))
    extras, extras_missing = _extra_gross(member, month, annual)
    missing.extend(extras_missing)
    if extras > 0:
        payments.append(_payment("extra_pay", extras, ratios, month.replace(day=pay_day.day)))
    return payments


def _payment(
    kind: Literal["net_pay", "extra_pay"], gross: Decimal, ratios: Ratios, paid_on: dt.date
) -> _Payment:
    return _Payment(
        kind,
        gross,
        round_money(gross * ratios.net_to_gross),
        round_money(gross * ratios.withholding_to_gross),
        paid_on,
    )


def _payments(member: StaffMember) -> Decimal:
    """The pay periods a year is split into: 13 or 14 for an employee, 12 for a director."""
    return Decimal(member.monthly_payments if member.monthly_payments else 12)


def _inail_month(law: PayrollLaw | None, month: dt.date) -> bool:
    return law is not None and month.month == law.inail_settlement.month


def _extra_gross(
    member: StaffMember, month: dt.date, annual: Decimal | None
) -> tuple[Decimal, list[NotEstimated]]:
    """The 13th and 14th payments belonging to `month`: gross, and what stopped one from being
    scheduled. Each is one payment of `annual / payments`, matured over the twelve months that
    end with the month it is paid in."""
    if member.kind != "employee" or annual is None or member.monthly_payments is None:
        return ZERO_MONEY, []
    # A payment is only a payment to someone who is still on the payroll that month.
    if member.end_date is not None and member.end_date < month:
        return ZERO_MONEY, []
    missing: list[NotEstimated] = []
    total = ZERO_MONEY
    if member.thirteenth_month is None:
        missing.append("thirteenth_month")
    elif member.thirteenth_month == month.month:
        total += _matured(member, month, annual)
    if member.monthly_payments == 14:
        if member.fourteenth_month is None:
            missing.append("fourteenth_month")
        elif member.fourteenth_month == month.month:
            total += _matured(member, month, annual)
    return total, missing


def _matured(member: StaffMember, month: dt.date, annual: Decimal) -> Decimal:
    window_start = add_months(month, -11)
    window_end = _last_of(month)
    window_days = (window_end - window_start).days + 1
    in_service = days_in_service(member, window_start, window_end)
    if in_service == 0:
        return ZERO_MONEY
    return round_money(annual / _payments(member) * Decimal(in_service) / Decimal(window_days))


def _gross_in_year(member: StaffMember, annual: Decimal, year: int) -> Decimal:
    days = days_in_service(member, dt.date(year, 1, 1), dt.date(year, 12, 31))
    year_days = 366 if calendar.isleap(year) else 365
    return annual * Decimal(days) / Decimal(year_days)


def _inail_settlement(member: StaffMember, year: int, law: PayrollLaw, rate: Decimal) -> Decimal:
    """The premium paid in February of `year`: the advance for `year` on what the member is
    expected to earn in it (nothing for one not employed on the 16th, whose pay enters the
    next regularisation), plus the regularisation of `year - 1` (the pay actually earned, less
    the advance February `year - 1` took for a member then employed)."""
    annual = _annual_gross(member)
    if annual is None:
        return ZERO_MONEY
    due = law.inail_settlement
    settlement_this_year = dt.date(year, due.month, due.day)
    employed_now = member.start_date <= settlement_this_year and (
        member.end_date is None or member.end_date >= settlement_this_year
    )
    advance = _gross_in_year(member, annual, year) if employed_now else ZERO_MONEY
    actual_before = _gross_in_year(member, annual, year - 1)
    settlement_day = dt.date(year - 1, due.month, due.day)
    paid_before = (
        annual
        if member.start_date <= settlement_day
        and (member.end_date is None or member.end_date >= settlement_day)
        else ZERO_MONEY
    )
    return round_money((advance + actual_before - paid_before) * rate / HUNDRED)


__all__ = [
    "CashItem",
    "CashKind",
    "CostLine",
    "MonthEstimate",
    "NotEstimated",
    "PayDay",
    "Ratios",
    "add_months",
    "days_in_service",
    "estimate_month",
    "first_of",
    "ratios_of",
]
