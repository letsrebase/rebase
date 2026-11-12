"""The IRAP an srl deducts from its IRES (REB-1089, taxes design § 1). Pure: it takes the IRAP the
estimate computed, the IRAP the legal entity paid and the staff cost, and answers the lines that
take the deductible IRAP off the profit.

Two deductions, which coexist and whose sum is capped at the IRAP paid in the year (circolare
8/E/2013 § 1.2, Redditi SC 2026 istruzioni, row RF55 code 33):

* **The IRAP on the staff cost** (art. 2 c. 1 D.L. 201/2011): the IRAP paid times the incidence of
  the staff cost on the IRAP base, both net of the deductions of art. 11 D.Lgs. 446/1997, between 0
  and 100%. The staff cost is that of the employees and of the assimilated workers of art. 50 c. 1
  lett. c-bis TUIR, the fees of the directors included.
* **The 10% lump sum** (art. 6 c. 1 D.L. 185/2008) of the IRAP paid, which is due only when net
  interest expense enters the IRAP base. The base this estimate computes holds no interest (revenue
  against purchases), and PigroCRM records none, so the lump sum is never taken and the
  assumptions say so: deducting it anyway would understate the IRES of every srl with no interest.

The IRAP is deducted in the year it is **paid** (art. 99 c. 1 TUIR): the balance of the year before
and the advances of the year, these within the tax actually due for the year. The payments are read
from the tax due items settled in the year (`fiscal/income_facts.py`); with none registered
the IRAP the estimate computed for the year stands in and the payload says so. The incidence is the
year's own for every payment, the balance of the year before included. The profit this starts
from has no IRAP cost in it, so nothing is added back (row RF16): the deduction is the whole
variation.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal

from pigrocrm.core.fiscal.cost_variations import VariationLine
from pigrocrm.core.fiscal.income_variations import IrapPaid
from pigrocrm.core.fiscal.law_ires import IrapDeductionLaw
from pigrocrm.core.money import ZERO_MONEY, round_money

_HUNDRED = Decimal(100)
# What the IRAP's own lines are made of before the deductions of art. 11: everything else in the
# lines is one of those deductions, whichever card added it. `assets_cost` and `depreciation` are
# the asset variations of `fiscal/cost_variations.py`.
VALUE_OF_PRODUCTION_CODES = frozenset({"revenue", "purchases", "assets_cost", "depreciation"})


@dataclass(frozen=True)
class IrapDeduction:
    """The lines that deduct the IRAP from the profit (negative amounts) and what they assume."""

    lines: tuple[VariationLine, ...] = ()
    assumptions: tuple[str, ...] = ()


def irap_deduction(
    law: IrapDeductionLaw,
    *,
    irap_lines: Iterable[tuple[str, Decimal]],
    irap_base: Decimal | None,
    irap_amount: Decimal | None,
    staff_cost: Decimal,
    paid: IrapPaid | None,
) -> IrapDeduction:
    """The deduction for a year whose IRAP estimate has `irap_lines` (code and signed amount),
    base `irap_base` and amount `irap_amount` (`None` when it cannot be given), whose staff and
    directors cost `staff_cost`, and whose IRAP payments are `paid` (`None` for none read)."""
    if paid is not None and paid.total > 0:
        counted_advances = (
            min(paid.advances, irap_amount) if irap_amount is not None else paid.advances
        )
        deductible = round_money(paid.total - paid.advances + max(counted_advances, ZERO_MONEY))
        source = (
            "L'IRAP dedotta è quella versata nell'esercizio secondo i versamenti registrati "
            "(saldo dell'anno prima e acconti, questi nei limiti dell'imposta dovuta): si deduce "
            "quando è pagata (art. 99 c. 1 TUIR)."
        )
    elif irap_amount is not None and irap_amount > 0:
        deductible = irap_amount
        source = (
            "Non risultano versamenti di IRAP registrati nell'esercizio: al posto dell'IRAP "
            "versata si deduce quella stimata dell'anno."
        )
    elif irap_amount is None:
        return IrapDeduction(
            assumptions=(
                "L'IRAP deducibile dall'IRES non è calcolata: l'IRAP dell'anno non è stimabile "
                "e non risultano versamenti registrati.",
            )
        )
    else:
        return IrapDeduction()

    deductions = -sum(
        (amount for code, amount in irap_lines if code not in VALUE_OF_PRODUCTION_CODES),
        ZERO_MONEY,
    )
    staff_net = max(staff_cost - deductions, ZERO_MONEY)
    incidence = Decimal(0)
    if irap_base is not None and irap_base > 0:
        incidence = min(staff_net / irap_base, Decimal(1))
    analytic = round_money(deductible * incidence)
    # The lump sum needs net interest expense in the IRAP base (circolare 8/E/2013 § 1.1); the
    # base computed here has none, so nothing is taken.

    lines: list[VariationLine] = []
    if analytic != 0:
        lines.append(
            VariationLine(
                "irap_staff",
                f"IRAP sul costo del personale ({law.staff_share_norm})",
                -analytic,
            )
        )
    assumptions = [source]
    if analytic != 0:
        assumptions.append(
            "La quota sul costo del lavoro usa l'incidenza del personale sulla base IRAP "
            "dell'anno, al netto delle deduzioni dell'art. 11, anche per il saldo dell'anno prima."
        )
    assumptions.append(
        f"Il {law.lump_sum_percent.normalize():f}% forfettario ({law.lump_sum_norm}) non è "
        "dedotto: spetta solo se nella base IRAP concorrono interessi passivi netti "
        "(circolare 8/E/2013), e il gestionale non registra interessi."
    )
    return IrapDeduction(lines=tuple(lines), assumptions=tuple(assumptions))


__all__ = [
    "VALUE_OF_PRODUCTION_CODES",
    "IrapDeduction",
    "irap_deduction",
]
