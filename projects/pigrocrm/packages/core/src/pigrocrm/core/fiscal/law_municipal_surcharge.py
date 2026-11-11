"""The municipal IRPEF surcharge of every municipality, per tax year (REB-1063, taxes design § 1).

The surcharge (addizionale comunale all'IRPEF) is set by each municipality's own deliberation:
a rate, or rates by income bracket, and sometimes an exemption up to an income. The
Dipartimento delle Finanze publishes the lot, one list per tax year, rebuilt daily from the
deliberations on its portal («Addizionale comunale all'Irpef > Aliquote applicabili > Elenchi
generali aggiornati quotidianamente»). `scripts/import_municipal_surcharge.py` turns a list into
`datasets/municipal_surcharge_<year>.csv` (and a `.source.json` that says where and when), and
this module reads those files. Nothing here fetches anything.

The key is the municipality's cadastral code, the Finanze's own and the one `places` hands out
(`Place.cadastral_code`, REB-1069), so a legal entity's address leads straight to its row.

What a lookup can say
---------------------
`MunicipalSurchargeLaw.lookup(year, cadastral_code)` is `None` for a year no list was shipped for
and for a code the list does not carry: no figure, never a guess. Otherwise it is the row, with a
`status`:

* `adopted`: the rates and the exemption of the municipality's deliberation, parsed;
* `none`: no surcharge instituted. Only a list read after 20 December of its year says this, since
  until then the Finanze shows the same `0*` for a municipality that has not deliberated yet;
* `pending`: no deliberation on the portal on the day the list was read (`0*` in a list read
  before 21 December): no figure, and the list's `final` flag in its sidecar says it will change;
* `irregular`: the deliberation's sentences do not make brackets (a typo in a municipality's
  publication): no figure, and the importer names each one when it runs.

`rate(year, cadastral_code, taxable_income)` is the percentage the deliberation applies to that
income, or `None` when the row has no figure.

`surcharge(income)` is the amount (REB-1072): the exemption threshold first, then the bracket rates
slice by slice. Both come from primary text read on 2026-11-11. The threshold: art. 1 c. 11
D.L. 138/2011, in Normattiva's notes to art. 1 D.Lgs. 360/1998, says the exemption is «un limite
di reddito al di sotto del quale l'addizionale comunale all'imposta sul reddito delle persone
fisiche non è dovuta e, nel caso di superamento del suddetto limite, la stessa si applica al
reddito complessivo»: at or below it nothing, above it the whole income is taxed, not only the
excess. The brackets: the Finanze's «Disciplina del tributo» says a municipality's rates must be
«articolate secondo i medesimi scaglioni di reddito stabiliti per l'IRPEF nazionale, nonché
diversificate e crescenti», and the Agenzia delle Entrate's Redditi PF 2026 instructions (fascicolo
1, rigo RV9) say the box «Aliquote per scaglioni» is for a municipality that «ha deliberato
l'applicazione di diverse aliquote articolate per scaglioni di reddito con gli stessi criteri
previsti per l'Irpef». The IRPEF is computed by slice, so each rate is applied to the part of the
income inside its bracket. Neither text writes the arithmetic out, so that last step is a reading,
which the estimate says in its assumptions (A5 row «How a municipality's bracket rates are
applied»). A municipality that exempts a category of taxpayer (pensioners, employees, large
families: the Department's flags 5 and 6, and some of flag 0) has `category_exemption` set and
its general figures are what is carried: the exemption is for a taxpayer this module is not asked
about, and no figure here applies it.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from functools import cache
from pathlib import Path
from typing import Literal

_CENT = Decimal("0.01")
_HUNDRED = Decimal(100)
DATASETS = Path(__file__).resolve().parent / "datasets"
_PREFIX = "municipal_surcharge_"

SurchargeStatus = Literal["adopted", "none", "pending", "irregular"]


@dataclass(frozen=True, slots=True)
class SurchargeBracket:
    """`rate` percent for an income up to `up_to` (inclusive), `None` for no upper limit."""

    up_to: Decimal | None
    rate: Decimal


@dataclass(frozen=True, slots=True)
class MunicipalSurcharge:
    """One municipality's surcharge for one tax year, as the Finanze's list has it."""

    year: int
    cadastral_code: str
    name: str
    province: str
    status: SurchargeStatus
    # The rates in income order, the last one open. Empty unless the status is `adopted`.
    brackets: tuple[SurchargeBracket, ...]
    # At or below this income nothing is due («l'importo fino al quale si applica
    # l'esenzione», the list's own column); `None` when the municipality has no general exemption.
    exemption_up_to: Decimal | None
    # The municipality also exempts a category of taxpayer, which no figure here applies.
    category_exemption: bool
    # The deliberation's number, its date and the day the portal published it, as the list
    # prints them (empty when there is none), and the publisher's note.
    deliberation_number: str
    deliberation_date: str
    published: str
    note: str

    def rate(self, taxable_income: Decimal) -> Decimal | None:
        """The percentage the deliberation applies to `taxable_income`: zero at or below the
        exemption and for a municipality that never instituted the surcharge, the rate of the
        bracket the income falls in otherwise, `None` when there is no figure (`pending`,
        `irregular`). A negative income is not an income: `ValueError`."""
        if taxable_income < 0:
            raise ValueError("a taxable income is not negative")
        if self.status == "none":
            return Decimal(0)
        if self.status != "adopted":
            return None
        if self.exemption_up_to is not None and taxable_income <= self.exemption_up_to:
            return Decimal(0)
        for bracket in self.brackets:
            if bracket.up_to is None or taxable_income <= bracket.up_to:
                return bracket.rate
        raise ValueError("a bracket table must end with an unbounded bracket")

    def surcharge(self, taxable_income: Decimal) -> Decimal | None:
        """The surcharge on `taxable_income` (the income net of deductible charges), in euro and
        cents rounded half up: zero for a municipality that never instituted it and at or below
        the exemption threshold, the rates of the brackets applied slice by slice above it, the
        whole income counting (see the module docstring). `None` when the row has no figure
        (`pending`, `irregular`). A negative income is not an income: `ValueError`."""
        if taxable_income < 0:
            raise ValueError("a taxable income is not negative")
        if self.status == "none":
            return Decimal("0.00")
        if self.status != "adopted":
            return None
        if self.exemption_up_to is not None and taxable_income <= self.exemption_up_to:
            return Decimal("0.00")
        amount = Decimal(0)
        lower = Decimal(0)
        for bracket in self.brackets:
            if taxable_income <= lower:
                break
            upper = taxable_income if bracket.up_to is None else min(taxable_income, bracket.up_to)
            amount += (upper - lower) * bracket.rate / _HUNDRED
            if bracket.up_to is None:
                break
            lower = bracket.up_to
        else:
            raise ValueError("a bracket table must end with an unbounded bracket")
        return amount.quantize(_CENT, rounding=ROUND_HALF_UP)


@dataclass(frozen=True, slots=True)
class MunicipalSurchargeYear:
    """The list of one tax year: its rows by cadastral code and what its sidecar says about it."""

    year: int
    downloaded: str
    # Read after 20 December of the year, when the Finanze has carried the previous year's rate
    # over to a municipality that did not deliberate: nothing in it is `pending`.
    final: bool
    url: str
    rows: dict[str, MunicipalSurcharge]


def _brackets(text: str) -> tuple[SurchargeBracket, ...]:
    if not text:
        return ()
    brackets = []
    for part in text.split("|"):
        up_to, rate = part.split(":")
        brackets.append(SurchargeBracket(Decimal(up_to) if up_to else None, Decimal(rate)))
    return tuple(brackets)


def _load(year: int) -> MunicipalSurchargeYear:
    stem = DATASETS / f"{_PREFIX}{year}"
    source = json.loads(stem.with_suffix(".source.json").read_text(encoding="utf-8"))
    rows: dict[str, MunicipalSurcharge] = {}
    with stem.with_suffix(".csv").open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            rows[row["cadastral_code"]] = MunicipalSurcharge(
                year=year,
                cadastral_code=row["cadastral_code"],
                name=row["name"],
                province=row["province"],
                status=row["status"],  # type: ignore[arg-type]
                brackets=_brackets(row["brackets"]),
                exemption_up_to=Decimal(row["exemption_up_to"]) if row["exemption_up_to"] else None,
                category_exemption=row["category_exemption"] == "1",
                deliberation_number=row["deliberation_number"],
                deliberation_date=row["deliberation_date"],
                published=row["published"],
                note=row["note"],
            )
    return MunicipalSurchargeYear(
        year=year,
        downloaded=source["downloaded"],
        final=source["final"],
        url=source["url"],
        rows=rows,
    )


@cache
def _year(year: int) -> MunicipalSurchargeYear:
    return _load(year)


@cache
def _years() -> tuple[int, ...]:
    return tuple(
        sorted(int(path.stem.removeprefix(_PREFIX)) for path in DATASETS.glob(f"{_PREFIX}*.csv"))
    )


@dataclass(frozen=True)
class MunicipalSurchargeLaw:
    """The lists the pack carries, read from `datasets/` on first use and kept."""

    def years(self) -> tuple[int, ...]:
        """The tax years a list was shipped for, oldest first."""
        return _years()

    def for_year(self, year: int) -> MunicipalSurchargeYear | None:
        return _year(year) if year in self.years() else None

    def lookup(self, year: int, cadastral_code: str) -> MunicipalSurcharge | None:
        """The row of `cadastral_code` in the list of tax year `year`, `None` when there is no
        list for that year or the list does not carry the code."""
        dataset = self.for_year(year)
        return None if dataset is None else dataset.rows.get(cadastral_code.strip().upper())

    def rate(self, year: int, cadastral_code: str, taxable_income: Decimal) -> Decimal | None:
        """The percentage the municipality's deliberation of `year` applies to
        `taxable_income` (see `MunicipalSurcharge.rate`), `None` for no figure: no list for the
        year, a code it does not carry, or a row with no figure."""
        surcharge = self.lookup(year, cadastral_code)
        return None if surcharge is None else surcharge.rate(taxable_income)


MUNICIPAL_SURCHARGE = MunicipalSurchargeLaw()

__all__ = [
    "MUNICIPAL_SURCHARGE",
    "MunicipalSurcharge",
    "MunicipalSurchargeLaw",
    "MunicipalSurchargeYear",
    "SurchargeBracket",
    "SurchargeStatus",
]
