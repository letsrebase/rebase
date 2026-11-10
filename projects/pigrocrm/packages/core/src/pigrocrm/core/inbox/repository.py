from uuid import UUID

from sqlalchemy import ColumnElement, case, select, text
from sqlalchemy.orm import Session

from pigrocrm.core.inbox.models import InboxItem
from pigrocrm.core.inbox.schemas import InboxListQuery

DEDUP_LOCK_KEY = 0x1B0C_0001
"""The advisory lock every writer of originals takes first: one number for the space's
inbox, held to the end of the transaction."""


class InboxRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def lock_for_dedup(self) -> None:
        """Serialises the writers of items. «Find the original, then insert» is two steps, and
        two uploads of one file racing through both would each find nothing and make two
        originals; the lock makes the second one see the first."""
        self.session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": DEDUP_LOCK_KEY})

    def add(self, item: InboxItem) -> InboxItem:
        self.session.add(item)
        self.session.flush()
        return item

    def get(self, item_id: UUID) -> InboxItem | None:
        return self.session.get(InboxItem, item_id)

    def find_original(
        self,
        content_sha256: str,
        natural_key: str | None,
        visible: ColumnElement[bool] | None,
        *,
        archive: bool = False,
    ) -> InboxItem | None:
        """The item that already holds this document **among those the caller can see**: the
        same content first, the same natural key after it, the earliest when several would do.
        A duplicate is never an original, so the answer is always the item the first copy
        became; an item the caller cannot see is not one to point at. A ZIP is only ever a
        duplicate of another ZIP that was opened (`kind` `archive`): one kept and discarded
        as nested inside another ZIP was never opened, and a person extracting it and sending
        it loose means it to be."""
        matches = InboxItem.content_sha256 == content_sha256
        if natural_key is not None:
            matches = matches | (InboxItem.natural_key == natural_key)
        stmt = select(InboxItem).where(InboxItem.state != "duplicate", matches)
        if visible is not None:
            stmt = stmt.where(visible)
        if archive:
            stmt = stmt.where(InboxItem.kind == "archive")
        stmt = stmt.order_by(
            case((InboxItem.content_sha256 == content_sha256, 0), else_=1),
            InboxItem.created_at,
            InboxItem.id,
        ).limit(1)
        return self.session.execute(stmt).scalars().first()

    # `list` stays the last method in this class: a later `def list(...)` would rebind
    # `list` in the class namespace, and Python 3.13 evaluates annotations eagerly.
    def list(self, query: InboxListQuery, visible: ColumnElement[bool] | None) -> list[InboxItem]:
        """Newest first, by id (UUIDv7 is time-ordered), one row over `limit` so the caller can
        tell «there is more» from «that was everything»."""
        stmt = select(InboxItem)
        if visible is not None:
            stmt = stmt.where(visible)
        if query.state is not None:
            stmt = stmt.where(InboxItem.state == query.state)
        if query.kind is not None:
            stmt = stmt.where(InboxItem.kind == query.kind)
        if query.classified is not None:
            stmt = stmt.where(
                InboxItem.kind.is_not(None)
                if query.classified
                # Nobody said what it is, and it is waiting: not a duplicate or a discarded file.
                else (InboxItem.kind.is_(None) & (InboxItem.state == "new"))
            )
        if query.parent_id is not None:
            stmt = stmt.where(InboxItem.parent_id == query.parent_id)
        if query.cursor is not None:
            stmt = stmt.where(InboxItem.id < query.cursor)
        return list(
            self.session.execute(
                stmt.order_by(InboxItem.id.desc()).limit(query.limit + 1)
            ).scalars()
        )
