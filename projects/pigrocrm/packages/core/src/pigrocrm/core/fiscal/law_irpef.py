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
full exemption up to an amount) and the detractions that depend on income alone (Lazio,
Umbria, Bolzano). Never negative: the regions' detractions do not make a credit.

Not computed, and written as text in `RegionalSurchargeLaw.unmodelled`: whatever depends on
the taxpayer's household or condition (detractions per child, per disabled child, a reduced
rate for a disabled taxpayer), because the profile holds none of it; and that the surcharge is
due only if the IRPEF, net of its detractions, is due (art. 50 c. 2 D.Lgs. 446/1997, Finanze
«Disciplina del tributo»), which is the estimate card's, as is the exclusion of the flat-rate
regime from the surcharge (same page).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

_CENT = Decimal("0.01")
_HUNDRED = Decimal(100)

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
    detractions: tuple[IncomeBandDetraction, ...] = ()
    # The rules the region states that depend on the taxpayer's household or condition, in
    # the region's words or a close summary. Not computed.
    unmodelled: str = ""
    # Where it was read: the Finanze page, its publication date, the regional law.
    source: str = ""

    def surcharge(self, taxable_income: Decimal) -> Decimal:
        if taxable_income <= 0:
            return Decimal("0.00")
        if self.exempt_up_to is not None and taxable_income <= self.exempt_up_to:
            return Decimal("0.00")
        if (
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
            above = detraction.from_income is None or taxable_income > detraction.from_income
            within = detraction.to_income is None or taxable_income <= detraction.to_income
            if above and within:
                gross -= detraction.amount
        return max(gross, Decimal(0)).quantize(_CENT, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class DeductibleCharge:
    """A charge the law takes off the total income before the tax is computed, with the words
    that say so. `wording` is the statute's own Italian text, kept verbatim so a later reader can
    check it against the source; `source` names it and the day it was read."""

    wording: str
    source: str


@dataclass(frozen=True)
class IrpefLaw:
    """The IRPEF of tax year `year`: the national brackets and every region's surcharge. The
    pack holds one per year and reads it by exact year (`FiscalPack.irpef_for`): the brackets and
    the regional rates are re-set by law and by every region, so a later year never reads an
    earlier one's.

    `social_security_deduction` is the deduction of the compulsory social security contributions
    a person pays (REB-1072); a law that does not carry it (`None`) makes no deduction, since a
    deduction no source is cited for is not applied."""

    year: int
    brackets: tuple[RateBracket, ...]
    regional: tuple[RegionalSurchargeLaw, ...]
    social_security_deduction: DeductibleCharge | None = None
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

    def regional_surcharge(self, region: str, taxable_income: Decimal) -> Decimal | None:
        """The regional surcharge of `region` (see the module docstring for the key) on
        `taxable_income`, `None` when the key is not one the law knows: a missing region is
        no figure, never a guess."""

        rule = self.regional_law(region)
        return None if rule is None else rule.surcharge(taxable_income)


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
    "IncomeBandDetraction",
    "IrpefLaw",
    "RateBracket",
    "RateMethod",
    "RegionalSurchargeLaw",
    "region_key",
]
