"""Reads and writes of `due_items` and `due_item_settlements`, for `DueItemService` alone."""

import datetime as dt
from decimal import Decimal
from typing import NamedTuple
from uuid import UUID

from sqlalchemy import ColumnElement, and_, func, or_, select
from sqlalchemy.orm import Session

from pigrocrm.core.customers.models import Customer
from pigrocrm.core.due_items.models import (
    OPEN_STATES,
    RESTRICTED_SOURCE_KINDS,
    DueItem,
    DueItemSettlement,
)
from pigrocrm.core.due_items.schemas import DueItemListQuery
from pigrocrm.core.errors import ValidationFailed

ZERO = Decimal("0.00")


class Totals(NamedTuple):
    """What an item's live settlements add up to, and the date of the latest one."""

    settled: Decimal
    last_settled_on: dt.date | None


class DueItemRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, item_id: UUID) -> DueItem | None:
        return self.session.get(DueItem, item_id)

    def get_locked(self, item_id: UUID) -> DueItem | None:
        """The item under `FOR UPDATE`, and fresh: two settlements of one item queue here,
        so the second reads what the first left and the over-settlement check holds."""
        return self.session.execute(
            select(DueItem).where(DueItem.id == item_id).with_for_update(),
            execution_options={"populate_existing": True},
        ).scalar_one_or_none()

    def add(self, item: DueItem) -> DueItem:
        self.session.add(item)
        self.session.flush()
        return item

    def add_settlement(self, settlement: DueItemSettlement) -> DueItemSettlement:
        self.session.add(settlement)
        self.session.flush()
        return settlement

    def get_settlement(self, settlement_id: UUID) -> DueItemSettlement | None:
        return self.session.get(DueItemSettlement, settlement_id)

    def settlements(self, item_id: UUID) -> list[DueItemSettlement]:
        return list(
            self.session.execute(
                select(DueItemSettlement)
                .where(DueItemSettlement.due_item_id == item_id)
                .order_by(DueItemSettlement.settled_on, DueItemSettlement.id)
            ).scalars()
        )

    def totals(self, item_id: UUID) -> Totals:
        settled, last = self.session.execute(
            select(
                func.coalesce(func.sum(DueItemSettlement.amount), ZERO),
                func.max(DueItemSettlement.settled_on),
            ).where(
                DueItemSettlement.due_item_id == item_id, DueItemSettlement.reversed_at.is_(None)
            )
        ).one()
        return Totals(Decimal(settled), last)

    def dependents(self, item_id: UUID) -> list[DueItem]:
        """The items whose date follows this one and can still move: a settled or dropped
        dependent keeps the date it had."""
        return list(
            self.session.execute(
                select(DueItem)
                .where(DueItem.depends_on_id == item_id, DueItem.state.in_(OPEN_STATES))
                .order_by(DueItem.id)
                # Locked, and the state re-read once the lock is won: a dependent that a
                # parallel payment just settled keeps the date it had.
                .with_for_update(),
                execution_options={"populate_existing": True},
            ).scalars()
        )

    def organization_legal_entity(self, organization_id: UUID) -> UUID | None:
        return self.session.execute(
            select(Customer.legal_entity_id).where(Customer.id == organization_id)
        ).scalar_one_or_none()

    def organization_name(self, organization_id: UUID | None) -> str | None:
        if organization_id is None:
            return None
        return self.session.execute(
            select(Customer.legal_name).where(Customer.id == organization_id)
        ).scalar_one_or_none()

    # `list_rows` stays the last method in this class -- the unconditional project rule (a
    # `def list(...)` would rebind `list` in the class namespace).
    def list_rows(
        self, query: DueItemListQuery, *, restricted: bool
    ) -> list[tuple[DueItem, str | None, Decimal]]:
        """Open items by default, in `(due_date, id)` order with the undated ones last.

        `restricted` says whether the caller may read the fiscal source kinds; without it
        they are left out of every page. One extra row is fetched so the service can tell
        «there is more» from «that was everything»."""
        settled = (
            select(func.coalesce(func.sum(DueItemSettlement.amount), ZERO))
            .where(
                DueItemSettlement.due_item_id == DueItem.id,
                DueItemSettlement.reversed_at.is_(None),
            )
            .correlate(DueItem)
            .scalar_subquery()
        )
        state_filter = (
            DueItem.state == query.state
            if query.state is not None
            else DueItem.state.in_(OPEN_STATES)
        )
        stmt = (
            select(DueItem, Customer.legal_name, settled)
            .outerjoin(Customer, Customer.id == DueItem.organization_id)
            .where(state_filter)
        )
        if not restricted:
            stmt = stmt.where(DueItem.source_kind.not_in(sorted(RESTRICTED_SOURCE_KINDS)))
        if query.direction is not None:
            stmt = stmt.where(DueItem.direction == query.direction)
        if query.legal_entity_id is not None:
            stmt = stmt.where(DueItem.legal_entity_id == query.legal_entity_id)
        if query.organization_id is not None:
            stmt = stmt.where(DueItem.organization_id == query.organization_id)
        if query.source_kind is not None:
            stmt = stmt.where(DueItem.source_kind == query.source_kind)
        if query.due_from is not None:
            stmt = stmt.where(DueItem.due_date >= query.due_from)
        if query.due_to is not None:
            stmt = stmt.where(DueItem.due_date <= query.due_to)
        if query.cursor is not None:
            anchor = self.get(query.cursor)
            # A fiscal item is no cursor for a caller who may not read it: the page would
            # start after its date and say that it exists.
            if (
                anchor is not None
                and not restricted
                and anchor.source_kind in RESTRICTED_SOURCE_KINDS
            ):
                anchor = None
            if anchor is None:
                raise ValidationFailed(
                    "due_item", "cursor", "cursore sconosciuto", expected="il cursore di una pagina"
                )
            stmt = stmt.where(_after(anchor))
        stmt = stmt.order_by(DueItem.due_date.asc().nulls_last(), DueItem.id.asc())
        return [
            (item, name, Decimal(total))
            for item, name, total in self.session.execute(stmt.limit(query.limit + 1)).all()
        ]


def _after(anchor: DueItem) -> ColumnElement[bool]:
    """The rows after `anchor` in `(due_date NULLS LAST, id)` order."""
    if anchor.due_date is None:
        return and_(DueItem.due_date.is_(None), DueItem.id > anchor.id)
    return or_(
        DueItem.due_date > anchor.due_date,
        and_(DueItem.due_date == anchor.due_date, DueItem.id > anchor.id),
        DueItem.due_date.is_(None),
    )
