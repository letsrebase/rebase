"""The amounts that fall due, and the payments that settle them."""

from collections.abc import Callable
from typing import Any, cast

from mcp.server import MCPServer

from pigrocrm_mcp.annotations import ADDITIVE, DESTRUCTIVE, READ_ONLY
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
    def list_payroll_totals(
        legal_entity_id: str | None = None, due_from: IsoDateStr = None, due_to: IsoDateStr = None
    ) -> dict[str, Any]:
        """Open payroll cash per date as one «Stipendi» amount, no person; any token may read it."""
        return due_items.payroll_totals(context, legal_entity_id, due_from, due_to)

    @mcp.tool(annotations=READ_ONLY)
    @guard
    def get_cash_schedule(weeks: int = 13, legal_entity_id: str | None = None) -> dict[str, Any]:
        """Cash schedule from this week: per week money in and out (expected and forecast), late
        figures, and with a full-access admin token the balance, its projection and alerts.
        `weeks` is 4, 13 or 26."""
        return due_items.cash_schedule(context, weeks, legal_entity_id)

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

    @mcp.tool(annotations=READ_ONLY)
    @guard
    def list_recurring_expenses(
        include_stopped: bool = False, legal_entity_id: str | None = None
    ) -> dict[str, Any]:
        """List recurring expense rules; each forecasts a payment out per due date."""
        return due_items.list_recurring_expenses(context, include_stopped, legal_entity_id)

    @mcp.tool(annotations=ADDITIVE)
    @guard
    def create_recurring_expense(
        supplier_id: str,
        category_id: str,
        description: str,
        amount: MoneyArg,
        cadence: str,
        first_due_date: str,
        ends_on: IsoDateStr = None,
        legal_entity_id: str | None = None,
    ) -> dict[str, Any]:
        """Forecast a supplier's fixed bill: `amount` (VAT included) every `cadence` (`monthly`,
        `bimonthly`, `quarterly`, `semiannual`, `annual`) from `first_due_date`. A purchase of that
        supplier in the same period replaces the forecast.
        """
        return due_items.create_recurring_expense(
            context,
            supplier_id,
            category_id,
            description,
            amount,
            cadence,
            first_due_date,
            ends_on,
            legal_entity_id,
        )

    @mcp.tool(annotations=DESTRUCTIVE)
    @guard
    def stop_recurring_expense(recurring_expense_id: str) -> dict[str, Any]:
        """Stop a recurring expense and drop its forecasts; it cannot be resumed."""
        return due_items.stop_recurring_expense(context, recurring_expense_id)
