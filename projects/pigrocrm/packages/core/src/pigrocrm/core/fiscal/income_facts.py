"""What the database says for the variations of an srl's income (REB-1089, taxes design § 1): the
purchases with the way the law treats them, and the IRAP paid in the year.

The estimate itself is pure (`fiscal/company_estimate.py`) and reads `IncomeVariations`
(`fiscal/income_variations.py`); this is the reader the analytics service calls before it, as it
calls the withholdings' reader. The pure parts are `fiscal/cost_variations.py` (the limited costs
and the assets) and `fiscal/irap_deduction.py` (the IRAP deducted from IRES).

**Purchases.** The registered purchases of the legal entity dated in the financial year, and the
assets of earlier years that still depreciate (a purchase flagged `asset_group`, dated before the
year, with its cost not yet fully deducted: the computation decides that, this reads them all).
The kind of a purchase is its own, else its category's.

**IRAP paid.** The settlements, not reversed, of the legal entity's IRAP due items in the financial
year (the balance of the year before, the advances of the year, their instalments). Which of them
paid the year's own advances is known from the slot a tax due item stands for
(`fiscal/tax_items.py`), since the item itself does not carry its tax year.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from pigrocrm.core.due_items.models import (
    TAX_ADVANCE_SOURCE_KIND,
    TAX_BALANCE_SOURCE_KIND,
    TAX_INSTALMENT_SOURCE_KIND,
    DueItem,
    DueItemSettlement,
)
from pigrocrm.core.fiscal.basis import financial_year
from pigrocrm.core.fiscal.basis_schemas import YearBasis
from pigrocrm.core.fiscal.cost_variations import PurchaseFact, compute_cost_variations
from pigrocrm.core.fiscal.income_variations import IncomeVariations, IrapPaid
from pigrocrm.core.fiscal.models import MAX_PLAN_INSTALMENTS
from pigrocrm.core.fiscal.pack import FiscalPack
from pigrocrm.core.fiscal.tax_items import instalment_slot_id, slot_id
from pigrocrm.core.legal_entities.models import LegalEntity
from pigrocrm.core.money import ZERO_MONEY, round_money
from pigrocrm.core.purchases.models import Purchase
from pigrocrm.core.timetracking.models import CostCategory

_IRAP = "irap"
_IRAP_SOURCE_KINDS = (TAX_BALANCE_SOURCE_KIND, TAX_ADVANCE_SOURCE_KIND, TAX_INSTALMENT_SOURCE_KIND)


def read_income_variations(
    session: Session, legal_entity: LegalEntity, basis: YearBasis, pack: FiscalPack
) -> IncomeVariations | None:
    """The variations for `basis`'s year, `None` when the pack carries none for it."""
    law = pack.ires_variations_for(basis.year)
    if law is None:
        return None
    facts = purchase_facts(session, legal_entity, basis)
    return IncomeVariations(
        law=law,
        costs=compute_cost_variations(law, facts, revenue=basis.revenue),
        irap_paid=irap_paid(session, legal_entity.id, basis),
    )


def closing_year(legal_entity: LegalEntity, day: dt.date) -> int:
    """The year the financial year that holds `day` closes in."""
    return day.year if day <= financial_year(legal_entity, day.year)[1] else day.year + 1


def purchase_facts(
    session: Session, legal_entity: LegalEntity, basis: YearBasis
) -> list[PurchaseFact]:
    """The registered purchases of the financial year, and the assets of earlier ones, as facts.
    The conditions of a registered purchase are the basis's own (`fiscal/basis.py`)."""
    rows = session.execute(
        select(Purchase, CostCategory)
        .join(CostCategory, CostCategory.id == Purchase.category_id)
        .where(
            Purchase.deleted_at.is_(None),
            Purchase.status == "registered",
            Purchase.legal_entity_id == legal_entity.id,
            Purchase.date <= basis.period_to,
            or_(
                Purchase.date >= basis.period_from,
                Purchase.asset_group.is_not(None),
            ),
        )
        .order_by(Purchase.date, Purchase.id)
    ).all()
    return [
        PurchaseFact(
            id=purchase.id,
            description=purchase.description,
            date=purchase.date,
            cost=purchase.cost_amount,
            kind=purchase.deductibility_kind or category.deductibility_kind,
            asset_group=purchase.asset_group,
            category_code=category.code,
            category_name=category.name,
            years_since=basis.year - closing_year(legal_entity, purchase.date),
        )
        for purchase, category in rows
    ]


def irap_paid(session: Session, legal_entity_id: UUID, basis: YearBasis) -> IrapPaid | None:
    """The IRAP the legal entity paid in the financial year, `None` when no payment is registered.
    `advances` is what paid the advances of the tax year itself, read from the slots of its
    advance items and of their instalments."""
    advance_slots = {
        slot_id(TAX_ADVANCE_SOURCE_KIND, legal_entity_id, _IRAP, basis.year, part)
        for part in ("first_advance", "second_advance")
    } | {
        instalment_slot_id(legal_entity_id, _IRAP, basis.year, part, number)
        for part in ("first_advance", "second_advance")
        for number in range(1, MAX_PLAN_INSTALMENTS + 1)
    }
    rows = session.execute(
        select(DueItem.source_id, func.coalesce(func.sum(DueItemSettlement.amount), 0))
        .join(DueItemSettlement, DueItemSettlement.due_item_id == DueItem.id)
        .where(
            DueItem.legal_entity_id == legal_entity_id,
            DueItem.tax == _IRAP,
            DueItem.direction == "out",
            DueItem.source_kind.in_(_IRAP_SOURCE_KINDS),
            DueItemSettlement.reversed_at.is_(None),
            DueItemSettlement.settled_on >= basis.period_from,
            DueItemSettlement.settled_on <= basis.period_to,
        )
        .group_by(DueItem.source_id)
    ).all()
    total = ZERO_MONEY
    advances = ZERO_MONEY
    for source_id, amount in rows:
        total += Decimal(amount)
        if source_id in advance_slots:
            advances += Decimal(amount)
    if total <= 0:
        return None
    return IrapPaid(total=round_money(total), advances=round_money(advances))


__all__ = [
    "IncomeVariations",
    "closing_year",
    "irap_paid",
    "purchase_facts",
    "read_income_variations",
]
