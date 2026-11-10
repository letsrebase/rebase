"""A staff member's expense notes: submit one and list them. Deciding on a note and paying it
back are an administrator's `full_access` tools (`tools/privileged.py`); every tool here is
behind the `staff` and `purchases` modules (`modules.py`)."""

from collections.abc import Callable
from typing import Any, cast

from mcp.server import MCPServer

from pigrocrm_mcp.annotations import ADDITIVE, READ_ONLY
from pigrocrm_mcp.context import McpContext
from pigrocrm_mcp.tools import expense_notes
from pigrocrm_mcp.tools.params import BoundedLimit, MoneyArg


def register(mcp: MCPServer, context: McpContext, guard: Callable[..., Any]) -> None:
    @mcp.tool(annotations=ADDITIVE)
    @guard
    def submit_expense_note(
        date: str,
        description: str,
        category_id: str,
        amount: MoneyArg | None = None,
        kind: str = "expense",
        inbox_item_id: str | None = None,
        kilometres: MoneyArg | None = None,
        rate_per_km: MoneyArg | None = None,
        route_from: str | None = None,
        route_to: str | None = None,
    ) -> dict[str, Any]:
        """Submit an expense note for the token owner's own staff member; a person approves it.
        `kind` `expense` needs `amount`; `mileage` needs `kilometres`, `rate_per_km`,
        `route_from`, `route_to` (the amount is their product). `inbox_item_id`: the receipt.
        """
        return expense_notes.submit(
            context,
            date=date,
            description=description,
            category_id=category_id,
            kind=kind,
            amount=amount,
            inbox_item_id=inbox_item_id,
            kilometres=kilometres,
            rate_per_km=rate_per_km,
            route_from=route_from,
            route_to=route_to,
        )

    @mcp.tool(annotations=READ_ONLY)
    @guard
    def list_expense_notes(
        status: str | None = None,
        bucket: str | None = None,
        staff_member_id: str | None = None,
        category_id: str | None = None,
        legal_entity_id: str | None = None,
        limit: BoundedLimit = 50,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        """List expense notes, newest first: the owner's own, or every one in scope with a
        full-access admin token. `status`: submitted, approved, rejected, reimbursed; `bucket`:
        to_approve, to_reimburse, closed.
        """
        return expense_notes.search(
            context,
            status=status,
            bucket=bucket,
            staff_member_id=staff_member_id,
            category_id=category_id,
            legal_entity_id=legal_entity_id,
            limit=cast(int, limit),
            cursor=cursor,
        )
