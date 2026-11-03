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
        """Aggiunge una nota a un deal, a un cliente o a una persona: passa uno solo fra
        `deal_id`, `customer_id`, `person_id`. Il CRM la firma e la data da solo: il testo non deve
        iniziare con una data né dire chi scrive.
        """
        return notes.add(context, text, deal_id, customer_id, person_id)

    @mcp.tool(annotations=DESTRUCTIVE)
    @guard
    def update_note(note_id: str, text: str) -> dict[str, Any]:
        """Riscrive una nota (`id` in `note` dei `get_*`): le proprie, o tutte se amministratore;
        mai quelle del sistema. Niente data né autore nel testo.
        """
        return notes.update(context, note_id, text)

    @mcp.tool(annotations=DESTRUCTIVE)
    @guard
    def delete_note(note_id: str) -> dict[str, Any]:
        """Toglie una nota dal thread (resta nella cronologia). Stesse regole di `update_note`."""
        return notes.delete(context, note_id)
