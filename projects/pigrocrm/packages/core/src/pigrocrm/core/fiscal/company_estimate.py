"""The IRES and IRAP of an srl for one year, estimated from its year basis (REB-1073, taxes
design § 1 and § 2).

Pure: it takes the year's `YearBasis` (`fiscal/basis.py`, the one basis every estimate reads), the
pack's law for the year and the region the legal entity's address names, and answers what each tax
would be and what it was computed from. Nothing here reads a session. The figure is an estimate,
never the declared one (A6 «Not the books»): the accountant's typed figure outranks it
(`fiscal/readings.py`).

IRES
----
24% (`CompanyTaxLaw.ires_rate`) of the year's accrual profit: revenue less purchases less staff
cost less director fees, as `year_basis` states them, then the variations of the tax return the
data can support (REB-1089, `fiscal/ires_adjustment.py`, law in `fiscal/law_ires.py`):

* the costs the law limits by kind, when a purchase or its category states it (`fiscal/
  cost_variations.py`): vehicles (art. 164 TUIR), phones (art. 102 c. 9), hotels and restaurants
  (art. 109 c. 5), representation within a cap on the revenue (art. 108 c. 2);
* depreciation instead of the full cost of a purchase flagged as an asset, with the coefficient of
  the ministerial table (art. 102 TUIR, D.M. 31/12/1988); an asset of a group the table does not
  carry makes the figure missing, never a guessed rate;
* the IRAP deducted: the IRAP paid in the year (due items settled; the estimate stands in when none
  is registered) on the staff cost net of art. 11 deductions (art. 2 c. 1 D.L. 201/2011) and the
  10% lump sum (art. 6 D.L. 185/2008), together at most the IRAP paid (`fiscal/irap_deduction.py`).

What is left out is the payload's `assumptions`. A pack with no entry for the year gives the profit
without variations. A loss gives a tax of zero and says so. The losses of earlier years reduce the
profit as art. 84 TUIR lets them (`fiscal/loss_carry.py`, REB-1088): the base is the profit less
what they take off, and the payload's `losses` says which years they come from, what they took and
what is left. The TUIR articles are those in force for 2026; the D.Lgs.
117/2026 renumbers them from 2027 (`fiscal/law_ires.py`).

The withholdings the customers kept on the srl's invoices (`fiscal/withholdings_suffered.py`,
`company_withholdings_suffered`) are credited against the IRES (art. 79 c. 1 TUIR, which sends them
to art. 22), by the period of the invoice's accrual and not the one it was paid in (art. 109 TUIR).
`ires.amount` is the tax net of them, which is what the advances are based on (A5 «The advance
base is the declared tax net of deductions, credits, withholdings and excesses»); what they exceed
the tax by is `credit`, never a negative tax. The IRAP takes no withholding off. A year whose law
carries no rule for them, or no figure for them, is computed without and says so.

IRAP
----
The value of production per `IrapLaw`: the year's revenue (item A1) less the year's purchases
(items B6, B7, B8 and B14, as far as PigroCRM records them). Staff cost (item B9) is left out of
the costs, so it stays in the base, and so do the director fees. Then the deductions of art. 11 the
data can support:

* c. 4-octies (permanent staff cost) is applied to the cost of the employees whose contract is
  `permanent` (`StaffMember.contract_type`), at the pack's percentage;
* c. 1 lett. a n. 5 (apprentices, disabled workers, training contracts) takes the whole cost of
  those people and leaves them out of the headcount of c. 4-bis.1 (REB-1091);
* c. 4-octies, second sentence (70% of the cost of a seasonal worker) is applied from the
  worker's contract history, by `company_seasonal.seasonal_outcome`, to the days of effective
  work and without the INAIL premium (REB-1090); a worker it is applied to does not also take the
  1,850 euro of c. 4-bis.1, which no source says may be added (the safer figure, said in the
  payload); a worker with no history, or with a year's days not stated, is named;
* c. 4-bis.1 (per-employee deduction) is applied to the employees who are `fixed_term` or
  `seasonal` and not in either group above, pro rata to the days in service and never above the
  person's cost, up to the pack's headcount, when the year's revenue (the positive components
  the estimate sees) does not exceed the pack's limit;
* an employee whose contract type is not stated gets no deduction and the payload names them, so
  the base is overstated by that cost;
* c. 4-bis (small base) is applied from the pack's table, to the base net of the other deductions.

The rate is the one of the region of the legal entity's registered office (art. 4 c. 1, single-site
case, `IrapTerritoryLaw`), `BZ` or `TN` by province for Trentino-Alto Adige. An address that names
no region gives no IRAP figure and the payload says which address is missing; so does a year or a
region the pack holds no rate for. The region is never guessed.

The purchases flagged as assets leave the costs and their depreciation (item B10 b, estimated with
the ministerial coefficient) enters them (REB-1089, `fiscal/cost_variations.py`); the limits of the
TUIR on vehicles, phones and hospitality do not apply to IRAP, which starts from the income
statement. An asset of a group the table does not carry makes the figure missing.

With places of business in more than one region (`LegalEntitySite`, REB-1092) the base is split
between the regions by the pay of the staff assigned to each (art. 4 c. 2, `fiscal/irap_sites.py`),
each part at its region's rate, and the payload's `regions` says how. A person whose place is not
stated stops the split: no regional figure, and `missing` names them. A site in the registered
office's region changes nothing.

A negative value of production gives a tax of zero and says so.
"""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict

from pigrocrm.core.fiscal.basis_schemas import BasisEmployee, YearBasis
from pigrocrm.core.fiscal.company_seasonal import seasonal_outcome
from pigrocrm.core.fiscal.income_variations import IncomeVariations
from pigrocrm.core.fiscal.irap_sites import (
    IrapRegionPart,
    RegionRate,
    SiteFacts,
    split_irap_base,
    splits,
)
from pigrocrm.core.fiscal.ires_adjustment import ires_adjustment
from pigrocrm.core.fiscal.law import CompanyTaxLaw
from pigrocrm.core.fiscal.law_irap import (
    IrapActivity,
    IrapLaw,
    PerEmployeeDeduction,
    calendar_year_days,
)
from pigrocrm.core.fiscal.law_irap_ateco import IrapRateAnswer
from pigrocrm.core.fiscal.loss_schemas import LossOffset
from pigrocrm.core.money import ZERO_MONEY, round_money
from pigrocrm.core.places.locate import Place

_HUNDRED = Decimal(100)

# How many places of business the rate sentences speak for (REB-1092).
SiteScope = Literal["one_site", "same_region", "split"]

# The region of the autonomous provinces of Trentino-Alto Adige, whose rate is each province's
# own (`IrapLaw.rate_for` keys them `BZ` and `TN`).
_TRENTINO_ALTO_ADIGE = "04"


class CompanyTaxLine(BaseModel):
    """One step from the basis to a tax's base: `amount` is signed (a cost or a deduction is
    negative), so a screen draws the lines and their sum without computing anything."""

    model_config = ConfigDict(frozen=True)

    code: str
    label: str
    amount: Decimal


class CompanyTaxEstimate(BaseModel):
    """One tax of the srl: `lines` lead to `base` (which can be negative, a loss), `rate` is the
    percentage applied and `amount` the tax, zero for a loss and `None` when the figure cannot be
    given, in which case `missing` says what is missing. `assumptions` are the simplifications
    this figure carries, each a sentence a person reads."""

    model_config = ConfigDict(frozen=True)

    lines: list[CompanyTaxLine]
    base: Decimal | None
    rate: Decimal | None
    amount: Decimal | None
    loss: bool = False
    # IRES only: what the customers withheld on the period's invoices (credited against the tax;
    # `amount` is net of it) and what it exceeds the tax by. `None` when the estimate has none.
    withholdings: Decimal | None = None
    credit: Decimal | None = None
    # The region whose rate was applied (IRAP only): the ISTAT code, or `BZ` or `TN`.
    region: str | None = None
    # IRAP of a company with sites in several regions: each region's part of the base and tax.
    # Empty otherwise; then `region` and `rate` are the figure, and when it is filled they are not.
    regions: list[IrapRegionPart] = []
    missing: str | None = None
    assumptions: list[str]
    # IRES only: what the losses of earlier years did to the base (REB-1088), `None` when none
    # was carried.
    losses: LossOffset | None = None


class CompanyEstimate(BaseModel):
    """What the srl owes for the year on its income, IRES and IRAP, each with what it was
    computed from. The year's own figures are the payload's `basis`."""

    model_config = ConfigDict(frozen=True)

    ires: CompanyTaxEstimate
    irap: CompanyTaxEstimate


def irap_region_key(place: Place, province: str | None) -> str | None:
    """The key of the pack's IRAP rate table for the place of a legal entity: the ISTAT region
    code, or `BZ`/`TN` for the two autonomous provinces. `None` when the address names no
    region (not Italian, or no province ISTAT lists)."""
    code = place.region_code
    if code is None:
        return None
    if code == _TRENTINO_ALTO_ADIGE:
        key = (province or "").strip().upper()
        return key if key in ("BZ", "TN") else None
    return code


def estimate_company_taxes(
    basis: YearBasis,
    law: CompanyTaxLaw,
    *,
    place: Place,
    province: str | None,
    withholdings: Decimal | None = None,
    withholdings_source: str = "",
    activity: IrapActivity | None = None,
    losses: Callable[[Decimal], LossOffset | None] | None = None,
    variations: IncomeVariations | None = None,
    sites: SiteFacts | None = None,
) -> CompanyEstimate | None:
    """The estimate of IRES and IRAP for `basis`'s year, `None` when the law carries no IRAP
    table or the basis has no costs (a year on a basis that deducts none is no srl's).
    `withholdings` is what the customers withheld on the period's invoices, `None` when the pack
    states no rule for the year they are credited against (`withholdings_source` is that rule's
    citation). `activity` is what the company states about its ATECO codes (REB-1086): the
    regional rows that depend on them need it. `losses` answers what the losses of earlier years
    take off the IRES base for the income it is given, the profit after the variations
    (`fiscal/loss_carry.py`), `None` when there is none to carry; `None` for no function at all.
    `sites` is the legal entity's places of business and its staff's pay, which only an srl with
    a site outside its registered office's region needs (REB-1092)."""
    costs = basis.costs
    if costs is None or law.irap is None:
        return None
    irap = _irap(basis, law, place, province, activity, variations, sites)
    return CompanyEstimate(
        ires=_ires(basis, law, withholdings, withholdings_source, losses, variations, irap),
        irap=irap,
    )


def _ires(
    basis: YearBasis,
    law: CompanyTaxLaw,
    withholdings: Decimal | None,
    withholdings_source: str,
    losses: Callable[[Decimal], LossOffset | None] | None,
    variations: IncomeVariations | None,
    irap: CompanyTaxEstimate,
) -> CompanyTaxEstimate:
    costs = basis.costs
    assert costs is not None
    profit = round_money(basis.revenue - costs.total)
    lines = [
        CompanyTaxLine(code="revenue", label="Ricavi di competenza", amount=basis.revenue),
        CompanyTaxLine(code="purchases", label="Acquisti", amount=-costs.purchases),
        CompanyTaxLine(code="staff", label="Costo del personale", amount=-costs.staff),
        CompanyTaxLine(
            code="director", label="Compensi degli amministratori", amount=-costs.director
        ),
    ]
    adjustment = ires_adjustment(
        variations,
        irap_lines=[(step.code, step.amount) for step in irap.lines],
        irap_base=irap.base,
        irap_amount=irap.amount,
        staff_cost=costs.staff + costs.director,
    )
    lines += [
        CompanyTaxLine(code=step.code, label=step.label, amount=step.amount)
        for step in adjustment.lines
    ]
    profit = round_money(profit + sum((step.amount for step in adjustment.lines), ZERO_MONEY))
    assumptions = [
        *(line.reason for line in basis.left_out),
        *adjustment.assumptions,
    ]
    loss = profit < 0
    if loss:
        assumptions.append("L'anno chiude in perdita: l'IRES stimata è zero.")
    offset = losses(profit) if losses is not None else None
    if offset is None:
        assumptions.append(
            "Non risultano perdite di anni precedenti da riportare: se ce n'è una, scrivila tra "
            "le perdite dell'azienda."
        )
        used = ZERO_MONEY
    else:
        assumptions += offset.assumptions
        if offset.missing:
            return CompanyTaxEstimate(
                lines=lines,
                base=None,
                rate=law.ires_rate,
                amount=None,
                loss=loss,
                missing=" ".join(
                    [*offset.missing, *([adjustment.missing] if adjustment.missing else [])]
                ),
                withholdings=withholdings,
                assumptions=assumptions,
                losses=offset,
            )
        used = offset.used
        if used > 0:
            lines.append(
                CompanyTaxLine(
                    code="loss_carried",
                    label="Perdite degli anni precedenti (art. 84 TUIR)",
                    amount=-used,
                )
            )
    taxable = round_money(profit - used)
    gross = ZERO_MONEY if loss else round_money(taxable * law.ires_rate / _HUNDRED)
    amount: Decimal | None = gross
    credit: Decimal | None = None
    if withholdings is None:
        assumptions.append(
            "Le ritenute subite sulle fatture non sono nella stima: il pacchetto fiscale non dice "
            "a quale anno si scomputano."
        )
    else:
        net = round_money(gross - withholdings)
        amount = max(net, ZERO_MONEY)
        credit = max(-net, ZERO_MONEY)
        assumptions.append(
            "Le ritenute sono quelle trattenute dai clienti sulle fatture di competenza "
            "dell'esercizio, quando le hanno pagate, e si scomputano dall'IRES dell'esercizio "
            "in cui il ricavo è di competenza, non da quello in cui è stato incassato"
            + (f" ({withholdings_source})." if withholdings_source else ".")
        )
    if adjustment.missing is not None:
        amount, credit = None, None
    return CompanyTaxEstimate(
        lines=lines,
        base=taxable,
        rate=law.ires_rate,
        amount=amount,
        loss=loss,
        withholdings=withholdings,
        credit=credit,
        missing=adjustment.missing,
        assumptions=assumptions,
        losses=offset,
    )


def _names(rows: list[BasisEmployee]) -> str:
    return ", ".join(row.name for row in rows)


def _is_excluded(row: BasisEmployee) -> bool:
    """Apprentices, workers on a training contract and disabled workers: out of the headcount of
    c. 4-bis.1 and deducted in full (c. 1 lett. a n. 5). A permanent employee is deducted in
    full by c. 4-octies already and is never counted twice."""
    if row.contract_type in ("apprentice", "training"):
        return True
    return row.disabled_worker is True and row.contract_type != "permanent"


def _per_employee_amount(row: BasisEmployee, rule: PerEmployeeDeduction, year_days: int) -> Decimal:
    """What c. 4-bis.1 gives for one person: pro rata to the days of the employment on the
    calendar year and to the part-time share (c. 4-bis.2), never above the person's cost."""
    return min(
        round_money(
            rule.amount * row.days_in_service / year_days * row.part_time_percent / _HUNDRED
        ),
        row.cost,
    )


def _disabled_unsaid(rows: list[BasisEmployee]) -> str:
    return (
        f"Non è indicato se {_names(rows)} sia disabile (L. 68/1999, L. 482/1968): se lo fosse, "
        "il costo sarebbe dedotto per intero (art. 11 c. 1 lett. a n. 5) e la persona non "
        "conterebbe tra i cinque dipendenti del comma 4-bis.1, quindi la base potrebbe essere "
        "sovrastimata."
    )


def _staff_deductions(
    basis: YearBasis, irap: IrapLaw, period_days: int, year_days: int
) -> list[tuple[CompanyTaxLine | None, str]]:
    """The deductions of art. 11 that need the staff's contract types (REB-1081): each a line
    (`None` where nothing is deducted) and the sentence that says what was and was not applied.
    The 1,850 euro of c. 4-bis.1 is «su base annua», pro rata to the days of the employment and
    to the part-time share (c. 4-bis.2), and the 400,000 euro test reads the revenue brought to
    the calendar year (REB-1086)."""
    costs = basis.costs
    assert costs is not None
    out: list[tuple[CompanyTaxLine | None, str]] = []
    employees = costs.employees
    if not employees:
        return out
    excluded = [row for row in employees if _is_excluded(row)]
    untyped = [row for row in employees if row.contract_type is None and not _is_excluded(row)]
    if untyped:
        out.append(
            (
                None,
                "Il tipo di contratto non è indicato per "
                f"{_names(untyped)}: il loro costo non è dedotto (a tempo indeterminato, "
                "art. 11 c. 4-octies, o per dipendente, c. 4-bis.1), quindi la base è "
                "sovrastimata di quel costo.",
            )
        )
    if excluded:
        cost = sum((row.cost for row in excluded), ZERO_MONEY)
        amount = round_money(cost * irap.excluded_staff.percent / _HUNDRED)
        out.append(
            (
                CompanyTaxLine(
                    code="excluded_staff",
                    label="Deduzione delle spese per apprendisti, disabili e contratti di "
                    "formazione lavoro (art. 11 c. 1 lett. a n. 5)",
                    amount=-amount,
                ),
                f"Dedotto per intero il costo di {_names(excluded)} (apprendisti, lavoratori "
                "disabili o contratti di formazione lavoro, art. 11 c. 1 lett. a n. 5): non "
                "contano tra i cinque dipendenti del comma 4-bis.1.",
            )
        )
    permanent = [row for row in employees if row.contract_type == "permanent"]
    if permanent:
        cost = sum((row.cost for row in permanent), ZERO_MONEY)
        amount = round_money(cost * irap.permanent_staff.permanent_percent / _HUNDRED)
        out.append(
            (
                CompanyTaxLine(
                    code="permanent_staff",
                    label="Deduzione del costo dei dipendenti a tempo indeterminato "
                    "(art. 11 c. 4-octies)",
                    amount=-amount,
                ),
                f"Dedotto il costo dei dipendenti a tempo indeterminato: {_names(permanent)}.",
            )
        )
    seasonal = [
        row for row in employees if row.contract_type == "seasonal" and not _is_excluded(row)
    ]
    seasonal_rule = irap.permanent_staff
    applied: list[BasisEmployee] = []
    seasonal_amount = ZERO_MONEY
    for row in seasonal:
        outcome = seasonal_outcome(
            row.seasonal_contracts,
            basis.year,
            min_days=seasonal_rule.seasonal_min_days,
            window_years=seasonal_rule.seasonal_window_years,
        )
        share = f"{seasonal_rule.seasonal_percent.normalize():f}%"
        if outcome.outcome == "due":
            applied.append(row)
            served = max(row.days_in_service, 1)
            worked = min(outcome.days, served)
            seasonal_amount += round_money(
                (row.cost - row.inail) * seasonal_rule.seasonal_percent / _HUNDRED * worked / served
            )
        elif outcome.outcome == "not_due":
            out.append(
                (None, f"Il {share} per lo stagionale {row.name} non spetta: {outcome.reason}.")
            )
        else:
            out.append(
                (
                    None,
                    f"Il {share} per lo stagionale {row.name} (art. 11 c. 4-octies) non è "
                    f"applicato: {outcome.reason}.",
                )
            )
    if applied:
        out.append(
            (
                CompanyTaxLine(
                    code="seasonal_staff",
                    label=f"Deduzione del {seasonal_rule.seasonal_percent.normalize():f}% del "
                    "costo degli stagionali (art. 11 c. 4-octies)",
                    amount=-seasonal_amount,
                ),
                f"Dedotto il {seasonal_rule.seasonal_percent.normalize():f}% del costo per i "
                f"giorni di lavoro effettivo di {_names(applied)}, senza i premi INAIL, che si "
                "deducono per intero a parte. Per loro non si applicano anche i "
                f"{irap.per_employee.amount:,.0f} euro del comma 4-bis.1: nessuna fonte letta "
                "dice che le due deduzioni si sommino sullo stesso lavoratore, e si usa la "
                "cifra più prudente.",
            )
        )
    applied_ids = {row.staff_member_id for row in applied}
    other = [
        row
        for row in employees
        if row.contract_type in ("fixed_term", "seasonal")
        and not _is_excluded(row)
        and row.staff_member_id not in applied_ids
    ]
    if other:
        rule = irap.per_employee
        annual_components = basis.revenue * year_days / period_days
        if annual_components > rule.components_limit:
            out.append(
                (
                    None,
                    "La deduzione per dipendente (art. 11 c. 4-bis.1) non spetta: i ricavi"
                    + (" ragguagliati all'anno solare" if period_days != year_days else "")
                    + f" superano {rule.components_limit:,.0f} euro.",
                )
            )
        else:
            rows = sorted(
                (
                    (_per_employee_amount(row, rule, year_days), row)
                    for row in other
                    if row.days_in_service > 0
                ),
                key=lambda pair: pair[0],
                reverse=True,
            )[: rule.max_employees]
            amount = sum((value for value, _ in rows), ZERO_MONEY)
            if amount > 0:
                out.append(
                    (
                        CompanyTaxLine(
                            code="per_employee",
                            label="Deduzione per dipendente non a tempo indeterminato "
                            "(art. 11 c. 4-bis.1)",
                            amount=-amount,
                        ),
                        f"Dedotti {rule.amount:,.0f} euro l'anno per dipendente non a tempo "
                        f"indeterminato, fino a {rule.max_employees}, in proporzione ai giorni "
                        "di servizio sull'anno solare e al tempo parziale, e mai oltre il suo "
                        "costo; apprendisti, lavoratori disabili e contratti di formazione "
                        "lavoro non sono contati.",
                    )
                )
            unsaid = [
                row
                for row in other
                if row.disabled_worker is None
                and row.cost > _per_employee_amount(row, rule, year_days)
            ]
            if unsaid:
                out.append((None, _disabled_unsaid(unsaid)))
    return out


def _unanswered_reason(answer: IrapRateAnswer, region_name: str) -> str:
    """Why a region gives no rate for this company, as the sentence of `missing`."""
    match answer.unanswered:
        case "ateco_2007_needed":
            return (
                f"Manca il codice ATECO 2007 dell'azienda: la Regione ({region_name}) applica "
                "aliquote IRAP diverse a elenchi di attività e senza il codice non si sa quale "
                "spetti. Indicalo in Impostazioni, Aziende."
            )
        case "ateco_2025_needed":
            return (
                "Manca il codice ATECO 2025 dell'azienda: serve a sapere se l'attività è nella "
                "tabella 1 del D.L. 21/2026 (settore energetico, aliquota IRAP più alta di due "
                "punti) o in un elenco della Regione. Indicalo in Impostazioni, Aziende."
            )
        case "list_not_decidable":
            return {
                "emilia_romagna_atecofin_2004": (
                    f"La Regione ({region_name}) applica un'aliquota diversa ai settori ATECOFIN "
                    "2004 23, 40, 64 e 67 e il pacchetto non ha la corrispondenza con i codici "
                    "ATECO 2007: per questa attività non si sa quale aliquota IRAP si applichi."
                ),
                "basilicata_energy_lists": (
                    f"La Regione ({region_name}) applica aliquote diverse ai settori dell'energia "
                    "elettrica, del gas e del petrolio con elenchi di codici che la pubblicazione "
                    "delle Finanze non permette di leggere con certezza: per questa attività non "
                    "si sa quale aliquota IRAP si applichi."
                ),
            }.get(
                answer.blocked_reason,
                f"Il pacchetto non sa quale riga della tabella IRAP della Regione ({region_name}) "
                "si applichi a questa attività.",
            )
        case _:
            return (
                f"La Regione ({region_name}) non pubblica un'aliquota IRAP per le attività della "
                "tabella 1 del D.L. 21/2026 che il pacchetto possa applicare a questo codice."
            )


def _rate_sentences(
    answer: IrapRateAnswer,
    region_name: str,
    activity: IrapActivity | None,
    scope: SiteScope = "one_site",
) -> list[str]:
    """What a person reads about the rate that was applied: where it comes from, and the rows
    that were left out. `scope` is how many places the company has (REB-1092): one, several in
    the registered office's region, or in several regions, where the rate is one region's among
    the parts and «one site» would be false."""
    stated = activity is not None and (activity.ateco_2007 or activity.ateco_2025) is not None
    sites_said = {
        "one_site": " Si considera una sola sede.",
        "same_region": " Le sedi indicate sono tutte in questa regione: la base non si ripartisce.",
        "split": "",
    }[scope]
    region = f"della sede ({region_name})" if scope != "split" else f"({region_name})"
    out: list[str] = []
    if answer.row is not None:
        out.append(
            f"Aliquota {answer.rate}% della Regione ({region_name}), riga {answer.row.code} "
            f"della tabella delle Finanze, scelta dal codice ATECO indicato: {answer.row.text}."
            + sites_said
        )
    elif stated:
        out.append(
            f"Si applica l'aliquota ordinaria della regione {region}: nessuna riga della sua "
            "tabella per attività si applica ai codici ATECO indicati." + sites_said
        )
    else:
        clause = {
            "one_site": ", con una sola sede",
            "same_region": ", con le sedi in questa regione",
            "split": "",
        }[scope]
        out.append(
            f"Si applica l'aliquota ordinaria della regione {region}{clause}: il codice ATECO "
            "dell'azienda non è indicato."
        )
    if "table_1_not_checked" in answer.notes:
        out.append(
            "Il codice ATECO 2025 non è indicato: l'aumento di due punti dell'aliquota IRAP "
            "per le imprese energetiche (tabella 1 del D.L. 21/2026, anni 2026 e 2027) non è "
            "verificato."
        )
    out.extend(
        f"La Regione prevede anche un'aliquota ridotta del {note.rate}% (riga {note.code}: "
        f"{note.text}) con i suoi requisiti"
        + (" e il regime «de minimis»" if note.aid else "")
        + ": non è applicata."
        for note in answer.reductions
    )
    return out


def _region_rate(
    irap: IrapLaw, year: int, activity: IrapActivity | None
) -> Callable[[str], RegionRate]:
    """A region key's name and rate for `year` and the company's activity, for the split of
    art. 4 c. 2: each part is rated by its own region's table, as the registered office's region
    is, so the rows an ATECO code selects and the ones left out are told for every region."""

    def rate_of(key: str) -> RegionRate:
        row = irap.region_row(year, key)
        answer = irap.rate_for(year, key, activity)
        name = row.name if row is not None else key
        if answer is None or row is None:
            return RegionRate(
                name,
                None,
                f"Il pacchetto fiscale non ha l'aliquota IRAP {year} della regione {name}.",
            )
        if answer.rate is None:
            return RegionRate(name, None, _unanswered_reason(answer, name))
        return RegionRate(
            name, answer.rate, notes=tuple(_rate_sentences(answer, name, activity, "split"))
        )

    return rate_of


def _irap(
    basis: YearBasis,
    law: CompanyTaxLaw,
    place: Place,
    province: str | None,
    activity: IrapActivity | None,
    variations: IncomeVariations | None = None,
    sites: SiteFacts | None = None,
) -> CompanyTaxEstimate:
    costs = basis.costs
    irap = law.irap
    assert costs is not None and irap is not None
    value = round_money(basis.revenue - costs.purchases)
    period_days = (basis.period_to - basis.period_from).days + 1
    year_days = calendar_year_days(basis.period_to)
    lines = [
        CompanyTaxLine(code="revenue", label="Ricavi di competenza", amount=basis.revenue),
        CompanyTaxLine(code="purchases", label="Acquisti", amount=-costs.purchases),
    ]
    assumptions = [
        *(line.reason for line in basis.left_out),
        "Il valore della produzione è ricavi meno acquisti: manca ciò che il gestionale non "
        "registra (ammortamenti dei beni non indicati come ammortizzabili, variazioni di "
        "rimanenze, altri ricavi e proventi).",
        "Il costo del personale e i compensi degli amministratori restano nella base (art. 5 "
        "D.Lgs. 446/1997), salvo le deduzioni dell'art. 11 indicate qui sotto.",
    ]
    if period_days != year_days:
        assumptions.append(
            f"Il periodo è di {period_days} giorni: la deduzione per basi ridotte, la soglia "
            "dei ricavi e la deduzione per dipendente sono ragguagliate all'anno solare (art. 11 "
            f"c. 4-bis.2) su {year_days} giorni, l'anno in cui chiude il periodo; nessuna "
            "fonte letta dice come contare i giorni di un anno bisestile."
        )
    if variations is not None:
        lines += [
            CompanyTaxLine(code=step.code, label=step.label, amount=step.amount)
            for step in variations.costs.irap_lines
        ]
        value = round_money(
            value + sum((s.amount for s in variations.costs.irap_lines), ZERO_MONEY)
        )
        assumptions += variations.costs.irap_assumptions
    key = irap_region_key(place, province)
    several_regions = sites is not None and splits(key, sites.sites)
    deducted = value
    for line, text in _staff_deductions(basis, irap, period_days, year_days):
        if line is not None:
            lines.append(line)
            deducted = round_money(deducted + line.amount)
        assumptions.append(text)
    small = round_money(irap.small_base_deduction(deducted, period_days, year_days))
    if small != 0:
        lines.append(
            CompanyTaxLine(
                code="small_base",
                label="Deduzione per basi imponibili ridotte (art. 11 c. 4-bis)",
                amount=-small,
            )
        )
    base = round_money(deducted - small)
    loss = base < 0

    if sites is not None and key is not None and several_regions:
        if loss:
            assumptions.append("Il valore della produzione è negativo: l'IRAP stimata è zero.")
        split = split_irap_base(
            base, loss, key, sites, irap.territory, _region_rate(irap, basis.year, activity)
        )
        assumptions.extend(split.assumptions)
        return CompanyTaxEstimate(
            lines=lines,
            base=base,
            rate=None,
            amount=split.amount,
            loss=loss,
            regions=list(split.parts),
            missing=split.missing,
            assumptions=assumptions,
        )
    missing: str | None = None
    rate: Decimal | None = None
    if key is None:
        missing = (
            "Manca l'indirizzo della sede dell'azienda (città e provincia in Italia): "
            "senza la regione non si sa quale aliquota IRAP applicare."
        )
    else:
        answer = irap.rate_for(basis.year, key, activity)
        row = irap.region_row(basis.year, key)
        if answer is None or row is None:
            missing = (
                f"Il pacchetto fiscale non ha l'aliquota IRAP {basis.year} della regione "
                f"della sede ({key})."
            )
        elif answer.rate is None:
            missing = _unanswered_reason(answer, row.name)
        else:
            rate = answer.rate
            assumptions.extend(
                _rate_sentences(
                    answer,
                    row.name,
                    activity,
                    "same_region" if sites and sites.sites else "one_site",
                )
            )
    if loss:
        assumptions.append("Il valore della produzione è negativo: l'IRAP stimata è zero.")
    amount: Decimal | None = None
    if rate is not None:
        amount = ZERO_MONEY if loss else round_money(base * rate / _HUNDRED)
    if variations is not None and variations.costs.missing is not None:
        missing = " ".join(part for part in (missing, variations.costs.missing) if part)
        amount = None
    return CompanyTaxEstimate(
        lines=lines,
        base=base,
        rate=rate,
        amount=amount,
        loss=loss,
        region=key,
        missing=missing,
        assumptions=assumptions,
    )


__all__ = [
    "CompanyEstimate",
    "CompanyTaxEstimate",
    "CompanyTaxLine",
    "estimate_company_taxes",
    "irap_region_key",
]
