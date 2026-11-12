"""The F24 code of the line a due item becomes (REB-1076, taxes design § 2).

The codes are law and live in the pack (`PaymentRulesLaw.f24_codes`, `fiscal/payment_data.py`),
keyed by the calendar year the payment falls in. This module only says which of the pack's lines
an item is, and answers the pack's code for it. A line the pack carries no code for, whatever the
reason, is answered with no code at all, which the surfaces read as «codice da verificare»: the
INAIL premium, the fourth quarter's IVA code 6034 and a credit's own code are not in the pack and
none is invented here.

What an item is, is read from the item, never from its description. The income taxes' balances and
advances carry a slot as their `source_id` (`tax_items.slot_id`, a uuid5 of the legal entity, the
tax, the tax year and the part), so the tax year and the part are found again by recomputing the
few slots that can match; the chamber fee is the item whose tax is `chamber_fee`. An IVA payment is
the settlement its `source_id` names (REB-1100): its frequency and number say which of the Agenzia's
period codes it is (6001 to 6012 by month, 6031 to 6033 by quarter, the fourth quarter with the
annual settlement 6099), and its year is the `anno di riferimento`. A payment of withholding or of
payroll contributions falls on the 16th of the month after the one it pays (`PayrollLaw`, the
calendar's `withholding_payment`), so the month the line is for is the one before its date: the
`mese rif.` of 1001 and 1040 (`MM`) and the INPS section's «periodo da» of DM10 (`MM/AAAA`).
"""

import datetime as dt
from dataclasses import dataclass

from sqlalchemy.orm import Session

from pigrocrm.core.db import today_local
from pigrocrm.core.due_items.models import (
    PAYROLL_SOURCE_KIND,
    TAX_ADVANCE_SOURCE_KIND,
    TAX_BALANCE_SOURCE_KIND,
    DueItem,
)
from pigrocrm.core.fiscal.advance_schemas import TaxItemPart
from pigrocrm.core.fiscal.advances import INCOME_TAXES
from pigrocrm.core.fiscal.models import IvaSettlement
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
    pack carries none. `reference_year` is the `anno di riferimento` when it is known;
    `reference_period` is the month a withholding line is for (`MM`) or a payroll contribution's
    «periodo da» (`MM/AAAA`)."""

    line: str | None = None
    code: str | None = None
    kind: str | None = None
    label: str | None = None
    reference_year: int | None = None
    reference_period: str | None = None


@dataclass(frozen=True)
class Found:
    """The pack's line an item is: its name, the year of the period it pays, the period number
    the line's code depends on (an IVA month or quarter) and the period column's text."""

    line: str
    reference_year: int | None = None
    period: int | None = None
    reference_period: str | None = None


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


def _month_before(day: dt.date) -> dt.date:
    """The first day of the month before `day`'s: a payment of the 16th pays that month."""
    first = day.replace(day=1)
    return (first - dt.timedelta(days=1)).replace(day=1)


def _iva_line(item: DueItem, session: Session | None) -> Found | None:
    if item.source_id is None or session is None:
        return None
    settlement = session.get(IvaSettlement, item.source_id)
    if settlement is None:
        return None
    number, year = settlement.period_number, settlement.year
    if settlement.frequency == "monthly":
        return Found("iva_monthly", year, period=number)
    # The fourth quarter of a quarterly payer is paid with the annual settlement.
    if number == 4:  # noqa: PLR2004 -- the fourth quarter
        return Found("iva_annual", year)
    return Found("iva_quarterly", year, period=number)


def _paid_for_month(item: DueItem) -> dt.date | None:
    due = item.due_date or item.own_date
    return None if due is None else _month_before(due)


def _withholding_or_payroll_line(item: DueItem) -> Found | None:
    month = _paid_for_month(item)
    if month is None:
        return None
    if item.source_kind == "withholding":
        return Found("withholding_self_employed", month.year, reference_period=f"{month:%m}")
    if item.source_kind == PAYROLL_SOURCE_KIND and item.tax == "payroll_withholding":
        return Found("withholding_employees", month.year, reference_period=f"{month:%m}")
    if item.source_kind == PAYROLL_SOURCE_KIND and item.tax == "payroll_inps":
        # The INPS section has no `anno di riferimento`: the month and year are the «periodo da».
        return Found("inps_payroll", None, reference_period=f"{month:%m/%Y}")
    return None


def line_of(item: DueItem, session: Session | None = None) -> Found | None:
    """The pack's line an item is, with its reference year and period, or `None` for an item the
    pack has no line for. The IVA lines need the settlement the item names, so they are found
    only when a `session` is given."""
    slot = _income_tax_slot(item)
    if slot is not None:
        return Found(slot[0], slot[1])
    if item.tax == "chamber_fee":
        # The fee of the year it is paid in (Agenzia, the code's own resolution).
        due = item.due_date or item.own_date
        return Found("chamber_fee", None if due is None else due.year)
    if item.source_kind == "iva_settlement":
        return _iva_line(item, session)
    return _withholding_or_payroll_line(item)


def code_for(
    item: DueItem, pack: FiscalPack | None, payment_date: dt.date, session: Session | None = None
) -> LineCode:
    """The line of `item` with the pack's code, for a payment on `payment_date`."""
    found = line_of(item, session)
    if found is None:
        return LineCode()
    bare = LineCode(
        line=found.line,
        reference_year=found.reference_year,
        reference_period=found.reference_period,
    )
    rules = pack.payment_rules_for(payment_date.year) if pack is not None else None
    if rules is None:
        return bare
    try:
        code = rules.f24_code(found.line, found.period)  # type: ignore[arg-type]
    except KeyError:
        return bare
    return LineCode(
        line=found.line,
        code=code.code,
        kind=code.kind,
        label=code.description,
        reference_year=found.reference_year,
        reference_period=found.reference_period,
    )


__all__ = ["Found", "LineCode", "code_for", "line_of"]
