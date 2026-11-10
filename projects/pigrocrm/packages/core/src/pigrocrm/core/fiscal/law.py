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
    sui redditi (Irpef, Ires)» (A5 rows «Income tax advance 100%...», «IRPEF: no advance due
    at or below €51.65...», «0.40% added for paying within 30 days after the deadline»), all
    verified. The company rows of that page (the IRES
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
    # The dates of the advances and of the instalments are the pack's tax calendar
    # (`TaxCalendarLaw`), not repeated here.


# What a date of the calendar is the date of, and which tax it belongs to.
TaxName = Literal["iva", "income_tax", "substitute_tax"]
DateKind = Literal[
    "periodic_payment",
    "quarterly_payment",
    "annual_balance",
    "advance",
    "balance_and_first_advance",
    "second_advance",
    "instalments_end",
]
# The IVA settlement cadence a date belongs to; `None` for a date that is no IVA settlement's.
Cadence = Literal["monthly", "quarterly"]


@dataclass(frozen=True)
class TaxDateRule:
    """One deadline, or one deadline per month, of the tax calendar.

    `dates` are the nominal days of the deadline in the calendar year the calendar entry is
    keyed by (an IVA rule repeated every month lists twelve). `covers` says in English what
    the payment settles. `source` names the primary source the date was verified against
    (the frame's A5 register row says the same)."""

    tax: TaxName
    kind: DateKind
    cadence: Cadence | None
    dates: tuple[MonthDay, ...]
    covers: str
    source: str


@dataclass(frozen=True)
class TaxCalendarLaw:
    """The tax dates that govern the calendar years from `from_year` until the next entry
    of the same pack (REB-1004, taxes design § 2, suppliers design § 6).

    Keyed by the calendar year a date FALLS in, because that is what a decree moves: a decree
    that moves a date of 2027 is a `TaxCalendarLaw(from_year=2027, ...)`, never a branch in a
    service. The dates are «salvo proroghe» (A5, verified). Separate from `TaxYearLaw`, whose
    `from_year` is a frozen reading's law year and the estimate's own history; a date that was
    not read for an earlier year has no entry for it, and `FiscalPack.calendar_for` answers
    `None` there.
    """

    from_year: int
    rules: tuple[TaxDateRule, ...]


@dataclass(frozen=True)
class CompanyTaxLaw:
    """What a company (an srl, the `it-company` pack) owes on its income, by rate.

    Both rates are verified (A5 rows «IRES rate» and «IRAP ordinary rate»; the IRAP region
    rules row stays to verify). They are percentages, like the rest of this module's rates.
    Nothing computes a tax from them yet: that is the estimate card's.
    """

    # IRES, art. 77 c. 1 of D.P.R. 917/1986: on the net total income.
    ires_rate: Decimal
    # IRAP's ordinary rate, art. 16 c. 1 of D.Lgs. 446/1997, on the net value of production.
    irap_ordinary_rate: Decimal
    # A region may vary it by up to this many points (art. 16 c. 3). Which region's rate
    # applies to a company is the legal entity's region and the IRAP region rules: TO VERIFY.
    irap_regional_variation_max_points: Decimal


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
    # A company's own taxes, for the pack that has a company (`it-company`).
    company_tax: CompanyTaxLaw | None = None

    @property
    def has_income_estimate(self) -> bool:
        return self.contribution_base is not None


__all__ = [
    "Cadence",
    "CompanyTaxLaw",
    "ContributionBase",
    "DateKind",
    "IncomeTaxAdvanceLaw",
    "MonthDay",
    "TaxCalendarLaw",
    "TaxDateRule",
    "TaxName",
    "TaxYearLaw",
]
