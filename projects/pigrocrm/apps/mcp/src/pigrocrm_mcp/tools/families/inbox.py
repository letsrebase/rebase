"""The inbox of incoming documents: reads only, and no bytes (design 2026-11-09, § 6). A file
is uploaded over the web or REST and never through a tool."""

from collections.abc import Callable
from typing import Any, cast
from uuid import UUID

from mcp.server import MCPServer

from pigrocrm.core.inbox.schemas import InboxItemKind, InboxItemState, InboxListQuery
from pigrocrm_mcp.annotations import READ_ONLY
from pigrocrm_mcp.context import McpContext
from pigrocrm_mcp.tools import inbox
from pigrocrm_mcp.tools.params import BoundedLimit, literal_arg


def register(mcp: MCPServer, context: McpContext, guard: Callable[..., Any]) -> None:
    @mcp.tool(annotations=READ_ONLY)
    @guard
    def get_inbox_item(item_id: str) -> dict[str, Any]:
        """Read an inbox item: file name, type, size, SHA-256, state, kind, why it is a duplicate or
        discarded, the item it duplicates (`duplicate_of_id`) and the ZIP it came from
        (`parent_id`). No file content.
        """
        return inbox.get(context, item_id)

    @mcp.tool(annotations=READ_ONLY)
    @guard
    def list_inbox_items(
        state: str | None = None,
        kind: str | None = None,
        parent_id: str | None = None,
        limit: BoundedLimit = 50,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        """List the files that arrived, newest first, with the `next_cursor` of the next page.
        `state`: `new`, `proposed`, `filed`, `duplicate`, `discarded`; `parent_id`: the entries of
        one ZIP. A person uploads files; this never does.
        """
        return inbox.search(
            context,
            InboxListQuery(
                state=literal_arg("state", state, InboxItemState),
                kind=literal_arg("kind", kind, InboxItemKind),
                parent_id=UUID(parent_id) if parent_id else None,
                limit=cast(int, limit),
                cursor=UUID(cursor) if cursor else None,
            ),
        )
