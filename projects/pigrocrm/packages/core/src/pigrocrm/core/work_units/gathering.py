"""The unbilled, worked days of a contract, priced by the rate card in force on each date.

What `invoicing.py` turns into the lines of a draft and `due_items/forecast.py` turns into a
forecast of money in: the same gather, so the amount forecast is the one the draft will carry.
Reads only; it imports no invoice service, so both callers can import it.
"""

from collections import defaultdict
from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy.orm import Session

from pigrocrm.core.contracts.models import RATE_CARD_DAY_HOUR_TYPES, RateCard
from pigrocrm.core.contracts.repository import RateCardRepository
from pigrocrm.core.invoices.schemas import InvoiceLineIn
from pigrocrm.core.labels import RATE_CARD_UNIT, label
from pigrocrm.core.timetracking.locks import period_label
from pigrocrm.core.work_units.models import WorkUnit
from pigrocrm.core.work_units.repository import WorkUnitRepository

# The groups a contract's ready days fall into: `(rate_card, year, month)`, in the order
# the lines come out.
GroupKey = tuple[UUID, int, int]


def gather(
    session: Session, contract_id: UUID, date_from: date | None, date_to: date | None
) -> tuple[dict[GroupKey, list[WorkUnit]], list[WorkUnit], dict[UUID, RateCard]]:
    """`(groups, uncovered, cards)`: the ready days grouped by the rate card in force on
    their own date and by month, the days no card covers, and the contract's cards by id."""
    work_units = WorkUnitRepository(session).unbilled_for_contract(
        contract_id, date_from=date_from, date_to=date_to
    )
    cards = RateCardRepository(session).list_for_contract(contract_id)

    groups: dict[GroupKey, list[WorkUnit]] = defaultdict(list)
    uncovered: list[WorkUnit] = []
    for work_unit in work_units:
        card = next(
            (
                c
                for c in cards
                if c.type in RATE_CARD_DAY_HOUR_TYPES
                and c.valid_from <= work_unit.date
                and (c.valid_to is None or work_unit.date <= c.valid_to)
            ),
            None,
        )
        if card is None:
            uncovered.append(work_unit)
            continue
        groups[(card.id, work_unit.date.year, work_unit.date.month)].append(work_unit)
    return groups, uncovered, {card.id: card for card in cards}


def ordered(groups: dict[GroupKey, list[WorkUnit]]) -> list[tuple[GroupKey, list[WorkUnit]]]:
    return sorted(groups.items(), key=lambda item: (item[0][1], item[0][2], str(item[0][0])))


def quantity_of(members: list[WorkUnit]) -> Decimal:
    return sum((m.quantity for m in members), start=Decimal("0"))


def line_in(card: RateCard, year: int, month: int, quantity: Decimal) -> InvoiceLineIn:
    """The line a group of days becomes. It carries the card and the first day of the month it
    bills (REB-997), so issuing the invoice drops the forecast of that period."""
    unit = label(RATE_CARD_UNIT, card.unit)
    return InvoiceLineIn(
        description=f"Attività {period_label(year, month)} - {quantity} {unit}",
        quantity=quantity,
        unit_of_measure=unit,
        unit_price=card.amount,
        # `vat_rate` deliberately omitted: `None` means "the regime's
        # answer" -- `vat_nature`/`legal_reference` are not on this schema
        # at all (see `invoicing.py`).
        rate_card_id=card.id,
        period_start=date(year, month, 1),
    )
