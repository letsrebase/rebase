"""Tax law, keyed by tax year (REB-974, taxes design § 1).

A fiscal pack (`fiscal/pack.py`) holds law. Law changes by statute or by decree, so a pack
carries it as a list of `TaxYearLaw`, each valid from a tax year until the next one starts,
and the year being computed picks its own (`FiscalPack.law_for`). The June balance of year
N-1 therefore reads the rules that governed N-1 even after the entity changes regime or the
pack gains a newer entry on 1 January. What depends on the person (the profitability
coefficient, the substitute rate, the INPS category, the ISA flag) is not here: it is
`FiscalProfile`'s, and each reading freezes both (`FiscalEstimateReading`).

Every figure below is a row of the register in `2026-11-09-space-profiles-modules-and-the-
money-picture-design.md` § A5 (the «frame»). A row marked «verified» there names its source
here; a row marked «to verify» stays marked in the comment of the figure that relies on it,
and a figure that has no row is not in this file.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

# How the flat-rate regime's INPS contributions are based, as the estimate
# (`analytics/fiscal.py::estimate_income`) reads it. One member today, the formula the estimate
# has always used; it is a datum of the law and not a branch in the service so that the
# accountant's answer (REB-975) becomes a second member here, never an `if` there.
ContributionBase = Literal["taxable_amount_net_of_substitute_tax"]


@dataclass(frozen=True)
class MonthDay:
    """A date of the tax calendar, with no year: the year is the tax year's own."""

    month: int
    day: int


@dataclass(frozen=True)
class IncomeTaxAdvanceLaw:
    """How a person's income tax is paid in advance: the percentages, the thresholds
    under which the advance is not due or is paid once, the deferral and the instalments.

    Source of every figure unless a comment says otherwise: Agenzia delle Entrate, «Imposte
    sui redditi (Irpef, Ires)» (A5 rows «Income tax advance 100%...», «Balance and first
    advance payable in monthly instalments...», «IRPEF: no advance due at or below
    €51.65...», «0.40% added for paying within 30 days after the deadline», «Income-tax
    dates are salvo proroghe»), all verified. The company rows of that page (the IRES
    split above €103, the dates that follow the fiscal year end) belong to a company pack
    and are not carried by a pack that has no company.
    """

    # 100% of the advance base, paid 40% in June and 60% in November, or 50% and 50% when
    # the taxpayer is under ISA (the profile's `isa` flag decides which).
    advance_percent_of_base: Decimal
    first_advance_percent: Decimal
    second_advance_percent: Decimal
    first_advance_percent_isa: Decimal
    second_advance_percent_isa: Decimal
    # IRPEF: no advance is due at or below this amount, and below the second one a single
    # payment is made in November.
    no_advance_at_or_below: Decimal
    single_november_payment_below: Decimal
    # Paying within `deferral_window_days` after a deadline adds this percentage.
    deferral_surcharge_percent: Decimal
    deferral_window_days: int
    # The balance and the first advance may be paid in monthly instalments up to this
    # date; the November advance is paid in one payment.
    instalments_until: MonthDay
    # The months are verified (June and November); the day of the month is TO VERIFY (A5
    # row «Income-tax dates: the day of the month»), and the dates are «salvo proroghe»:
    # a decree that moves them is a new `TaxYearLaw`, never a branch in a service.
    balance_and_first_advance_date: MonthDay
    second_advance_date: MonthDay


@dataclass(frozen=True)
class TaxYearLaw:
    """The law that governs the tax years from `from_year` until the next entry of the
    same pack. `None` for a section means the pack's regime has nothing of that kind."""

    from_year: int
    # The flat-rate regime's income estimate: `None` for a regime that has none, and the
    # economic overview then draws no estimate for it instead of a card of zeros
    # (REB-760). «To verify» (A5 row «Flat-rate contribution base...», REB-975).
    contribution_base: ContributionBase | None = None
    advances: IncomeTaxAdvanceLaw | None = None

    @property
    def has_income_estimate(self) -> bool:
        return self.contribution_base is not None


__all__ = ["ContributionBase", "IncomeTaxAdvanceLaw", "MonthDay", "TaxYearLaw"]
