"""Engagements with freelancers: update a draft, read, and ask when a freelancer is free.

Creating, cancelling and closing an engagement and the tax data are in `privileged.py`: each draws
a letter number or ends a legal document, so they exist only where `mcp_full_access` is on.
"""

from collections.abc import Callable
from typing import Any, cast

from mcp.server import MCPServer

from pigrocrm_mcp.annotations import DESTRUCTIVE, READ_ONLY
from pigrocrm_mcp.context import McpContext
from pigrocrm_mcp.tools import collaborators
from pigrocrm_mcp.tools.params import (
    BoundedLimit,
    EngagementChanges,
    IsoDateStr,
)


def register(mcp: MCPServer, context: McpContext, guard: Callable[..., Any]) -> None:

    @mcp.tool(annotations=DESTRUCTIVE)
    @guard
    def update_engagement(engagement_id: str, changes: EngagementChanges) -> dict[str, Any]:
        """Update an engagement. A draft changes freely; once sent only `days_per_week` and the
        client, deal and contract. Naming `payment_rule` restates the whole rule.
        """
        return collaborators.update(context, engagement_id, changes)

    @mcp.tool(annotations=READ_ONLY)
    @guard
    def get_engagement(engagement_id: str) -> dict[str, Any]:
        """One engagement: `missing` lists what is still to fill in, `warnings` the overlaps."""
        return collaborators.get(context, engagement_id)

    @mcp.tool(annotations=READ_ONLY)
    @guard
    def list_engagements(
        freelancer_id: str | None = None,
        state: str | None = None,
        client_organization_id: str | None = None,
        deal_id: str | None = None,
        contract_id: str | None = None,
        limit: BoundedLimit = 50,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        """List engagements, newest first. `state`: draft, awaiting_signature, active, concluded
        or cancelled.
        """
        return collaborators.search(
            context,
            freelancer_id=freelancer_id,
            state=state,
            client_organization_id=client_organization_id,
            deal_id=deal_id,
            contract_id=contract_id,
            limit=cast(int, limit),
            cursor=cursor,
        )

    @mcp.tool(annotations=READ_ONLY)
    @guard
    def preview_engagement(
        engagement_id: str, variables: dict[str, str | None] | None = None
    ) -> dict[str, Any]:
        """Render an engagement's letter as it would be written now, from the space's template for
        its regime. Nothing is stored. Answers the text `version`, whether it is still a `draft`
        (a draft is never sent), the letter `number`, the `markdown` and `blank`: the fields the
        page prints as blank lines. `variables` are the template's free-text fields by name
        (`attivita`, `luogo`, ...); the engagement, the parties and the issuer cannot be set.
        The letter never carries the client's price.
        """
        return collaborators.preview_letter(context, engagement_id, variables)

    @mcp.tool(annotations=READ_ONLY)
    @guard
    def preview_framework_agreement(
        freelancer_id: str, letter_type: str | None = None
    ) -> dict[str, Any]:
        """Render a freelancer's framework agreement as it would be written now. Nothing is
        stored. `letter_type`: ordinary or occasional, omitted following the freelancer's fiscal
        kind. Same answer as `preview_engagement`.
        """
        return collaborators.preview_framework_agreement(context, freelancer_id, letter_type)

    @mcp.tool(annotations=READ_ONLY)
    @guard
    def get_freelancer_availability(
        freelancer_id: str | None = None,
        hub_freelancer_id: str | None = None,
        date_from: IsoDateStr = None,
        date_to: IsoDateStr = None,
    ) -> dict[str, Any]:
        """Days a week a freelancer is committed to, by ISO week, from their active engagements.
        Name the freelancer by organization id or hub id. `status` `unknown` (sconosciuta) where
        an engagement has no `days_per_week`, never `free`. Default: twelve weeks from this one.
        """
        return collaborators.availability(
            context,
            freelancer_id=freelancer_id,
            hub_freelancer_id=hub_freelancer_id,
            date_from=date_from,
            date_to=date_to,
        )
