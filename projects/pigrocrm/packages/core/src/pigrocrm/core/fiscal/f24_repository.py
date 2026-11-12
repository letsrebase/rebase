"""The reads and the plain writes of F24 groups (REB-1076). No permission, no activity and no
commit: `F24GroupService` is the surface, and the bank matching and the due items ask this module
only which items are grouped and to flip a group between `open` and `settled`."""

import datetime as dt
from collections.abc import Iterable, Sequence
from decimal import Decimal
from uuid import UUID

from sqlalchemy import ColumnElement, select
from sqlalchemy.orm import Session

from pigrocrm.core.due_items.models import DueItem
from pigrocrm.core.fiscal.f24_models import F24Group, F24GroupItem, F24GroupReceipt, TaxCredit


def grouped_item_clause() -> ColumnElement[bool]:
    """In SQL, the due items that are a line of a live F24 group: the ones a movement pays only
    through their group, and that nothing recomputes."""
    return DueItem.id.in_(
        select(F24GroupItem.due_item_id).where(F24GroupItem.released_at.is_(None))
    )


class F24GroupRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, group_id: UUID, *, lock: bool = False) -> F24Group | None:
        return self.session.get(F24Group, group_id, with_for_update=lock)

    def live_lines(self, group_id: UUID) -> list[F24GroupItem]:
        """The lines of a group that were not released, in a stable order."""
        return list(
            self.session.scalars(
                select(F24GroupItem)
                .where(F24GroupItem.group_id == group_id, F24GroupItem.released_at.is_(None))
                .order_by(F24GroupItem.created_at, F24GroupItem.id)
            )
        )

    def lines_of(self, group_ids: Sequence[UUID]) -> dict[UUID, list[F24GroupItem]]:
        """Every line of each group, released ones included (a dissolved group keeps its
        trace), by group id."""
        by_group: dict[UUID, list[F24GroupItem]] = {group_id: [] for group_id in group_ids}
        if not group_ids:
            return by_group
        for line in self.session.scalars(
            select(F24GroupItem)
            .where(F24GroupItem.group_id.in_(list(group_ids)))
            .order_by(F24GroupItem.created_at, F24GroupItem.id)
        ):
            by_group[line.group_id].append(line)
        return by_group

    def receipts_of(self, group_ids: Sequence[UUID]) -> dict[UUID, list[F24GroupReceipt]]:
        by_group: dict[UUID, list[F24GroupReceipt]] = {group_id: [] for group_id in group_ids}
        if not group_ids:
            return by_group
        for receipt in self.session.scalars(
            select(F24GroupReceipt)
            .where(F24GroupReceipt.group_id.in_(list(group_ids)))
            .order_by(F24GroupReceipt.created_at, F24GroupReceipt.id)
        ):
            by_group[receipt.group_id].append(receipt)
        return by_group

    def groups_of_items(self, item_ids: Iterable[UUID]) -> dict[UUID, UUID]:
        """The live group of each item that is in one, by item id."""
        ids = list(item_ids)
        if not ids:
            return {}
        rows = self.session.execute(
            select(F24GroupItem.due_item_id, F24GroupItem.group_id).where(
                F24GroupItem.due_item_id.in_(ids), F24GroupItem.released_at.is_(None)
            )
        )
        return {item_id: group_id for item_id, group_id in rows}

    def group_of(self, item_id: UUID) -> UUID | None:
        return self.groups_of_items([item_id]).get(item_id)

    def open_groups(self, legal_entity_id: UUID) -> list[F24Group]:
        return list(
            self.session.scalars(
                select(F24Group)
                .where(F24Group.legal_entity_id == legal_entity_id, F24Group.state == "open")
                .order_by(F24Group.payment_date, F24Group.id)
            )
        )

    def settled_group_of_movement(self, movement_id: UUID) -> list[F24Group]:
        return list(
            self.session.scalars(
                select(F24Group).where(
                    F24Group.bank_movement_id == movement_id, F24Group.state == "settled"
                )
            )
        )

    def credits_by_id(self, credit_ids: Iterable[UUID]) -> dict[UUID, TaxCredit]:
        ids = list(credit_ids)
        if not ids:
            return {}
        return {
            credit.id: credit
            for credit in self.session.scalars(select(TaxCredit).where(TaxCredit.id.in_(ids)))
        }

    # ---- the plain writes ------------------------------------------------------------------

    def settle(
        self, group: F24Group, settled_on: dt.date, *, movement_id: UUID | None = None
    ) -> None:
        """The group is paid: by the movement, or, with none, because a person marked it filed.
        Either way every live line is taken whole (a filing narrows a credit afterwards, with
        `use_line`). Flushes."""
        group.state = "settled"
        group.bank_movement_id = movement_id
        group.settled_on = settled_on
        for line in self.live_lines(group.id):
            line.used_amount = line.amount
        self.session.flush()

    def use_line(
        self,
        line: F24GroupItem,
        used: Decimal,
        settlement_id: UUID | None,
        *,
        release: bool,
        at: dt.datetime,
    ) -> None:
        """What a filing took of a line of the settled group and the payment that wrote it. A
        credit the F24 left unspent in part or whole is released, its rest free for another F24.
        Flushes."""
        line.used_amount = used
        line.settlement_id = settlement_id
        if release:
            line.released_at = at
        self.session.flush()

    def reopen(self, group: F24Group) -> None:
        """What paid the group was taken back (the movement's match undone, or the filing): it
        waits again. Every line goes back to live and untaken. Flushes."""
        group.state = "open"
        group.bank_movement_id = None
        group.settled_on = None
        for line in self.lines_of([group.id])[group.id]:
            line.used_amount = None
            line.settlement_id = None
            line.released_at = None
        self.session.flush()
