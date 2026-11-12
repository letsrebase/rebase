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
cost less director fees, as `year_basis` states them. The simplification is the whole of the
estimate and the payload says it (`assumptions`): the profit is **not** adjusted for the
variations of the tax return (non-deductible costs, the deduction of IRAP on staff cost, ACE,
depreciation differences, the limits on interest and vehicles). A loss gives a tax of zero and
says so. The losses of earlier years reduce the profit as art. 84 TUIR lets them
(`fiscal/loss_carry.py`, REB-1088): the base is the profit less what they take off, and the
payload's `losses` says which years they come from, what they took and what is left.

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
* c. 4-bis.1 (per-employee deduction) is applied to the employees who are `fixed_term` or
  `seasonal`, pro rata to the days in service and never above the person's cost, up to the pack's
  headcount, when the year's revenue (the positive components the estimate sees) does not
  exceed the pack's limit;
* an employee whose contract type is not stated gets no deduction and the payload names them, so
  the base is overstated by that cost. The seasonal deduction of c. 4-octies (70% from 120 days
  in two tax periods, from the second contract) is not applied: the staff records do not say
  which contracts follow another, and the payload says so when a seasonal employee exists;
* c. 4-bis (small base) is applied from the pack's table, to the base net of the other deductions.

The rate is the one of the region of the legal entity's registered office (art. 4 c. 1, single-site
case, `IrapTerritoryLaw`), `BZ` or `TN` by province for Trentino-Alto Adige. An address that names
no region gives no IRAP figure and the payload says which address is missing; so does a year or a
region the pack holds no rate for. The region is never guessed.

A negative value of production gives a tax of zero and says so.
"""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from pigrocrm.core.fiscal.basis_schemas import BasisEmployee, YearBasis
from pigrocrm.core.fiscal.law import CompanyTaxLaw
from pigrocrm.core.fiscal.law_irap import IrapActivity, IrapLaw, calendar_year_days
from pigrocrm.core.fiscal.law_irap_ateco import IrapRateAnswer
from pigrocrm.core.fiscal.loss_schemas import LossOffset
from pigrocrm.core.money import ZERO_MONEY, round_money
from pigrocrm.core.places.locate import Place

_HUNDRED = Decimal(100)

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
    losses: LossOffset | None = None,
) -> CompanyEstimate | None:
    """The estimate of IRES and IRAP for `basis`'s year, `None` when the law carries no IRAP
    table or the basis has no costs (a year on a basis that deducts none is no srl's).
    `withholdings` is what the customers withheld on the period's invoices, `None` when the pack
    states no rule for the year they are credited against (`withholdings_source` is that rule's
    citation). `activity` is what the company states about its ATECO codes (REB-1086): the
    regional rows that depend on them need it. `losses` is what the losses of earlier years take
    off the IRES base (`fiscal/loss_carry.py`), `None` when there is none to carry."""
    costs = basis.costs
    if costs is None or law.irap is None:
        return None
    return CompanyEstimate(
        ires=_ires(basis, law, withholdings, withholdings_source, losses),
        irap=_irap(basis, law, place, province, activity),
    )


def _ires(
    basis: YearBasis,
    law: CompanyTaxLaw,
    withholdings: Decimal | None,
    withholdings_source: str,
    losses: LossOffset | None,
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
    assumptions = [
        *(line.reason for line in basis.left_out),
        "L'utile è ricavi meno acquisti, personale e amministratori, senza le variazioni "
        "della dichiarazione (costi non deducibili, deduzione dell'IRAP sul costo del lavoro, "
        "ammortamenti): è una stima, non il calcolo del commercialista.",
    ]
    loss = profit < 0
    if loss:
        assumptions.append("L'anno chiude in perdita: l'IRES stimata è zero.")
    if losses is None:
        assumptions.append(
            "Non risultano perdite di anni precedenti da riportare: se ce n'è una, scrivila tra "
            "le perdite dell'azienda."
        )
        used = ZERO_MONEY
    else:
        assumptions += losses.assumptions
        if losses.missing:
            return CompanyTaxEstimate(
                lines=lines,
                base=None,
                rate=law.ires_rate,
                amount=None,
                loss=loss,
                missing=" ".join(losses.missing),
                withholdings=withholdings,
                assumptions=assumptions,
                losses=losses,
            )
        used = losses.used
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
    amount = gross
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
    return CompanyTaxEstimate(
        lines=lines,
        base=taxable,
        rate=law.ires_rate,
        amount=amount,
        loss=loss,
        withholdings=withholdings,
        credit=credit,
        assumptions=assumptions,
        losses=losses,
    )


def _names(rows: list[BasisEmployee]) -> str:
    return ", ".join(row.name for row in rows)


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
    untyped = [row for row in employees if row.contract_type is None]
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
    seasonal = [row for row in employees if row.contract_type == "seasonal"]
    if seasonal:
        out.append(
            (
                None,
                "La deduzione del "
                f"{irap.permanent_staff.seasonal_percent.normalize():f}% per gli stagionali "
                f"con almeno {irap.permanent_staff.seasonal_min_days} giorni in due periodi "
                "d'imposta (art. 11 c. 4-octies) non è applicata: i dati non dicono se il "
                f"contratto segue un altro con lo stesso datore ({_names(seasonal)}).",
            )
        )
    other = [row for row in employees if row.contract_type in ("fixed_term", "seasonal")]
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
                    (
                        min(
                            round_money(
                                rule.amount
                                * row.days_in_service
                                / year_days
                                * row.part_time_percent
                                / _HUNDRED
                            ),
                            row.cost,
                        ),
                        row,
                    )
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
                        "costo; apprendisti e lavoratori disabili, che la legge non conta, non "
                        "sono distinti.",
                    )
                )
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
    answer: IrapRateAnswer, region_name: str, activity: IrapActivity | None
) -> list[str]:
    """What a person reads about the rate that was applied: where it comes from, and the rows
    that were left out."""
    stated = activity is not None and (activity.ateco_2007 or activity.ateco_2025) is not None
    out: list[str] = []
    if answer.row is not None:
        out.append(
            f"Aliquota {answer.rate}% della Regione ({region_name}), riga {answer.row.code} "
            f"della tabella delle Finanze, scelta dal codice ATECO indicato: {answer.row.text}. "
            "Si considera una sola sede."
        )
    elif stated:
        out.append(
            f"Si applica l'aliquota ordinaria della regione della sede ({region_name}): nessuna "
            "riga della sua tabella per attività si applica ai codici ATECO indicati. Si "
            "considera una sola sede."
        )
    else:
        out.append(
            f"Si applica l'aliquota ordinaria della regione della sede ({region_name}), con una "
            "sola sede: il codice ATECO dell'azienda non è indicato."
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


def _irap(
    basis: YearBasis,
    law: CompanyTaxLaw,
    place: Place,
    province: str | None,
    activity: IrapActivity | None,
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
        "registra (ammortamenti, variazioni di rimanenze, altri ricavi e proventi).",
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

    key = irap_region_key(place, province)
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
            assumptions.extend(_rate_sentences(answer, row.name, activity))
    if loss:
        assumptions.append("Il valore della produzione è negativo: l'IRAP stimata è zero.")
    amount: Decimal | None = None
    if rate is not None:
        amount = ZERO_MONEY if loss else round_money(base * rate / _HUNDRED)
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
