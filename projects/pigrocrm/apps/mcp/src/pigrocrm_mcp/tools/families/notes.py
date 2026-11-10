"""The notes thread of a deal, a customer and a person."""

from collections.abc import Callable
from typing import Any

from mcp.server import MCPServer

from pigrocrm_mcp.annotations import ADDITIVE, DESTRUCTIVE
from pigrocrm_mcp.context import McpContext
from pigrocrm_mcp.tools import notes


def register(mcp: MCPServer, context: McpContext, guard: Callable[..., Any]) -> None:
    @mcp.tool(annotations=ADDITIVE)
    @guard
    def add_note(
        text: str,
        deal_id: str | None = None,
        customer_id: str | None = None,
        person_id: str | None = None,
    ) -> dict[str, Any]:
        """Add a note to a deal, a customer or a person: pass exactly one of
        `deal_id`, `customer_id`, `person_id`. The CRM signs and dates it itself: the text must not
        start with a date or say who is writing.
        """
        return notes.add(context, text, deal_id, customer_id, person_id)

    @mcp.tool(annotations=DESTRUCTIVE)
    @guard
    def update_note(note_id: str, text: str) -> dict[str, Any]:
        """Rewrite a note (`id` in `note` of the `get_*` tools): your own, or any if admin;
        never system notes. No date or author in the text.
        """
        return notes.update(context, note_id, text)

    @mcp.tool(annotations=DESTRUCTIVE)
    @guard
    def delete_note(note_id: str) -> dict[str, Any]:
        """Remove a note from the thread (it stays in the history). Same rules as `update_note`."""
        return notes.delete(context, note_id)
