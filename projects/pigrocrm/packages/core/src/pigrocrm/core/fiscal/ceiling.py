"""Ceiling evaluation, the social security surcharge invoice line, and the tagged-figure split
between them -- REB-361, ported against REB-344 §8-9 and REB-352 §2/§6.

Three things live here, all reading or writing invoices through a `Session`, which
is why none of them belong in `pack.py` (pure data, no database access at all):

1. **Ceiling evaluation.** `evaluate_pack` sums the `Invoice.taxable_amount` received
   within a calendar year (REB-344 §8's `cash_received_calendar_year` basis, read per
   settlement and by the settlement's own date, spec 2026-11-09 § 1.1, so instalments
   across 31 December land in the right year) and compares it against each of the pack's own
   ceilings. A social security surcharge line is
   one more VAT-grouped line on the same invoice and is therefore already inside
   `taxable_amount` the moment it exists -- no extra step, exactly as REB-352 §2's "no
   code change is needed for the ceiling side" says.

2. **The social security surcharge invoice line.** `social_security_surcharge_line_for_contract` is
the "new step" REB-344
   §9 describes for whatever assembles a contract-linked invoice's lines: given the
   contract's own election (`contracts.applies_social_charge`) and the fee subtotal
   already assembled, it returns the ordinary `InvoiceLineIn` to append, or `None`.
   No new schema: the line goes through the exact same `compute_lines`/
   `RegimeStrategy.resolve_line_vat` path every other line does, so its `vat_nature`/
   `legal_reference` are whatever the regime already computes for a zero-rate
   domestic or foreign line -- identical to a fee line's own. What marks it as *this*
   charge is `description`, the one column the regime never touches and never
   overwrites (see `pack.py::StatutoryCharge.description_line`'s own docstring for
   why that, and not `vat_nature`/`legal_reference`, is the tag).

3. **The tagged, subtractable figure.** REB-352 §2 names a real tension: the social security
   surcharge amount counts toward a ceiling's revenue in full but is not taxable income, and
   `estimate_income` has exactly one `revenue` parameter. §6 resolves it: the amount is
   carried as its own tagged figure at the point revenue is summed, not a second
   parameter on `estimate_income` -- so its signature stays stable for every other
   caller. `collected_charge_lines_for_calendar_year` is that tagged figure for the cash
   basis (identified by `description_line`, the same tag construction wrote), read off the
   same settlements as the ceiling's revenue; `fiscal/basis.py` takes it off, so the
   coefficiente base and the ceiling's own revenue sum can diverge by exactly the social
   security surcharge amount, as designed (REB-1071).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy import ColumnElement, func, select
from sqlalchemy.orm import Session

from pigrocrm.core.due_items.models import DueItem, DueItemSettlement
from pigrocrm.core.due_items.sales import join_sales_item
from pigrocrm.core.fiscal.pack import (
    SOCIAL_SECURITY_SURCHARGE_INPS_CHARGE_ID,
    Ceiling,
    FiscalPack,
    known_charge_markers,
)
from pigrocrm.core.invoices.models import Invoice, InvoiceLine
from pigrocrm.core.invoices.schemas import InvoiceLineIn
from pigrocrm.core.money import ZERO_MONEY, round_money


# The one definition of "an invoice that is revenue", duplicated rather than imported
# from `analytics/filters.py`'s `revenue_filter`: `analytics` already imports `fiscal`'s
# `FiscalProfileService`, so reaching back into it from here is a package cycle waiting to
# happen. Three comparisons cost nothing to rebuild, and `revenue_filter` says the same
# about not sharing a module-level tuple.
def _legal_entity_filter(legal_entity_id: UUID | None) -> tuple[ColumnElement[bool], ...]:
    """One legal entity's register, or every one (REB-630, spec 2026-10-03 §1.9). A ceiling
    exists on one pack and a pack is one legal entity's, so every caller on the service side
    passes an id; `None` is kept for the arithmetic's own tests, which built their
    corpus before the column existed and still describe one legal entity."""
    return () if legal_entity_id is None else (Invoice.legal_entity_id == legal_entity_id,)


def _collected_in_year_filter(
    year: int, legal_entity_id: UUID | None = None
) -> tuple[ColumnElement[bool], ...]:
    """`cash_received_calendar_year` (REB-344 §8): a payment made within `year`, by the date
    of the settlement itself (spec 2026-11-09, due items, § 1.1). An invoice paid in
    instalments across 31 December is attributed to each year for what arrived in it, and a
    reversed settlement is not a payment."""
    first, next_year = date(year, 1, 1), date(year + 1, 1, 1)
    return (
        Invoice.type == "invoice",
        Invoice.status == "issued",
        Invoice.deleted_at.is_(None),
        DueItemSettlement.reversed_at.is_(None),
        # Half-open, so the settlement date's index stays usable.
        DueItemSettlement.settled_on >= first,
        DueItemSettlement.settled_on < next_year,
        *_legal_entity_filter(legal_entity_id),
    )


def paid_revenue_for_calendar_year(
    session: Session,
    year: int,
    legal_entity_id: UUID | None = None,
    *,
    issued_in_year: bool = False,
) -> Decimal:
    """The taxable amount received within `year` -- the ceiling's own basis, in full, social
    security surcharge included (REB-352 §2) -- of one legal entity when named (REB-630): a
    ceiling is a fact about one legal entity's receipts, never the space's.

    Each settlement counts for its share of the invoice's `taxable_amount`, its amount over
    the item's (`taxable_amount * settlement / total`): a payment in full is exactly the
    taxable amount, and one in instalments adds up to it. The shares are summed before they
    are rounded, so the instalments never drift by a cent.

    `issued_in_year` keeps to the invoices of the year's own register (`Invoice.year`): what of
    the year's invoicing was collected in the year, which is what the cash basis says is left
    for later (`fiscal/basis.py`, REB-1071)."""
    share = Invoice.taxable_amount * DueItemSettlement.amount / DueItem.amount
    stmt = (
        join_sales_item(select(func.coalesce(func.sum(share), 0)))
        .join(DueItemSettlement, DueItemSettlement.due_item_id == DueItem.id)
        .where(*_collected_in_year_filter(year, legal_entity_id))
    )
    if issued_in_year:
        stmt = stmt.where(Invoice.year == year)
    return round_money(Decimal(session.execute(stmt).scalar_one()))


def collected_charge_lines_for_calendar_year(
    session: Session, year: int, markers: Iterable[str], legal_entity_id: UUID | None = None
) -> Decimal:
    """The part of what was collected within `year` that is the lines carrying one of the
    charge `markers`, each settlement counting for its share of the invoice's charge lines as
    `paid_revenue_for_calendar_year` counts it for the taxable amount: the two figures are
    read off the same settlements, so what a pack tags as not counting toward the coefficient
    base is taken off exactly what was collected, and by nothing else."""
    markers = tuple(markers)
    if not markers:
        return ZERO_MONEY
    charges = (
        select(func.coalesce(func.sum(InvoiceLine.total_price), 0))
        .where(InvoiceLine.invoice_id == Invoice.id, charge_line_clause(markers))
        .correlate(Invoice)
        .scalar_subquery()
    )
    share = charges * DueItemSettlement.amount / DueItem.amount
    return round_money(
        Decimal(
            session.execute(
                join_sales_item(select(func.coalesce(func.sum(share), 0)))
                .join(DueItemSettlement, DueItemSettlement.due_item_id == DueItem.id)
                .where(*_collected_in_year_filter(year, legal_entity_id))
            ).scalar_one()
        )
    )


@dataclass(frozen=True)
class CeilingStatus:
    """One ceiling, evaluated against a calendar year's paid revenue."""

    ceiling: Ceiling
    revenue: Decimal
    remaining: Decimal
    exceeded: bool
    alert_level: str | None


def evaluate_ceiling(ceiling: Ceiling, revenue: Decimal) -> CeilingStatus:
    """`ceiling` against an already-summed `revenue` -- the pure half, so a caller
    that already has the figure (the "would this fit?" simulator, REB-352 §1.4)
    never re-reads the database to ask a question about a synthetic addition."""
    alert_level: str | None = None
    for level in ceiling.levels_alert:
        if revenue >= ceiling.threshold * level.report:
            alert_level = level.label
    return CeilingStatus(
        ceiling=ceiling,
        revenue=revenue,
        remaining=ceiling.threshold - revenue,
        exceeded=revenue >= ceiling.threshold,
        alert_level=alert_level,
    )


def evaluate_pack(
    pack: FiscalPack, session: Session, year: int, legal_entity_id: UUID | None = None
) -> list[CeilingStatus]:
    """Every ceiling `pack` declares, evaluated against `year`'s paid revenue -- one
    query shared by every ceiling, since `IT_FLAT_RATE_PACK`'s own two share the same
    `all_clients` perimeter and `cash_received_calendar_year` basis. `legal_entity_id` is
    the legal entity whose profile points at `pack` (REB-630)."""
    revenue = paid_revenue_for_calendar_year(session, year, legal_entity_id)
    return [evaluate_ceiling(ceiling, revenue) for ceiling in pack.ceilings]


def social_security_surcharge_line_for_contract(
    pack: FiscalPack, *, applies_social_charge: bool, fee_subtotal: Decimal
) -> InvoiceLineIn | None:
    """The social security surcharge line to append after a contract-linked invoice's fee lines, or
    `None` when the contract has not elected it (`contracts.applies_social_charge`,
    REB-344 §3/§9). `fee_subtotal` is the fee lines already assembled -- the base the
    charge's own rate applies to, which never widens beyond the fee alone because
    PigroCRM never recharges stamp duty to the client (REB-344 §9's own note; mastro's
    `chargeSlotOrder` would widen it the day that ever changes)."""
    if not applies_social_charge:
        return None
    try:
        charge = pack.charge(SOCIAL_SECURITY_SURCHARGE_INPS_CHARGE_ID)
    except KeyError:
        # A pack with no such charge (`non-it`, REB-619): the contract's election names
        # an Italian charge a foreign legal entity does not owe, so there is no line to add.
        return None
    amount = round_money(fee_subtotal * charge.rate)
    return InvoiceLineIn(
        description=charge.description_line,
        quantity=Decimal("1.000000"),
        unit_price=amount,
        vat_rate=Decimal("0.00"),
    )


# A statutory charge's line is recognised by its `description` alone. No structural
# signature can replace that without a new column: the line is an ordinary zero-VAT line
# whose `vat_nature`/`legal_reference` are the regime's single pair, shared by every
# zero-rate fee line, with a quantity of one and no unit like any flat fee. These two
# helpers are the only place that reads the text marker, so the day `invoice_lines`
# gains a charge column both change together and nothing else does.
def charge_line_clause(markers: Iterable[str]) -> ColumnElement[bool]:
    """SQL predicate: `InvoiceLine` rows carrying one of the charge `markers`."""
    return InvoiceLine.description.in_(tuple(markers))


def is_statutory_charge_line(line: InvoiceLine) -> bool:
    """Whether `line` is the line of a statutory charge any shipped pack declares,
    whatever pack the legal entity is on today: an invoice keeps the lines it was issued
    with after the profile moves to another pack."""
    return line.description in known_charge_markers()


__all__ = [
    "CeilingStatus",
    "charge_line_clause",
    "evaluate_ceiling",
    "evaluate_pack",
    "collected_charge_lines_for_calendar_year",
    "is_statutory_charge_line",
    "paid_revenue_for_calendar_year",
    "social_security_surcharge_line_for_contract",
]
