"""IRPEF and its regional surcharge, as law data (REB-1062, taxes design § 1).

Two pure lookups, both keyed by what the law keys them by and neither guessing:

- `IrpefLaw.gross_tax(taxable_income)`: the gross IRPEF (the gross tax) of art. 11 c. 1
  TUIR, the bracket rates applied slice by slice to the income net of deductible charges.
  No detraction, credit or other deduction is subtracted (that is the estimate card's, and
  the art. 16-ter c. 5-bis TUIR cut of 440 euro on the 19% detractions, party donations and
  disaster insurance premiums of an income above 200,000 euro, added by L. 199/2025 art. 1
  c. 4, is a detraction rule, not a bracket).
- `IrpefLaw.regional_surcharge(region, taxable_income)`: the regional surcharge
  of the taxpayer's region or autonomous province, from the
  rules each region publishes on the Finance Department's table.

The region key
--------------
`region` is the ISTAT region code as two digits (`"01"` Piemonte, `"02"` Valle d'Aosta,
`"03"` Lombardia, `"05"` Veneto, `"06"` Friuli Venezia Giulia, `"07"` Liguria, `"08"`
Emilia-Romagna, `"09"` Toscana, `"10"` Umbria, `"11"` Marche, `"12"` Lazio, `"13"`
Abruzzo, `"14"` Molise, `"15"` Campania, `"16"` Puglia, `"17"` Basilicata, `"18"`
Calabria, `"19"` Sicilia, `"20"` Sardegna), except Trentino-Alto Adige/Suedtirol, whose
code `"04"` is not a key: the surcharge there is the autonomous province's, so the keys are
`"TN"` (Trento) and `"BZ"` (Bolzano). That is 21 keys. The Finance Department's
own region code (01 Abruzzo ... 21 Veneto, alphabetical) is not ISTAT's and is kept
only in each rule's `source`. The key to use for a taxpayer is the region of the tax
domicile (art. 50 c. 1 D.Lgs. 446/1997, as the Finanze's «Disciplina del tributo» page
states it: the region where the taxpayer has the tax domicile on 1 January of the year).

What is computed and what is not
--------------------------------
Computed: the rates by bracket, the way each region applies them (by slice, or on the whole
income by the bracket it falls in), the income thresholds a region sets (a flat rate or a
full exemption up to an amount), the detractions that depend on income alone (Lazio, Umbria,
Bolzano, Bolzano's second one growing with income) and, from the household the taxpayer states
(`fiscal/household.py`), the detractions per child and the reduced rates for a disabled person
or a disabled child that Piemonte, Veneto, Marche, Campania, Puglia, Sardegna, Trento and Bolzano
set (REB-1087). Never negative: the regions' detractions do not make a credit.

Not computed, and written as text in `RegionalSurchargeLaw.unmodelled`: the sub-points of those
rules that no source settles (Puglia's 375 euro with fewer than four children, Campania's 30
euro for a disabled child) and what depends on income the profile does not hold. That the
surcharge is due only if the IRPEF, net of its detractions, is due (art. 50 c. 2 D.Lgs.
446/1997, Finanze «Disciplina del tributo») is the estimate's, as is the exclusion of the
flat-rate regime from the surcharge (same page).

The IRPEF's own detractions
---------------------------
`IrpefLaw.self_employment_detraction` is art. 13 c. 5 TUIR and `IrpefLaw.family_detractions` is
art. 12 c. 1 lett. a), c) and d): the spouse, the children of 21 to 29 and the disabled ones, the
cohabiting ascendants. Both are subtracted from the gross tax (`gross_tax`), which the estimate
does. They are data of the year, with their wording and source, and a law that does not carry one
makes none: a detraction no source is cited for is not applied.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal
from typing import Literal

from pigrocrm.core.fiscal.household import (
    MONTHS_IN_YEAR,
    Household,
    HouseholdMember,
    age_on,
    months_in_age_band,
)

_CENT = Decimal("0.01")
_FOUR_DECIMALS = Decimal("0.0001")
_HUNDRED = Decimal(100)
_ZERO = Decimal(0)

# How a bracketed table is applied. `by_slice`: each rate to the part of the income inside its
# bracket, as the IRPEF is (D.Lgs. 68/2011 art. 6 says the regions' rates follow the IRPEF
# brackets and rise with them; the Finanze's «Disciplina del tributo» page). `whole_income`:
# the rate of the bracket the income falls in, on all of it, which a region says in words
# (Friuli Venezia Giulia).
RateMethod = Literal["by_slice", "whole_income"]


@dataclass(frozen=True)
class RateBracket:
    """`rate` percent on the income up to `up_to` (inclusive), `None` for no upper limit."""

    up_to: Decimal | None
    rate: Decimal


@dataclass(frozen=True)
class IncomeBandDetraction:
    """A fixed amount the region takes off the surcharge when the taxable income is within
    the band (`from_income` exclusive, `to_income` inclusive; `None` is unbounded). It never
    leaves the surcharge below zero: the regions' texts say it does not make a credit."""

    from_income: Decimal | None
    to_income: Decimal | None
    amount: Decimal

    def amount_for(self, taxable_income: Decimal) -> Decimal:
        above = self.from_income is None or taxable_income > self.from_income
        within = self.to_income is None or taxable_income <= self.to_income
        return self.amount if above and within else _ZERO


@dataclass(frozen=True)
class ProgressiveDetraction:
    """A detraction that grows with the taxable income: nothing up to `from_income`, then
    `maximum` times the part of the income above it over `full_at - from_income`, and `maximum`
    from `full_at` on, with no upper limit (Bolzano, l.p. 9/1998 art. 21-sexiesdecies c. 4-bis:
    125 euro times (income - 50,000) over 25,000, at most 125 euro). With `to_income` set it is
    nothing above that income."""

    from_income: Decimal
    full_at: Decimal
    maximum: Decimal
    # At or below this income the detraction is due, nothing above: set where the text states no
    # upper limit but the reading above one is doubtful (Bolzano's c. 4-bis, see its rule).
    to_income: Decimal | None = None

    def amount_for(self, taxable_income: Decimal) -> Decimal:
        if taxable_income <= self.from_income:
            return _ZERO
        if self.to_income is not None and taxable_income > self.to_income:
            return _ZERO
        share = min(
            Decimal(1), (taxable_income - self.from_income) / (self.full_at - self.from_income)
        )
        return self.maximum * share


# Which children a regional rule counts, tested on the household's year: any dependent child, from
# the month it is born (`any`); under 30 or disabled (`under_30_or_disabled`, Piemonte); those
# who have the art. 12 c. 1 lett. c) detraction, aged 21 to under 30 or disabled from 21 on
# (`lett_c_children`, Puglia, whose law increases «le detrazioni previste dall'articolo 12, comma
# 1, lettera c)»); a minor on 1 January, for the months the minority lasted at least 15 days
# (`minor_on_1_january`, Sardegna).
ChildScope = Literal["any", "under_30_or_disabled", "lett_c_children", "minor_on_1_january"]
_MINOR_AGE = 18
_UNDER_AGE = 30
# The ages of art. 12 c. 1 lett. c), the same as `FamilyDetractions.child_from_age` and
# `child_until_age` of the year's law: a rule that counts those children reads them here.
_LETT_C_FROM_AGE = 21


def _weight(member: HouseholdMember, months: int) -> Decimal:
    """The share of a full year's, full detraction a member gives: share times months."""
    months = min(member.months, months)
    return Decimal(member.share_percent) / _HUNDRED * Decimal(months) / Decimal(MONTHS_IN_YEAR)


@dataclass(frozen=True)
class ChildDetractionRule:
    """A region's detraction from the surcharge for the household's children, in proportion to
    the share and the months each is dependent (every region's text says so or refers to art. 12
    TUIR, which does). `per_child` needs at least `min_children` counted children; `per_disabled`
    is the amount for each disabled child, and with `disabled_needs_min_children` it needs the
    same minimum (Puglia, read literally). `per_child_counts_disabled` false leaves a disabled
    child out of `per_child` while it still counts toward the minimum (Campania: whether the 30
    euro adds to the 40 is not settled). `income_cap` is the taxable income at most which the
    detraction is due."""

    per_child: Decimal = _ZERO
    min_children: int = 1
    per_disabled: Decimal = _ZERO
    disabled_needs_min_children: bool = False
    per_child_counts_disabled: bool = True
    income_cap: Decimal | None = None
    scope: ChildScope = "any"

    def counted(self, household: Household) -> list[tuple[HouseholdMember, Decimal]]:
        """The children this rule counts, each with its weight (share times months over 12)."""
        year = household.year
        counted: list[tuple[HouseholdMember, Decimal]] = []
        for child in household.children:
            birth = child.birth_date
            assert birth is not None  # noqa: S101  a child's birth date is validated
            if self.scope == "any":
                months = months_in_age_band(birth, year, minimum=0, below=None)
            elif self.scope == "under_30_or_disabled":
                months = months_in_age_band(
                    birth, year, minimum=0, below=None if child.disabled else _UNDER_AGE
                )
            elif self.scope == "lett_c_children":
                months = months_in_age_band(
                    birth,
                    year,
                    minimum=_LETT_C_FROM_AGE,
                    below=None if child.disabled else _UNDER_AGE,
                )
            else:
                first = date(year, 1, 1)
                if birth > first or age_on(birth, first) >= _MINOR_AGE:
                    continue
                months = months_in_age_band(
                    birth, year, minimum=0, below=_MINOR_AGE, day_of_month=15
                )
            weight = _weight(child, months)
            if weight > 0:
                counted.append((child, weight))
        return counted

    def amount(self, taxable_income: Decimal, household: Household) -> Decimal:
        if self.income_cap is not None and taxable_income > self.income_cap:
            return _ZERO
        counted = self.counted(household)
        enough = len(counted) >= self.min_children
        total = _ZERO
        if enough:
            total += self.per_child * sum(
                (
                    weight
                    for child, weight in counted
                    if self.per_child_counts_disabled or not child.disabled
                ),
                _ZERO,
            )
        if enough or not self.disabled_needs_min_children:
            total += self.per_disabled * sum(
                (weight for child, weight in counted if child.disabled), _ZERO
            )
        return total


@dataclass(frozen=True)
class ReducedRateRule:
    """A rate that replaces the region's whole table for a taxable income of at most
    `income_cap` when the household has a disabled person: `taxpayer_or_dependant` (Veneto: the
    taxpayer, or any person fiscally dependent on them) or `disabled_child` (Marche). With a
    disabled dependant who depends on several people the sum of their taxable incomes is tested:
    `other_supporters_income` states the others', and `None` leaves the answer open."""

    rate: Decimal
    income_cap: Decimal
    who: Literal["taxpayer_or_dependant", "disabled_child"]

    def applies(self, taxable_income: Decimal, household: Household | None) -> bool | None:
        """Whether the reduced rate applies: `True`, `False`, or `None` when it depends on the
        incomes of the others a disabled dependant is dependent on and the household does not
        state them."""
        if household is None or taxable_income > self.income_cap:
            return False
        if self.who == "taxpayer_or_dependant" and household.taxpayer_disabled:
            return True
        dependants = household.children if self.who == "disabled_child" else household.members
        if not any(member.disabled for member in dependants):
            return False
        if household.other_supporters_income is None:
            return None
        return taxable_income + household.other_supporters_income <= self.income_cap


@dataclass(frozen=True)
class RegionalSurchargeLaw:
    """One region's (or autonomous province's) surcharge for one tax year."""

    key: str
    name: str
    brackets: tuple[RateBracket, ...]
    method: RateMethod = "by_slice"
    # At or below this income a single rate applies to the whole income, in place of the
    # brackets (Lazio 1.73%, Umbria 1.23%, both up to 28,000 euro).
    flat_rate_up_to: Decimal | None = None
    flat_rate: Decimal | None = None
    # At or below this income nothing is due (Trento: deduction of 30,000 euro for an income
    # up to 30,000; Valle d'Aosta: exempt up to 15,000 euro).
    exempt_up_to: Decimal | None = None
    # What the exemption threshold is tested on: the taxable income (net of deductible charges)
    # or, as Valle d'Aosta's page says, the «reddito complessivo» before them.
    exempt_tested_on: Literal["taxable", "total"] = "taxable"
    detractions: tuple[IncomeBandDetraction | ProgressiveDetraction, ...] = ()
    # What depends on the household the taxpayer states (`fiscal/household.py`): a detraction per
    # child, and a rate for a disabled person or child.
    child_rule: ChildDetractionRule | None = None
    reduced_rate: ReducedRateRule | None = None
    # The rules the region states that the lookup does not apply, in the region's words or a
    # close summary: a sub-point no source settles, or a fact the profile does not hold.
    unmodelled: str = ""
    # Where it was read: the Finanze page, its publication date, the regional law.
    source: str = ""

    def surcharge(self, taxable_income: Decimal, household: Household | None = None) -> Decimal:
        """The surcharge on `taxable_income`; with a `household` its detractions and reduced
        rate apply, without one they do not (the taxpayer has stated none)."""
        if taxable_income <= 0:
            return Decimal("0.00")
        if self.exempt_up_to is not None and taxable_income <= self.exempt_up_to:
            return Decimal("0.00")
        if self.reduced_rate is not None and self.reduced_rate.applies(taxable_income, household):
            gross = taxable_income * self.reduced_rate.rate / _HUNDRED
        elif (
            self.flat_rate_up_to is not None
            and self.flat_rate is not None
            and taxable_income <= self.flat_rate_up_to
        ):
            gross = taxable_income * self.flat_rate / _HUNDRED
        elif self.method == "whole_income":
            gross = taxable_income * _bracket_of(self.brackets, taxable_income).rate / _HUNDRED
        else:
            gross = _tax_by_slice(self.brackets, taxable_income)
        for detraction in self.detractions:
            gross -= detraction.amount_for(taxable_income)
        if self.child_rule is not None and household is not None:
            gross -= self.child_rule.amount(taxable_income, household)
        return max(gross, Decimal(0)).quantize(_CENT, rounding=ROUND_HALF_UP)

    def undetermined(self, taxable_income: Decimal, household: Household | None) -> bool:
        """Whether the surcharge depends on a fact the household leaves unstated: a disabled
        dependant who may depend on others too, whose incomes decide the reduced rate."""
        return (
            self.reduced_rate is not None
            and self.reduced_rate.applies(taxable_income, household) is None
        )

    def household_applied(self, taxable_income: Decimal, household: Household | None) -> list[str]:
        """What of the household's rules changed the surcharge, as short English labels the
        estimate words in Italian: `reduced_rate`, `child_detraction`."""
        if household is None:
            return []
        applied: list[str] = []
        if self.reduced_rate is not None and self.reduced_rate.applies(taxable_income, household):
            applied.append("reduced_rate")
        if self.child_rule is not None and self.child_rule.amount(taxable_income, household) > 0:
            applied.append("child_detraction")
        return applied

    @property
    def reads_household(self) -> bool:
        return self.child_rule is not None or self.reduced_rate is not None


@dataclass(frozen=True)
class DeductibleCharge:
    """A charge the law takes off the total income before the tax is computed, with the words
    that say so. `wording` is the statute's own Italian text, kept verbatim so a later reader can
    check it against the source; `source` names it and the day it was read."""

    wording: str
    source: str


def _ratio(numerator: Decimal, denominator: Decimal) -> Decimal:
    """A ratio the law truncates to its first four decimals (art. 13 c. 6 and art. 12 c. 4
    TUIR): cut, never rounded."""
    return (numerator / denominator).quantize(_FOUR_DECIMALS, rounding=ROUND_DOWN)


@dataclass(frozen=True)
class SelfEmploymentDetraction:
    """The detraction of art. 13 c. 5 TUIR for a person with income of art. 53 (an art or a
    profession) or art. 66 (the simplified business regime), not cumulable with the employee and
    pension detractions of c. 1 to 4 and not prorated by period. It is computed on the reference
    income and subtracted from the gross tax: `full_amount` up to `full_up_to`; `middle_base`
    plus `middle_extra` times the ratio of `middle_to` less the income over `middle_span` up to
    `middle_to`; `upper_amount` times the ratio of `upper_to` less the income over `upper_span`
    up to `upper_to`; nothing above. The ratios are cut at four decimals (c. 6).

    The 50 euro of c. 5-ter for an income from 11,000 to 17,000 euro is not added: the Agenzia's
    technical specifications apply it to this group and its explanation for the taxpayer (Redditi
    PF fasc. 1, rigo RN7) does not, so a sentence that settles it is missing (A5 register)."""

    wording: str
    source: str
    full_up_to: Decimal
    full_amount: Decimal
    middle_to: Decimal
    middle_base: Decimal
    middle_extra: Decimal
    middle_span: Decimal
    upper_to: Decimal
    upper_amount: Decimal
    upper_span: Decimal

    def amount(self, reference_income: Decimal) -> Decimal:
        income = reference_income
        if income <= self.full_up_to:
            value = self.full_amount
        elif income <= self.middle_to:
            value = self.middle_base + self.middle_extra * _ratio(
                self.middle_to - income, self.middle_span
            )
        elif income <= self.upper_to:
            value = self.upper_amount * _ratio(self.upper_to - income, self.upper_span)
        else:
            value = _ZERO
        return value.quantize(_CENT, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class SpouseBand:
    """`amount` euro more on the spouse's detraction for a reference income above `from_income`
    and at most `to_income` (art. 12 c. 1 lett. b)."""

    from_income: Decimal
    to_income: Decimal
    amount: Decimal


@dataclass(frozen=True)
class FamilyDetractions:
    """The detractions of art. 12 c. 1 TUIR in force for 2026, each from the reference income
    and for a full year, a full share and one person; `for_household` prorates them.

    * Spouse (lett. a): `spouse_full` less `spouse_reduction` times income over `spouse_low`
      up to `spouse_low`; `spouse_mid` up to `spouse_mid_to`; then `spouse_mid` times
      (`spouse_high` less income) over `spouse_high - spouse_mid_to` up to `spouse_high`; plus
      the bands of `spouse_bands`.
    * Child (lett. c): `child_amount` times the ratio of the limit less the income over the
      limit, the limit being `child_limit` plus `child_limit_step` for every counted child after
      the first. Only a child of `child_from_age` to under `child_until_age` counts, and a
      disabled one from `child_from_age` on: the younger ones have no detraction.
    * Ascendant (lett. d): `ascendant_amount` times the ratio of `ascendant_limit` less the
      income over `ascendant_limit`, for each cohabiting ascendant.

    The ratios of lett. c) and d) are cut at four decimals and no detraction is due when one is
    zero, negative or one (c. 4); lett. a)'s are not cut, a difference of a cent or two."""

    wording: str
    source: str
    spouse_full: Decimal
    spouse_reduction: Decimal
    spouse_low: Decimal
    spouse_mid: Decimal
    spouse_mid_to: Decimal
    spouse_high: Decimal
    spouse_bands: tuple[SpouseBand, ...]
    child_amount: Decimal
    child_limit: Decimal
    child_limit_step: Decimal
    child_from_age: int
    child_until_age: int
    ascendant_amount: Decimal
    ascendant_limit: Decimal

    def spouse(self, income: Decimal) -> Decimal:
        if income <= self.spouse_low:
            value = self.spouse_full - self.spouse_reduction * income / self.spouse_low
        elif income <= self.spouse_mid_to:
            value = self.spouse_mid
        elif income <= self.spouse_high:
            value = (
                self.spouse_mid
                * (self.spouse_high - income)
                / (self.spouse_high - self.spouse_mid_to)
            )
        else:
            return _ZERO
        for band in self.spouse_bands:
            if band.from_income < income <= band.to_income:
                value += band.amount
        return value

    def child(self, income: Decimal, children: int) -> Decimal:
        limit = self.child_limit + self.child_limit_step * (children - 1)
        ratio = _ratio(limit - income, limit)
        if ratio <= 0 or ratio >= 1:
            return _ZERO
        return self.child_amount * ratio

    def ascendant(self, income: Decimal) -> Decimal:
        ratio = _ratio(self.ascendant_limit - income, self.ascendant_limit)
        if ratio <= 0 or ratio >= 1:
            return _ZERO
        return self.ascendant_amount * ratio

    def child_months(self, child: HouseholdMember, year: int) -> int:
        """The months of `year` a child gives the lett. c) detraction: those at `child_from_age`
        or more, and under `child_until_age` unless disabled, read at the month's end and
        within the months the child was dependent."""
        birth = child.birth_date
        assert birth is not None  # noqa: S101  a child's birth date is validated
        until = None if child.disabled else self.child_until_age
        return min(
            child.months,
            months_in_age_band(birth, year, minimum=self.child_from_age, below=until),
        )

    def for_household(
        self, household: Household, reference_income: Decimal
    ) -> list[tuple[str, Decimal]]:
        """The detractions the household gives at `reference_income`, as `(kind, amount)` with
        kind `spouse`, `children` or `ascendants` (each total rounded half up to the cent), the
        ones that are zero left out."""
        year_fraction = Decimal(MONTHS_IN_YEAR)
        result: list[tuple[str, Decimal]] = []
        spouse = household.spouse
        if spouse is not None:
            amount = self.spouse(reference_income) * Decimal(spouse.months) / year_fraction
            result.append(("spouse", amount))
        counted = [
            (child, months)
            for child in household.children
            if (months := self.child_months(child, household.year)) > 0
        ]
        if counted:
            each = self.child(reference_income, len(counted))
            result.append(
                (
                    "children",
                    sum((each * _weight(child, months) for child, months in counted), _ZERO),
                )
            )
        ascendants = household.ascendants
        if ascendants:
            each = self.ascendant(reference_income)
            result.append(
                (
                    "ascendants",
                    sum((each * _weight(member, member.months) for member in ascendants), _ZERO),
                )
            )
        return [
            (kind, amount.quantize(_CENT, rounding=ROUND_HALF_UP))
            for kind, amount in result
            if amount > 0
        ]


@dataclass(frozen=True)
class IrpefLaw:
    """The IRPEF of tax year `year`: the national brackets and every region's surcharge. The
    pack holds one per year and reads it by exact year (`FiscalPack.irpef_for`): the brackets and
    the regional rates are re-set by law and by every region, so a later year never reads an
    earlier one's.

    `social_security_deduction` is the deduction of the compulsory social security contributions
    a person pays (REB-1072); a law that does not carry it (`None`) makes no deduction, since a
    deduction no source is cited for is not applied. The two detractions from the gross tax
    work the same way (REB-1087)."""

    year: int
    brackets: tuple[RateBracket, ...]
    regional: tuple[RegionalSurchargeLaw, ...]
    social_security_deduction: DeductibleCharge | None = None
    # The detractions from the gross tax (module docstring): a law that does not carry one
    # subtracts none.
    self_employment_detraction: SelfEmploymentDetraction | None = None
    family_detractions: FamilyDetractions | None = None
    # Where the brackets were read, for the figure that shows what it was computed from.
    brackets_source: str = ""

    def gross_tax(self, taxable_income: Decimal) -> Decimal:
        """Gross IRPEF of `taxable_income` (the income net of deductible charges), in euro
        and cents, rounded half up. Zero for an income of zero or less."""

        if taxable_income <= 0:
            return Decimal("0.00")
        return _tax_by_slice(self.brackets, taxable_income).quantize(_CENT, rounding=ROUND_HALF_UP)

    def regional_law(self, region: str) -> RegionalSurchargeLaw | None:
        for rule in self.regional:
            if rule.key == region:
                return rule
        return None

    def regional_surcharge(
        self, region: str, taxable_income: Decimal, household: Household | None = None
    ) -> Decimal | None:
        """The regional surcharge of `region` (see the module docstring for the key) on
        `taxable_income`, `None` when the key is not one the law knows: a missing region is
        no figure, never a guess."""

        rule = self.regional_law(region)
        return None if rule is None else rule.surcharge(taxable_income, household)


def _bracket_of(brackets: tuple[RateBracket, ...], income: Decimal) -> RateBracket:
    for bracket in brackets:
        if bracket.up_to is None or income <= bracket.up_to:
            return bracket
    raise ValueError("a bracket table must end with an unbounded bracket")


def _tax_by_slice(brackets: tuple[RateBracket, ...], income: Decimal) -> Decimal:
    tax = Decimal(0)
    lower = Decimal(0)
    for bracket in brackets:
        if income <= lower:
            break
        upper = income if bracket.up_to is None else min(income, bracket.up_to)
        tax += (upper - lower) * bracket.rate / _HUNDRED
        if bracket.up_to is None:
            break
        lower = bracket.up_to
    return tax


def region_key(region_code: str | None, province: str | None) -> str | None:
    """The key `IrpefLaw.regional_surcharge` takes for a taxpayer's place (module docstring): the
    ISTAT region code, or `BZ` or `TN` for Trentino-Alto Adige, whose surcharge is the
    autonomous province's, so the province decides. `None` when there is no region (an address
    that is not Italian, a province ISTAT does not list) or, for Trentino-Alto Adige, no
    province of the two: a region is never guessed."""
    if region_code is None:
        return None
    if region_code == "04":
        key = (province or "").strip().upper()
        return key if key in ("BZ", "TN") else None
    return region_code


__all__ = [
    "DeductibleCharge",
    "ChildDetractionRule",
    "FamilyDetractions",
    "IncomeBandDetraction",
    "IrpefLaw",
    "ProgressiveDetraction",
    "ReducedRateRule",
    "SelfEmploymentDetraction",
    "SpouseBand",
    "RateBracket",
    "RateMethod",
    "RegionalSurchargeLaw",
    "region_key",
]
