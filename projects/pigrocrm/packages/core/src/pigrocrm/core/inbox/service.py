"""Reading the inbox: the list and one item, with no bytes (design § 6: bytes never travel over
MCP, and a read answers metadata only). Writing is `uploads.InboxUploads`, reading what arrived
is `classifier.InboxClassifier`."""

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.inbox.access import visible_item, visible_to
from pigrocrm.core.inbox.repository import InboxRepository
from pigrocrm.core.inbox.schemas import (
    InboxItemDetail,
    InboxItemPage,
    InboxItemRead,
    InboxListQuery,
)
from pigrocrm.core.proposals.models import Proposal


def attach_proposals(session: Session, reads: Sequence[InboxItemRead]) -> list[InboxItemRead]:
    """Each item of `reads` with the proposal waiting for a person on it, when it has one (an
    item in `proposed` links to its proposal, design § 3.5). The item was read by somebody who
    may see it, and so is its proposal."""
    ids = [read.id for read in reads if read.state == "proposed"]
    pending: dict[UUID, UUID] = {}
    if ids:
        rows = session.execute(
            select(Proposal.inbox_item_id, Proposal.id).where(
                Proposal.inbox_item_id.in_(ids), Proposal.status == "pending"
            )
        )
        pending = {item_id: proposal_id for item_id, proposal_id in rows if item_id is not None}
    return [read.model_copy(update={"proposal_id": pending.get(read.id)}) for read in reads]


class InboxService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.repo = InboxRepository(session)

    def get(self, item_id: UUID, actor: Actor) -> InboxItemDetail:
        item = visible_item(self.session, item_id, actor)
        read = InboxItemDetail.model_validate(item)
        return read.model_copy(
            update={"proposal_id": attach_proposals(self.session, [read])[0].proposal_id}
        )

    # `list` stays the last method in this class, as in the repository.
    def list(self, query: InboxListQuery, actor: Actor) -> InboxItemPage:
        rows = self.repo.list(query, visible_to(actor))
        items = rows[: query.limit]
        return InboxItemPage(
            items=attach_proposals(self.session, [InboxItemRead.model_validate(i) for i in items]),
            next_cursor=items[-1].id if len(rows) > query.limit and items else None,
        )
