"""The IRAP base of an srl with places of business in more than one region (REB-1092).

Pure: it takes the base the estimate has already reached, the key of the region of the registered
office, the legal entity's sites and the pay of its staff, and answers how the base is attributed to
each region and what each part owes. Nothing here reads a session or knows a rate: the caller hands
in a function from a region key to its rate (`IrapLaw.rate_for`, which may pick a row by the
company's ATECO code), so the two rules stay in their own places.

The rule, art. 4 c. 2 D.Lgs. 446/1997 (Normattiva, text in force from 1 January 2016, read
2026-11-12): with activity in several regions the net value of production «si considera prodotto
nel territorio di ciascuna regione» in proportion to «l'ammontare delle retribuzioni spettanti al
personale a qualunque titolo utilizzato, compresi i redditi assimilati a quelli di lavoro
dipendente, i compensi ai collaboratori coordinati e continuativi e gli utili agli associati in
partecipazione [...]» of the staff «addetto, con continuità, a stabilimenti, cantieri, uffici o basi
fisse, operanti per un periodo di tempo non inferiore a tre mesi nel territorio di ciascuna
regione». And «si considera prodotto nella regione nel cui territorio il soggetto passivo è
domiciliato il valore della produzione netta derivante dalle attività esercitate nel territorio di
altre regioni senza l'impiego, per almeno tre mesi, di personale».

The Agenzia delle Entrate's instructions to the IRAP 2026 return (Provvedimento prot. n. 71997 of
27 February 2026, tax year 2025), «Quadro IR», section I, read 2026-11-12, say how to count: the
pay is the amount due «così come determinato ai fini previdenziali a norma dell'articolo 12 della
legge 30 aprile 1969, n. 153», the assimilated incomes are included, and the share of each region
is the pay of its staff over the total pay of the staff of the activities carried on in Italy
(row IS11 of quadro IS, column 2). «Se l'attività esercitata nel territorio di regioni [...]
diverse da quella in cui risulta domiciliato il soggetto passivo non è svolta con l'impiego di
personale [...] per almeno tre mesi, non si verifica la condizione per procedere al riparto
territoriale». The negative values are split the same way, in column 2 of rows IR1 to IR8.

How the estimate reads it
-------------------------
* The pay of a person is the gross pay of the payroll estimate (`CostLine.gross`: the gross spread
  over the months, the 13th and 14th included, without the employer's contributions), employees
  and directors alike, since a director's fee is an assimilated income. A month a person closed
  counts as it was closed.
* A person works at one place for the whole period: a site of the legal entity or its registered
  office (`StaffMember.site_id`, `StaffMember.at_registered_office`). The records keep no history of
  transfers, and the payload says so.
* The region of a site counts for a person who was in service at least `minimum_months` (the pack's
  `IrapTerritoryLaw`, three) within the basis period: from the day they started in it (or the
  period's first day) to the day they left (or its last), by calendar months, so 1 January to 31
  March is three. No source read says how to count the months; the payload says it is a reading.
  The pay of a person in service for less than that goes to the region of the registered office,
  with the pay of the people who work there: that is where the law sends what is done elsewhere
  without staff for that time.
* The denominator is the pay of everybody the payroll estimates, so the shares add up to the base.
* A person whose place is not stated, or whose pay cannot be estimated, makes the split unknown:
  nobody is guessed to work in any region. The figure is withheld and `missing` names them.
* With no site outside the registered office's region the rule has nothing to split and the
  estimate is the single-region one it always was (`splits`).

Not modelled, and said in the payload: staff seconded to third parties, which the instructions
leave out of the pay, and third-party staff seconded to the company, which they include; the
regions' deductions and credits of columns 4, 9 and 10 of rows IR1 to IR8; and the ordering of the
activities that carry different rates inside one region.
"""

from __future__ import annotations

import calendar
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from pigrocrm.core.fiscal.law_irap import IRAP_LAW, IrapLaw, IrapTerritoryLaw
from pigrocrm.core.money import ZERO_MONEY, round_money

_HUNDRED = Decimal(100)
_SHARE_PLACES = Decimal("0.01")


@dataclass(frozen=True)
class SitePlace:
    """A place of business of the legal entity beside its registered office: its id, the key of
    the IRAP rate table of its region, and the words a person knows it by."""

    id: UUID
    region: str
    label: str


@dataclass(frozen=True)
class StaffPay:
    """What one person is paid in the basis period and where they work. `service_from` and
    `service_to` are the days of the period they were in service, both included; a person whose
    place is not stated has neither `site_id` nor `at_registered_office`."""

    staff_member_id: UUID
    name: str
    pay: Decimal
    service_from: date
    service_to: date
    site_id: UUID | None = None
    at_registered_office: bool = False


@dataclass(frozen=True)
class SiteFacts:
    """The legal entity's sites and its staff's pay: everything the split reads besides the base.
    `unpriced` names the people in service whose pay the payroll could not estimate."""

    sites: tuple[SitePlace, ...]
    staff: tuple[StaffPay, ...]
    unpriced: tuple[str, ...] = ()


@dataclass(frozen=True)
class RegionRate:
    """What the caller knows of a region's rate: its name, the rate in percent, or why there is
    none (`missing`, an Italian sentence for the payload)."""

    name: str
    rate: Decimal | None
    missing: str | None = None
    # What a person reads about the rate that was applied (the row it came from, the rows left out).
    notes: tuple[str, ...] = ()


class IrapRegionPart(BaseModel):
    """One region's part of the IRAP base: the pay that decided it (`pay`, `share` in percent of
    the total), the base attributed to it, its rate and its tax. `rate` and `amount` are `None`
    when the pack has no rate for the region."""

    model_config = ConfigDict(frozen=True)

    region: str
    name: str
    pay: Decimal
    share: Decimal
    base: Decimal
    rate: Decimal | None
    amount: Decimal | None


@dataclass(frozen=True)
class IrapSplit:
    """The attribution: the parts by region (empty while `missing`), the sentence that says what
    stops it, and the sentences that say how it was read."""

    parts: tuple[IrapRegionPart, ...]
    missing: str | None
    assumptions: tuple[str, ...]

    @property
    def amount(self) -> Decimal | None:
        """The tax of the whole base, `None` while any part has no rate or the split is unknown."""
        if self.missing is not None or any(part.amount is None for part in self.parts):
            return None
        return round_money(sum((part.amount or ZERO_MONEY for part in self.parts), ZERO_MONEY))


def irap_regions(law: IrapLaw = IRAP_LAW) -> tuple[tuple[str, str], ...]:
    """The regions and autonomous provinces the pack holds an IRAP rate table for, as
    `(key, name)`: the keys a site's `region` may take. The name is the latest table's."""
    names: dict[str, str] = {}
    for table in law.regional_rates:
        for row in table.rates:
            names[row.key] = row.name
    return tuple(names.items())


def splits(domicile: str | None, sites: Iterable[SitePlace]) -> bool:
    """Whether art. 4 c. 2 has anything to split: the registered office's region is known and a
    site is in another. A site in the registered office's region adds nothing to the rule, so the
    estimate is the single-region one."""
    if domicile is None:
        return False
    return any(site.region != domicile for site in sites)


def served_months(first: date, last: date, minimum_months: int) -> bool:
    """Whether the days `first` to `last` (both included) are at least `minimum_months` calendar
    months: the day after `last` is not before the same day `minimum_months` months on."""
    return last + timedelta(days=1) >= _add_months(first, minimum_months)


def _add_months(day: date, months: int) -> date:
    index = day.month - 1 + months
    year, month = day.year + index // 12, index % 12 + 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def split_irap_base(
    base: Decimal,
    loss: bool,
    domicile: str,
    facts: SiteFacts,
    territory: IrapTerritoryLaw,
    rate_of: Callable[[str], RegionRate],
) -> IrapSplit:
    """Attributes `base` (the whole value of production, already net of the deductions) to the
    regions as art. 4 c. 2 says, and taxes each part at its region's rate. `loss` is whether the
    base is negative: each part is then zero tax, as the single-region estimate is. `domicile` is
    the key of the registered office's region. Call it only when `splits` is true."""
    unplaced = [
        row.name for row in facts.staff if row.site_id is None and not row.at_registered_office
    ]
    stopped: list[str] = []
    if unplaced:
        stopped.append(
            "Con sedi in più regioni la base IRAP si ripartisce in proporzione alle retribuzioni "
            "del personale di ciascuna sede (art. 4 c. 2 D.Lgs. 446/1997): manca la sede di lavoro "
            f"di {_names(unplaced)}. Indicala nella scheda della persona."
        )
    if facts.unpriced:
        stopped.append(
            "Con sedi in più regioni serve la paga di ciascuno: non si stima quella di "
            f"{_names(facts.unpriced)}, perché mancano i dati di paga."
        )
    if stopped:
        return IrapSplit(parts=(), missing=" ".join(stopped), assumptions=())

    region_of = {site.id: site.region for site in facts.sites}
    pay_by_region: dict[str, Decimal] = {}
    short: list[str] = []
    for row in facts.staff:
        if row.site_id is None or row.site_id not in region_of:
            region = domicile
        elif served_months(row.service_from, row.service_to, territory.minimum_months):
            region = region_of[row.site_id]
        else:
            region = domicile
            short.append(row.name)
        pay_by_region[region] = pay_by_region.get(region, ZERO_MONEY) + row.pay
    total = sum(pay_by_region.values(), ZERO_MONEY)
    if total <= 0:
        pay_by_region = {domicile: ZERO_MONEY}

    shares = _shares(base, pay_by_region, total)
    parts: list[IrapRegionPart] = []
    missing: list[str] = []
    rate_notes: list[str] = []
    for region in sorted(pay_by_region):
        info = rate_of(region)
        rate_notes.extend(info.notes)
        attributed = shares[region]
        amount: Decimal | None = None
        if info.rate is not None:
            amount = ZERO_MONEY if loss else round_money(attributed * info.rate / _HUNDRED)
        elif info.missing is not None:
            missing.append(info.missing)
        percent = (
            Decimal(100)
            if total <= 0
            else (pay_by_region[region] * _HUNDRED / total).quantize(_SHARE_PLACES)
        )
        parts.append(
            IrapRegionPart(
                region=region,
                name=info.name,
                pay=round_money(pay_by_region[region]),
                share=percent,
                base=attributed,
                rate=info.rate,
                amount=amount,
            )
        )
    return IrapSplit(
        parts=tuple(parts),
        missing=" ".join(missing) or None,
        assumptions=tuple(
            [*_sentences(parts, domicile, territory, short, total, facts), *rate_notes]
        ),
    )


def _shares(base: Decimal, pay_by_region: dict[str, Decimal], total: Decimal) -> dict[str, Decimal]:
    """The base of each region, in proportion to its pay. Cents are rounded per region and the
    difference to the base goes to the region with the most pay, so the parts add up to it."""
    if total <= 0:
        return {region: round_money(base) for region in pay_by_region}
    out = {region: round_money(base * pay / total) for region, pay in pay_by_region.items()}
    largest = max(sorted(pay_by_region), key=lambda region: pay_by_region[region])
    out[largest] = round_money(out[largest] + (round_money(base) - sum(out.values(), ZERO_MONEY)))
    return out


def _names(names: Sequence[str]) -> str:
    return ", ".join(names)


def _sentences(
    parts: list[IrapRegionPart],
    domicile: str,
    territory: IrapTerritoryLaw,
    short: list[str],
    total: Decimal,
    facts: SiteFacts,
) -> list[str]:
    months = territory.minimum_months
    out = [
        "Con sedi in più regioni la base IRAP è ripartita in proporzione alle retribuzioni lorde "
        f"del personale assegnato a ciascuna sede per almeno {months} mesi (art. 4 c. 2 D.Lgs. "
        "446/1997; istruzioni IRAP 2026, quadro IR); gli amministratori contano con i loro "
        "compensi. Il valore delle attività svolte in altre regioni senza personale per quel "
        "tempo spetta alla regione della sede legale.",
        f"Si conta come periodo di {months} mesi quello dei mesi di calendario di servizio nel "
        "periodo; l'assegnazione a una sede vale per tutto il periodo, perché non si tiene lo "
        "storico dei trasferimenti.",
        "Non si distinguono il personale distaccato presso terzi (escluso dalle istruzioni) e "
        "quello di terzi distaccato in azienda (incluso), né le deduzioni e i crediti che le "
        "regioni stabiliscono per proprio conto.",
    ]
    if short:
        out.append(
            f"In servizio meno di {months} mesi nel periodo: {_names(short)}. La loro paga conta "
            "per la regione della sede legale."
        )
    if total <= 0:
        out.append(
            "Nessuna paga stimata nel periodo: tutta la base va alla regione della sede legale."
        )
    elif all(part.region == domicile for part in parts):
        out.append(
            "Nessuna sede delle altre regioni ha personale assegnato per almeno "
            f"{months} mesi: tutta la base va alla regione della sede legale."
        )
    return out


__all__ = [
    "IrapRegionPart",
    "IrapSplit",
    "RegionRate",
    "SiteFacts",
    "SitePlace",
    "StaffPay",
    "irap_regions",
    "served_months",
    "split_irap_base",
    "splits",
]
