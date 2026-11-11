"""The F24 code of the line a due item becomes (REB-1076, taxes design § 2).

The codes are law and live in the pack (`PaymentRulesLaw.f24_codes`, `fiscal/payment_data.py`),
keyed by the calendar year the payment falls in. This module only says which of the pack's lines
an item is, and answers the pack's code for it. A line the pack carries no code for, whatever the
reason, is answered with no code at all, which the surfaces read as «codice da verificare»: the
IVA period codes (60xx), the withholding code (1040), the payroll codes and the credits' own
codes are not in the pack today and none is invented here.

What an item is, is read from the item alone. The income taxes' balances and advances carry a slot
as their `source_id` (`tax_items.slot_id`, a uuid5 of the legal entity, the tax, the tax year and
the part), so the tax year and the part are found again by recomputing the few slots that can
match, never by reading the item's description; the chamber fee is the item whose tax is
`chamber_fee`.
"""

import datetime as dt
from dataclasses import dataclass

from pigrocrm.core.db import today_local
from pigrocrm.core.due_items.models import (
    TAX_ADVANCE_SOURCE_KIND,
    TAX_BALANCE_SOURCE_KIND,
    DueItem,
)
from pigrocrm.core.fiscal.advance_schemas import TaxItemPart
from pigrocrm.core.fiscal.advances import INCOME_TAXES
from pigrocrm.core.fiscal.pack import FiscalPack
from pigrocrm.core.fiscal.tax_items import slot_id

# The parts of a tax year each source kind writes, in the words of `TaxItemPart`.
_PARTS: dict[str, tuple[TaxItemPart, ...]] = {
    TAX_BALANCE_SOURCE_KIND: ("balance",),
    TAX_ADVANCE_SOURCE_KIND: ("first_advance", "second_advance"),
}
# How far from the item's date a tax year can be: the balance of year Y falls in Y + 1, an
# advance in Y, and a plan the year after that.
_YEARS_BEFORE = 2
_YEARS_AFTER = 1


@dataclass(frozen=True)
class LineCode:
    """What an item is as an F24 line and the pack's code for it, all absent together when the
    pack carries none. `reference_year` is the `anno di riferimento` when it is known."""

    line: str | None = None
    code: str | None = None
    kind: str | None = None
    label: str | None = None
    reference_year: int | None = None


def _income_tax_slot(item: DueItem) -> tuple[str, int] | None:
    """`(F24 line, tax year)` of a tax balance or advance, found from its slot."""
    parts = _PARTS.get(item.source_kind)
    tax = item.tax
    if parts is None or item.source_id is None or tax is None or tax not in INCOME_TAXES:
        return None
    anchor = (item.due_date or item.own_date or today_local()).year
    for year in range(anchor - _YEARS_BEFORE, anchor + _YEARS_AFTER + 1):
        for part in parts:
            slot = slot_id(item.source_kind, item.legal_entity_id, tax, year, part)
            if slot == item.source_id:
                return f"{tax}_{part}", year
    return None


def line_of(item: DueItem) -> tuple[str, int | None] | None:
    """The pack's line an item is and its reference year, or `None` for an item the pack has no
    line for."""
    slot = _income_tax_slot(item)
    if slot is not None:
        return slot
    if item.tax == "chamber_fee":
        # The fee of the year it is paid in (Agenzia, the code's own resolution).
        due = item.due_date or item.own_date
        return "chamber_fee", None if due is None else due.year
    return None


def code_for(item: DueItem, pack: FiscalPack | None, payment_date: dt.date) -> LineCode:
    """The line of `item` with the pack's code, for a payment on `payment_date`."""
    found = line_of(item)
    if found is None:
        return LineCode()
    line, year = found
    rules = pack.payment_rules_for(payment_date.year) if pack is not None else None
    if rules is None:
        return LineCode(line=line, reference_year=year)
    try:
        code = rules.f24_code(line)  # type: ignore[arg-type]
    except KeyError:
        return LineCode(line=line, reference_year=year)
    return LineCode(
        line=line,
        code=code.code,
        kind=code.kind,
        label=code.description,
        reference_year=year,
    )


__all__ = ["LineCode", "code_for", "line_of"]
