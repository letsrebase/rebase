"""How income taxes are paid, as the packs carry it (REB-1067, taxes design § 2).

Four kinds of law, none of which computes a tax: the rules for paying in instalments and the
interest they carry, the conditions of the forecast advance method and the penalty and
interest on a shortfall, the F24 code of every line an estimate will write, the date the
income-tax returns are filed, and the year a withholding suffered counts as a credit.

A `PaymentRulesLaw` is keyed by the calendar year a payment or a filing falls in, like the tax
calendar (`TaxCalendarLaw`), because that is what a decree moves: a decree that moves a rule of
2027 is a new entry from 2027, never a branch in a service. `FiscalPack.payment_rules_for(year)`
picks the entry. Every figure carries the primary source it was read from and the day it was
read, and is a row of the frame's A5 register (`2026-11-09-space-profiles-modules-and-the-
money-picture-design.md`); a rule no primary source states is not here, it is on the accountant's
list (REB-975).

The statutes change on 1 January 2027 and the rules read below keep their content: art. 20 of
D.Lgs. 241/1997 is replaced by art. 10 of D.Lgs. 33/2025 (Normattiva, text as in force on
2027-01-02, read 2026-11-11: «ARTICOLO ABROGATO DAL D.LGS. 24 MARZO 2025, N. 33, COME MODIFICATO
DAL D.L. 31 DICEMBRE 2025, N. 200»; its c. 2 leaves the interest to the ministerial decree of
2009 that already sets it), and art. 22 of the TUIR by art. 24 of the testo unico of D.Lgs. 19
June 2026 n. 117 («PROVVEDIMENTO ABROGATO DAL D.LGS. 19 GIUGNO 2026, N. 117», the letter on
withholdings is word for word the same). Each rule names both.
"""

from __future__ import annotations

from calendar import monthrange
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

from pigrocrm.core.fiscal.law import MonthDay

_CENT = Decimal("0.01")
_DAYS_IN_A_MONTH = 30  # the commercial method counts every month as thirty days
_DAYS_IN_A_YEAR = 360

# What an instalment plan can cover, in the words the F24 instructions use.
PaymentKind = Literal["balance", "first_advance", "second_advance"]


# --- instalments -----------------------------------------------------------------------


@dataclass(frozen=True)
class InstalmentLaw:
    """Monthly instalments of the balance and the first advance of an income tax.

    Sources, all read on 2026-11-11: art. 20 c. 1 and c. 4 of D.Lgs. 9 July 1997 n. 241, text in
    force on that day on Normattiva (monthly instalments of equal amount, the payment completed
    by 16 December of the year the return is filed, each instalment by the 16th of the month);
    art. 10 c. 1 and c. 4 of D.Lgs. 24 March 2025 n. 33 from 1 January 2027, GU n. 71 of
    26-3-2025 Suppl. Ord. n. 8 (the same words); Agenzia delle Entrate, Redditi PF 2026
    fascicolo 1, «Rateazione» («Tutti i contribuenti possono avvalersi della facoltà di
    rateizzare i versamenti delle somme dovute a titolo di saldo e di primo acconto»), Redditi SC
    2026 «Parte generale» § 4.4 («Sono esclusi dalla rateazione solo gli importi a
    titolo di seconda o unica rata di acconto IRES») and IRAP 2026 instructions § 5 «Rateazione»
    («Sono esclusi dalla rateazione solo gli importi a titolo di seconda o unica rata di
    acconto»). Their «Rateazione» tables are the test of
    `payment_rules.instalment_interest_percent`.

    The interest is the DM 21 May 2009 (MEF, GU n. 136 of 15-6-2009) art. 5 c. 1: «sono dovuti
    nella misura del 4 per cento annuo», which the Agenzia's instructions apply as follows: 4%
    a year by the commercial method (months of thirty days, a year of 360) from the day after the
    first instalment's due date to the second's, and «lo 0,33 per cento in misura forfetaria»
    for each instalment after that, whatever day it is paid on.
    """

    # The payments that may be paid in instalments; the second or only advance may not.
    rateable: tuple[PaymentKind, ...]
    # The last instalment falls on this day of the year the return is filed (art. 20 c. 1).
    last_instalment: MonthDay
    # Each instalment is due on this day of its month (art. 20 c. 4).
    instalment_day: int
    # A deadline from 1 to 20 August moves to 20 August, with no surcharge (art. 37 c. 11-bis
    # D.L. 223/2006; art. 11 D.Lgs. 33/2025 from 2027).
    august_last_day: int
    # Interest between the first and the second instalment, in percent a year.
    annual_interest_percent: Decimal
    # Interest added by every instalment after the second, in percent.
    later_instalment_interest_percent: Decimal
    source: str

    def may_be_paid_in_instalments(self, kind: PaymentKind) -> bool:
        return kind in self.rateable

    def second_instalment_interest_percent(self, first_due: date, second_nominal: date) -> Decimal:
        """The interest of the second instalment: the annual rate by the commercial method from
        `first_due` to the second instalment's nominal day, two decimals half up (0.18 for 30
        June to 16 July, as the Agenzia prints it)."""
        days = commercial_days(first_due, second_nominal)
        return round_percent(self.annual_interest_percent * days / _DAYS_IN_A_YEAR)


# --- the forecast advance method and a shortfall ---------------------------------------


@dataclass(frozen=True)
class ForecastAdvanceLaw:
    """When an advance computed on the tax expected for the current year (the forecast, or
    «previsionale», method) carries no penalty or interest if it turns out short.

    Source: art. 4 c. 2 of D.L. 2 March 1989 n. 69, converted by L. 27 April 1989 n. 154 (text
    on Normattiva, read 2026-11-11): the rules on interest and penalty for an omitted,
    insufficient or late advance «non si applicano»:

    a) when an instalment is omitted, if the tax due by the current year's return, net of
       deductions, credits and withholdings, is not above 100,000 lire for IRPEF and 40,000 lire
       for IRES (and IRAP, as the Agenzia's IRAP 2026 instructions apply it), that is the two
       euro figures below;
    b) when the first instalment is insufficient, if what was paid is at least 40% of what the
       current year's return would make the advance;
    c) when the second instalment is omitted or insufficient, if the first alone or both
       together reach what the current year's return would make the advance.

    That the method is open to the taxpayer, and that the penalty regime of art. 4 c. 2 applies
    to it, is Agenzia delle Entrate's: Redditi PF 2026 fascicolo 1 (rigo RN62, «Se il
    contribuente prevede ... una minore imposta ... può determinare gli acconti ... sulla base di
    tale minore imposta») and IRAP 2026 instructions § 5.2 («ferma restando, in tal caso,
    l'applicazione, ai fini del regime sanzionatorio, delle disposizioni dell'articolo 4,
    comma 2, del D.L. n. 69 del 1989»). The article says «interessi e sopratassa»: the sopratassa
    is today the penalty of art. 13 D.Lgs. 471/1997 (`ShortfallLaw`).
    """

    # (b): the first instalment must be at least this share of the amount the current year's
    # return would make the first instalment.
    first_instalment_min_percent: Decimal
    # (c): the second instalment is regular when the amounts paid add up to at least this share
    # of the amount the current year's return makes the whole advance.
    total_min_percent: Decimal
    # (a): an omitted advance is regular when the tax due by the current return is not above this.
    omitted_regular_if_tax_not_above: Decimal


@dataclass(frozen=True)
class LegalInterestRate:
    """The legal interest rate fixed for a calendar year (art. 1284 c.c.)."""

    year: int
    percent: Decimal
    source: str


@dataclass(frozen=True)
class RegularisationTier:
    """One ravvedimento reduction: a payment up to `max_days_late` days late pays one
    `divisor`-th of the penalty it would otherwise carry (art. 13 c. 1 lett. a) and a-bis)
    D.Lgs. 472/1997: a tenth up to 30 days, a ninth up to 90)."""

    max_days_late: int
    divisor: int


@dataclass(frozen=True)
class ShortfallLaw:
    """The penalty and the interest on a payment, an advance included, that is not made in full
    by its deadline.

    Penalty: art. 13 c. 1 of D.Lgs. 18 December 1997 n. 471, as replaced by D.Lgs. 14 June 2024
    n. 87 for violations from 1 September 2024 (Normattiva, read 2026-11-11): «sanzione
    amministrativa pari al venticinque per cento di ogni importo non versato»; «Per i versamenti
    effettuati con un ritardo non superiore a novanta giorni, la sanzione ... è ridotta alla
    metà»; for a delay of up to fifteen days it is «ulteriormente ridotta a un importo pari a un
    quindicesimo per ciascun giorno di ritardo».

    Ravvedimento (art. 13 c. 1 D.Lgs. 472/1997, Normattiva in force 2026-11-12, for violations
    from 1 September 2024; the Agenzia's page «Ravvedimento - Come regolarizzare»): the
    taxpayer who pays by himself pays a fraction of the minimum penalty above, «ad un decimo del
    minimo» within 30 days of the omission (lett. a) and «ad un nono del minimo» within 90 days
    (lett. a-bis). Those two tiers are `regularisation_tiers`. A payment more than 90 days late
    belongs to lett. b) and b-bis) (an eighth, then a seventh of 25%), which turn on the return
    term of the year of the violation (the statute) or on «1 anno» (the Agenzia's table), and
    the sources read do not say which a payment tied to an annual return follows: that is
    the accountant's (REB-975), so `regularisation_penalty_percent` answers `None` there, never
    a guess. The tiers from a seventh on (after a notice) are out of scope. From 1 January 2027
    these are art. 38 and art. 14 of the consolidated act of D.Lgs. 5 November 2024 n. 173
    (Normattiva at 2027-01-02: both articles «ABROGATO DAL D.LGS. 5 NOVEMBRE 2024, N. 173»), the
    same percentages and fractions.

    Interest: a late or short payment regularised by the taxpayer carries «interessi moratori
    calcolati al tasso legale con maturazione giorno per giorno» (art. 13 c. 2 D.Lgs. 472/1997,
    Normattiva, read 2026-11-11). The legal rate of each year is fixed by decree of the Minister
    of Economy and Finance (art. 1284 c.c.). The Agenzia's formula is «Imposta da versare x
    tasso legale d'interesse x giorni di ritardo/36500»: the days are the actual days late, and
    a delay that crosses 1 January is counted per calendar year with each year's rate. The
    interest on a shortfall found by the office is not given a rate here (REB-975).
    """

    penalty_percent: Decimal
    # A delay of up to this many days halves the penalty.
    reduced_if_late_up_to_days: int
    reduced_penalty_fraction: Decimal
    # A delay of up to this many days earns one `daily_fraction_denominator`-th of the reduced
    # penalty for each day of delay.
    daily_if_late_up_to_days: int
    daily_fraction_denominator: int
    legal_interest: tuple[LegalInterestRate, ...]
    # The ravvedimento reductions, by days late, up to the last one the pack states (90 days).
    regularisation_tiers: tuple[RegularisationTier, ...] = ()

    def legal_interest_percent(self, year: int) -> Decimal | None:
        """The legal rate of calendar year `year`, `None` when the pack does not carry it
        (a future year's rate is not known, never guessed)."""
        for rate in self.legal_interest:
            if rate.year == year:
                return rate.percent
        return None

    def late_penalty_percent(self, days_late: int) -> Decimal:
        """The penalty, in percent of the amount not paid, for a payment `days_late` days after
        its deadline (`days_late` of 0 is on time, no penalty)."""
        if days_late <= 0:
            return Decimal(0)
        if days_late <= self.daily_if_late_up_to_days:
            reduced = self.penalty_percent * self.reduced_penalty_fraction
            return (reduced * days_late / self.daily_fraction_denominator).quantize(
                Decimal("0.0001"), rounding=ROUND_HALF_UP
            )
        if days_late <= self.reduced_if_late_up_to_days:
            return self.penalty_percent * self.reduced_penalty_fraction
        return self.penalty_percent

    def regularisation_penalty_percent(self, days_late: int) -> Decimal | None:
        """The penalty, in percent of the amount not paid, of a taxpayer who regularises a
        payment `days_late` days after its deadline (`days_late` of 0 is on time, no penalty): the
        penalty of `late_penalty_percent` cut to its ravvedimento tier, four decimals half up.
        `None` after the last tier the pack states: the law has more tiers there and the
        sources read do not settle which one applies (see the class docstring)."""
        if days_late <= 0:
            return Decimal(0)
        for tier in self.regularisation_tiers:
            if days_late <= tier.max_days_late:
                # The un-rounded penalty, so a daily figure is not rounded twice.
                reduced = self.penalty_percent * self.reduced_penalty_fraction
                if days_late <= self.daily_if_late_up_to_days:
                    base = reduced * days_late / self.daily_fraction_denominator
                else:
                    base = reduced
                return (base / tier.divisor).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
        return None


# --- deferral by 30 days ---------------------------------------------------------------


@dataclass(frozen=True)
class DeferralLaw:
    """Paying the balance and the first advance up to 30 days after the deadline.

    Source: art. 17 c. 2 of D.P.R. 7 December 2001 n. 435 (Normattiva, read 2026-11-11):
    «possono essere effettuati entro il trentesimo giorno successivo ai termini ivi previsti,
    maggiorando le somme da versare dello 0,40 per cento a titolo di interesse corrispettivo»;
    Agenzia delle Entrate, Redditi PF 2026 fascicolo 1 and Redditi SC 2026 «Parte generale» § 4.1
    (the first advance follows the balance). The surcharge is added to the amount of the tax's
    own F24 line, never a line of its own, except for the «gestione separata» (`DPPI`).
    """

    surcharge_percent: Decimal
    window_days: int


# --- F24 codes --------------------------------------------------------------------------

F24Line = Literal[
    "irpef_balance",
    "irpef_first_advance",
    "irpef_second_advance",
    "regional_surcharge_balance",
    "municipal_surcharge_balance",
    "municipal_surcharge_advance",
    "substitute_tax_balance",
    "substitute_tax_first_advance",
    "substitute_tax_second_advance",
    "ires_balance",
    "ires_first_advance",
    "ires_second_advance",
    "irap_balance",
    "irap_first_advance",
    "irap_second_advance",
    "chamber_fee",
    "instalment_interest_erario",
    "instalment_interest_regional",
    "instalment_interest_municipal",
    "inps_gs_contribution",
    "inps_gs_contribution_other_cover",
    "inps_gs_instalment",
    "inps_gs_instalment_other_cover",
    "inps_gs_interest",
    "inps_artigiani_interest",
    "inps_commercianti_interest",
    "inps_artigiani_minimum",
    "inps_commercianti_minimum",
    "inps_artigiani_excess",
    "inps_commercianti_excess",
    "inps_artigiani_excess_instalment",
    "inps_commercianti_excess_instalment",
]

# A tax code goes in the «Erario» or «Regioni» section's `codice tributo` column, an INPS
# causale in the «INPS» section's `causale contributo` column.
F24CodeKind = Literal["tax_code", "inps_causale"]


@dataclass(frozen=True)
class F24Code:
    """The F24 code of one line an estimate writes, with the issuing body's own wording.

    The year in the `anno di riferimento` column is the tax year the payment is for (Agenzia,
    Redditi PF 2026 fascicolo 1, rigo RN38 col. 6: advances paid «codici tributo 4033 e 4034,
    anno di riferimento 2025» for tax year 2025). An instalment is written in the
    `rateazione` column as the instalment number and the number of instalments, two digits
    each («NNRR», Agenzia, «Come compilare il modello F24»)."""

    line: F24Line
    code: str
    kind: F24CodeKind
    description: str
    source: str


# --- return filing ----------------------------------------------------------------------

ReturnForm = Literal["redditi_pf", "redditi_sc", "irap"]


@dataclass(frozen=True)
class ReturnFilingRule:
    """When a tax return is filed, by the tax period it is for.

    A person files on a fixed day of the year after the tax period (art. 2 c. 1 of D.P.R.
    22 July 1998 n. 322: «in via telematica tra il 15 aprile e il 31 ottobre dell'anno
    successivo»); a company files by the last day of the tenth month after the period closes
    (art. 2 c. 2: «entro l'ultimo giorno del decimo mese successivo a quello di chiusura del
    periodo d'imposta»). A day that is not a working day moves to the next one
    (`payment_rules.return_filing_date`; the Agenzia's own 2026 example moves 31 October to
    2 November).
    """

    form: ReturnForm
    covers: str
    source: str
    # Exactly one of the two.
    fixed_day: MonthDay | None = None
    months_after_period_end: int | None = None

    def __post_init__(self) -> None:
        if (self.fixed_day is None) == (self.months_after_period_end is None):
            raise ValueError("a filing rule has a fixed day or a number of months, not both")

    def nominal_date(self, period_end: date) -> date:
        """The nominal day the return for the period closing on `period_end` is filed."""
        if self.fixed_day is not None:
            return date(period_end.year + 1, self.fixed_day.month, self.fixed_day.day)
        assert self.months_after_period_end is not None
        month_index = period_end.year * 12 + period_end.month - 1 + self.months_after_period_end
        year, month = divmod(month_index, 12)
        month += 1
        return date(year, month, monthrange(year, month)[1])


# --- withholdings suffered ---------------------------------------------------------------


@dataclass(frozen=True)
class WithholdingCreditLaw:
    """The tax year a withholding suffered on one's own invoices is credited against.

    Source: art. 22 c. 1 lett. c) of D.P.R. 22 December 1986 n. 917 (TUIR), text in force on
    Normattiva on 2026-11-11, and art. 24 c. 1 lett. c) of the testo unico of D.Lgs. 19 June 2026
    n. 117 (GU n. 152 of 3-7-2026, Suppl. Ord. n. 26) from 1 January 2027, the same words:
    «le ritenute alla fonte a titolo di acconto operate sui redditi che concorrono a formare il
    reddito complessivo» are credited against the tax of the period the income is taxed in.
    Two exceptions are written in the same letter. First: «Le ritenute operate nell'anno
    successivo a quello di competenza dei redditi e anteriormente alla presentazione della
    dichiarazione dei redditi possono essere scomputate dall'imposta relativa al periodo
    d'imposta di competenza dei redditi o, alternativamente, dall'imposta relativa al periodo
    d'imposta nel quale sono state operate.» Second: «Le ritenute operate dopo la presentazione
    della dichiarazione dei redditi si scomputano dall'imposta relativa al periodo d'imposta
    nel quale sono state operate.»

    A professional's income is taxed by the cash principle (art. 54 c. 1 TUIR: the sums «percepiti
    nel periodo di imposta»; sums received the year after the withholding agent paid them belong
    to the year in which the agent had to withhold), so the period of the income is the year the
    invoice is collected. This is the rule for a natural person (`income_basis` «cash»).

    A company's rule is the same letter with a different period of the income: art. 79 c. 1 TUIR
    (art. 88 c. 1 of the consolidated act of D.Lgs. 117/2026 from 2027) sends the withholdings a
    company suffers to art. 22, and a company's income is by accrual, not by cash: the
    revenue of a service belongs to the period in which the service «è ultimata» (art. 109 c. 1
    and c. 2 lett. b) TUIR), not to the one in which it is collected. So the credit goes to the
    tax period of the invoice's accrual (`income_basis` «accrual»), whenever the customer
    paid, and that period is the company's fiscal period, not a calendar year (Redditi SC 2026,
    form section RN, row RN15, col. 2: «l'ammontare delle ritenute d'acconto subite»).
    """

    # The withholding may go either to the income's own year or to the year it was operated
    # when it is operated in the year after and before the return is filed.
    alternative_in_year_after_before_filing: bool
    # Where the rule was read, for the figure that shows what it was computed from.
    source: str = ""
    # Which period the income belongs to: the one it is collected in (a person) or the one it
    # accrues in (a company, `withholdings_suffered.company_withholdings_suffered`).
    income_basis: Literal["cash", "accrual"] = "cash"

    def credit_years(
        self, income_year: int, operated_on: date, return_filed_on: date | None
    ) -> tuple[int, ...]:
        """The tax year(s) a withholding operated on `operated_on` on income of `income_year`
        may be credited against, when the return of `income_year` was filed on
        `return_filed_on` (`None`: not yet filed). One year, or two when the law lets the
        taxpayer choose."""
        if operated_on.year <= income_year:
            return (income_year,)
        if return_filed_on is not None and return_filed_on < operated_on:
            return (operated_on.year,)
        # Not yet filed: the letter's first exception is the year after the income's, and a
        # withholding operated later than that is the main rule's, the income's own year.
        if operated_on.year == income_year + 1 and self.alternative_in_year_after_before_filing:
            return (income_year, operated_on.year)
        return (income_year,)


# --- the whole ----------------------------------------------------------------------------


@dataclass(frozen=True)
class PaymentRulesLaw:
    """The payment rules that govern the calendar years from `from_year` until the next entry
    of the same pack. A section a regime has no rule for is `None` (the withholding credit) or an
    empty tuple (the filings, the F24 codes)."""

    from_year: int
    instalments: InstalmentLaw
    deferral: DeferralLaw
    forecast: ForecastAdvanceLaw
    shortfall: ShortfallLaw
    f24_codes: tuple[F24Code, ...]
    filings: tuple[ReturnFilingRule, ...]
    withholding_credit: WithholdingCreditLaw | None = None

    def f24_code(self, line: F24Line) -> F24Code:
        """The code of `line`; `KeyError` when the pack's regime owes no such line."""
        for code in self.f24_codes:
            if code.line == line:
                return code
        raise KeyError(line)

    def filing_rule(self, form: ReturnForm) -> ReturnFilingRule | None:
        for rule in self.filings:
            if rule.form == form:
                return rule
        return None

    def __post_init__(self) -> None:
        lines = [code.line for code in self.f24_codes]
        if len(lines) != len(set(lines)):
            raise ValueError("an F24 line has one code")
        forms = [rule.form for rule in self.filings]
        if len(forms) != len(set(forms)):
            raise ValueError("a return form has one filing rule")


def commercial_days(start: date, end: date) -> int:
    """Days from `start` to `end` by the commercial method: months of thirty days, the last day
    of a month counted as the 30th (the Agenzia's «metodo commerciale», Redditi PF 2026
    fascicolo 1 and Redditi SC 2026 «Parte generale» § 4.4, read 2026-11-12).

    The period runs «dal giorno successivo a quello di scadenza della prima rata fino alla data
    di scadenza della seconda», and the Agenzia's examples count both ends: from 1 to 16 July is
    16 days (0.18%). A first payment on the last day of a month therefore starts the count on
    the 1st of the next, the 28th or 29th of February included: 1 to 16 March is 16 days, the
    length of February never enters. A start on the 30th or 31st is the same case."""
    last_day_of_month = start.day == monthrange(start.year, start.month)[1]
    start_day = _DAYS_IN_A_MONTH if last_day_of_month else min(start.day, _DAYS_IN_A_MONTH)
    end_day = min(end.day, _DAYS_IN_A_MONTH)
    return (
        (end.year - start.year) * _DAYS_IN_A_YEAR
        + (end.month - start.month) * _DAYS_IN_A_MONTH
        + (end_day - start_day)
    )


def round_percent(value: Decimal) -> Decimal:
    """Two decimals, half up: how the Agenzia's tables print an interest rate."""
    return value.quantize(_CENT, rounding=ROUND_HALF_UP)


__all__ = [
    "DeferralLaw",
    "F24Code",
    "F24CodeKind",
    "F24Line",
    "ForecastAdvanceLaw",
    "InstalmentLaw",
    "LegalInterestRate",
    "PaymentKind",
    "PaymentRulesLaw",
    "RegularisationTier",
    "ReturnFilingRule",
    "ReturnForm",
    "ShortfallLaw",
    "WithholdingCreditLaw",
    "commercial_days",
    "round_percent",
]
