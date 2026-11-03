"""The day ledger, read only."""

from collections.abc import Callable
from typing import Annotated, Any, cast

from mcp.server import MCPServer
from pydantic import WithJsonSchema

from pigrocrm_mcp.annotations import READ_ONLY
from pigrocrm_mcp.context import McpContext
from pigrocrm_mcp.tools import (
    work_units,
)
from pigrocrm_mcp.tools.params import IsoDateStr, parse_legal_entity_id


def register(mcp: MCPServer, context: McpContext, guard: Callable[..., Any]) -> None:
    # ---- the day ledger, read only ------------------------------------------
    #
    # An agent reads days and proposes them; it never records or moves one. The four
    # writes are refused to an agent credential, over REST as well
    # (`FULL_ACCESS_ACTIONS`, `test_mcp_surface_coverage.py`).

    @mcp.tool(annotations=READ_ONLY)
    @guard
    def get_work_unit(work_unit_id: str) -> dict[str, Any]:
        """Read a contract day: status, contract and customer, approval with the
        document that proves it, history of status changes and moves allowed from here
        (`actions`). `out_of_term` says whether the date falls outside the contract. An agent
        proposes one with `propose_day`.
        """
        return work_units.get(context, work_unit_id)

    @mcp.tool(annotations=READ_ONLY)
    @guard
    def list_work_units(
        contract_id: str | None = None,
        date_from: IsoDateStr = None,
        date_to: IsoDateStr = None,
        status: str | None = None,
        legal_entity_id: str | None = None,
        limit: Annotated[
            int | str,
            WithJsonSchema({"type": "integer", "minimum": 1, "maximum": 500, "default": 200}),
        ] = 200,
    ) -> dict[str, Any]:
        """List contract days, most recent first. `contract_id` or a date is required
        (`date_from`, `date_to`, inclusive, `YYYY-MM-DD`). `status`: `proposed`, `approved`,
        `worked`,
        `worked_without_approval`, `invoiced`, `paid`, `disputed`, `revoked`,
        `rejected`, `non_billable`. `truncated: true` = there were more than `limit`.
        `legal_entity_id` filters by company.
        """
        return work_units.search(
            context,
            contract_id=contract_id,
            date_from=date_from,
            date_to=date_to,
            status=status,
            legal_entity_id=parse_legal_entity_id(legal_entity_id),
            limit=cast(int, limit),
        )
