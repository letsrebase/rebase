from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import Select, func, or_, select
from sqlalchemy.orm import Session

from pigrocrm.core.errors import ValidationFailed
from pigrocrm.core.money import round_money
from pigrocrm.core.purchases.models import Purchase
from pigrocrm.core.purchases.schemas import PurchaseListQuery


@dataclass(frozen=True)
class NotesScope:
    """Whose expense-note purchases a reader sees: the staff member they are (`None`: none)."""

    member_id: UUID | None


class PurchaseRepository:
    """Never commits (project rule): every method reads or flushes, and the surrounding service
    method is the one transaction."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, purchase_id: UUID, *, include_deleted: bool = False) -> Purchase | None:
        purchase = self.session.get(Purchase, purchase_id)
        if purchase is None:
            return None
        if purchase.deleted_at is not None and not include_deleted:
            return None
        return purchase

    def add(self, purchase: Purchase) -> Purchase:
        self.session.add(purchase)
        self.session.flush()
        return purchase

    @staticmethod
    def _filtered(query: PurchaseListQuery, notes_of: NotesScope | None = None) -> Select[Any]:
        """The rows a query selects, before paging: what the page and its totals both read."""
        stmt = select(Purchase).where(Purchase.deleted_at.is_(None))
        if notes_of is not None:
            # An expense note's purchase is the note's, as private as the note: only the staff
            # member it pays back reads it (an administrator reads all, with `notes_of` unset).
            stmt = stmt.where(
                or_(Purchase.kind != "expense_note", Purchase.staff_member_id == notes_of.member_id)
            )
        if query.only_general:
            # `deal_id IS NULL` is a general expense (§7.4). A separate flag is needed
            # because `deal_id=None` on the query already means "do not filter".
            stmt = stmt.where(Purchase.deal_id.is_(None))
        elif query.deal_id is not None:
            stmt = stmt.where(Purchase.deal_id == query.deal_id)
        if query.legal_entity_id is not None:
            # A shared purchase (`NULL`) is nobody's in particular: it answers only the
            # view over every legal entity (REB-623, spec §1.7).
            stmt = stmt.where(Purchase.legal_entity_id == query.legal_entity_id)
        if query.category_id is not None:
            stmt = stmt.where(Purchase.category_id == query.category_id)
        if query.supplier_id is not None:
            stmt = stmt.where(Purchase.supplier_id == query.supplier_id)
        if query.kind is not None:
            stmt = stmt.where(Purchase.kind == query.kind)
        if query.status is not None:
            stmt = stmt.where(Purchase.status == query.status)
        if query.date_from is not None:
            stmt = stmt.where(Purchase.date >= query.date_from)
        if query.date_to is not None:
            stmt = stmt.where(Purchase.date <= query.date_to)
        if query.custom:
            stmt = stmt.where(Purchase.custom_fields.contains(query.custom))
        return stmt

    def totals(
        self, query: PurchaseListQuery, notes_of: NotesScope | None = None
    ) -> tuple[int, Decimal]:
        """How many purchases the filters select and the sum of their document totals (signed:
        a credit note and a refund subtract), whatever page was asked for. The sum is the
        document's total in euro, the figure a list column shows, and not what the purchase
        costs."""
        rows = self._filtered(query, notes_of).subquery()
        count, total = self.session.execute(
            select(func.count(), func.coalesce(func.sum(rows.c.amount), 0))
        ).one()
        return int(count), round_money(Decimal(total))

    # `list` stays the last method in this class.
    def list(self, query: PurchaseListQuery, notes_of: NotesScope | None = None) -> list[Purchase]:
        stmt = self._filtered(query, notes_of)
        if query.cursor is not None:
            anchor = self.session.get(Purchase, query.cursor)
            if anchor is None:
                raise ValidationFailed(
                    "purchase", "cursor", "cursore sconosciuto", expected="il cursore di una pagina"
                )
            stmt = stmt.where(
                (Purchase.date < anchor.date)
                | ((Purchase.date == anchor.date) & (Purchase.id < anchor.id))
            )
        return list(
            self.session.execute(
                stmt.order_by(Purchase.date.desc(), Purchase.id.desc()).limit(query.limit + 1)
            )
            .scalars()
            .all()
        )
