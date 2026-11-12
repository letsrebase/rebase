"""What the pack's law says an income tax owes in advance and on which days (REB-1074, taxes
design § 2). Pure lookups over a pack: nothing here reads a figure of a legal entity or writes a
due item, and a rule or a date the pack does not state is a `Missing` with its reason, never a
guess (the card that writes the due items turns each `Missing` into «no item, and why»).

The three regimes pay by three laws and one shape: a **base** (the tax declared for the previous
year, net of deductions, credits, withholdings and excesses), a **percentage** of it, a **split**
in a first and a second instalment (40/60, or 50/50 under ISA, and 50/50 for the flat-rate
regime), a **floor** at or below which nothing is due and a **single-payment limit** up to which
the whole advance is paid once, with the second instalment (for a company the test is on the
first instalment, not on the total). Where each figure comes from is in
the docstring of its law: `IncomeTaxAdvanceLaw` (a person), `FlatRateAdvanceLaw` (the
substitute tax), `CompanyAdvanceLaw` (IRES and IRAP).
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from pigrocrm.core.fiscal.calendar import next_working_day, tax_calendar
from pigrocrm.core.fiscal.law import DeclaredTax, TaxName, TaxYearLaw
from pigrocrm.core.fiscal.pack import FiscalPack
from pigrocrm.core.money import round_money

HUNDRED = Decimal(100)
ZERO = Decimal("0.00")

# The date the calendar names a tax by: IRPEF is the calendar's `income_tax`.
_CALENDAR_TAX: dict[DeclaredTax, TaxName] = {
    "irpef": "income_tax",
    "substitute_tax": "substitute_tax",
    "ires": "ires",
    "irap": "irap",
}
# The income taxes whose advances and balances are due items; INPS has its own dates and split
# (`law_inps.py`), which are not here.
INCOME_TAXES: tuple[DeclaredTax, ...] = ("irpef", "substitute_tax", "ires", "irap")


@dataclass(frozen=True)
class Missing:
    """Why a rule, a figure or a date is not available. `reason` is English text for the
    developer and the report, not product copy."""

    reason: str


@dataclass(frozen=True)
class AdvanceRule:
    """The advance rule of one tax in one year, for one taxpayer."""

    percent_of_base: Decimal
    first_percent: Decimal
    second_percent: Decimal
    # No advance is due when the base is at or below this.
    no_advance_at_or_below: Decimal
    # When the total advance is up to (`single_inclusive`) or below this, it is paid once, with
    # the second instalment. `None`: the pack states no such limit and the advance is always split.
    single_limit: Decimal | None
    single_inclusive: bool
    # A company's test (art. 17 c. 3 D.P.R. 435/2001): the whole advance is paid once when its
    # first instalment is up to this, whatever the total. `None`: the pack states no such limit.
    single_if_first_instalment_at_or_below: Decimal | None = None

    def is_single(self, total: Decimal) -> bool:
        if self.single_limit is None:
            return False
        return total <= self.single_limit if self.single_inclusive else total < self.single_limit


@dataclass(frozen=True)
class AdvanceAmounts:
    """What a base makes of the advance: the total and its instalments. `first` is zero when the
    advance is paid once (`single`) and both are zero when nothing is due."""

    total: Decimal
    first: Decimal
    second: Decimal
    single: bool


def advance_rule(
    law: TaxYearLaw | None, tax: DeclaredTax, isa: bool | None
) -> AdvanceRule | Missing:
    """The rule of `tax` under `law`. A person's and a company's split depends on the ISA flag, so
    a profile that does not state it has no rule; the flat-rate regime's split is 50/50 whatever
    the flag says."""
    if law is None:
        return Missing("the pack carries no law for the year")
    if tax == "substitute_tax":
        flat = law.flat_rate.advances if law.flat_rate is not None else None
        if flat is None:
            return Missing("the pack carries no advance rule of the substitute tax for the year")
        return AdvanceRule(
            percent_of_base=flat.advance_percent_of_base,
            first_percent=flat.first_advance_percent,
            second_percent=flat.second_advance_percent,
            no_advance_at_or_below=flat.no_advance_at_or_below,
            single_limit=flat.single_november_payment_at_or_below,
            single_inclusive=True,
        )
    if isa is None:
        return Missing("the profile does not say whether the taxpayer is under ISA")
    if tax == "irpef":
        person = law.advances
        if person is None:
            return Missing("the pack carries no advance rule of IRPEF for the year")
        if isa and person.single_november_payment_at_or_below_isa is not None:
            limit, inclusive = person.single_november_payment_at_or_below_isa, True
        else:
            limit, inclusive = person.single_november_payment_below, False
        return AdvanceRule(
            percent_of_base=person.advance_percent_of_base,
            first_percent=person.first_advance_percent_isa if isa else person.first_advance_percent,
            second_percent=(
                person.second_advance_percent_isa if isa else person.second_advance_percent
            ),
            no_advance_at_or_below=person.no_advance_at_or_below,
            single_limit=limit,
            single_inclusive=inclusive,
        )
    company = law.company_advances
    if company is None:
        return Missing(f"the pack carries no advance rule of {tax.upper()} for the year")
    return AdvanceRule(
        percent_of_base=company.advance_percent_of_base,
        first_percent=company.first_advance_percent_isa if isa else company.first_advance_percent,
        second_percent=(
            company.second_advance_percent_isa if isa else company.second_advance_percent
        ),
        no_advance_at_or_below=company.no_advance_at_or_below,
        single_limit=None,
        single_inclusive=True,
        single_if_first_instalment_at_or_below=company.first_instalment_single_payment_at_or_below,
    )


def advance_amounts(rule: AdvanceRule, base: Decimal) -> AdvanceAmounts:
    """The advance a `base` makes. The instalments add up to the total: the second takes the
    cent the first rounded away."""
    if base <= rule.no_advance_at_or_below:
        return AdvanceAmounts(ZERO, ZERO, ZERO, single=False)
    total = round_money(base * rule.percent_of_base / HUNDRED)
    if rule.is_single(total):
        return AdvanceAmounts(total, ZERO, total, single=True)
    first = round_money(total * rule.first_percent / HUNDRED)
    limit = rule.single_if_first_instalment_at_or_below
    if limit is not None and first <= limit:
        return AdvanceAmounts(total, ZERO, total, single=True)
    return AdvanceAmounts(total, first, total - first, single=False)


@dataclass(frozen=True)
class PaymentDates:
    """The days a tax year's payments fall on: its balance (paid the year after, with the next
    year's first advance), and its own first and second advance."""

    balance: date
    first_advance: date
    second_advance: date


def _calendar_date(pack: FiscalPack, year: int, tax: DeclaredTax, kind: str) -> date | Missing:
    entries = tax_calendar(pack, year)
    if entries is None:
        return Missing(f"the pack's tax calendar has no dates for {year}")
    for entry in entries:
        if entry.tax == _CALENDAR_TAX[tax] and entry.kind == kind:
            return entry.due_date
    return Missing(f"the pack's tax calendar has no {kind.replace('_', ' ')} of {tax} for {year}")


def _last_day(year: int, month: int) -> date:
    return date(year, month, calendar.monthrange(year, month)[1])


def _month_end(day: date) -> bool:
    return day.day == calendar.monthrange(day.year, day.month)[1]


def _add_months_to_month_end(year: int, month: int, months: int) -> date:
    """The last day of the month `months` after (`year`, `month`)."""
    index = year * 12 + (month - 1) + months
    return _last_day(index // 12, index % 12 + 1)


def fiscal_year_end(tax_year: int, month: int, day: int) -> date:
    """The day the tax period `tax_year` ends on, for a legal entity whose fiscal year ends on
    `month` and `day`. A tax year is named by the calendar year its period STARTS in: the pack's
    own convention, which the Agenzia's «Avvertenze per la compilazione del mod. F24» state for
    the F24: a company whose period is not the calendar year ticks the box «non coincidente con
    anno solare» and writes in «anno di riferimento» «il primo dei due anni solari interessati»
    (read 2026-11-12). The period of a year ending 30
    June that starts on 1 July 2025 is tax year 2025 and ends on 30 June 2026.
    A year ending 31 December is the calendar year. A year end on 29 February is the last day of
    February in a year that has no 29th."""
    year = tax_year if (month, day) == (12, 31) else tax_year + 1
    return date(year, month, min(day, calendar.monthrange(year, month)[1]))


def company_dates(
    law: TaxYearLaw | None, tax_year: int, fiscal_year_end_month: int, fiscal_year_end_day: int
) -> PaymentDates | Missing:
    """A company's dates: the balance and the first advance on the last day of the sixth month
    after the fiscal year end, the second advance on the last day of the eleventh month of the
    period (A5 row «Company dates», verified; `CompanyAdvanceLaw`). A year end that is not the last
    day of a month is not covered: the sources read say «the sixth month after» and «the eleventh
    month» and not how a period that ends mid-month counts them (A5, to verify)."""
    if law is None or law.company_advances is None:
        return Missing("the pack carries no company dates for the year")
    rules = law.company_advances
    end = fiscal_year_end(tax_year, fiscal_year_end_month, fiscal_year_end_day)
    if not _month_end(end):
        return Missing(
            "the fiscal year does not end on the last day of a month, and the sources read do not "
            "say how the sixth and the eleventh month are counted then"
        )
    previous_end = fiscal_year_end(tax_year - 1, fiscal_year_end_month, fiscal_year_end_day)
    return PaymentDates(
        balance=next_working_day(
            _add_months_to_month_end(end.year, end.month, rules.balance_months_after_year_end)
        ),
        first_advance=next_working_day(
            _add_months_to_month_end(
                previous_end.year, previous_end.month, rules.balance_months_after_year_end
            )
        ),
        second_advance=next_working_day(
            _add_months_to_month_end(
                previous_end.year, previous_end.month, rules.second_advance_month_of_year
            )
        ),
    )


@dataclass(frozen=True)
class AdvanceDates:
    """The days a tax year's two advances fall on."""

    first_advance: date
    second_advance: date


def balance_date(
    pack: FiscalPack,
    law: TaxYearLaw | None,
    tax: DeclaredTax,
    tax_year: int,
    fiscal_year_end_month: int,
    fiscal_year_end_day: int,
) -> date | Missing:
    """The day the balance of `tax`'s tax year is paid: a person's and the flat-rate regime's
    from the pack's tax calendar of the calendar year after it, a company's from its fiscal year
    end. Independent of the dates of that year's own advances, which a calendar may lack."""
    if tax in ("ires", "irap"):
        dates = company_dates(law, tax_year, fiscal_year_end_month, fiscal_year_end_day)
        if isinstance(dates, Missing):
            return dates
        if (fiscal_year_end_month, fiscal_year_end_day) == (12, 31):
            # A calendar-year company pays on the calendar's day, which a decree may move (the
            # deferral of 2026, REB-1100); every other year end is the formula's.
            moved = _calendar_date(pack, tax_year + 1, tax, "balance_and_first_advance")
            if not isinstance(moved, Missing):
                return moved
        return dates.balance
    return _calendar_date(pack, tax_year + 1, tax, "balance_and_first_advance")


def advance_dates(
    pack: FiscalPack,
    law: TaxYearLaw | None,
    tax: DeclaredTax,
    tax_year: int,
    fiscal_year_end_month: int,
    fiscal_year_end_day: int,
) -> AdvanceDates | Missing:
    """The days `tax`'s advances of `tax_year` are paid: a person's and the flat-rate regime's
    from the pack's tax calendar of the same calendar year, a company's from its fiscal year
    end (a calendar-year company's first advance from the calendar, as its balance)."""
    if tax in ("ires", "irap"):
        dates = company_dates(law, tax_year, fiscal_year_end_month, fiscal_year_end_day)
        if isinstance(dates, Missing):
            return dates
        first_advance = dates.first_advance
        if (fiscal_year_end_month, fiscal_year_end_day) == (12, 31):
            moved = _calendar_date(pack, tax_year, tax, "balance_and_first_advance")
            if not isinstance(moved, Missing):
                first_advance = moved
        return AdvanceDates(first_advance, dates.second_advance)
    first = _calendar_date(pack, tax_year, tax, "balance_and_first_advance")
    second = _calendar_date(pack, tax_year, tax, "second_advance")
    if isinstance(first, Missing):
        return first
    if isinstance(second, Missing):
        return second
    return AdvanceDates(first, second)


__all__ = [
    "INCOME_TAXES",
    "AdvanceAmounts",
    "AdvanceDates",
    "AdvanceRule",
    "Missing",
    "PaymentDates",
    "advance_amounts",
    "advance_dates",
    "advance_rule",
    "balance_date",
    "company_dates",
    "fiscal_year_end",
]
