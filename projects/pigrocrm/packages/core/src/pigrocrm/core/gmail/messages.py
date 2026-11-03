"""Reading the mail already stored in the CRM, for every surface that shows it.

The REST route and the MCP tools both go through here, so "the same services the UI
calls" holds for the Gmail mirror too. Reading is not role-gated (visibility is the
row-level policy's job, migration 0048); the actor is part of the signature like on every
other read service, so a rule added here later reaches both surfaces at once.
"""

from typing import Literal
from uuid import UUID

from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.errors import NotFound
from pigrocrm.core.gmail.repository import GmailRepository
from pigrocrm.core.gmail.schemas import GmailMessageRead

LinkedEntityType = Literal["customer", "person", "deal"]


class GmailMessageService:
    def __init__(self, session: Session) -> None:
        self.repo = GmailRepository(session)

    def list_for_entity(
        self,
        entity_type: LinkedEntityType,
        entity_id: UUID,
        actor: Actor,
        *,
        limit: int,
        newest_first: bool = False,
    ) -> list[GmailMessageRead]:
        """The messages filed against one customer, person or deal; see
        `GmailRepository.messages_for_entity` for the two orderings."""
        del actor
        rows = self.repo.messages_for_entity(
            entity_type, entity_id, limit=limit, newest_first=newest_first
        )
        return [GmailMessageRead.model_validate(row) for row in rows]

    def get(self, message_id: UUID, actor: Actor) -> GmailMessageRead:
        del actor
        row = self.repo.message(message_id)
        if row is None:
            raise NotFound("gmail_message", message_id)
        return GmailMessageRead.model_validate(row)
