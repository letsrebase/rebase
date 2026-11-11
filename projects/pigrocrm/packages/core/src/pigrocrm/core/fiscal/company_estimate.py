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
says so; no loss carry-forward is modelled (art. 84 TUIR would let a loss of an earlier year
reduce the profit), so a year after a loss is estimated as if there were none.

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
from pigrocrm.core.fiscal.law_irap import IrapLaw
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
    # The region whose rate was applied (IRAP only): the ISTAT code, or `BZ` or `TN`.
    region: str | None = None
    missing: str | None = None
    assumptions: list[str]


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
) -> CompanyEstimate | None:
    """The estimate of IRES and IRAP for `basis`'s year, `None` when the law carries no IRAP
    table or the basis has no costs (a year on a basis that deducts none is no srl's)."""
    costs = basis.costs
    if costs is None or law.irap is None:
        return None
    return CompanyEstimate(
        ires=_ires(basis, law),
        irap=_irap(basis, law, place, province),
    )


def _ires(basis: YearBasis, law: CompanyTaxLaw) -> CompanyTaxEstimate:
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
        "Le perdite degli anni precedenti non sono riportate: ogni anno è stimato da solo.",
    ]
    loss = profit < 0
    if loss:
        assumptions.append("L'anno chiude in perdita: l'IRES stimata è zero.")
    return CompanyTaxEstimate(
        lines=lines,
        base=profit,
        rate=law.ires_rate,
        amount=ZERO_MONEY if loss else round_money(profit * law.ires_rate / _HUNDRED),
        loss=loss,
        assumptions=assumptions,
    )


def _names(rows: list[BasisEmployee]) -> str:
    return ", ".join(row.name for row in rows)


def _staff_deductions(basis: YearBasis, irap: IrapLaw) -> list[tuple[CompanyTaxLine | None, str]]:
    """The deductions of art. 11 that need the staff's contract types (REB-1081): each a line
    (`None` where nothing is deducted) and the sentence that says what was and was not applied."""
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
        if basis.revenue > rule.components_limit:
            out.append(
                (
                    None,
                    "La deduzione per dipendente (art. 11 c. 4-bis.1) non spetta: i ricavi "
                    f"superano {rule.components_limit:,.0f} euro.",
                )
            )
        else:
            period_days = (basis.period_to - basis.period_from).days + 1
            rows = sorted(
                (
                    (
                        min(round_money(rule.amount * row.days_in_service / period_days), row.cost),
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
                        "e mai oltre il suo costo; apprendisti e lavoratori disabili, che la "
                        "legge non conta, non sono distinti.",
                    )
                )
    return out


def _irap(
    basis: YearBasis, law: CompanyTaxLaw, place: Place, province: str | None
) -> CompanyTaxEstimate:
    costs = basis.costs
    irap = law.irap
    assert costs is not None and irap is not None
    value = round_money(basis.revenue - costs.purchases)
    lines = [
        CompanyTaxLine(code="revenue", label="Ricavi di competenza", amount=basis.revenue),
        CompanyTaxLine(code="purchases", label="Acquisti", amount=-costs.purchases),
    ]
    assumptions = [
        # The staff cost left out of the base's figures (`staff_cost_not_estimated`) does not
        # move the IRAP base, which keeps staff cost in; what else the basis left out does.
        *(line.reason for line in basis.left_out if line.code != "staff_cost_not_estimated"),
        "Il valore della produzione è ricavi meno acquisti: manca ciò che il gestionale non "
        "registra (ammortamenti, variazioni di rimanenze, altri ricavi e proventi).",
        "Il costo del personale e i compensi degli amministratori restano nella base, come "
        "dice la legge (art. 5 D.Lgs. 446/1997).",
        "Si applica l'aliquota ordinaria della regione della sede, con una sola sede.",
    ]
    deducted = value
    for line, text in _staff_deductions(basis, irap):
        if line is not None:
            lines.append(line)
            deducted = round_money(deducted + line.amount)
        assumptions.append(text)
    small = irap.small_base_deduction(deducted)
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
        rate = irap.rate_for(basis.year, key)
        if rate is None:
            missing = (
                f"Il pacchetto fiscale non ha l'aliquota IRAP {basis.year} della regione "
                f"della sede ({key})."
            )
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
