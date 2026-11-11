"""Pure lookups over a pack's payment rules (REB-1067, `fiscal/law_payment.py`).

Nothing here estimates a tax or writes a due item: it answers what the law says, from the data in
the pack, for the card that does (the instalment schedule of a plan and its interest, the date a
return is filed). The dates move to the next working day through `calendar.next_working_day`,
as every other date of the pack does.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from pigrocrm.core.fiscal.calendar import next_working_day
from pigrocrm.core.fiscal.law_payment import InstalmentLaw, ReturnForm
from pigrocrm.core.fiscal.pack import FiscalPack

_AUGUST = 8


@dataclass(frozen=True)
class InstalmentDate:
    """One instalment of a plan: its number, the nominal day the interest is counted to, the
    day it is due once the August window and a non-working day are applied, and the interest
    it carries in percent of the instalment (the first carries none)."""

    number: int
    nominal_date: date
    due_date: date
    interest_percent: Decimal


def instalment_schedule(
    law: InstalmentLaw, first_due: date, year_of_filing: int
) -> list[InstalmentDate]:
    """Every instalment of a plan whose first payment is due on `first_due` and that must be
    completed by the last-instalment day of `year_of_filing`, the year the return is filed
    (art. 20 c. 1 D.Lgs. 241/1997). The number of instalments a taxpayer may choose is up to
    `len(...)`; the Agenzia's own example, a first payment on 30 June 2026, has seven.

    The first instalment is paid on `first_due` as it stands: the caller gives the day the
    deadline falls on, 30 June or 30 July when the 0.40% deferral is used, a company's own
    date. Each later one is due on the 16th of the following months, a deadline of 1 to 20
    August on 20 August, any other non-working day on the next working day."""
    last = date(year_of_filing, law.last_instalment.month, law.last_instalment.day)
    schedule = [
        InstalmentDate(
            number=1, nominal_date=first_due, due_date=first_due, interest_percent=Decimal(0)
        )
    ]
    year, month = first_due.year, first_due.month
    interest = Decimal(0)
    while True:
        month += 1
        if month > 12:  # noqa: PLR2004 -- December
            year, month = year + 1, 1
        nominal = date(year, month, law.instalment_day)
        if nominal > last:
            return schedule
        number = len(schedule) + 1
        if number == 2:  # noqa: PLR2004 -- the second instalment carries the annual rate
            interest = law.second_instalment_interest_percent(first_due, nominal)
        else:
            interest += law.later_instalment_interest_percent
        due = date(year, month, law.august_last_day) if month == _AUGUST else nominal
        schedule.append(
            InstalmentDate(
                number=number,
                nominal_date=nominal,
                due_date=next_working_day(due),
                interest_percent=interest,
            )
        )


def instalment_interest_percent(
    law: InstalmentLaw, first_due: date, number: int, year_of_filing: int
) -> Decimal | None:
    """The interest, in percent of its amount, that instalment `number` of a plan starting on
    `first_due` carries; `None` when the plan has no such instalment by the last day."""
    for entry in instalment_schedule(law, first_due, year_of_filing):
        if entry.number == number:
            return entry.interest_percent
    return None


@dataclass(frozen=True)
class ReturnFilingDate:
    """The day a return is filed: the nominal day the rule gives and the day it is due once a
    Saturday, a Sunday or a holiday is moved forward (the Agenzia's own 2026 example)."""

    form: ReturnForm
    nominal_date: date
    due_date: date
    covers: str
    source: str


def return_filing_date(
    pack: FiscalPack, form: ReturnForm, period_end: date
) -> ReturnFilingDate | None:
    """When the `form` return for the tax period closing on `period_end` is filed, `None` when
    the pack carries no rule for it (the form is not the regime's, or no entry covers the year
    the filing falls in: never another year's rule)."""
    for year in (period_end.year, period_end.year + 1):
        payment = pack.payment_rules_for(year)
        rule = payment.filing_rule(form) if payment is not None else None
        if rule is None:
            continue
        nominal = rule.nominal_date(period_end)
        if nominal.year != year:
            continue
        return ReturnFilingDate(
            form=form,
            nominal_date=nominal,
            due_date=next_working_day(nominal),
            covers=rule.covers,
            source=rule.source,
        )
    return None


__all__ = [
    "InstalmentDate",
    "ReturnFilingDate",
    "instalment_interest_percent",
    "instalment_schedule",
    "return_filing_date",
]
