"""Profit and loss, budget, ceilings and the year overview. Reads only."""

from collections.abc import Callable
from decimal import Decimal
from typing import Any, cast
from uuid import UUID

from mcp.server import MCPServer

from pigrocrm.core.analytics.schemas import BudgetQuery, CeilingSimulationQuery, PeriodPnlQuery
from pigrocrm_mcp.annotations import READ_ONLY
from pigrocrm_mcp.context import McpContext
from pigrocrm_mcp.tools import (
    timetracking,
)
from pigrocrm_mcp.tools.params import (
    BoundedLimit,
    IsoDateStr,
    OptionalFactor,
    Year,
    iso_date,
    parse_legal_entity_id,
)


def register(mcp: MCPServer, context: McpContext, guard: Callable[..., Any]) -> None:
    # ---- analytics ---------------------------------------------------------
    # Reads only. `bind_time_to_invoice` has no tool because binding hours to a draft
    # is the step that determines their freezing at issue, and choosing *which* hours
    # to invoice is a commercial decision -- slice 3 §11 withdrew `issue_invoice` with
    # the same reasoning and this is the rung below it. `get_fiscal_estimate` has no
    # tool for a different reason: taxable income, contributions and estimated net for
    # a real person are the most sensitive data this product holds, and residual R10
    # leaves a PAT indistinguishable from full account access.

    @mcp.tool(annotations=READ_ONLY)
    @guard
    def get_deal_pnl(deal_id: str) -> dict[str, Any]:
        """Profit and loss of a deal: invoiced revenue, direct costs, labor cost, margin and
        status. `percentage_margin` is `null` if revenue is zero: it is not 0%, it means there is no
        collected revenue yet. `accrued_value` is an estimate, not revenue.
        """
        return timetracking.get_deal_pnl(context, deal_id)

    @mcp.tool(annotations=READ_ONLY)
    @guard
    def get_period_pnl(
        date_from: str, date_to: str, customer_id: str | None = None, base: str = "issue"
    ) -> dict[str, Any]:
        """Period profit and loss in two columns: closed deals and ongoing deals; the figure
        to report is the first. `closed_period` and `written_entries_overdue` say whether the number
        can still change. Overheads are on a separate row, not allocated.

        `base` chooses the date that places an invoice's revenue in the period: `"issue"`
        (default, as for the tax authority) or `"accrual"` (`accrual_from`, or the issue date if
        missing; for invoices issued late). Costs and hours stay at their own date; a proforma is
        never revenue.
        """
        return timetracking.get_period_pnl(
            context,
            PeriodPnlQuery.model_validate(
                {
                    "date_from": date_from,
                    "date_to": date_to,
                    "customer_id": UUID(customer_id) if customer_id else None,
                    "base": base,
                }
            ),
        )

    @mcp.tool(annotations=READ_ONLY)
    @guard
    def get_unbilled_backlog() -> dict[str, Any]:
        """Billable hours not yet on an issued invoice, in total and with no period: hours,
        accrued value (hours × rate) and entries without a rate. Entries without a rate count
        in the hours but are worth zero (absent ≠ zero). Hours on a draft still count. The
        accrued value is not revenue and enters no margin. It includes unbilled contract
        days, only if in the approved state or later.
        """
        return timetracking.get_unbilled_backlog(context)

    @mcp.tool(annotations=READ_ONLY)
    @guard
    def get_budget_vs_actual(
        date_from: str,
        date_to: str,
        customer_id: str | None = None,
        limit: BoundedLimit = 50,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        """Budget against actuals per deal. The value variance is against the pro-rata budget
        (budget × hours progress). A row with `unbudgeted = true` has no
        budget (it is not zero) and is excluded from the aggregates.
        """
        return timetracking.get_budget_vs_actual(
            context,
            BudgetQuery.model_validate(
                {
                    "date_from": date_from,
                    "date_to": date_to,
                    "customer_id": UUID(customer_id) if customer_id else None,
                    "limit": limit,
                    "cursor": UUID(cursor) if cursor else None,
                }
            ),
        )

    @mcp.tool(annotations=READ_ONLY)
    @guard
    def get_ceiling_headroom(year: Year, legal_entity_id: str | None = None) -> dict[str, Any]:
        """How much room is left before each active threshold of the fiscal package, on the year's
        collected, real revenue: `remaining` is threshold minus revenue. Does not require `admin`.
        For a single company: without `legal_entity_id` it is the only one in the space; with
        several companies it must be given. A foreign company has no thresholds.
        """
        return timetracking.get_ceiling_headroom(
            context, year, parse_legal_entity_id(legal_entity_id)
        )

    @mcp.tool(annotations=READ_ONLY)
    @guard
    def get_days_analysis(
        date_from: str | None = None, date_to: str | None = None, legal_entity_id: str | None = None
    ) -> dict[str, Any]:
        """The days of a period: by month and status, actual rate against list price, by customer
        and
        contract, and how many days ended up in «worked without approval» month by month.
        `date_from`/`date_to` (YYYY-MM-DD) are given both or neither (then it is the current
        month). `legal_entity_id` filters by company; without it, all. Does not require `admin`.
        """
        return timetracking.get_days_analysis(
            context, date_from, date_to, parse_legal_entity_id(legal_entity_id)
        )

    @mcp.tool(annotations=READ_ONLY)
    @guard
    def get_year_overview(year: Year, legal_entity_id: str | None = None) -> dict[str, Any]:
        """A company's year as the accountant reads it: revenue by regime and by
        tax treatment, with legal reference, and the register of issued invoices with
        stamp duty and INPS contribution shown separately. `totali.taxable_amount` is the year's
        revenue. Does not require
        `admin`. Without `legal_entity_id` it is the only company in the space; with several
        companies it must be given. It does not return files.
        """
        return timetracking.get_year_overview(context, year, parse_legal_entity_id(legal_entity_id))

    @mcp.tool(annotations=READ_ONLY)
    @guard
    def simulate_ceiling(
        year: Year,
        budgeted_hours: OptionalFactor = None,
        budgeted_value: OptionalFactor = None,
        hourly_rate: OptionalFactor = None,
        legal_entity_id: str | None = None,
        contract_id: str | None = None,
        expected_date: IsoDateStr = None,
        deal_id: str | None = None,
    ) -> dict[str, Any]:
        """The «does it fit?» simulator: adds a deal's estimate to the year's real revenue and
        re-evaluates every active threshold, without saving. Give `budgeted_value`, or
        `budgeted_hours` with `hourly_rate`, or a saved deal's `deal_id` (its value lines' share
        in `year`), never both. `legal_entity_id` as in `get_ceiling_headroom`. With
        `contract_id` it is also read against the contract's cap on the customer's share
        (`customer_share_cap`), over the anniversary year containing `expected_date`
        (default today): `contract_cap` gives the share before and after and
        whether it `rientra`.
        """
        return timetracking.simulate_ceiling(
            context,
            year,
            CeilingSimulationQuery(
                budgeted_hours=cast(Decimal, budgeted_hours)
                if budgeted_hours is not None
                else None,
                budgeted_value=cast(Decimal, budgeted_value)
                if budgeted_value is not None
                else None,
                hourly_rate=cast(Decimal, hourly_rate) if hourly_rate is not None else None,
                deal_id=UUID(deal_id) if deal_id else None,
                contract_id=UUID(contract_id) if contract_id else None,
                expected_date=iso_date(expected_date),
            ),
            parse_legal_entity_id(legal_entity_id),
        )
