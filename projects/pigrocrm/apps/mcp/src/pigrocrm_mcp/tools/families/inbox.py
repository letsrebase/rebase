"""The inbox of incoming documents: reads, and one proposal, and no bytes (design 2026-11-09,
§ 6). A file is uploaded over the web or REST and never through a tool; what a PDF says is read
here by the user's own agent and proposed, and a person accepts it."""

from collections.abc import Callable
from typing import Any, cast
from uuid import UUID

from mcp.server import MCPServer

from pigrocrm.core.inbox.schemas import InboxItemKind, InboxItemState, InboxListQuery
from pigrocrm.core.proposals.schemas import InboxProposalTargetType
from pigrocrm_mcp.annotations import ADDITIVE, READ_ONLY
from pigrocrm_mcp.context import McpContext
from pigrocrm_mcp.tools import inbox
from pigrocrm_mcp.tools.params import BoundedLimit, Confidence, literal_arg


def register(mcp: MCPServer, context: McpContext, guard: Callable[..., Any]) -> None:
    @mcp.tool(annotations=READ_ONLY)
    @guard
    def get_inbox_item(item_id: str) -> dict[str, Any]:
        """Read an inbox item: metadata, `extracted_text` (a PDF's text; none for a photo) and
        `parsed_fields` (a FatturaPA's). No file.
        """
        return inbox.get(context, item_id)

    @mcp.tool(annotations=READ_ONLY)
    @guard
    def list_inbox_items(
        state: str | None = None,
        kind: str | None = None,
        parent_id: str | None = None,
        classified: bool | None = None,
        limit: BoundedLimit = 50,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        """List the files that arrived, newest first. `state`: new, proposed, filed, duplicate,
        discarded; `parent_id`: a ZIP's entries; `classified=false`: unread ones to propose for.
        """
        return inbox.search(
            context,
            InboxListQuery(
                state=literal_arg("state", state, InboxItemState),
                kind=literal_arg("kind", kind, InboxItemKind),
                parent_id=UUID(parent_id) if parent_id else None,
                classified=classified,
                limit=cast(int, limit),
                cursor=UUID(cursor) if cursor else None,
            ),
        )

    @mcp.tool(annotations=ADDITIVE)
    @guard
    def propose_inbox_item(
        item_id: str,
        kind: str,
        excerpt: str,
        confidence: Confidence,
        fields: dict[str, Any],
    ) -> dict[str, Any]:
        """Propose what an unclassified inbox item is, from its text; a person decides, nothing is
        filed. `kind`: supplier_invoice (fields issuer_name, issue_date, total), receipt
        (issue_date, total), bank_statement (period_start, period_end); also number, due_date,
        currency, vat_amount, iban, description. `excerpt`: verbatim from the text.
        """
        return inbox.propose(
            context,
            {
                "inbox_item_id": UUID(item_id),
                "target_type": literal_arg("kind", kind, InboxProposalTargetType),
                "excerpt": excerpt,
                "confidence": confidence,
                "proposed_fields": fields,
            },
        )
