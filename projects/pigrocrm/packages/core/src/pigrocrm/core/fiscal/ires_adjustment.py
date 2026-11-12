"""The variations that take an srl's accounting profit to its IRES base (REB-1089), put together for
the estimate: the limited costs and the assets (`fiscal/cost_variations.py`) then the IRAP deducted
(`fiscal/irap_deduction.py`), with the sentence that says what the estimate still leaves out.

Pure, and kept apart from `fiscal/company_estimate.py` so that module only turns the lines into its
own and adds them up.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal

from pigrocrm.core.fiscal.cost_variations import VariationLine
from pigrocrm.core.fiscal.income_variations import IncomeVariations
from pigrocrm.core.fiscal.irap_deduction import irap_deduction

# What the estimate does when the pack has no entry for the year: the profit as it always was.
_UNADJUSTED = (
    "L'utile è ricavi meno acquisti, personale e amministratori, senza le variazioni della "
    "dichiarazione (limiti di deducibilità dei costi, ammortamenti, deduzione dell'IRAP sul costo "
    "del lavoro): è una stima, non il calcolo del commercialista."
)
# What it still leaves out when the year's variations are modelled.
_STILL_OUT = (
    "L'utile è ricavi meno acquisti, personale e amministratori, con le variazioni della "
    "dichiarazione che i dati permettono. Restano fuori il limite agli interessi passivi (art. 96 "
    "TUIR), l'ACE, i limiti ai canoni di locazione e noleggio dei veicoli, i beni immateriali, gli "
    "altri costi non deducibili e l'ammortamento dei beni non indicati come ammortizzabili, che "
    "sono dedotti per intero: è una stima, non il calcolo del commercialista."
)


@dataclass(frozen=True)
class IresAdjustment:
    """The lines to add to the profit, what they assume, and what they lack (`missing`)."""

    lines: tuple[VariationLine, ...]
    assumptions: tuple[str, ...]
    missing: str | None = None


def ires_adjustment(
    variations: IncomeVariations | None,
    *,
    irap_lines: Iterable[tuple[str, Decimal]],
    irap_base: Decimal | None,
    irap_amount: Decimal | None,
    staff_cost: Decimal,
) -> IresAdjustment:
    """The adjustment of the year's profit, from the IRAP estimate's lines, base and amount and the
    cost of staff and directors. `variations` is `None` where the pack has no entry for the year."""
    if variations is None:
        return IresAdjustment(lines=(), assumptions=(_UNADJUSTED,))
    deduction = irap_deduction(
        variations.law.irap_deduction,
        irap_lines=irap_lines,
        irap_base=irap_base,
        irap_amount=irap_amount,
        staff_cost=staff_cost,
        paid=variations.irap_paid,
    )
    return IresAdjustment(
        lines=(*variations.costs.ires_lines, *deduction.lines),
        assumptions=(_STILL_OUT, *variations.costs.ires_assumptions, *deduction.assumptions),
        missing=variations.costs.missing,
    )


__all__ = ["IresAdjustment", "ires_adjustment"]
