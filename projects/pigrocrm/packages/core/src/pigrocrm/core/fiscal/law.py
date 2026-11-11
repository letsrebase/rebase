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

from pigrocrm.core.fiscal.law_chamber_fee import ChamberFeeLaw
from pigrocrm.core.fiscal.law_flat_rate import FlatRateLaw
from pigrocrm.core.fiscal.law_income_basis import IncomeBasisLaw
from pigrocrm.core.fiscal.law_irap import IrapLaw
from pigrocrm.core.fiscal.law_irpef import IrpefLaw


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
    # Under ISA the same page says «un unico versamento entro il 30 novembre se l'importo totale
    # dovuto non supera 206 euro» (Agenzia delle Entrate, «Come si paga l'Irpef», updated
    # 12-3-2026, the sentence A5 row «Advances of the flat-rate regime's substitute tax» quotes):
    # a total up to this amount is paid once. `None` for a pack that has not read it.
    single_november_payment_at_or_below_isa: Decimal | None = None
    # The dates of the advances and of the instalments are the pack's tax calendar
    # (`TaxCalendarLaw`), not repeated here.


# The taxes and contributions of one legal entity's year, by the design's own names (taxes
# design § 1 and § 2): what a pack lists for a year (`TaxYearLaw.taxes`) and what a declared
# figure may be typed for.
DeclaredTax = Literal["irpef", "substitute_tax", "ires", "irap", "inps"]

# What a date of the calendar is the date of, and which tax it belongs to.
TaxName = Literal[
    "iva", "income_tax", "substitute_tax", "ires", "irap", "withholding", "chamber_fee"
]
DateKind = Literal[
    "periodic_payment",
    "quarterly_payment",
    "annual_balance",
    "advance",
    "balance_and_first_advance",
    "second_advance",
    "instalments_end",
    "withholding_payment",
    "annual_fee",
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
    # The dates hold only for a legal entity whose fiscal year is the calendar year (it ends on
    # 31 December): a company's income-tax dates follow its year end, not a fixed month, and
    # the calendar says nothing for another year end rather than a date that is wrong
    # (`fiscal/calendar.py::tax_calendar`).
    calendar_year_only: bool = False


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

    Both rates are verified (A5 rows «IRES rate» and «IRAP ordinary rate»); the IRAP base and
    the regional rates are `irap`. They are percentages, like the rest of this module's rates.
    Nothing computes a tax from them yet: that is the estimate card's.
    """

    # IRES, art. 77 c. 1 of D.P.R. 917/1986: on the net total income.
    ires_rate: Decimal
    # IRAP's ordinary rate, art. 16 c. 1 of D.Lgs. 446/1997, on the net value of production.
    irap_ordinary_rate: Decimal
    # A region may vary it by up to this many points (art. 16 c. 3). Which region's rate
    # applies and each region's rate are `irap` below (REB-1065).
    irap_regional_variation_max_points: Decimal
    # IRAP's base, deductions, territorial rule and regional rates (`fiscal/law_irap.py`,
    # REB-1065). `None` for a pack that has not verified them.
    irap: IrapLaw | None = None


@dataclass(frozen=True)
class CompanyAdvanceLaw:
    """How a company's IRES and IRAP are paid in advance, and the dates that follow its fiscal
    year (REB-1074, taxes design § 2).

    Source of every figure unless a comment says otherwise: Agenzia delle Entrate, «Imposte sui
    redditi (Irpef, Ires)» (updated 30 September 2026, section on companies; A5 rows «Income tax
    advance 100%, in 40%/60% (50%/50% under ISA), June and November», «IRES: the advance is split
    in two instalments only above €103» and «Company dates»), all verified; the 50% and 50% of an
    ISA subject is art. 58 D.L. 124/2019 (Normattiva), which the Agenzia's page applies to
    income taxes and IRAP alike.

    * The advance is 100% of the tax declared for the previous year, net of deductions, credits,
      withholdings and excesses, paid 40% and 60%, or 50% and 50% for an ISA subject.
    * No advance is due when the tax does not exceed `no_advance_at_or_below`: 40,000 lire, the
      figure the pack already carries for IRES and IRAP in `ForecastAdvanceLaw` (art. 4 c. 2
      D.L. 69/1989; A5 row «Conditions of the forecast method», verified 2026-11-11, REB-1067).
    * IRES: the advance is split in two only above `ires_single_payment_at_or_below`; up to it,
      the whole advance is paid once, with the second instalment. No source read states a
      single-payment limit for IRAP, so `irap_single_payment_at_or_below` is `None` and the IRAP
      advance is always split (A5, to verify, REB-975).
    * The balance and the first advance fall on the last day of the `balance_months_after_year_end`
      month after the fiscal year end (or of the month after a late approval of the financial
      statements, which is not modelled); the second or only advance on the last day of the
      `second_advance_month_of_year` month of the tax period. For a year ending 31 December these
      are the calendar's 30 June and 30 November (`_IT_COMPANY_CALENDAR_2026`); the calendar marks
      them `calendar_year_only` and holds no date for another year end, so another year end's
      dates come from here. A date that falls on a non-working day moves forward, as the
      calendar's do.
    """

    advance_percent_of_base: Decimal
    first_advance_percent: Decimal
    second_advance_percent: Decimal
    first_advance_percent_isa: Decimal
    second_advance_percent_isa: Decimal
    no_advance_at_or_below: Decimal
    ires_single_payment_at_or_below: Decimal
    irap_single_payment_at_or_below: Decimal | None
    balance_months_after_year_end: int
    second_advance_month_of_year: int


@dataclass(frozen=True)
class PayrollLaw:
    """When an employer's payroll charges leave, as the staff estimate dates them (REB-1008).

    Three dates, each a verified row of the register (A5, checked 2026-11-10):

    * `withholding_day`: the income tax withheld on pay is paid by F24 on the 16th of the
      month after the **payment** (Agenzia delle Entrate, «F24 Ritenute reddito lavoro
      dipendente», «Come e quando si versa»). From 1 January 2027 the same rule is art. 32 c.
      1 lett. a D.Lgs. 33/2025 («entro il giorno 16 del mese successivo a quello in cui sono
      state operate le ritenute previste dagli articoli 33, 34, ...», read on
      portalenormativo.it 2026-11-10) with the F24 term of art. 8 c. 1: the dates do not change.
    * `contributions_day`: INPS contributions (the employer's and the employee's share,
      which the employer pays) are paid by the 16th of the month after the **month the
      work was done**, whether or not the worker was paid (INPS, «Pagamento dei contributi
      previdenziali», updated 15 October 2024).
    * `inail_settlement`: INAIL's yearly premium (the advance of the year and the
      regularisation of the one before, together) is paid by F24 on 16 February (INAIL,
      «Autoliquidazione 2025/2026», 8 January 2026). The option of four instalments is not
      modelled: it is the employer's election and later dates move every year.

    What is not here is not verified: the employee's INPS share, the months of the 13th and
    14th payments, the director's «gestione separata» rate and IRPEF treatment. Those are
    typed per staff member, or not estimated (A5, REB-975).
    """

    withholding_day: int
    contributions_day: int
    inail_settlement: MonthDay


@dataclass(frozen=True)
class IvaSettlementLaw:
    """How a periodic IVA settlement is computed and when a small debit waits (REB-1021,
    suppliers design § 6). The dates are the pack's calendar (`TaxCalendarLaw`), not repeated.

    Sources, all verified on 2026-11-10 (A5 rows «Quarterly settlement surcharge of 1%...»,
    «Eligibility thresholds for quarterly settlement» and «The result of a periodic
    settlement...»): D.P.R. 100/1998 art. 1 c. 1 and c. 4 and D.P.R. 542/1999 art. 7 c. 1
    lett. a) and c. 3 (Dipartimento delle Finanze, text in force from 6 August 2024), and the
    Agenzia delle Entrate pages «F24 IVA Periodica - Che cos'è» (30 July 2026) and «Come e
    quando si versa».
    """

    # A debit that does not exceed this is paid with the next period's (art. 1 c. 4 and art. 7
    # c. 1 a). «Not exceeding»: the statute's words, so exactly this amount waits. The Agenzia's
    # page says «pari o superiore» is paid; the two differ at exactly 100 and the accountant
    # list (REB-975) carries the point.
    carry_forward_limit: Decimal
    # The periods whose small debit may wait: the monthly ones of January to October (November
    # is paid by 16 December whatever it is, December by 16 January) and the first two
    # quarters (the third is paid by 16 November whatever it is, the fourth goes with the
    # annual return).
    monthly_carry_periods: tuple[int, ...]
    quarterly_carry_periods: tuple[int, ...]
    # Interest on a quarterly settlement's debit (art. 7 c. 3), in percent, and the quarters
    # it is added to. The fourth quarter is not listed: the texts read do not say whether
    # it carries the 1%, so none is computed for it (A5, to verify, REB-975).
    quarterly_surcharge_percent: Decimal
    quarterly_surcharge_periods: tuple[int, ...]
    # The previous year's turnover above which the quarterly option is closed: services and
    # professions, and other activities. Which one a legal entity is under is not stored, so
    # nothing refuses a quarterly setting below the higher limit; it is data of the law for
    # the page that will ask.
    quarterly_turnover_limit_services: Decimal
    quarterly_turnover_limit_other: Decimal


@dataclass(frozen=True)
class TaxYearLaw:
    """The law that governs the tax years from `from_year` until the next entry of the
    same pack. `None` for a section means the pack's regime has nothing of that kind."""

    from_year: int
    # The flat-rate regime's law (`FlatRateLaw`): `None` for a regime that has none, and the
    # economic overview then draws no estimate for it instead of a card of zeros (REB-760).
    flat_rate: FlatRateLaw | None = None
    # The basis the regime's income is measured on, cash or accrual (`IncomeBasisLaw`,
    # REB-1066): `None` where the pack states none.
    income_basis: IncomeBasisLaw | None = None
    advances: IncomeTaxAdvanceLaw | None = None
    # A company's own taxes, for the pack that has a company (`it-company`).
    company_tax: CompanyTaxLaw | None = None
    # The payroll dates of an employer in this regime (`PayrollLaw`): `None` where the pack
    # has not verified them, and the estimate then dates no F24 line.
    payroll: PayrollLaw | None = None
    # The lines of the year's taxes page, in the order they are drawn: the taxes and
    # contributions the regime owes (taxes design § 1 table). Which lines exist is the pack's;
    # whether a figure can be computed for one is not: a line with no estimate reads «not
    # computable» and never zero. IVA, withholdings and payroll are not lines: they are dates
    # and due items (taxes design § 3).
    taxes: tuple[DeclaredTax, ...] = ()

    # The periodic IVA settlement (REB-1021): `None` for a regime with no periodic IVA.
    iva: IvaSettlementLaw | None = None

    # The IRPEF brackets and every region's surcharge (`fiscal/law_irpef.py`, REB-1062): `None`
    # for a regime that owes no IRPEF by brackets.
    irpef: IrpefLaw | None = None

    # The annual chamber of commerce fee of a company (`fiscal/law_chamber_fee.py`, REB-1068).
    chamber_fee: ChamberFeeLaw | None = None

    # How a company's IRES and IRAP are paid in advance and when (`CompanyAdvanceLaw`, REB-1074):
    # `None` for a regime that is no company.
    company_advances: CompanyAdvanceLaw | None = None

    @property
    def has_income_estimate(self) -> bool:
        return self.flat_rate is not None


__all__ = [
    "Cadence",
    "CompanyAdvanceLaw",
    "CompanyTaxLaw",
    "DateKind",
    "DeclaredTax",
    "IncomeTaxAdvanceLaw",
    "IvaSettlementLaw",
    "MonthDay",
    "PayrollLaw",
    "TaxCalendarLaw",
    "TaxDateRule",
    "TaxName",
    "TaxYearLaw",
]
