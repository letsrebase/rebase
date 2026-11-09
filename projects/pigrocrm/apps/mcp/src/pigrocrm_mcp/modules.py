"""Which tools belong to which module (spec 2026-11-09, A2 and B2), and the one step that
leaves out what the space has switched off.

`prune_off_modules` runs once in `build_server`, after every tool is registered and before
the listing is trimmed: a tool whose modules are not all on is removed, so it is not listed
and not callable (a call by name answers «Unknown tool»), exactly as a tool whose Gmail or
Slack settings are off. A tool that spans modules (`convert_deal_to_contract`,
`bind_time_to_invoice`, `get_deal_pnl`) lists all of them and exists only when every one is
on. The server is rebuilt when a space's settings change (`http.py`), so a switch reaches
this surface within the settings cache's window with no restart.

`TOOL_MODULES` holds the tools of a module and nothing else: a tool in none of its rows is
core and always registered. `tests/test_module_guard.py` lists every registered tool and
fails for one that is in neither this table nor its explicit list of core tools, so a new
tool is classified on purpose and not by omission.
"""

from collections.abc import Mapping

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from pigrocrm.core.config import Settings
from pigrocrm.core.modules import ModuleKey, enabled_modules

TOOL_MODULES: Mapping[str, tuple[ModuleKey, ...]] = {
    # deals: the pipeline, its stages and offers.
    "create_deal": ("deals",),
    "update_deal": ("deals",),
    "get_deal": ("deals",),
    "preview_deal_value": ("deals",),
    "search_deals": ("deals",),
    "move_deal": ("deals",),
    "archive_deal": ("deals",),
    "restore_deal": ("deals",),
    "list_pipeline_stages": ("deals",),
    "get_commercial_dashboard": ("deals",),
    "set_offer_state": ("deals",),
    # contracts: the contract, its cards, its days.
    "create_contract": ("contracts",),
    "get_contract": ("contracts",),
    "update_contract": ("contracts",),
    "set_contract_state": ("contracts",),
    "renew_contract": ("contracts",),
    "search_contracts": ("contracts",),
    "create_rate_card": ("contracts",),
    "update_rate_card": ("contracts",),
    "list_rate_cards": ("contracts",),
    "create_clause_note": ("contracts",),
    "update_clause_note": ("contracts",),
    "list_clause_notes": ("contracts",),
    "create_contract_expense": ("contracts",),
    "update_contract_expense": ("contracts",),
    "list_contract_expenses": ("contracts",),
    "get_contract_concentration": ("contracts",),
    "get_contract_register": ("contracts",),
    "list_contract_email_templates": ("contracts",),
    "preview_contract_email": ("contracts",),
    "set_renewal_assumption": ("contracts",),
    "get_renewal_assumption": ("contracts",),
    "project_contract_revenue": ("contracts",),
    "propose_contract": ("contracts",),
    "propose_day": ("contracts",),
    "get_work_unit": ("contracts",),
    "list_work_units": ("contracts",),
    "get_days_analysis": ("contracts",),
    # time: hours, the timer and rates.
    "log_time": ("time",),
    "update_time_entry": ("time",),
    "archive_time_entry": ("time",),
    "restore_time_entry": ("time",),
    "get_time_entry": ("time",),
    "list_time_entries": ("time",),
    "get_running_timer": ("time",),
    "start_timer": ("time",),
    "update_timer": ("time",),
    "stop_timer": ("time",),
    "discard_timer": ("time",),
    "get_deal_time_summary": ("time",),
    "describe_rates": ("time",),
    "get_unbilled_backlog": ("time",),
    "update_user_rates": ("time",),
    "update_deal_rate": ("time",),
    "recalculate_rates": ("time",),
    "bind_time_to_invoice": ("time",),
    "get_budget_vs_actual": ("deals", "time"),
    # costs.
    "create_cost": ("costs",),
    "update_cost": ("costs",),
    "archive_cost": ("costs",),
    "restore_cost": ("costs",),
    "get_cost": ("costs",),
    "list_costs": ("costs",),
    "list_cost_categories": ("costs",),
    "create_cost_category": ("costs",),
    "update_cost_category": ("costs",),
    "archive_cost_category": ("costs",),
    "unarchive_cost_category": ("costs",),
    # taxes: the estimate, the ceilings and the year.
    "get_ceiling_headroom": ("taxes",),
    "get_year_overview": ("taxes",),
    "get_fiscal_estimate": ("taxes",),
    # Across modules: all of them, or the tool is not there.
    "convert_deal_to_contract": ("deals", "contracts"),
    "preview_deal_contract": ("deals", "contracts"),
    "get_deal_pnl": ("deals", "time", "costs"),
    "simulate_ceiling": ("taxes", "deals"),
}


def prune_off_modules(mcp: MCPServer, settings: Settings) -> None:
    """Removes the tools whose modules are not all on for `settings`."""
    on = enabled_modules(settings)
    for name, needed in TOOL_MODULES.items():
        if not set(needed) <= on:
            try:
                mcp.remove_tool(name)
            except ToolError:
                continue  # not registered on this installation (privileged, for one)


# A prompt reads the tools' data, so it follows the same rule: registered only when the
# modules it works on are on.
PROMPT_MODULES: Mapping[str, tuple[ModuleKey, ...]] = {
    "pipeline-review": ("deals",),
    "hours-to-log": ("time",),
}


def prompt_available(name: str, settings: Settings) -> bool:
    return set(PROMPT_MODULES.get(name, ())) <= enabled_modules(settings)
