from typing import Any
from uuid import UUID

from pigrocrm.core.inbox.schemas import InboxDiscard, InboxDuplicate, InboxKindSet, InboxListQuery
from pigrocrm.core.inbox.service import InboxService
from pigrocrm_mcp.context import McpContext
from pigrocrm_mcp.tools import proposals


def get(context: McpContext, item_id: str) -> dict[str, Any]:
    return InboxService(context.session).get(UUID(item_id), context.actor).model_dump(mode="json")


def search(context: McpContext, query: InboxListQuery) -> dict[str, Any]:
    return InboxService(context.session).list(query, context.actor).model_dump(mode="json")


def propose(context: McpContext, data: dict[str, Any]) -> dict[str, Any]:
    return proposals.propose(context, data)


def discard(context: McpContext, item_id: str, reason: str) -> dict[str, Any]:
    read = InboxService(context.session).discard(
        UUID(item_id), InboxDiscard(reason=reason), context.actor
    )
    return read.model_dump(mode="json")


def mark_duplicate(context: McpContext, item_id: str, original_id: str) -> dict[str, Any]:
    read = InboxService(context.session).mark_duplicate(
        UUID(item_id), InboxDuplicate(original_id=UUID(original_id)), context.actor
    )
    return read.model_dump(mode="json")


def set_kind(
    context: McpContext, item_id: str, kind: str | None, legal_entity_id: str | None
) -> dict[str, Any]:
    data = InboxKindSet.model_validate(
        {"kind": kind, "legal_entity_id": UUID(legal_entity_id) if legal_entity_id else None}
    )
    return (
        InboxService(context.session)
        .set_kind(UUID(item_id), data, context.actor)
        .model_dump(mode="json")
    )
