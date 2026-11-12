"""The INPS contributions a legal entity paid in a calendar year (REB-1096, taxes design § 1).

The law deducts the contributions **paid** in the tax period, whatever year they are for: art. 10
c. 1 lett. e) TUIR says «i contributi previdenziali ed assistenziali versati in ottemperanza a
disposizioni di legge» and, for the flat-rate regime, art. 1 c. 64 L. 190/2014 «i contributi
previdenziali versati in ottemperanza a disposizioni di legge [...] si deducono dal reddito
determinato ai sensi del presente comma; l'eventuale eccedenza è deducibile dal reddito complessivo
ai sensi dell'articolo 10». A contribution due and not paid is not deducted, and the balance of the
year before and the advances of this one are (Agenzia, Redditi PF 2026 fascicolo 1, rigo RP21:
«versati»; «Quadro LM - Dichiarazione precompilata»: «tutti i contributi previdenziali ed
assistenziali versati nell'anno»). No primary text says in one sentence «paid in the year, even if
for an earlier one»; it follows from «versati» (REB-1086, Q8).

The payments are the live settlements, by their own date, of the legal entity's INPS tax due items
(`fiscal/tax_items.py`: the balance, the advances and the instalments of a plan, all `tax='inps'`).
A reversed settlement is not a payment. `None` is the honest answer when no INPS payment is
recorded in the year: the caller then says it stands in the computed contributions, since a
person's payments may simply not have been entered.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from pigrocrm.core.due_items.models import (
    TAX_ADVANCE_SOURCE_KIND,
    TAX_BALANCE_SOURCE_KIND,
    TAX_INSTALMENT_SOURCE_KIND,
    DueItem,
    DueItemSettlement,
)
from pigrocrm.core.money import round_money

_INPS_SOURCE_KINDS = (
    TAX_BALANCE_SOURCE_KIND,
    TAX_ADVANCE_SOURCE_KIND,
    TAX_INSTALMENT_SOURCE_KIND,
)


def inps_paid_in_year(session: Session, legal_entity_id: UUID, year: int) -> Decimal | None:
    """The INPS contributions paid in calendar `year`, in euro and cents, `None` when no INPS
    payment is recorded in it."""
    total, count = session.execute(
        select(func.coalesce(func.sum(DueItemSettlement.amount), 0), func.count())
        .select_from(DueItem)
        .join(DueItemSettlement, DueItemSettlement.due_item_id == DueItem.id)
        .where(
            DueItem.legal_entity_id == legal_entity_id,
            DueItem.direction == "out",
            DueItem.tax == "inps",
            DueItem.source_kind.in_(_INPS_SOURCE_KINDS),
            DueItemSettlement.reversed_at.is_(None),
            DueItemSettlement.settled_on >= date(year, 1, 1),
            DueItemSettlement.settled_on <= date(year, 12, 31),
        )
    ).one()
    return round_money(Decimal(total)) if count else None


__all__ = ["inps_paid_in_year"]
