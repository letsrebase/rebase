"""The IRPEF, its surcharges and the INPS contributions of an ordinary-regime person for one year,
estimated from the year's cash basis (REB-1072, taxes design § 1 and § 2).

Pure: it takes the year's `YearBasis` (`fiscal/basis.py`, the one basis every estimate reads), the
pack and its law for the year, the taxpayer's INPS facts, the place the legal entity's address
names and the withholdings it suffered, and answers what each tax would be and what it was
computed from. Nothing here reads a session. The figure is an estimate, never the declared one
(A6 «Not the books»): the accountant's typed figure outranks it (`fiscal/readings.py`).

The order is the law's, and it is why INPS comes first
------------------------------------------------------
1. **Income.** The income of a person who practises an art or a profession is what was collected
   less what was spent in the year (art. 54 c. 1 TUIR, the pack's `PERSON_CASH` basis): revenue
   less the costs the basis counts, a loss giving a tax of zero.
2. **INPS.** The contributions are owed on that income (`FiscalPack.inps_for`): the «gestione
   separata» on the net income up to its ceiling, the artisans and traders a fixed part on the
   minimum income plus a percentage above it. They need the profile's category, and the facts the
   category reads.
3. **Deduction.** The compulsory contributions are deducted from the total income (art. 10 c. 1
   lett. e) TUIR, `IrpefLaw.social_security_deduction`), so the IRPEF base is the income less
   them. The law deducts what was **paid** in the year (the balance of the year before and the
   advances of this one); the estimate deducts the year's own computed contributions, the
   approximation the flat-rate estimate makes (A5 row «The contributions deducted are those paid
   in the tax period»). Without the contributions there is no IRPEF figure: a base computed as if
   none were deducted would overstate it, which is a guess.
4. **IRPEF** by brackets (`IrpefLaw.gross_tax`), less its detractions (REB-1087): art. 13 c. 5
   TUIR for self-employment income, which needs the income alone, and art. 12 TUIR for the
   spouse, the children of 21 to 29 and the disabled ones and the cohabiting ascendants, which
   need the household the profile states for the year (`fiscal/household.py`). Both are computed
   on the **reference income**, the income before the contributions are deducted (Agenzia,
   specifiche § 21.4), not on the IRPEF base. A household the profile does not state gives no
   family detraction, and the assumptions say so. Then the regional surcharge of the region of
   the legal entity's address (`BZ` or `TN` by province), with the household's regional rules,
   and the municipal surcharge of its municipality, which are due only if the IRPEF net of
   detractions is due (art. 50 c. 2 D.Lgs. 446/1997; art. 1 c. 4 D.Lgs. 360/1998): a net IRPEF
   of zero gives none.
5. **Withholdings.** What the customers withheld on the invoices collected in the year
   (`fiscal/withholdings_suffered.py`) is credited against the IRPEF alone. The IRPEF's `amount`,
   the reading's, is the IRPEF net of the detractions and of them, which is what the advances are
   based on (A5
   «The advance base is the declared tax net of deductions, credits, withholdings and excesses»);
   what they exceed it by is `credit`, never a negative tax. The surcharges are added after, in
   `with_surcharges`, since the withholdings do not reduce them and they have their own advances.

A figure that cannot be given is `None` and says why in `missing`; a profile field it needs is in
`missing_parameters`. The 2026 municipal rate that is not yet published (`pending` in the Finanze's
list: until 20 December it shows `0*` for a municipality that has not deliberated yet too) gives no
municipal figure and says the list was read before then: the previous year's rate is not carried
over here, because after 20 December the Finanze does it itself on the list, and the next import of
the list brings it, as the single place the carry-over is done (REB-1063).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict

from pigrocrm.core.fiscal.basis_schemas import YearBasis
from pigrocrm.core.fiscal.household import Household
from pigrocrm.core.fiscal.law import TaxYearLaw
from pigrocrm.core.fiscal.law_inps import (
    InpsCategoryName,
    InpsContributions,
    InpsYearLaw,
    TradeGestione,
)
from pigrocrm.core.fiscal.law_irpef import IrpefLaw, region_key
from pigrocrm.core.fiscal.pack import FiscalPack
from pigrocrm.core.money import ZERO_MONEY, round_money
from pigrocrm.core.places.locate import Place

_ZERO = Decimal("0.00")

# Named in the payload, as the flat-rate estimate's own is (`analytics/fiscal.py::WARNING`): a
# labelled estimate is useful, an estimate presented as an actual is the original defect in a new
# form. Each clause is a real reason the figure differs from the return.
ORDINARY_WARNING = (
    "Stima indicativa. Non tiene conto di altri redditi, degli oneri detraibili e dei crediti "
    "d'imposta, degli acconti già versati né di altre deduzioni. Per la dichiarazione fai "
    "riferimento al tuo commercialista."
)


class OrdinaryLine(BaseModel):
    """One step or one piece of a figure: `amount` is signed (a cost, a deduction or a credit is
    negative), so a screen draws the lines and their sum without computing anything, and `None`
    when that piece cannot be given, which `missing` explains."""

    model_config = ConfigDict(frozen=True)

    code: str
    label: str
    amount: Decimal | None


class OrdinaryIncome(BaseModel):
    """The income the taxes are computed on: `lines` lead from the revenue collected to the
    income of art. 54 (`professional_income`, negative for a loss) and from it, less the INPS
    contributions deducted, to the IRPEF base (`taxable_income`, never negative, `None` when the
    contributions to deduct are not known)."""

    model_config = ConfigDict(frozen=True)

    lines: list[OrdinaryLine]
    professional_income: Decimal | None
    taxable_income: Decimal | None
    loss: bool = False
    assumptions: list[str]
    sources: list[str]


class OrdinaryTax(BaseModel):
    """One tax of the person. `lines` are the steps from the income to `base` and `rate` is the
    percentage applied (INPS); `parts` are the pieces that add up to `amount`, in the order a
    reader adds them (the surcharges and the withholdings of the IRPEF, the fixed and the
    percentage part of the INPS). `amount` is `None` when the figure cannot be given, in which
    case `missing` says what is missing, one sentence each. `credit` is, for the IRPEF, what the
    withholdings exceed the tax by. `assumptions` are the simplifications the figure carries and
    `sources` where its law was read."""

    model_config = ConfigDict(frozen=True)

    lines: list[OrdinaryLine]
    base: Decimal | None
    rate: Decimal | None
    parts: list[OrdinaryLine]
    amount: Decimal | None
    credit: Decimal | None = None
    # IRPEF only: the net IRPEF (`amount`) plus the regional and the municipal surcharge, `None`
    # until both are known. The surcharges are not netted of the withholdings, which are credited
    # against the IRPEF alone, and have their own advances, so `amount` stays the IRPEF.
    with_surcharges: Decimal | None = None
    # The region whose surcharge was applied (ISTAT code, or `BZ`/`TN`) and the municipality's
    # cadastral code: what the legal entity's address said when the figure was computed.
    region: str | None = None
    cadastral_code: str | None = None
    missing: list[str]
    assumptions: list[str]
    sources: list[str]


class OrdinaryEstimate(BaseModel):
    """What the ordinary-regime person owes for the year on its income, with what each figure was
    computed from. The year's own figures are the payload's `basis`. `missing_parameters` are the
    profile fields the INPS needs and the profile does not state, by field name."""

    model_config = ConfigDict(frozen=True)

    income: OrdinaryIncome
    inps: OrdinaryTax
    irpef: OrdinaryTax
    missing_parameters: list[str]
    # Net IRPEF with its surcharges plus INPS: the one figure a person sets aside; `None` until
    # both are known.
    due_total: Decimal | None


@dataclass(frozen=True)
class InpsFacts:
    """The taxpayer's INPS facts, as the fiscal profile states them (`None`: not stated).

    The three last ones are the reductions of an artisan or a trader that INPS granted
    (REB-1097): none is inferred, and an absent one means no reduction is applied."""

    category: InpsCategoryName | None
    trade: TradeGestione | None
    covered_elsewhere: bool | None
    before_1996: bool | None
    over_65_reduction: bool | None = None
    new_enrolment_reduction: bool | None = None
    first_enrolment_month: date | None = None


def estimate_ordinary_taxes(
    basis: YearBasis,
    pack: FiscalPack,
    law: TaxYearLaw,
    *,
    inps_facts: InpsFacts,
    place: Place,
    province: str | None,
    withholdings: Decimal | None,
    household: Household | None = None,
) -> OrdinaryEstimate | None:
    """The estimate of INPS and IRPEF for `basis`'s year, `None` when the regime owes no IRPEF
    or the basis has no costs (a year on a basis that deducts none is no ordinary person's). When
    the pack has no IRPEF figures for the year (`FiscalPack.irpef_for`) the IRPEF is computed
    nowhere and says so in its `missing`; the INPS stands on its own.
    `withholdings` is what the customers withheld in the year, `None` when the pack states no rule
    for the year a withholding is credited against. `household` is what the profile states about
    the taxpayer's family; `None`, or a statement of another year, applies no family detraction."""
    costs = basis.costs
    if costs is None or "irpef" not in law.taxes:
        return None
    irpef_law = pack.irpef_for(basis.year)

    professional = round_money(basis.revenue - costs.total)
    loss = professional < 0
    income_for_inps = max(professional, _ZERO)

    inps, inps_missing_parameters = _inps(pack, basis.year, income_for_inps, inps_facts)
    deduction = irpef_law.social_security_deduction if irpef_law is not None else None
    deducted: Decimal | None = None
    taxable: Decimal | None = None
    if inps.amount is not None and deduction is not None:
        deducted = min(inps.amount, income_for_inps)
        taxable = round_money(income_for_inps - deducted)

    income_assumptions = [
        "Il reddito è quello di chi esercita un'arte o una professione: quanto incassato meno "
        "quanto pagato nell'anno. Per chi ha un reddito d'impresa la base dipende dalla "
        "contabilità (di cassa se semplificata, di competenza se ordinaria) e il profilo non "
        "la dice.",
        "Si deducono i contributi INPS calcolati per l'anno. La legge deduce quelli versati "
        "nell'anno, cioè il saldo dell'anno prima e gli acconti di questo: la stima li "
        "approssima con quelli dell'anno stesso.",
        "Non ci sono altri redditi, né altri oneri deducibili.",
    ]
    if loss:
        income_assumptions.append("L'anno chiude in perdita: le imposte stimate sono zero.")
    income_lines = [
        OrdinaryLine(code="revenue", label="Incassato nell'anno", amount=basis.revenue),
        OrdinaryLine(code="costs", label="Spese pagate nell'anno", amount=-costs.total),
    ]
    income_lines.append(
        OrdinaryLine(
            code="inps_deducted",
            label="Contributi INPS dedotti (art. 10 c. 1 lett. e) TUIR)",
            amount=None if deducted is None else -deducted,
        )
    )
    income = OrdinaryIncome(
        lines=income_lines,
        professional_income=professional,
        taxable_income=taxable,
        loss=loss,
        assumptions=income_assumptions,
        sources=[basis.source] + ([deduction.source] if deduction is not None else []),
    )
    irpef = (
        _irpef(
            pack,
            irpef_law,
            basis.year,
            taxable,
            place,
            province,
            withholdings,
            inps,
            income_for_inps,
            household,
        )
        if irpef_law is not None
        else _no_irpef(basis.year)
    )
    due_total = (
        round_money(irpef.with_surcharges + inps.amount)
        if irpef.with_surcharges is not None and inps.amount is not None
        else None
    )
    return OrdinaryEstimate(
        income=income,
        inps=inps,
        irpef=irpef,
        missing_parameters=inps_missing_parameters,
        due_total=due_total,
    )


# --- INPS ----------------------------------------------------------------------------------


def _inps(
    pack: FiscalPack,
    year: int,
    income: Decimal,
    facts: InpsFacts,
) -> tuple[OrdinaryTax, list[str]]:
    """The contributions on `income`, or why there are none to give and which profile fields
    would give them."""
    law = pack.inps_for(year)
    missing: list[str] = []
    missing_parameters: list[str] = []
    assumptions = [
        "Le contributi sono calcolate sul reddito dell'anno e non tengono conto di acconti già "
        "versati né di altre gestioni previdenziali.",
    ]
    sources: list[str] = []
    if law is None:
        missing.append(
            f"Il pacchetto fiscale non ha le regole INPS del {year}: l'INPS le fissa ogni anno "
            "e non si usano quelle dell'anno prima."
        )
        return _no_inps(missing, assumptions), missing_parameters
    sources.append(law.source)

    category = facts.category
    if category is None:
        missing_parameters.append("inps_category")
        missing.append(
            "Manca la categoria INPS (gestione separata, oppure artigiani e commercianti): "
            "si indica nelle impostazioni dell'azienda."
        )
        return _no_inps(missing, assumptions, sources), missing_parameters

    contributions: InpsContributions | None = None
    if category == "gestione_separata":
        if facts.covered_elsewhere is None:
            missing_parameters.append("inps_covered_elsewhere")
            missing.append(
                "Manca se sei pensionato o assicurato presso un'altra forma di previdenza "
                "obbligatoria: l'aliquota della gestione separata cambia."
            )
        else:
            contributions = law.contributions(
                category, income, covered_elsewhere=facts.covered_elsewhere
            )
    else:
        if facts.trade is None:
            missing_parameters.append("inps_trade")
            missing.append(
                "Manca se sei artigiano o commerciante: le aliquote delle due gestioni sono "
                "diverse."
            )
        else:
            reductions, reduction_notes = inps_reductions(law, facts)
            assumptions.extend(reduction_notes)
            # The older ceiling matters only above the lower one: where both answer the same,
            # the question is not needed and its absence is no missing fact.
            by_flag = {
                flag: law.contributions(
                    category,
                    income,
                    trade=facts.trade,
                    contributions_before_1996=flag,
                    **reductions,
                )
                for flag in (False, True)
            }
            if facts.before_1996 is not None:
                contributions = by_flag[facts.before_1996]
            elif by_flag[False] == by_flag[True]:
                contributions = by_flag[False]
            else:
                missing_parameters.append("inps_before_1996")
                missing.append(
                    "Manca se hai contributi versati prima del 1996: con il tuo reddito il "
                    "massimale contributivo cambia."
                )
        assumptions.append(
            "Per artigiani e commercianti il reddito è qui quello di cassa di un professionista: "
            "per un reddito d'impresa la base dipende dalla contabilità."
        )
    if contributions is None:
        return _no_inps(missing, assumptions, sources), missing_parameters

    lines = [OrdinaryLine(code="income", label="Reddito dell'anno", amount=income)]
    if contributions.base != contributions.income:
        lines.append(
            OrdinaryLine(
                code="above_ceiling",
                label="Parte oltre il massimale contributivo",
                amount=contributions.base - contributions.income,
            )
        )
    parts: list[OrdinaryLine] = []
    if contributions.fixed != 0:
        parts.append(
            OrdinaryLine(
                code="fixed",
                label="Contributi sul reddito minimale e maternità (quote fisse)",
                amount=contributions.fixed,
            )
        )
        parts.append(
            OrdinaryLine(
                code="percentage",
                label="Contributi sul reddito oltre il minimale",
                amount=contributions.percentage,
            )
        )
    else:
        parts.append(
            OrdinaryLine(
                code="percentage", label="Contributi sul reddito", amount=contributions.percentage
            )
        )
    return (
        OrdinaryTax(
            lines=lines,
            base=contributions.base,
            rate=contributions.rate,
            parts=parts,
            amount=round_money(contributions.total),
            missing=[],
            assumptions=assumptions,
            sources=sources,
        ),
        missing_parameters,
    )


def inps_reductions(law: InpsYearLaw, facts: InpsFacts) -> tuple[dict[str, Any], list[str]]:
    """The reduction INPS granted an artisan or a trader, as the arguments of
    `InpsYearLaw.contributions`, and what the figure says about it (REB-1097). A reduction is
    applied only when the profile says INPS granted it, never from the age or the history, and
    the first-enrolment one only where the year's law covers the enrolment's month."""
    over_65 = bool(facts.over_65_reduction)
    new_enrolment = bool(facts.new_enrolment_reduction)
    if over_65 and new_enrolment:
        return {}, [
            "Il profilo indica sia la riduzione per i pensionati ultrasessantacinquenni sia quella "
            "per i nuovi iscritti, che non si cumulano: nessuna è applicata."
        ]
    if over_65:
        return {"over_65_pensioner": True}, [
            "Applicata la riduzione del 50% per i pensionati con più di 65 anni (legge 449/1997, "
            "art. 59, comma 15), sulla sola aliquota IVS: nessuna fonte dice se vale anche per "
            "lo 0,48% dei commercianti e per il contributo di maternità, che restano interi."
        ]
    if new_enrolment:
        month = facts.first_enrolment_month
        enrolment = law.artisans_traders.new_enrolment
        if month is None or enrolment is None or not enrolment.covers(month, law.year):
            return {}, [
                f"La riduzione per i nuovi iscritti indicata nel profilo non copre il {law.year}: "
                "non è applicata."
            ]
        return {"first_enrolment_month": month}, [
            f"Applicata la riduzione del 50% della sola aliquota IVS per chi si è iscritto per la "
            f"prima volta nel {enrolment.enrolment_year} (legge 207/2024, art. 1, comma 186): "
            "il contributo di maternità e lo 0,48% dei commercianti restano interi."
        ]
    return {}, [
        "Non è applicata nessuna riduzione per i pensionati con più di 65 anni o per i nuovi "
        "iscritti: si applicano solo se il profilo dice che l'INPS le ha concesse."
    ]


def _no_inps(
    missing: list[str], assumptions: list[str], sources: list[str] | None = None
) -> OrdinaryTax:
    return OrdinaryTax(
        lines=[],
        base=None,
        rate=None,
        parts=[],
        amount=None,
        missing=missing,
        assumptions=assumptions,
        sources=sources or [],
    )


# --- IRPEF ---------------------------------------------------------------------------------


def _no_irpef(year: int) -> OrdinaryTax:
    """The IRPEF of a year the pack has no figures for: no amount, and what is missing."""
    return OrdinaryTax(
        lines=[],
        base=None,
        rate=None,
        parts=[],
        amount=None,
        missing=[
            f"Il pacchetto fiscale non ha ancora le cifre dell'IRPEF del {year}: la legge le "
            "fissa ogni anno e non si usano quelle dell'anno prima."
        ],
        assumptions=[],
        sources=[],
    )


def _irpef(
    pack: FiscalPack,
    irpef_law: IrpefLaw,
    year: int,
    taxable: Decimal | None,
    place: Place,
    province: str | None,
    withholdings: Decimal | None,
    inps: OrdinaryTax,
    reference_income: Decimal,
    household: Household | None,
) -> OrdinaryTax:
    assumptions = [
        "Gli oneri detraibili dell'articolo 15 del TUIR (per esempio le spese sanitarie) e i "
        "crediti d'imposta non sono applicati.",
        "L'addizionale regionale e quella comunale spettano se l'IRPEF, al netto delle "
        "detrazioni, è dovuta (art. 50 c. 2 D.Lgs. 446/1997; art. 1 c. 4 D.Lgs. 360/1998).",
        "La regione e il comune sono quelli dell'indirizzo dell'azienda, preso come domicilio "
        "fiscale; la legge guarda il domicilio fiscale al 1° gennaio dell'anno.",
    ]
    sources = [irpef_law.brackets_source] if irpef_law.brackets_source else []
    missing: list[str] = []
    if taxable is None:
        missing.append(
            "L'IRPEF si calcola sul reddito meno i contributi INPS: senza i contributi non c'è "
            "una cifra, perché non dedurli la farebbe sovrastimare."
            if inps.amount is None
            else "Il pacchetto fiscale non deduce i contributi dal reddito: senza la regola "
            "citata non c'è una cifra."
        )
        return OrdinaryTax(
            lines=[],
            base=None,
            rate=None,
            parts=[],
            amount=None,
            missing=missing,
            assumptions=assumptions,
            sources=sources,
        )

    gross = irpef_law.gross_tax(taxable)
    parts = [OrdinaryLine(code="gross_irpef", label="IRPEF lorda", amount=gross)]
    stated = household if household is not None and household.year == year else None
    detraction_lines, detraction_notes, detraction_sources = _detractions(
        irpef_law, year, reference_income, household, stated
    )
    # A detraction takes off what the tax still has: a line is capped at what the lines before it
    # left, so the parts add up to the net tax and none shows more than was owed.
    remaining = gross
    for line in detraction_lines:
        assert line.amount is not None  # noqa: S101  a detraction line always has an amount
        taken = min(-line.amount, remaining)
        remaining -= taken
        if taken > 0:
            parts.append(line.model_copy(update={"amount": -taken}))
    assumptions += detraction_notes
    sources += detraction_sources
    net = round_money(remaining)

    key = region_key(place.region_code, province)
    regional: Decimal | None = None
    municipal: Decimal | None = None
    cadastral = place.cadastral_code
    if net == 0:
        # Nothing due, so no surcharge: the place does not matter and is not asked for.
        regional = municipal = ZERO_MONEY
        assumptions.append(
            "L'IRPEF stimata, al netto delle detrazioni, è zero: le addizionali non sono dovute."
        )
        key = cadastral = None
        regional_label = "Addizionale regionale"
        municipal_label = "Addizionale comunale"
    else:
        regional, regional_label, regional_missing, regional_notes, regional_source = _regional(
            irpef_law, key, place, taxable, reference_income, stated
        )
        missing += regional_missing
        assumptions += regional_notes
        sources += regional_source
        municipal, municipal_label, municipal_missing, municipal_notes, municipal_source = (
            _municipal(pack, year, place, taxable)
        )
        missing += municipal_missing
        assumptions += municipal_notes
        sources += municipal_source
    if withholdings is None:
        missing.append(
            f"Il pacchetto fiscale non dice a quale anno si scomputano le ritenute del {year}: "
            "le ritenute subite non sono nella stima."
        )
        parts.append(
            OrdinaryLine(code="withholdings", label="Ritenute subite sulle fatture", amount=None)
        )
    else:
        parts.append(
            OrdinaryLine(
                code="withholdings",
                label="Ritenute subite sulle fatture incassate",
                amount=-withholdings,
            )
        )
        rules = pack.payment_rules_for(year)
        if rules is not None and rules.withholding_credit is not None:
            sources.append(rules.withholding_credit.source)
        assumptions.append(
            "Le ritenute sono quelle trattenute dai clienti sui pagamenti incassati nell'anno "
            "(art. 25 D.P.R. 600/1973), scomputate dall'IRPEF dell'anno in cui il reddito è "
            "tassato; le addizionali non si riducono delle ritenute."
        )
    parts.append(OrdinaryLine(code="regional_surcharge", label=regional_label, amount=regional))
    parts.append(OrdinaryLine(code="municipal_surcharge", label=municipal_label, amount=municipal))

    amount: Decimal | None = None
    credit: Decimal | None = None
    with_surcharges: Decimal | None = None
    if withholdings is not None:
        left = round_money(net - withholdings)
        amount = max(left, ZERO_MONEY)
        credit = max(-left, ZERO_MONEY)
        if regional is not None and municipal is not None:
            with_surcharges = round_money(amount + regional + municipal)
    return OrdinaryTax(
        lines=[],
        base=taxable,
        rate=None,
        parts=parts,
        amount=amount,
        credit=credit,
        with_surcharges=with_surcharges,
        region=key,
        cadastral_code=cadastral,
        missing=missing,
        assumptions=assumptions,
        sources=sources,
    )


_FAMILY_LABELS = {
    "spouse": ("detraction_spouse", "Detrazione per il coniuge a carico (art. 12 TUIR)"),
    "children": ("detraction_children", "Detrazione per i figli a carico (art. 12 TUIR)"),
    "ascendants": (
        "detraction_ascendants",
        "Detrazione per gli ascendenti conviventi (art. 12 TUIR)",
    ),
}


def _detractions(
    irpef_law: IrpefLaw,
    year: int,
    reference_income: Decimal,
    household: Household | None,
    stated: Household | None,
) -> tuple[list[OrdinaryLine], list[str], list[str]]:
    """The IRPEF's detractions at `reference_income`: the lines (negative, the ones that are not
    zero), what the figure assumes and where the rules were read. `household` is what the
    profile holds and `stated` the same when it is the year's own."""
    lines: list[OrdinaryLine] = []
    notes: list[str] = []
    sources: list[str] = []
    self_employment = irpef_law.self_employment_detraction
    if self_employment is not None:
        sources.append(self_employment.source)
        amount = self_employment.amount(reference_income)
        if amount > 0:
            lines.append(
                OrdinaryLine(
                    code="detraction_self_employment",
                    label="Detrazione per i redditi di lavoro autonomo (art. 13 c. 5 TUIR)",
                    amount=-amount,
                )
            )
        notes.append(
            "La detrazione per i redditi di lavoro autonomo (art. 13 c. 5 TUIR) è calcolata sul "
            "reddito prima dei contributi dedotti, come chiede l'Agenzia delle Entrate, e senza "
            "i 50 euro in più per un reddito da 11.000 a 17.000 euro (art. 13 c. 5-ter), su cui "
            "i testi dell'Agenzia non concordano. Si assume che non ci siano redditi "
            "da lavoro dipendente o da pensione, e che l'attività sia in regime semplificato o "
            "professionale: in contabilità ordinaria la detrazione non spetta."
        )
    family = irpef_law.family_detractions
    if family is None:
        return lines, notes, sources
    sources.append(family.source)
    if household is None:
        notes.append(
            "Il profilo non dice se hai familiari a carico: non si applicano le detrazioni "
            "dell'articolo 12 del TUIR (coniuge, figli da 21 a 29 anni o, se disabili, da 21 "
            "anni in su, ascendenti conviventi) né le regole regionali per la famiglia. Se ne "
            "hai, indicali nei parametri fiscali e l'imposta scende."
        )
    elif stated is None:
        notes.append(
            f"Il profilo indica i familiari a carico del {household.year}, non quelli del "
            f"{year}: non si applicano le detrazioni dell'articolo 12 del TUIR né le regole "
            "regionali per la famiglia."
        )
    else:
        given = family.for_household(stated, reference_income)
        for kind, amount in given:
            code, label = _FAMILY_LABELS[kind]
            lines.append(OrdinaryLine(code=code, label=label, amount=-amount))
        notes.append(
            "Detrazioni dell'articolo 12 del TUIR dai familiari a carico indicati nel profilo "
            "("
            + (
                ", ".join(_FAMILY_LABELS[kind][1].split(" (")[0].lower() for kind, _ in given)
                if given
                else "nessuna: i familiari indicati non danno detrazioni a questo reddito, o sono "
                "figli sotto i 21 anni, per i quali l'articolo non prevede detrazione"
            )
            + "). Si assume che ogni familiare abbia un reddito entro i limiti dell'articolo 12 "
            "c. 2 e che non siano familiari residenti all'estero di un contribuente non "
            "dell'Unione europea (c. 2-bis); non si calcola la detrazione del coniuge per il "
            "primo figlio di chi non ha un altro genitore (c. 1 lett. c)."
        )
    return lines, notes, sources


def _regional(
    irpef_law: IrpefLaw,
    key: str | None,
    place: Place,
    taxable: Decimal,
    total_income: Decimal,
    household: Household | None,
) -> tuple[Decimal | None, str, list[str], list[str], list[str]]:
    """The regional surcharge: `(amount, label, missing, assumptions, sources)`. `household` is
    the profile's statement for the year, `None` when it holds none."""
    label = "Addizionale regionale"
    if key is None:
        if place.status == "not_italian":
            return (
                None,
                label,
                [
                    "L'indirizzo dell'azienda non è in Italia: non si sa a quale regione spetta "
                    "l'addizionale regionale."
                ],
                [],
                [],
            )
        return (
            None,
            label,
            [
                "Manca l'indirizzo della sede dell'azienda (città e provincia in Italia): senza "
                "la regione non si sa quale addizionale regionale applicare."
            ],
            [],
            [],
        )
    rule = irpef_law.regional_law(key)
    if rule is None:
        return (
            None,
            label,
            [f"Il pacchetto fiscale non ha l'addizionale regionale della regione ({key})."],
            [],
            [],
        )
    name = rule.name
    labelled = f"Addizionale regionale ({name})"
    source = [rule.source] if rule.source else []
    # Valle d'Aosta's threshold is tested on the «reddito complessivo», before the deductible
    # charges: where the total income is above it and the base is not, the surcharge is due and
    # the base alone would say it is not, so no figure is given rather than the smaller one.
    exempt = rule.exempt_up_to
    if rule.exempt_tested_on == "total" and exempt is not None and taxable <= exempt < total_income:
        return (
            None,
            labelled,
            [
                f"L'esenzione dell'addizionale regionale ({name}) guarda il reddito complessivo, "
                "prima dei contributi dedotti: con il tuo reddito non si sa se spetta, e non si "
                "stima."
            ],
            [],
            source,
        )
    if rule.undetermined(taxable, household):
        return (
            None,
            labelled,
            [
                f"Per l'addizionale regionale ({name}) l'aliquota ridotta dipende dalla somma "
                "dei redditi imponibili di tutti quelli a carico dei quali è il familiare "
                "disabile (al massimo 50.000 euro): nei parametri fiscali indica quel reddito "
                "degli altri, o 0 se non ce ne sono."
            ],
            [],
            source,
        )
    notes: list[str] = []
    if rule.reads_household:
        if household is None:
            notes.append(
                f"Dell'addizionale regionale ({name}) non sono applicate le regole per i figli "
                "o per le persone disabili: il profilo non indica la famiglia dell'anno."
            )
        else:
            applied = rule.household_applied(taxable, household)
            if applied:
                words = {
                    "reduced_rate": "l'aliquota ridotta per disabilità",
                    "child_detraction": "la detrazione per i figli",
                }
                notes.append(
                    f"Dell'addizionale regionale ({name}) sono applicate, dalla famiglia "
                    f"indicata nel profilo, {' e '.join(words[item] for item in applied)}."
                )
    if rule.unmodelled:
        notes.append(
            f"Per l'addizionale regionale ({name}) restano punti che la legge non chiarisce o "
            "che dipendono da fatti che il profilo non ha: la stima non li applica, sono nelle "
            "fonti e vanno confermati con il commercialista."
        )
    return rule.surcharge(taxable, household), labelled, [], notes, source


def _municipal(
    pack: FiscalPack, year: int, place: Place, taxable: Decimal
) -> tuple[Decimal | None, str, list[str], list[str], list[str]]:
    """The municipal surcharge: `(amount, label, missing, assumptions, sources)`."""
    label = "Addizionale comunale"
    law = pack.municipal_surcharge
    if law is None:
        return None, label, ["Il pacchetto fiscale non ha le addizionali comunali."], [], []
    if place.status == "not_italian":
        return (
            None,
            label,
            [
                "L'indirizzo dell'azienda non è in Italia: non si sa a quale comune spetta "
                "l'addizionale comunale."
            ],
            [],
            [],
        )
    if place.cadastral_code is None:
        return (
            None,
            label,
            [
                "Manca il comune: la città dell'azienda non corrisponde a un comune della "
                "provincia, e senza il comune non si sa quale addizionale comunale applicare."
            ],
            [],
            [],
        )
    name = place.municipality_name or place.cadastral_code
    dataset = law.for_year(year)
    if dataset is None:
        return (
            None,
            label,
            [f"Il pacchetto fiscale non ha l'elenco delle addizionali comunali del {year}."],
            [],
            [],
        )
    source = [
        f"Dipartimento delle Finanze, «Addizionale comunale all'Irpef», elenchi generali "
        f"{year}, letto il {dataset.downloaded}, {dataset.url}"
    ]
    row = law.lookup(year, place.cadastral_code)
    labelled = f"Addizionale comunale ({name})"
    if row is None:
        return (
            None,
            labelled,
            [f"Il comune di {name} non è nell'elenco {year} della Finanze."],
            [],
            source,
        )
    amount = row.surcharge(taxable)
    if row.status == "pending":
        return (
            None,
            labelled,
            [
                f"L'aliquota {year} dell'addizionale comunale di {name} non è ancora "
                f"pubblicata: l'elenco della Finanze letto il {dataset.downloaded} non ha la "
                "delibera, e fino al 20 dicembre mostra così anche i comuni che non hanno "
                "ancora deliberato. Non si usa l'aliquota dell'anno prima: dopo il 20 dicembre "
                "la Finanze la riporta da sola."
            ],
            [],
            source,
        )
    if row.status == "irregular":
        return (
            None,
            labelled,
            [
                f"Le aliquote che il comune di {name} ha pubblicato per il {year} non sono "
                "leggibili come scaglioni di reddito: l'addizionale comunale non è stimata."
            ],
            [],
            source,
        )
    notes = [
        "Le aliquote del comune si applicano a scaglioni, come l'IRPEF; sopra la soglia di "
        "esenzione si tassa tutto il reddito, non solo l'eccedenza.",
    ]
    if row.status == "none":
        notes = [f"Il comune di {name} non ha istituito l'addizionale comunale."]
    elif row.category_exemption:
        notes.append(
            f"Il comune di {name} esenta anche una categoria di contribuenti (per esempio i "
            "pensionati): l'esenzione non è applicata."
        )
    return amount, labelled, [], notes, source


__all__ = [
    "ORDINARY_WARNING",
    "InpsFacts",
    "OrdinaryEstimate",
    "OrdinaryIncome",
    "OrdinaryLine",
    "OrdinaryTax",
    "estimate_ordinary_taxes",
]
