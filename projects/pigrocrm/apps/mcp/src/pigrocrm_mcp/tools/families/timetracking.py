"""Hours, the timer, costs and period locks."""

from collections.abc import Callable
from typing import Any, cast
from uuid import UUID

from mcp.server import MCPServer

from pigrocrm.core.timetracking.schemas import (
    CostListQuery,
    TimeEntryListQuery,
)
from pigrocrm_mcp.annotations import ADDITIVE, DESTRUCTIVE, READ_ONLY
from pigrocrm_mcp.context import McpContext
from pigrocrm_mcp.tools import (
    timetracking,
)
from pigrocrm_mcp.tools.params import (
    BoundedLimit,
    CostChanges,
    HoursArg,
    IsoDateStr,
    MoneyArg,
    OptionalFactor,
    OptionalYear,
    TimeEntryChanges,
    iso_date,
    parse_legal_entity_id,
)


def register(mcp: MCPServer, context: McpContext, guard: Callable[..., Any]) -> None:
    # ---- time tracking -----------------------------------------------------
    # An agent may record and read. It may not change what already-recorded numbers
    # mean. Eleven methods are therefore deliberately absent from this module and from
    # `tools/timetracking.py` -- `recalculate_rates`, `update_user_rates`,
    # `update_deal_rate`, the four cost-category writes (`unarchive_cost_category`
    # included: slice 4 §11 listed only the other three, but bringing a category back
    # is the same decision as archiving it, taken in the other direction),
    # `bind_time_to_invoice`, `close_period`, `reopen_period`, `get_fiscal_estimate` --
    # and
    # `apps/mcp/tests/test_mcp_invoice_ban.py` fails the build if a tool for any of
    # them appears anywhere under `tools/`, or if that list changes. The defence is
    # structural rather than permission-based because residual R10 is open: a PAT has
    # no scopes and inherits its owner's full role, so an admin token would pass any
    # authorisation check. Not registering the tool is the only mechanism that holds.

    @mcp.tool(annotations=ADDITIVE)
    @guard
    def log_time(
        deal_id: str,
        user_id: str,
        date: str,
        hours: HoursArg,
        description: str,
        billable: bool = True,
        applied_rate: OptionalFactor = None,
        applied_cost: OptionalFactor = None,
        internal_notes: str | None = None,
        custom_fields: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Log hours on a deal. `date` in `YYYY-MM-DD` format, not in the future. `deal_id` must be
        a real id: resolve the project with `search_deals`, since billable hours on the wrong
        customer only surface on the invoice. The rate is frozen on the row: `describe_rates`
        says which one applies; if there is none, the entry stays without a rate.
        """
        return timetracking.log_time(
            context,
            {
                "deal_id": UUID(deal_id),
                "user_id": UUID(user_id),
                "date": date,
                "hours": hours,
                "description": description,
                "billable": billable,
                "applied_rate": applied_rate,
                "applied_cost": applied_cost,
                "internal_notes": internal_notes,
                "custom_fields": custom_fields or {},
            },
        )

    @mcp.tool(annotations=DESTRUCTIVE)
    @guard
    def update_time_entry(entry_id: str, changes: TimeEntryChanges) -> dict[str, Any]:
        """Update a time entry. If it is already on an issued invoice, only `internal_notes` and
        the custom
        fields can be changed.
        """
        return timetracking.update_time_entry(context, entry_id, changes)

    @mcp.tool(annotations=ADDITIVE)
    @guard
    def archive_time_entry(entry_id: str) -> dict[str, str]:
        """Archive a time entry (reversible with `restore_time_entry`). Fails if it is linked to
        an invoice line.
        """
        return timetracking.archive_time_entry(context, entry_id)

    @mcp.tool(annotations=ADDITIVE)
    @guard
    def restore_time_entry(entry_id: str) -> dict[str, Any]:
        """Restore an archived time entry. Fails if the deal is archived: restore
        the deal first.
        """
        return timetracking.restore_time_entry(context, entry_id)

    @mcp.tool(annotations=READ_ONLY)
    @guard
    def get_time_entry(entry_id: str) -> dict[str, Any]:
        """Read a time entry, with the line value already computed."""
        return timetracking.get_time_entry(context, entry_id)

    @mcp.tool(annotations=READ_ONLY)
    @guard
    def list_time_entries(
        deal_id: str | None = None,
        user_id: str | None = None,
        date_from: IsoDateStr = None,
        date_to: IsoDateStr = None,
        billable: bool | None = None,
        invoiced: bool | None = None,
        legal_entity_id: str | None = None,
        limit: BoundedLimit = 50,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        """List time entries, most recent first. `invoiced = false` says how much is left to
        invoice. `legal_entity_id` filters by company.
        """
        return timetracking.list_time_entries(
            context,
            TimeEntryListQuery(
                deal_id=UUID(deal_id) if deal_id else None,
                legal_entity_id=parse_legal_entity_id(legal_entity_id),
                user_id=UUID(user_id) if user_id else None,
                date_from=iso_date(date_from, "date_from"),
                date_to=iso_date(date_to, "date_to"),
                billable=billable,
                invoiced=invoiced,
                limit=cast(int, limit),
                cursor=UUID(cursor) if cursor else None,
            ),
        )

    @mcp.tool(annotations=READ_ONLY)
    @guard
    def get_running_timer() -> dict[str, Any] | None:
        """The running timer of the token owner, or `null`: deal, description and `started_at`.
        Hours
        already logged are in `list_time_entries`.
        """
        return timetracking.get_running_timer(context)

    @mcp.tool(annotations=ADDITIVE)
    @guard
    def start_timer(
        deal_id: str | None = None, description: str = "", billable: bool = True
    ) -> dict[str, Any]:
        """Start the stopwatch. Only one at a time: if one is running it fails; stop it
        (`stop_timer`) or discard it (`discard_timer`). The deal can be chosen later with
        `update_timer` or at stop, but it must be real: resolve it with `search_deals`.
        """
        return timetracking.start_timer(
            context,
            {
                "deal_id": UUID(deal_id) if deal_id else None,
                "description": description,
                "billable": billable,
            },
        )

    @mcp.tool(annotations=DESTRUCTIVE)
    @guard
    def update_timer(
        deal_id: str | None = None,
        description: str | None = None,
        billable: bool | None = None,
    ) -> dict[str, Any]:
        """Change the deal, description or billable flag of the running timer without stopping
        it."""
        return timetracking.update_timer(
            context,
            {
                "deal_id": UUID(deal_id) if deal_id else None,
                "description": description,
                "billable": billable,
            },
        )

    @mcp.tool(annotations=ADDITIVE)
    @guard
    def stop_timer(
        deal_id: str | None = None, description: str | None = None, date: str | None = None
    ) -> dict[str, Any]:
        """Stop the timer and log the hours as an entry, with the rate frozen as in `log_time`.
        `date` (YYYY-MM-DD) is the day, today if omitted. Returns the created entry.
        """
        return timetracking.stop_timer(
            context,
            {
                "deal_id": UUID(deal_id) if deal_id else None,
                "description": description,
                "date": date,
            },
        )

    @mcp.tool(annotations=DESTRUCTIVE)
    @guard
    def discard_timer() -> dict[str, str]:
        """Discard the running timer without logging anything."""
        return timetracking.discard_timer(context)

    @mcp.tool(annotations=READ_ONLY)
    @guard
    def get_deal_time_summary(deal_id: str) -> dict[str, Any]:
        """Total hours, hours to invoice, value of unbilled hours, labor cost and status
        of the deal. Revenue is not here: it is the invoice.
        """
        return timetracking.get_deal_time_summary(context, deal_id)

    @mcp.tool(annotations=READ_ONLY)
    @guard
    def describe_rates(deal_id: str, user_id: str) -> dict[str, Any]:
        """Which rate and which cost would be frozen on a new entry and from which level
        (`manual`, `deal`, `user`, `absent`). Call it before `log_time`.
        """
        return timetracking.describe_rates(context, deal_id, user_id)

    @mcp.tool(annotations=ADDITIVE)
    @guard
    def create_cost(
        category_id: str,
        date: str,
        amount: MoneyArg,
        description: str,
        deal_id: str | None = None,
        supplier: str | None = None,
        document_id: str | None = None,
        custom_fields: dict[str, Any] | None = None,
        legal_entity_id: str | None = None,
    ) -> dict[str, Any]:
        """Record a cost. Without `deal_id` it is an overhead, in the period profit and loss
        and not allocated to deals. `amount` is the total paid, VAT included; negative is a
        refund; zero is rejected. Categories are in `list_cost_categories`. `legal_entity_id`
        applies only to an overhead (omitted: shared); a cost on a deal belongs to the deal's
        company.
        """
        return timetracking.create_cost(
            context,
            {
                "category_id": UUID(category_id),
                "date": date,
                "amount": amount,
                "description": description,
                "deal_id": UUID(deal_id) if deal_id else None,
                "legal_entity_id": parse_legal_entity_id(legal_entity_id),
                "supplier": supplier,
                "document_id": UUID(document_id) if document_id else None,
                "custom_fields": custom_fields or {},
            },
        )

    @mcp.tool(annotations=DESTRUCTIVE)
    @guard
    def update_cost(cost_id: str, changes: CostChanges) -> dict[str, Any]:
        """Update a cost."""
        return timetracking.update_cost(context, cost_id, changes)

    @mcp.tool(annotations=ADDITIVE)
    @guard
    def archive_cost(cost_id: str) -> dict[str, str]:
        """Archive a cost (reversible with `restore_cost`)."""
        return timetracking.archive_cost(context, cost_id)

    @mcp.tool(annotations=ADDITIVE)
    @guard
    def restore_cost(cost_id: str) -> dict[str, Any]:
        """Restore an archived cost."""
        return timetracking.restore_cost(context, cost_id)

    @mcp.tool(annotations=READ_ONLY)
    @guard
    def get_cost(cost_id: str) -> dict[str, Any]:
        """Read a cost."""
        return timetracking.get_cost(context, cost_id)

    @mcp.tool(annotations=READ_ONLY)
    @guard
    def list_costs(
        deal_id: str | None = None,
        only_general: bool = False,
        category_id: str | None = None,
        date_from: IsoDateStr = None,
        date_to: IsoDateStr = None,
        legal_entity_id: str | None = None,
        limit: BoundedLimit = 50,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        """List costs, most recent first. `only_general = true` shows only costs without a deal.
        `legal_entity_id` filters by company; omitted, all, including shared costs with no
        company.
        """
        return timetracking.list_costs(
            context,
            CostListQuery(
                deal_id=UUID(deal_id) if deal_id else None,
                legal_entity_id=parse_legal_entity_id(legal_entity_id),
                only_general=only_general,
                category_id=UUID(category_id) if category_id else None,
                date_from=iso_date(date_from, "date_from"),
                date_to=iso_date(date_to, "date_to"),
                limit=cast(int, limit),
                cursor=UUID(cursor) if cursor else None,
            ),
        )

    @mcp.tool(annotations=READ_ONLY)
    @guard
    def list_cost_categories(include_archived: bool = False) -> dict[str, Any]:
        """List the cost categories. They are created and archived from the app."""
        return timetracking.list_cost_categories(context, include_archived)

    @mcp.tool(annotations=READ_ONLY)
    @guard
    def list_period_locks(year: OptionalYear = None) -> dict[str, Any]:
        """List closed months, most recent first. A closed month rejects every write of hours and
        costs dated in that month: read here before logging old entries.
        """
        return timetracking.list_period_locks(context, year)
