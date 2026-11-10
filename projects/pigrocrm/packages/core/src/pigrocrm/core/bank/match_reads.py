"""Reading the matches of movements, and the facts about a due item that ranking and the panel
share: who it is owed to or by, the number on its document and what is still owed on it (spec
2026-11-09, § 3; REB-1042). Reads only; `MovementMatchService` is the writer."""

import datetime as dt
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from pigrocrm.core.bank.match_rank import invoice_number
from pigrocrm.core.bank.match_schemas import MatchItemRead, MovementMatchRead
from pigrocrm.core.bank.models import MovementMatch, MovementMatchItem
from pigrocrm.core.due_items.models import DueItem, DueItemSettlement
from pigrocrm.core.due_items.sales import remaining_amount
from pigrocrm.core.invoices.models import Invoice
from pigrocrm.core.organizations.models import Organization
from pigrocrm.core.purchases.models import Purchase
from pigrocrm.core.staff.models import StaffMember

SALES_INVOICE = "sales_invoice"
PURCHASE = "purchase"
ZERO = Decimal("0.00")


@dataclass
class ItemFacts:
    """A due item with what a match needs to know about it. `remaining` is signed like the item
    (zero once settled); `unevidenced` are the magnitudes of its live payments no bank movement
    proves, the ones a movement may adopt."""

    item: DueItem
    remaining: Decimal
    counterparty_name: str | None = None
    document_number: str | None = None
    unevidenced: list[Decimal] = field(default_factory=list)

    @property
    def remaining_magnitude(self) -> Decimal:
        return abs(self.remaining)


def facts_for_items(
    session: Session, rows: Sequence[tuple[DueItem, Decimal]]
) -> dict[UUID, ItemFacts]:
    """The facts of each `(item, remaining)` row, by item id, in four reads however many rows."""
    facts = {item.id: ItemFacts(item, remaining) for item, remaining in rows}
    if not facts:
        return facts
    items = [fact.item for fact in facts.values()]

    organization_ids = {i.organization_id for i in items if i.organization_id is not None}
    names: dict[UUID, str] = {}
    if organization_ids:
        names = dict(
            session.execute(
                select(Organization.id, Organization.legal_name).where(
                    Organization.id.in_(organization_ids)
                )
            ).all()
        )
    staff_ids = {i.staff_member_id for i in items if i.staff_member_id is not None}
    staff: dict[UUID, str] = {}
    if staff_ids:
        staff = dict(
            session.execute(
                select(StaffMember.id, StaffMember.name).where(StaffMember.id.in_(staff_ids))
            ).all()
        )
    invoice_ids = {i.source_id for i in items if i.source_kind == SALES_INVOICE and i.source_id}
    invoices: dict[UUID, str] = {}
    if invoice_ids:
        invoices = {
            invoice_id: invoice_number(year, number)
            for invoice_id, year, number in session.execute(
                select(Invoice.id, Invoice.year, Invoice.number).where(
                    Invoice.id.in_(invoice_ids),
                    Invoice.year.is_not(None),
                    Invoice.number.is_not(None),
                )
            )
            if year is not None and number is not None
        }
    purchase_ids = {i.source_id for i in items if i.source_kind == PURCHASE and i.source_id}
    purchases: dict[UUID, str] = {}
    if purchase_ids:
        purchases = {
            purchase_id: number
            for purchase_id, number in session.execute(
                select(Purchase.id, Purchase.number).where(
                    Purchase.id.in_(purchase_ids), Purchase.number.is_not(None)
                )
            )
            if number
        }
    for fact in facts.values():
        item = fact.item
        if item.organization_id is not None:
            fact.counterparty_name = names.get(item.organization_id)
        elif item.staff_member_id is not None:
            fact.counterparty_name = staff.get(item.staff_member_id)
        elif item.tax is not None:
            fact.counterparty_name = item.tax
        if item.source_id is not None:
            if item.source_kind == SALES_INVOICE:
                fact.document_number = invoices.get(item.source_id)
            elif item.source_kind == PURCHASE:
                fact.document_number = purchases.get(item.source_id)
    for item_id, amount in session.execute(
        select(DueItemSettlement.due_item_id, DueItemSettlement.amount).where(
            DueItemSettlement.due_item_id.in_(list(facts)),
            DueItemSettlement.reversed_at.is_(None),
            DueItemSettlement.bank_movement_id.is_(None),
        )
    ):
        facts[item_id].unevidenced.append(abs(amount))
    return facts


def facts_for_ids(session: Session, item_ids: Iterable[UUID]) -> dict[UUID, ItemFacts]:
    ids = list(item_ids)
    if not ids:
        return {}
    rows = [
        (item, Decimal(remaining))
        for item, remaining in session.execute(
            select(DueItem, remaining_amount()).where(DueItem.id.in_(ids)),
            execution_options={"populate_existing": True},
        )
    ]
    return facts_for_items(session, rows)


def item_read(fact: ItemFacts, allocated: Decimal, adopts: bool) -> MatchItemRead:
    item = fact.item
    return MatchItemRead(
        due_item_id=item.id,
        direction=item.direction,  # type: ignore[arg-type]
        source_kind=item.source_kind,
        source_id=item.source_id,
        organization_id=item.organization_id,
        counterparty_name=fact.counterparty_name,
        description=item.description,
        document_number=fact.document_number,
        due_date=item.due_date,
        amount=abs(item.amount),
        remaining=fact.remaining_magnitude if item.state != "dropped" else ZERO,
        allocated=allocated,
        adopts_payment=adopts,
    )


def _is_current(
    match: MovementMatch, rows: Sequence[MovementMatchItem], facts: dict[UUID, ItemFacts]
) -> bool:
    """A proposal still fits when each item is open for the part it would pay, or holds the
    payment by hand it would adopt. A confirmed or closed match is a record, not a proposal."""
    if match.state != "proposed":
        return False
    for row in rows:
        fact = facts.get(row.due_item_id)
        if fact is None:
            return False
        if fact.item.state == "expected" and fact.remaining_magnitude >= row.amount:
            continue
        if fact.item.state in ("expected", "settled") and row.amount in fact.unevidenced:
            continue
        return False
    return True


def match_reads(
    session: Session, matches: Sequence[MovementMatch]
) -> dict[UUID, MovementMatchRead]:
    """The read of each match, by match id, its items and the facts about them loaded once."""
    if not matches:
        return {}
    items_by_match: dict[UUID, list[MovementMatchItem]] = {match.id: [] for match in matches}
    for row in session.scalars(
        select(MovementMatchItem)
        .where(MovementMatchItem.match_id.in_(list(items_by_match)))
        .order_by(MovementMatchItem.match_id, MovementMatchItem.due_item_id)
    ):
        items_by_match[row.match_id].append(row)
    facts = facts_for_ids(
        session, {row.due_item_id for rows in items_by_match.values() for row in rows}
    )
    reads: dict[UUID, MovementMatchRead] = {}
    for match in matches:
        rows = items_by_match[match.id]
        ordered = sorted(
            rows,
            key=lambda row: (
                facts[row.due_item_id].item.due_date is None,
                facts[row.due_item_id].item.due_date or dt.date.max,
                str(row.due_item_id),
            ),
        )
        reads[match.id] = MovementMatchRead(
            id=match.id,
            state=match.state,  # type: ignore[arg-type]
            reasons=list(match.reasons),  # type: ignore[arg-type]
            note=match.note,
            proposed_by_type=match.proposed_by_type,
            created_at=match.created_at,
            confirmed_at=match.confirmed_at,
            confirmed_by_type=match.confirmed_by_type,
            closed_at=match.closed_at,
            closed_by_type=match.closed_by_type,
            close_reason=match.close_reason,
            is_current=_is_current(match, rows, facts),
            items=[item_read(facts[row.due_item_id], row.amount, row.adopted) for row in ordered],
        )
    return reads


def live_matches(session: Session, movement_ids: Sequence[UUID]) -> dict[UUID, MovementMatchRead]:
    """The proposal or confirmed match of each movement that has one, by movement id."""
    if not movement_ids:
        return {}
    matches = list(
        session.scalars(
            select(MovementMatch).where(
                MovementMatch.bank_movement_id.in_(list(movement_ids)),
                MovementMatch.state.in_(("proposed", "confirmed")),
            )
        )
    )
    reads = match_reads(session, matches)
    return {match.bank_movement_id: reads[match.id] for match in matches}


def match_history(session: Session, movement_id: UUID) -> list[MovementMatchRead]:
    """What was proposed, dismissed and undone for a movement, oldest first: its trace."""
    matches = list(
        session.scalars(
            select(MovementMatch)
            .where(
                MovementMatch.bank_movement_id == movement_id,
                MovementMatch.state.in_(("dismissed", "undone")),
            )
            .order_by(MovementMatch.created_at, MovementMatch.id)
        )
    )
    reads = match_reads(session, matches)
    return [reads[match.id] for match in matches]
