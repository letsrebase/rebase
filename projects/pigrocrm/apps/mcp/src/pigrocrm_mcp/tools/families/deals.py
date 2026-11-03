"""Deals and their pipeline stage."""

from collections.abc import Callable
from typing import Any, cast
from uuid import UUID

from mcp.server import MCPServer

from pigrocrm.core.db import SortDirection
from pigrocrm.core.deals.schemas import DealListQuery
from pigrocrm_mcp.annotations import ADDITIVE, DESTRUCTIVE, READ_ONLY
from pigrocrm_mcp.context import McpContext
from pigrocrm_mcp.tools import (
    deals,
)
from pigrocrm_mcp.tools.params import (
    BoundedLimit,
    DealChanges,
    IsoDateStr,
    OptionalMoney,
    OptionalProbability,
    OptionalValueLines,
    parse_legal_entity_id,
)


def register(mcp: MCPServer, context: McpContext, guard: Callable[..., Any]) -> None:
    # ---- deals -----------------------------------------------------------

    @mcp.tool(annotations=ADDITIVE)
    @guard
    def create_deal(
        name: str,
        customer_id: str,
        value_lines: OptionalValueLines = None,
        probability: OptionalProbability = None,
        expected_close_date: IsoDateStr = None,
        note: str | None = None,
        budgeted_hours: OptionalMoney = None,
        budgeted_value: OptionalMoney = None,
        custom_fields: dict[str, Any] | None = None,
        owner_id: str | None = None,
    ) -> dict[str, Any]:
        """Create a deal for a customer, in the first pipeline stage. `expected_close_date` in
        `YYYY-MM-DD` format. `owner_id` is the active user who follows the deal and receives
        reminders: if omitted, it is the creator. `note` is the first note (without date or
        author: the CRM signs it).

        The value is `value_lines`, never `expected_value`.
        """
        return deals.create(
            context,
            {
                "name": name,
                "customer_id": UUID(customer_id),
                "value_lines": value_lines,
                "probability": probability,
                "expected_close_date": expected_close_date,
                "note": note,
                "budgeted_hours": budgeted_hours,
                "budgeted_value": budgeted_value,
                "custom_fields": custom_fields or {},
                "owner_id": UUID(owner_id) if owner_id else None,
            },
        )

    @mcp.tool(annotations=DESTRUCTIVE)
    @guard
    def update_deal(deal_id: str, changes: DealChanges) -> dict[str, Any]:
        """Update a deal. To change its stage use `move_deal`. `owner_id` changes who follows the
        deal (an active user); `null` removes them. `changes.value_lines` replaces the whole list
        (`[]` clears the value).
        """
        return deals.update(context, deal_id, changes)

    @mcp.tool(annotations=READ_ONLY)
    @guard
    def get_deal(deal_id: str) -> dict[str, Any]:
        """Read a deal, with its `value_lines` and the latest 50 notes (`note`, most recent
        first; `note_total` counts them, all of them are in `deal://<id>`).
        """
        return deals.get(context, deal_id)

    @mcp.tool(annotations=READ_ONLY)
    @guard
    def preview_deal_value(
        value_lines: list[dict[str, Any]] | None = None, expected_close_date: IsoDateStr = None
    ) -> dict[str, Any]:
        """The `expected_value`, `one_off_value` and `monthly_recurring_value` that `value_lines`
        (as in `create_deal`) would give a deal, checked as a write is, saving nothing.
        `expected_close_date` is where lines with no `start` begin.
        """
        return deals.preview_value(context, value_lines, expected_close_date)

    @mcp.tool(annotations=READ_ONLY)
    @guard
    def search_deals(
        search: str | None = None,
        customer_id: str | None = None,
        stage_id: str | None = None,
        custom: dict[str, Any] | None = None,
        legal_entity_id: str | None = None,
        limit: BoundedLimit = 50,
        cursor: str | None = None,
        sort: str | None = None,
        # `str`, not a Literal -- see `search_customers`.
        dir: str = "asc",
    ) -> dict[str, Any]:
        """Search deals by name (`name`), customer or pipeline stage. `sort`: `created_at`,
        `updated_at`, `name`. `legal_entity_id` filters by company.
        """
        return deals.search(
            context,
            DealListQuery(
                search=search,
                customer_id=UUID(customer_id) if customer_id else None,
                stage_id=UUID(stage_id) if stage_id else None,
                custom=custom,
                legal_entity_id=parse_legal_entity_id(legal_entity_id),
                # cast: limit is `int | str` at runtime for the SDK-bypass reason
                # documented on `BoundedLimit` in `tools/params.py`; the *ListQuery schema this
                # feeds is what actually enforces (and coerces) "must be an int".
                limit=cast(int, limit),
                # An opaque string since slice 6 -- see `search_customers`.
                cursor=cursor,
                sort=sort,
                dir=cast(SortDirection, dir),
            ),
        )

    @mcp.tool(annotations=ADDITIVE)
    @guard
    def move_deal(
        deal_id: str, stage_id: str | None = None, pipeline_stage_id: str | None = None
    ) -> dict[str, Any]:
        """Move a deal to another stage (`stage_id` or `pipeline_stage_id`, one is enough; list
        in `list_pipeline_stages`). A won/lost stage sets the probability to 100/0.
        """
        return deals.move(context, deal_id, stage_id, pipeline_stage_id)

    @mcp.tool(annotations=ADDITIVE)
    @guard
    def archive_deal(deal_id: str) -> dict[str, str]:
        """Archive a deal (reversible with `restore_deal`). Fails if it has logged hours:
        invoice or archive them first.
        """
        return deals.archive(context, deal_id)

    @mcp.tool(annotations=ADDITIVE)
    @guard
    def restore_deal(deal_id: str) -> dict[str, Any]:
        """Restore an archived deal."""
        return deals.restore(context, deal_id)
