"""The money arithmetic of spec 6.1, as pure functions over `Decimal`.

No `float` reaches this module and none leaves it. The previous system applied a percentage to a
total it had re-read out of a formatted string (`parseAmount(values.TOTALE)`); here an
amount is only ever a `Decimal` computed from other `Decimal`s, and text is produced
at the very end by `format_amount_*` for the XML and the PDF, never parsed back.

Kept free of the database, the regime and the exporter on purpose: rules 2 and 3 below
disagree by cents, the disagreement is invisible under the flat-rate regime the product
ships with, and the only way to pin them is a test with rates nobody uses -- which
needs no session, no container and no XML.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID

from pigrocrm.core.invoices.schemas import MONEY_DECIMAL_PLACES, MONEY_MAX_DIGITS
from pigrocrm.core.money import FACTOR_QUANT, round_money

RATE_EXPONENT = Decimal("0.01")
_HUNDRED = Decimal("100")

# The smallest amount a `Numeric(12, 2)` column cannot hold: 10^10, because the scale takes
# two of the twelve digits and Postgres refuses anything that does not *round* to an
# absolute value below that. Derived from the two width constants rather than written as a
# literal, so widening the column is one edit.
#
# Declared here because this is the module that produces the values those columns receive.
# `quantity` and `unit_price` are `Numeric(12, 6)` and their Pydantic bounds mirror
# that faithfully -- but the column their *product* lands in is `Numeric(12, 2)`, and no
# bound on two factors can express a bound on the product: 100000 x 100000 is two valid
# six-digit factors and an eleven-digit result. Summation reaches the same place without
# any large line at all, since an invoice may carry `MAX_LINES` of them.
MONEY_MAX_EXCLUSIVE = Decimal(10) ** (MONEY_MAX_DIGITS - MONEY_DECIMAL_PLACES)


def overflows_money_column(value: Decimal) -> bool:
    """True when `value` cannot be stored in one of the `Numeric(12, 2)` money columns.

    On the *rounded* value, which is what is stored and what Postgres measures: 9999999999.995
    is below the limit and rounds to a number that is not.

    On the absolute value, because a discount is a line (spec 6.1 rule 6) and a line total is
    legitimately negative; a check on the signed value would leave half the range open.

    A predicate rather than a raise: `totals.py` is deliberately free of the database, the
    regime and the exporter, and a domain error naming a field belongs to the service that
    knows which field the caller supplied.
    """
    return abs(round_money(value)) >= MONEY_MAX_EXCLUSIVE


def format_amount_2(value: Decimal) -> str:
    r"""FPR12 `Amount2DecimalType`: `[\-]?[0-9]{1,11}\.[0-9]{2}` -- exactly two
    decimals, so a bare "1400" is schema-invalid and `str(Decimal("1400"))` cannot be
    used directly."""
    return f"{round_money(value):.2f}"


def format_amount_8(value: Decimal) -> str:
    """FPR12 `Amount8DecimalType`: two to eight decimals. Six, matching the
    `Numeric(12, 6)` columns exactly, so the printed value is the stored value."""
    return f"{value.quantize(FACTOR_QUANT, rounding=ROUND_HALF_UP):.6f}"


def format_rate(value: Decimal) -> str:
    r"""FPR12 `RateType`: `[0-9]{1,3}\.[0-9]{2}`."""
    return f"{value.quantize(RATE_EXPONENT, rounding=ROUND_HALF_UP):.2f}"


@dataclass(frozen=True)
class ComputedLine:
    """One `DettaglioLinee`, with every derived value already decided.

    Built by `InvoiceService` from the caller's input plus the `RegimeStrategy`'s
    answer; this module never decides `vat_rate`, `vat_nature` or
    `legal_reference`, it only groups by them.
    """

    line_number: int
    description: str
    quantity: Decimal
    unit_of_measure: str | None
    unit_price: Decimal
    discount_percentage: Decimal | None
    discount_amount: Decimal | None
    total_price: Decimal
    vat_rate: Decimal
    vat_nature: str | None
    legal_reference: str | None
    # The contract rate card this line bills and the day its period starts (REB-997).
    rate_card_id: UUID | None = None
    period_start: date | None = None


@dataclass(frozen=True)
class RecapGroup:
    """One `DatiRiepilogo`. The key is `(aliquota_iva, natura)` -- spec 6.1 rule 5."""

    vat_rate: Decimal
    vat_nature: str | None
    legal_reference: str | None
    taxable_amount: Decimal
    vat_amount: Decimal


def line_total(
    *,
    quantity: Decimal,
    unit_price: Decimal,
    discount_percentage: Decimal | None,
    discount_amount: Decimal | None,
) -> Decimal:
    """Spec 6.1 rule 1: `ROUND(quantita x prezzo_unitario - sconto, 2)`.

    The percentage is applied to the exact product and the fixed amount is subtracted
    after it, so the two discounts compose in a defined order instead of depending on
    which one the caller happened to fill in. Rounding happens once, at the end: a
    line's own intermediate product is never rounded, because rounding twice is how a
    total stops matching the sum of what is printed.

    A negative result is returned as-is. A discount is a line (spec 6.1 rule 6), so
    refusing one here would make the ordinary case unrepresentable; the check that a
    whole *invoice* must total more than zero belongs to emission, not to a line.
    """
    gross = quantity * unit_price
    if discount_percentage is not None:
        gross -= gross * discount_percentage / _HUNDRED
    if discount_amount is not None:
        gross -= discount_amount
    return round_money(gross)


def build_recap(lines: Sequence[ComputedLine]) -> tuple[RecapGroup, ...]:
    """One group per `(aliquota_iva, natura)`, ordered by that key so the output is
    deterministic and a re-export is byte-identical.

    Two rules that disagree, both from spec 6.1:

    * rule 2 -- `taxable_amount` is the sum of the group's **already-rounded**
      `total_price` values, not the rounding of the exact sum. The SdI checks
      `ImponibileImporto` against the sum of the group's printed `PrezzoTotale`, and
      rounding the exact sum can differ from that by a cent;
    * rule 3 -- `vat_amount` is `ROUND(imponibile x aliquota / 100, 2)` computed **from
      the group**, not the sum of per-line taxes. The SdI checks `Imposta` against
      that product, and the sum of rounded per-line taxes can differ from it by
      several cents.

    Both rules are unobservable under a regime where every rate is zero, which is why
    they are stated here rather than discovered on the first rejected file.
    """
    grouped: dict[tuple[Decimal, str | None], list[ComputedLine]] = {}
    for line in lines:
        grouped.setdefault((line.vat_rate, line.vat_nature), []).append(line)

    groups: list[RecapGroup] = []
    for (rate, vat_nature), members in sorted(
        grouped.items(), key=lambda item: (item[0][0], item[0][1] or "")
    ):
        taxable_amount = sum((m.total_price for m in members), start=Decimal("0.00"))
        groups.append(
            RecapGroup(
                vat_rate=rate,
                vat_nature=vat_nature,
                # Every member of a group shares a VAT nature, and the regime derives the
                # normative reference from the VAT nature, so the first member's value is
                # the group's value by construction.
                legal_reference=members[0].legal_reference,
                taxable_amount=round_money(taxable_amount),
                vat_amount=round_money(taxable_amount * rate / _HUNDRED),
            )
        )
    return tuple(groups)


def sum_totals(recap: Sequence[RecapGroup]) -> tuple[Decimal, Decimal, Decimal]:
    """`(imponibile, imposta, totale)`, where `totale = imponibile + imposta`.

    The stamp duty is **not** part of the total (spec 6.1 rule 4 and 7.2):
    `DatiBollo/BolloVirtuale` declares that the issuer has settled it virtually, and
    charging it back to the customer would need a line with `Natura N1` -- a feature
    with its own semantics, explicitly out of scope.
    """
    taxable_amount = round_money(sum((g.taxable_amount for g in recap), start=Decimal("0.00")))
    vat_amount = round_money(sum((g.vat_amount for g in recap), start=Decimal("0.00")))
    return taxable_amount, vat_amount, round_money(taxable_amount + vat_amount)
