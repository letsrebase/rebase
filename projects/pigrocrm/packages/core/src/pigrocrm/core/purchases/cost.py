"""What a purchase costs, from the buyer's pack (spec 2026-11-09, suppliers, § 2, «What a
purchase costs»).

One pure function, so one rule decides the figure the profit and loss reads, and the service
freezes its answer on the purchase when it is registered:

- an **ordinary** buyer (`it-standard`) recovers the VAT it may deduct, so a purchase costs the
  taxable amount plus the VAT that is not deductible, bollo and all: the total less the
  deductible VAT. On a reverse-charge purchase the VAT is owed by the buyer and not paid to
  the supplier, so the cost is the amount paid plus the part of that VAT it cannot deduct;
- a **flat-rate** buyer (`it-flat-rate`) never recovers VAT (art. 1 c. 58 L. 190/2014, A5), so
  a purchase costs its total, and on a reverse-charge purchase the total plus the VAT it
  integrates and pays, which it cannot deduct either (art. 1 c. 58 lett. e-bis, A5);
- a **shared** purchase, a legal entity on the `non-it` pack (no VAT register) and a legal
  entity with no pack on record: the total.

A receipt and an expense carry no VAT lines (VAT on a retail receipt is not deductible, art. 19
c. 1 and art. 22 DPR 633/72, A5), so for them every rule above reads the total.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal

from pigrocrm.core.fiscal.pack import PACK_IT_FLAT_RATE, PACK_IT_STANDARD
from pigrocrm.core.money import ZERO_MONEY, round_money

HUNDRED = Decimal(100)


@dataclass(frozen=True)
class VatFigures:
    """The two numbers of a recap line that a cost reads."""

    vat_amount: Decimal
    deductible_share: Decimal


def deductible_vat(lines: Iterable[VatFigures]) -> Decimal:
    """The VAT the buyer may deduct: each line's VAT at its deductible share."""
    return sum(
        (line.vat_amount * line.deductible_share / HUNDRED for line in lines), start=ZERO_MONEY
    )


def purchase_cost(
    *,
    pack_id: str | None,
    amount: Decimal,
    lines: Iterable[VatFigures],
    reverse_charge: bool,
) -> Decimal:
    """What the purchase costs. `pack_id` is the buyer's, `None` for a shared purchase or an
    entity with no profile; `lines` are the VAT recap lines, none on a receipt or an expense."""
    recap = list(lines)
    if not recap:
        return round_money(amount)
    vat = sum((line.vat_amount for line in recap), start=ZERO_MONEY)
    if pack_id == PACK_IT_STANDARD:
        deductible = deductible_vat(recap)
        # Reverse charge: the VAT is not in `amount`, it is owed on top; what cannot be
        # deducted of it is cost. Otherwise it is in `amount`, and what is deducted comes out.
        return round_money(amount + vat - deductible if reverse_charge else amount - deductible)
    if pack_id == PACK_IT_FLAT_RATE and reverse_charge:
        return round_money(amount + vat)
    return round_money(amount)
