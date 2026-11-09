from typing import Any
from uuid import UUID

from pigrocrm.core.inbox.schemas import InboxListQuery
from pigrocrm.core.inbox.service import InboxService
from pigrocrm_mcp.context import McpContext


def get(context: McpContext, item_id: str) -> dict[str, Any]:
    return InboxService(context.session).get(UUID(item_id), context.actor).model_dump(mode="json")


def search(context: McpContext, query: InboxListQuery) -> dict[str, Any]:
    return InboxService(context.session).list(query, context.actor).model_dump(mode="json")
