"""The four dashboards."""

from collections.abc import Callable
from typing import Any

from mcp.server import MCPServer

from pigrocrm.core.dashboard.schemas import PeriodQuery
from pigrocrm_mcp.annotations import READ_ONLY
from pigrocrm_mcp.context import McpContext
from pigrocrm_mcp.tools import dashboard as dashboard_tools
from pigrocrm_mcp.tools.params import IsoDateStr, parse_legal_entity_id


def register(mcp: MCPServer, context: McpContext, guard: Callable[..., Any]) -> None:
    # ---- dashboard ---------------------------------------------------------
    # No resource counterpart, deliberately (§11.1): a resource is addressed by a URI and a
    # dashboard is a question with a period.

    @mcp.tool(annotations=READ_ONLY)
    @guard
    def get_commercial_dashboard(
        date_from: IsoDateStr = None, date_to: IsoDateStr = None, legal_entity_id: str | None = None
    ) -> dict[str, Any]:
        """Commercial overview: pipeline by stage, deals closed in the period with conversion
        rate, sent offers awaiting a reply, closings expected in the next 30 days, accepted
        offers whose deal is not won. `date_from` and `date_to` (`YYYY-MM-DD`) go together;
        without them, the current month. `weighted_value` is an estimate, not invoiced revenue.
        `open_monthly_recurring` and `closings.won_monthly_recurring` are per-month figures
        (what the recurring lines of the open deals, and of the deals won in the period, bring
        in each month): never add them to a total.
        `legal_entity_id` filters by company; without it, all companies are summed.
        """
        # `IsoDateStr`, the alias this file already uses for a date parameter: the runtime
        # type stays `str | None` so a malformed date is rejected inside `PeriodQuery`,
        # inside the guarded call, and the agent gets this project's own rendered guidance
        # instead of an SDK rejection whose wording is not ours -- while `list_tools()`
        # still advertises `format: date`. `model_validate` on a dict rather than
        # `PeriodQuery(date_from=..., date_to=...)`: the two do the same coercion, and the
        # keyword form would be a type error to a reader (and to mypy) that says nothing true
        # about the runtime.
        return dashboard_tools.get_commercial_dashboard(
            context,
            PeriodQuery.model_validate(
                {"date_from": date_from, "date_to": date_to, "legal_entity_id": legal_entity_id}
            ),
        )

    @mcp.tool(annotations=READ_ONLY)
    @guard
    def get_economic_dashboard(
        date_from: IsoDateStr = None, date_to: IsoDateStr = None, legal_entity_id: str | None = None
    ) -> dict[str, Any]:
        """Income statement for the period: revenue, direct costs, labour cost and margin in two
        columns, `closed` and `in_progress`, which must not be added together; plus overheads,
        accrued but not invoiced, `unpaid` and `expired` (no period). `date_from` and `date_to`
        (`YYYY-MM-DD`) go together; without them, the current month. No tax estimate.
        `legal_entity_id` filters by company; without it, all companies are summed.
        """
        return dashboard_tools.get_economic_dashboard(
            context,
            PeriodQuery.model_validate(
                {"date_from": date_from, "date_to": date_to, "legal_entity_id": legal_entity_id}
            ),
        )

    # Registered by Task C6, with the same reasoning. No parameters at all: §6's dashboard
    # takes no period, so there is none for an agent to get wrong. Task C7 must not
    # register a second one.

    @mcp.tool(annotations=READ_ONLY)
    @guard
    def get_receivables_dashboard(legal_entity_id: str | None = None) -> dict[str, Any]:
        """Receivables schedule: issued, unpaid invoices in six due-date buckets (their sum is
        `total`, equal to `unpaid`), expected per month, exposure per customer, overdue invoices
        with reminders sent. No period: it is what is owed today. For reminders there is
        `list_payment_reminder_candidates`. `legal_entity_id` filters by company; without it,
        all companies are summed.
        """
        return dashboard_tools.get_receivables_dashboard(
            context, parse_legal_entity_id(legal_entity_id)
        )

    @mcp.tool(annotations=READ_ONLY)
    @guard
    def get_operational_dashboard(legal_entity_id: str | None = None) -> dict[str, Any]:
        """What needs doing now: hours day by day for the current week (also days with no hours),
        backlog to invoice, four inconsistency signals (invoiced but not won, won but to be
        invoiced, overdue and unpaid, customer concentration) and latest activity. No period.
        `legal_entity_id` filters by company; without it, all companies together.
        """
        return dashboard_tools.get_operational_dashboard(
            context, parse_legal_entity_id(legal_entity_id)
        )
