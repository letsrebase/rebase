"""The amounts that fall due, and the payments that settle them."""

from collections.abc import Callable
from typing import Any, cast

from mcp.server import MCPServer

from pigrocrm_mcp.annotations import ADDITIVE, READ_ONLY
from pigrocrm_mcp.context import McpContext
from pigrocrm_mcp.tools import due_items
from pigrocrm_mcp.tools.params import BoundedLimit, IsoDateStr, MoneyArg


def register(mcp: MCPServer, context: McpContext, guard: Callable[..., Any]) -> None:
    @mcp.tool(annotations=READ_ONLY)
    @guard
    def list_due_items(
        direction: str | None = None,
        state: str | None = None,
        source_kind: str | None = None,
        legal_entity_id: str | None = None,
        organization_id: str | None = None,
        due_from: IsoDateStr = None,
        due_to: IsoDateStr = None,
        limit: BoundedLimit = 50,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        """List amounts due by date, undated last: `direction` `in` or `out`; `state` `forecast`,
        `expected`, `settled` or `dropped` (omitted: forecast and expected). `remaining` is what
        is still owed. Tax, IVA, withholding and payroll items need a full-access admin token.
        """
        return due_items.search(
            context,
            direction=direction,
            state=state,
            source_kind=source_kind,
            legal_entity_id=legal_entity_id,
            organization_id=organization_id,
            due_from=due_from,
            due_to=due_to,
            limit=cast(int, limit),
            cursor=cursor,
        )

    @mcp.tool(annotations=READ_ONLY)
    @guard
    def get_due_item(due_item_id: str) -> dict[str, Any]:
        """One due item with every settlement, reversed ones included."""
        return due_items.get(context, due_item_id)

    @mcp.tool(annotations=ADDITIVE)
    @guard
    def settle_due_item(
        due_item_id: str,
        amount: MoneyArg,
        settled_on: str,
        value_date: IsoDateStr = None,
        account_amount: MoneyArg | None = None,
        account_currency: str | None = None,
        note: str | None = None,
    ) -> dict[str, Any]:
        """Record a payment on a due item, partial or full, in the item's currency (negative for a
        credit); more than `remaining` is refused. `settled_on` is a past `YYYY-MM-DD`;
        `account_amount` and `account_currency` say what moved on the account when it differs.
        A client's payment is open to any token that writes; money going out or fiscal needs a
        full-access admin token.
        """
        return due_items.settle(
            context,
            due_item_id,
            amount,
            settled_on,
            value_date,
            account_amount,
            account_currency,
            note,
        )

    @mcp.tool(annotations=ADDITIVE)
    @guard
    def reverse_settlement(settlement_id: str, reason: str | None = None) -> dict[str, Any]:
        """Undo a payment recorded on a due item: the row stays, stamped, and the item reopens. Same
        permission as `settle_due_item` for that item.
        """
        return due_items.reverse_settlement(context, settlement_id, reason)
