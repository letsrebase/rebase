"""Reading the inbox: the list and one item, with no bytes (design § 6: bytes never travel over
MCP, and a read answers metadata only). Writing is `uploads.InboxUploads`."""

from uuid import UUID

from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.inbox.access import visible_item, visible_to
from pigrocrm.core.inbox.repository import InboxRepository
from pigrocrm.core.inbox.schemas import InboxItemPage, InboxItemRead, InboxListQuery


class InboxService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.repo = InboxRepository(session)

    def get(self, item_id: UUID, actor: Actor) -> InboxItemRead:
        return InboxItemRead.model_validate(visible_item(self.session, item_id, actor))

    # `list` stays the last method in this class, as in the repository.
    def list(self, query: InboxListQuery, actor: Actor) -> InboxItemPage:
        rows = self.repo.list(query, visible_to(actor))
        items = rows[: query.limit]
        return InboxItemPage(
            items=[InboxItemRead.model_validate(item) for item in items],
            next_cursor=items[-1].id if len(rows) > query.limit and items else None,
        )
