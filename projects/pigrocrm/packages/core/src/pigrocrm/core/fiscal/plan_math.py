"""What an instalment plan makes of a June payment (REB-1075, taxes design § 2).

Pure arithmetic over the pack, with no session and no due item: the dates and the interest of each
instalment are `payment_rules.instalment_schedule` (the Agenzia's own «Rateazione» tables are its
test), and this adds only the two things a plan needs on top of it: *which* schedule a payment
follows (the last instalment falls in the year the return is filed, which for a company with a
broken fiscal year is not the year of the first payment) and how an amount is split.

**The split.** The instalments are of equal amount (art. 20 c. 4 D.Lgs. 241/1997): the amount
divided by their number, each rounded to the cent half up, the last taking the cent difference so
the principal always adds up. The interest is the schedule's percentage of the instalment's own
principal (4% a year to the second instalment, 0.33% more for each later one), rounded half up,
and an instalment's amount is its principal and its interest.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal

from pigrocrm.core.fiscal.advances import Missing, fiscal_year_end
from pigrocrm.core.fiscal.law import DeclaredTax
from pigrocrm.core.fiscal.law_payment import PaymentKind, ReturnForm
from pigrocrm.core.fiscal.pack import FiscalPack
from pigrocrm.core.fiscal.payment_rules import (
    InstalmentDate,
    instalment_schedule,
    return_filing_date,
)
from pigrocrm.core.money import round_money

HUNDRED = Decimal(100)

# The return each tax is filed in, by the pack's own form names.
RETURN_FORM: dict[DeclaredTax, ReturnForm] = {
    "irpef": "redditi_pf",
    "substitute_tax": "redditi_pf",
    "ires": "redditi_sc",
    "irap": "irap",
}


@dataclass(frozen=True)
class PlannedInstalment:
    """One instalment of a plan: the day it is due, the part of the amount it pays, the interest
    it carries (in percent and in euro) and what it comes to."""

    number: int
    due_date: dt.date
    principal: Decimal
    interest_percent: Decimal
    interest: Decimal

    @property
    def amount(self) -> Decimal:
        return self.principal + self.interest


def plan_schedule(
    pack: FiscalPack,
    tax: DeclaredTax,
    tax_year: int,
    year_end: tuple[int, int],
    kind: PaymentKind,
    first_due: dt.date,
) -> list[InstalmentDate] | Missing:
    """The instalments a payment of `tax` can be split into, the first on `first_due`. `tax_year`
    is the year of the return being filed (the balance's), and `year_end` the legal entity's
    fiscal year end (month, day): the last instalment is due in the year that return is filed.
    A payment the law does not let be split, a return the pack does not say when it is filed and a
    year with no payment rules are each a `Missing` with its reason, never another year's rule."""
    form = RETURN_FORM.get(tax)
    if form is None:
        return Missing(f"{tax} is not paid in instalments here")
    period_end = fiscal_year_end(tax_year, year_end[0], year_end[1])
    filing = return_filing_date(pack, form, period_end)
    if filing is None:
        return Missing(
            f"the pack does not say when the {form} return of the period ending {period_end} "
            "is filed, and the last instalment depends on it"
        )
    rules = pack.payment_rules_for(filing.nominal_date.year)
    if rules is None:
        return Missing(f"the pack carries no payment rules for {filing.nominal_date.year}")
    if not rules.instalments.may_be_paid_in_instalments(kind):
        return Missing(f"the {kind.replace('_', ' ')} cannot be paid in instalments")
    return instalment_schedule(rules.instalments, first_due, filing.nominal_date.year)


def split_into_instalments(
    total: Decimal, count: int, schedule: list[InstalmentDate]
) -> list[PlannedInstalment]:
    """`total` in `count` instalments of equal amount on the first `count` days of `schedule`.
    The caller has checked `count` is two or more and `schedule` has that many."""
    each = round_money(total / count)
    planned: list[PlannedInstalment] = []
    paid = Decimal("0.00")
    for date in schedule[:count]:
        principal = total - paid if date.number == count else each
        paid += principal
        planned.append(
            PlannedInstalment(
                number=date.number,
                due_date=date.due_date,
                principal=principal,
                interest_percent=date.interest_percent,
                interest=round_money(principal * date.interest_percent / HUNDRED),
            )
        )
    return planned


__all__ = [
    "RETURN_FORM",
    "PlannedInstalment",
    "plan_schedule",
    "split_into_instalments",
]
