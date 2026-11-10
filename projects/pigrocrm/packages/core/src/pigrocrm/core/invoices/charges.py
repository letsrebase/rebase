"""What an ordinary-regime sale carries beyond its lines: the withholding and the split
payment, and so the net the customer pays (REB-994).

Pure functions over `Decimal`, like `totals.py`: the lines and the frozen profile in, figures
out. `InvoiceService` stores the result when a document is written. Every rule is a row of the
frame's A5 register (`2026-11-09-space-profiles-modules-and-the-money-picture-design.md`):

* the withholding is the pack's rate on the fee and the expenses refunded at cost (the
  Agenzia's «F24 Ritenute reddito lavoro autonomo» pages, «Aliquote» and «Importi su cui si
  applica la ritenuta»), and it is owed only when the customer is a withholding agent in
  Italy and the issuer is not a flat-rate taxpayer (L. 190/2014, art. 1 c. 67: only the
  ordinary regime of the `it-standard` pack carries a `SalesWithholding`);
* a split payment applies to a sale to a public body (art. 17-ter c. 1 DPR 633/72), is
  excluded for a service whose fee is subject to withholding (c. 1-sexies, added by DL 87/2018
  art. 12), and exists only where there is VAT to split.

The contribution of a pension fund and the INPS surcharge are not here: whether and how VAT
and the withholding reach them is still «to verify» in the register, and a rule that is not
verified is not computed.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from pigrocrm.core.fiscal.pack import PACK_IT_COMPANY, PACK_IT_STANDARD, current_pack
from pigrocrm.core.fiscal.schemas import FiscalSnapshot
from pigrocrm.core.invoices.totals import ComputedLine, RecapGroup, build_recap, sum_totals
from pigrocrm.core.money import round_money

ZERO = Decimal("0.00")
_HUNDRED = Decimal("100")
PUBLIC_BODY = "public_body"


class SalesCustomer(Protocol):
    """What a sale reads of its customer: an `Organization` row at the moment a draft is
    written, the customer's frozen `PartySnapshot` at emission."""

    @property
    def country(self) -> str: ...
    @property
    def customer_kind(self) -> str | None: ...
    @property
    def is_withholding_agent(self) -> bool: ...


@dataclass(frozen=True)
class SalesCharges:
    """The figures a sale stores, from its lines."""

    recap: tuple[RecapGroup, ...]
    taxable_amount: Decimal
    vat_amount: Decimal
    total: Decimal
    withholding_amount: Decimal
    withholding_rate: Decimal | None
    split_payment: bool
    net_receivable: Decimal


def italian(country: str | None) -> bool:
    """Whether a country reads as Italy: an empty one is Italy too, the column's own default."""
    return ((country or "").strip().upper() or "IT") == "IT"


def compute_charges(
    computed: Sequence[ComputedLine], *, tax: FiscalSnapshot, customer: SalesCustomer
) -> SalesCharges:
    """The recap, the totals and the net receivable of a sale, with its withholding and its
    split payment where the rules above call for them."""
    recap = build_recap(computed)
    taxable_amount, vat_amount, total = sum_totals(recap)

    withholding_amount = ZERO
    withholding_rate: Decimal | None = None
    law = current_pack(tax.pack_id).sales_withholding
    if law is not None and customer.is_withholding_agent and italian(customer.country):
        base = round_money(sum((line.total_price for line in computed), start=ZERO))
        amount = round_money(base * law.rate / _HUNDRED)
        if amount > ZERO:
            withholding_amount = amount
            withholding_rate = law.rate

    split_payment = (
        tax.pack_id in (PACK_IT_STANDARD, PACK_IT_COMPANY)
        and customer.customer_kind == PUBLIC_BODY
        and italian(customer.country)
        and withholding_amount == ZERO
        and vat_amount > ZERO
    )
    net_receivable = round_money(
        total - withholding_amount - (vat_amount if split_payment else ZERO)
    )
    return SalesCharges(
        recap=recap,
        taxable_amount=taxable_amount,
        vat_amount=vat_amount,
        total=total,
        withholding_amount=withholding_amount,
        withholding_rate=withholding_rate,
        split_payment=split_payment,
        net_receivable=net_receivable,
    )


__all__ = ["PUBLIC_BODY", "SalesCharges", "SalesCustomer", "compute_charges", "italian"]
