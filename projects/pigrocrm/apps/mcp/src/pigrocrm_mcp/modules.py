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
from collections.abc import Set as AbstractSet
from typing import Any

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from pigrocrm.core.config import Settings
from pigrocrm.core.modules import ModuleKey, enabled_modules, require_in

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
    "get_contract_concentration": ("contracts",),
    "get_contract_register": ("contracts",),
    "list_contract_email_templates": ("contracts",),
    "preview_contract_email": ("contracts",),
    "set_renewal_assumption": ("contracts",),
    "get_renewal_assumption": ("contracts",),
    "project_contract_revenue": ("contracts",),
    "list_fee_occurrences": ("contracts",),
    "bill_contract_fee": ("contracts",),
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
    "update_user_rates": ("time",),
    "update_deal_rate": ("time",),
    "recalculate_rates": ("time",),
    "bind_time_to_invoice": ("time",),
    "get_budget_vs_actual": ("deals", "time"),
    # An expense is a contract's and carries a cost category: both modules, as in the web app.
    "create_contract_expense": ("contracts", "purchases"),
    "update_contract_expense": ("contracts", "purchases"),
    "list_contract_expenses": ("contracts", "purchases"),
    # purchases.
    "create_purchase": ("purchases",),
    "update_purchase": ("purchases",),
    "cancel_purchase": ("purchases",),
    "archive_purchase": ("purchases",),
    "restore_purchase": ("purchases",),
    "get_purchase": ("purchases",),
    "list_purchases": ("purchases",),
    "list_cost_categories": ("purchases",),
    "create_cost_category": ("purchases",),
    "update_cost_category": ("purchases",),
    "archive_cost_category": ("purchases",),
    "unarchive_cost_category": ("purchases",),
    "list_iva_settlements": ("purchases",),
    "create_recurring_expense": ("purchases",),
    "stop_recurring_expense": ("purchases",),
    "list_recurring_expenses": ("purchases",),
    "list_iva_periods": ("purchases",),
    "get_iva_register": ("purchases",),
    "close_iva_period": ("purchases",),
    "reopen_iva_period": ("purchases",),
    # expense notes: a staff member's receipts become purchases, so both modules.
    "submit_expense_note": ("staff", "purchases"),
    "list_expense_notes": ("staff", "purchases"),
    "approve_expense_note": ("staff", "purchases"),
    "reject_expense_note": ("staff", "purchases"),
    "reimburse_expense_note": ("staff", "purchases"),
    # taxes: the estimate, the ceilings and the year.
    "get_ceiling_headroom": ("taxes",),
    "get_year_overview": ("taxes",),
    "get_fiscal_estimate": ("taxes",),
    "get_tax_calendar": ("taxes",),
    "set_declared_tax_figure": ("taxes",),
    # The raw figures are `taxes` alone; `deal_id` and `contract_id` are references into
    # their modules, refused by `ID_REFERENCES` below only when the call carries one.
    "simulate_ceiling": ("taxes",),
    # staff: employees and directors with their pay.
    "create_staff_member": ("staff",),
    "update_staff_member": ("staff",),
    "get_staff_member": ("staff",),
    "list_staff": ("staff",),
    "get_payroll_month": ("staff",),
    # collaborators: engagements with freelancers and their availability.
    "create_engagement": ("collaborators",),
    "update_engagement": ("collaborators",),
    "get_engagement": ("collaborators",),
    "get_engagement_report": ("collaborators",),
    "list_engagements": ("collaborators",),
    "preview_engagement": ("collaborators",),
    "preview_framework_agreement": ("collaborators",),
    "cancel_engagement": ("collaborators",),
    "close_engagement": ("collaborators",),
    "send_engagement_for_signature": ("collaborators",),
    "resend_signing_mail": ("collaborators",),
    "refresh_document_signature": ("collaborators",),
    "cancel_framework_agreement": ("collaborators",),
    "record_framework_notice": ("collaborators",),
    "get_freelancer_availability": ("collaborators",),
    "set_freelancer_tax_data": ("collaborators",),
    # cash: bank accounts, their statements and movements. The schedule is the core's, with
    # the balances left out when the module is off.
    "list_bank_accounts": ("cash",),
    "create_bank_account": ("cash",),
    "update_bank_account": ("cash",),
    "archive_bank_account": ("cash",),
    "list_statements": ("cash",),
    "list_bank_movements": ("cash",),
    "get_bank_movement": ("cash",),
    "propose_movement_match": ("cash",),
    # Across modules: all of them, or the tool is not there.
    "convert_deal_to_contract": ("deals", "contracts"),
    "preview_deal_contract": ("deals", "contracts"),
    "get_deal_pnl": ("deals", "time", "purchases"),
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


# A core tool that takes the id of a record of a module, or names the kind of record it is
# about: with that module off the reference is refused with the sentence the REST routes use
# (`ModuleOff`), so an agent cannot write to or read the records of a module that is gone.
# One table, checked by the guard every tool runs in (`server._make_guards`), not an `if` in
# each tool; `test_module_guard.py` fails for a tool that takes such an id and is not here.
ID_REFERENCES: Mapping[str, tuple[tuple[str, ModuleKey], ...]] = {
    "add_note": (("deal_id", "deals"),),
    "create_task": (("deal_id", "deals"),),
    "list_tasks": (("deal_id", "deals"),),
    "create_proforma": (("deal_id", "deals"),),
    "list_invoices": (("deal_id", "deals"),),
    "list_documents": (("deal_id", "deals"),),
    "simulate_ceiling": (("deal_id", "deals"), ("contract_id", "contracts")),
    "create_document_from_template": (("deal_id", "deals"),),
}
# Tools whose argument names the kind of record, mapped to that argument.
KIND_REFERENCES: Mapping[str, str] = {
    "get_timeline": "entity_type",
    "list_gmail_messages": "entity_type",
    "draft_email": "entity_type",
}
KIND_MODULES: Mapping[str, ModuleKey] = {
    "deal": "deals",
    "contract": "contracts",
    "work_unit": "contracts",
    "time_entry": "time",
    "purchase": "purchases",
}


def refuse_off_references(
    tool: str, arguments: Mapping[str, Any], modules: AbstractSet[str] | None
) -> None:
    """Raises `ModuleOff` when `tool` was handed a reference into a module that is off
    (`None` for `modules`: everything is on)."""
    if modules is None:
        return
    needed: list[ModuleKey] = [
        module for param, module in ID_REFERENCES.get(tool, ()) if arguments.get(param)
    ]
    kind = arguments.get(KIND_REFERENCES.get(tool, ""))
    if isinstance(kind, str) and kind in KIND_MODULES:
        needed.append(KIND_MODULES[kind])
    require_in(modules, *needed)
