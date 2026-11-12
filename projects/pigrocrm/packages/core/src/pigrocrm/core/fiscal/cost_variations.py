"""What the law does to the costs of the year, from what a person stated about each purchase
(REB-1089, taxes design § 1). Pure: it takes the purchases as facts and the pack's law, and answers
the lines that move a company's income and IRAP base away from «revenue less every purchase».

**Limited costs (IRES only).** A purchase whose kind is stated (its own, else its category's) is
deducted in the percentage the law gives that kind (`CostLimit`): the part that is not deductible is
added back as one line per kind. The representation expenses of the year are deducted within the cap
of art. 108 c. 2 TUIR on the year's revenue (`RepresentationLaw`), the excess added back. A purchase
that states nothing, or `full`, is deducted in full, as the estimate always did. IRAP follows the
income statement and has none of these limits (`fiscal/law_irap.py`).

**Assets (IRES and IRAP).** A purchase flagged as an asset with a group of the ministerial table
leaves the costs of the year and its depreciation enters them: the cost times the group's
coefficient, halved in the year of purchase (art. 102 c. 2 TUIR), and until the whole cost has been
deducted, so an asset bought in an earlier year still depreciates in this one. For IRES the cost of
a vehicle counts up to the cap of art. 164 and the percentage of its kind is applied to the
depreciation; IRAP depreciates the whole cost, with the same coefficient as a stand-in for the
rate of the financial statements. An asset of at most `full_deduction_limit` euro is deducted in
full in the year it is bought (art. 102 c. 5): it stays in the purchases and needs no coefficient.
An asset whose group the table does not carry has no coefficient: the figure is missing and the
payload names the asset, never a guessed rate. A representation expense flagged as an asset is
depreciated in full and counts in no cap.

The unit cost of art. 102 c. 5 is read as the purchase's cost, since a purchase does not say how
many goods it holds.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from pigrocrm.core.fiscal.law_ires import IresVariationsLaw
from pigrocrm.core.money import ZERO_MONEY, round_money
from pigrocrm.core.purchases.deductibility import AMBIGUOUS_CATEGORY_CODES

_HUNDRED = Decimal(100)
REPRESENTATION_KIND = "representation"
# Names listed in a sentence before it says «and N more».
_NAMES_LISTED = 3


@dataclass(frozen=True)
class VariationLine:
    """One step a variation adds to a tax's base: `amount` is signed (positive adds to the base,
    negative takes from it), like `CompanyTaxLine`, which the estimate turns it into."""

    code: str
    label: str
    amount: Decimal


@dataclass(frozen=True)
class PurchaseFact:
    """One purchase as the variations read it. `kind` is the effective deductibility kind (the
    purchase's own, else its category's, `None` when neither says). `years_since` is how many
    financial years the purchase's own is before the one being estimated: 0 for a purchase of the
    year, more for an asset bought earlier."""

    id: UUID
    description: str
    date: dt.date
    cost: Decimal
    kind: str | None
    asset_group: str | None
    category_code: str | None
    category_name: str
    years_since: int = 0


@dataclass(frozen=True)
class CostVariations:
    """The lines that move the IRES profit and the IRAP value of production, each tax's
    assumptions about them, and what is missing for them (`None` when nothing is)."""

    ires_lines: tuple[VariationLine, ...] = ()
    irap_lines: tuple[VariationLine, ...] = ()
    ires_assumptions: tuple[str, ...] = ()
    irap_assumptions: tuple[str, ...] = ()
    missing: str | None = None


def depreciation(
    cost: Decimal, coefficient: Decimal, first_year_percent: Decimal, years_since: int
) -> Decimal:
    """What the year `years_since` after the one of the purchase deducts of `cost` at
    `coefficient` percent: halved (`first_year_percent`) in the year of purchase, the whole
    coefficient after, and never more than what the earlier years left."""
    full = cost * coefficient / _HUNDRED
    first = full * first_year_percent / _HUNDRED
    if years_since <= 0:
        amount = first
    else:
        left = cost - first - full * (years_since - 1)
        amount = min(full, left)
    return max(ZERO_MONEY, min(amount, cost))


def _percent(value: Decimal) -> str:
    return f"{value.normalize():f}%"


def _names(names: list[str]) -> str:
    shown = ", ".join(f"«{name}»" for name in names[:_NAMES_LISTED])
    rest = len(names) - _NAMES_LISTED
    return shown if rest <= 0 else f"{shown} e altri {rest}"


def compute_cost_variations(
    law: IresVariationsLaw, facts: Iterable[PurchaseFact], *, revenue: Decimal
) -> CostVariations:
    """The variations of the year for `facts`, on a year with `revenue` of the characteristic
    activity (the basis of the representation cap)."""
    depreciation_law = law.depreciation
    cost_removed = ZERO_MONEY
    ires_depreciation = ZERO_MONEY
    irap_depreciation = ZERO_MONEY
    disallowed: dict[str, Decimal] = {}
    representation = ZERO_MONEY
    uncoefficiented: list[str] = []
    undeclared = 0

    for fact in facts:
        limit = law.limit(fact.kind) if fact.kind is not None else None
        percent = limit.percent if limit is not None else _HUNDRED
        is_asset = (
            fact.asset_group is not None and fact.cost > depreciation_law.full_deduction_limit
        )
        if not is_asset:
            if fact.years_since > 0:
                continue
            if limit is not None:
                disallowed[limit.kind] = disallowed.get(limit.kind, ZERO_MONEY) + (
                    fact.cost * (_HUNDRED - percent) / _HUNDRED
                )
            elif fact.kind == REPRESENTATION_KIND:
                representation += fact.cost
            elif fact.kind is None and fact.category_code in AMBIGUOUS_CATEGORY_CODES:
                undeclared += 1
            continue

        coefficient = depreciation_law.coefficient(fact.asset_group)
        if coefficient is None:
            uncoefficiented.append(fact.description)
            continue
        taxable_cost = fact.cost
        if limit is not None and limit.acquisition_cost_cap is not None:
            taxable_cost = min(fact.cost, limit.acquisition_cost_cap)
        for_ires = depreciation(
            taxable_cost, coefficient, depreciation_law.first_year_percent, fact.years_since
        )
        ires_depreciation += for_ires
        irap_depreciation += depreciation(
            fact.cost, coefficient, depreciation_law.first_year_percent, fact.years_since
        )
        if fact.years_since == 0:
            cost_removed += fact.cost
        if limit is not None:
            disallowed[limit.kind] = disallowed.get(limit.kind, ZERO_MONEY) + (
                for_ires * (_HUNDRED - percent) / _HUNDRED
            )

    if uncoefficiented:
        return CostVariations(
            missing=(
                f"Manca il coefficiente di ammortamento di {_names(uncoefficiented)}: il gruppo "
                "del bene non è nella tabella del pacchetto fiscale (D.M. 31/12/1988), e senza "
                "non si sa quanto dedurre."
            )
        )

    ires_lines: list[VariationLine] = []
    irap_lines: list[VariationLine] = []
    if cost_removed != 0:
        removed = VariationLine(
            "assets_cost",
            "Beni ammortizzabili: costo tolto dagli acquisti",
            round_money(cost_removed),
        )
        ires_lines.append(removed)
        irap_lines.append(removed)
    if ires_depreciation != 0:
        ires_lines.append(
            VariationLine(
                "depreciation",
                f"Ammortamento dei beni ({depreciation_law.norm})",
                -round_money(ires_depreciation),
            )
        )
    if irap_depreciation != 0:
        irap_lines.append(
            VariationLine(
                "depreciation",
                f"Ammortamento dei beni ({depreciation_law.norm})",
                -round_money(irap_depreciation),
            )
        )
    for limit in law.limits:
        amount = round_money(disallowed.get(limit.kind, ZERO_MONEY))
        if amount != 0:
            ires_lines.append(
                VariationLine(
                    f"limit_{limit.kind}",
                    f"Costi non deducibili: {limit.label}, deducibili al "
                    f"{_percent(limit.percent)} ({limit.norm})",
                    amount,
                )
            )
    cap = law.representation.cap(revenue)
    excess = round_money(max(ZERO_MONEY, representation - cap))
    if excess != 0:
        ires_lines.append(
            VariationLine(
                f"limit_{REPRESENTATION_KIND}",
                f"Costi non deducibili: {law.representation.label} ({law.representation.norm})",
                excess,
            )
        )

    ires_assumptions: list[str] = []
    if undeclared:
        ires_assumptions.append(
            f"{undeclared} acquisti in categorie che possono contenere costi limitati dalla legge "
            "(veicoli, telefono, alberghi e ristoranti, rappresentanza) non indicano di che costo "
            "si tratta e sono dedotti per intero: se lo sono, la deduzione è sovrastimata."
        )
    irap_assumptions: list[str] = []
    if irap_lines and any(line.code == "depreciation" for line in irap_lines):
        irap_assumptions.append(
            "L'ammortamento dei beni indicati è stimato con il coefficiente del decreto "
            "31/12/1988; in bilancio vale l'aliquota che la società ha scelto."
        )
    return CostVariations(
        ires_lines=tuple(ires_lines),
        irap_lines=tuple(irap_lines),
        ires_assumptions=tuple(ires_assumptions),
        irap_assumptions=tuple(irap_assumptions),
    )


__all__ = [
    "REPRESENTATION_KIND",
    "CostVariations",
    "PurchaseFact",
    "VariationLine",
    "compute_cost_variations",
    "depreciation",
]
