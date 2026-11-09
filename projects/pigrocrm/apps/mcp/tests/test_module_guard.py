"""The guard of a module that is off, on the MCP surface (spec 2026-11-09, A2, B2, B8; REB-953).

A tool of a switched-off module is not registered: not listed, and «Unknown tool» when called by
name. A tool that spans modules (`convert_deal_to_contract`, `get_deal_pnl`) exists only with
all of them on. The server is rebuilt when a space's settings change, so each test builds the
server for the settings it wants and the records, in the one database, are the same across
them: off hides, it never touches.
"""

from pathlib import Path
from typing import Any

import pytest
from mcp import Client
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.config import Settings
from pigrocrm.core.modules import MODULE_KEYS, enabled_modules
from pigrocrm.core.pipeline.service import PipelineService
from pigrocrm.core.storage import LocalFileStorage
from pigrocrm_mcp.modules import PROMPT_MODULES, TOOL_MODULES
from pigrocrm_mcp.server import build_server

SETUP = Actor(id=None, type="system", role="admin")
OPEN = {"mcp_full_access": True}


def _settings(**values: Any) -> Settings:
    return Settings(_env_file=None, **values)  # type: ignore[call-arg]


def _server(
    session: Session,
    tmp_path: Path,
    settings: Settings,
    *,
    role: str = "admin",
    full_access: bool = False,
) -> Any:
    return build_server(
        lambda: session,
        lambda: Actor(id=None, type="mcp", role=role, full_access=full_access),  # type: ignore[arg-type]
        LocalFileStorage(tmp_path),
        settings,
    )


async def _names(server: Any) -> set[str]:
    return {tool.name for tool in await server.list_tools()}


def _tools_of(module: str) -> set[str]:
    return {name for name, needed in TOOL_MODULES.items() if module in needed}


# Every tool that belongs to no module, written out so that a new tool has to be classified
# on purpose: in `TOOL_MODULES` if a module owns it, here if it is the core's.
CORE_TOOLS = frozenset(
    {
        "describe_schema",
        "create_customer",
        "suggest_legal_entity",
        "update_customer",
        "get_customer",
        "search_customers",
        "archive_customer",
        "restore_customer",
        "create_person",
        "update_person",
        "get_person",
        "search_people",
        "archive_person",
        "restore_person",
        "add_note",
        "update_note",
        "delete_note",
        "get_proposal",
        "list_proposals",
        "get_timeline",
        "search_everything",
        "describe_automations",
        "get_economic_dashboard",
        "get_receivables_dashboard",
        "get_operational_dashboard",
        "list_documents",
        "get_document",
        "get_document_versions",
        "read_document_text",
        "list_templates",
        "describe_template",
        "preview_template",
        "create_document_from_template",
        "regenerate_document_version",
        "archive_document",
        "restore_document",
        "list_invoices",
        "list_invoice_register_gaps",
        "get_invoice",
        "create_proforma",
        "update_proforma",
        "replace_proforma_lines",
        "confirm_proforma",
        "discard_proforma",
        "render_proforma_pdf",
        "get_invoice_xml_url",
        "set_invoice_payment_state",
        "list_members",
        "list_legal_entities",
        "describe_fiscal_profile",
        "describe_legal_entity",
        "update_fiscal_profile",
        "create_legal_entity",
        "update_legal_entity",
        "list_period_locks",
        "get_period_pnl",
        "create_task",
        "update_task",
        "get_task",
        "complete_task",
        "cancel_task",
        "reopen_task",
        "archive_task",
        "restore_task",
        "list_tasks",
        "get_tasks_today",
        "get_calendar_month",
        "list_reminders",
        "list_reminder_types",
        "get_reminder_preferences",
        "set_reminder_preference",
        "reset_reminder_preference",
        "acknowledge_reminder",
        "snooze_reminder",
        "list_my_reminder_state",
        "describe_scheduled_jobs",
        "describe_space",
        "set_space_profile",
        "set_space_modules",
        "issue_invoice",
        "annul_invoice",
        "mark_invoice_transmitted",
        "export_invoice_xml",
        "review_invoice_import",
        "confirm_invoice_import",
        "import_issued_invoice",
        "declare_invoice_register_gaps",
        "close_period",
        "reopen_period",
    }
)


async def test_every_registered_tool_is_in_a_module_or_declared_core(
    mcp_session: Session, tmp_path: Path
) -> None:
    names = await _names(_server(mcp_session, tmp_path, _settings(**OPEN), full_access=True))

    unclassified = names - set(TOOL_MODULES) - CORE_TOOLS
    assert not unclassified, (
        f"{sorted(unclassified)} are in no module: add them to `TOOL_MODULES` in "
        "`pigrocrm_mcp/modules.py`, or to `CORE_TOOLS` here if the core owns them"
    )
    assert not set(TOOL_MODULES) & CORE_TOOLS
    # Nothing in the table that no longer exists, so a rename cannot leave a tool unguarded.
    assert set(TOOL_MODULES) <= names, sorted(set(TOOL_MODULES) - names)
    assert names >= CORE_TOOLS, sorted(CORE_TOOLS - names)


async def test_with_every_module_on_every_tool_is_listed(
    mcp_session: Session, tmp_path: Path
) -> None:
    on = await _names(_server(mcp_session, tmp_path, _settings(), full_access=False))

    assert {"create_deal", "get_deal", "log_time", "create_cost", "get_contract"} <= on
    assert {"convert_deal_to_contract", "get_deal_pnl", "get_year_overview"} <= on


@pytest.mark.parametrize("module", MODULE_KEYS)
async def test_a_module_that_is_off_has_no_tool_and_its_name_is_unknown(
    mcp_session: Session, tmp_path: Path, module: str
) -> None:
    """Each module in turn, with what depends on it going off too (`time` needs `deals`)."""
    off = {module: False} if module != "deals" else {"deals": False, "time": False}
    settings = _settings(**{f"module_{key}": value for key, value in off.items()})
    server = _server(mcp_session, tmp_path, settings)
    names = await _names(server)

    gone = set().union(*(_tools_of(key) for key in off))
    assert gone, "a module with no tool would make this test vacuous"
    assert not names & gone
    assert "describe_space" in names and "describe_schema" in names
    async with Client(server) as client:
        sample = sorted(gone)[0]
        result = await client.call_tool(sample, {})
    assert result.is_error
    assert result.content[0].text == f"Unknown tool: {sample}"  # type: ignore[union-attr]


async def test_a_tool_across_modules_exists_only_with_all_of_them_on(
    mcp_session: Session, tmp_path: Path
) -> None:
    only_contracts_off = await _names(
        _server(mcp_session, tmp_path, _settings(module_contracts=False))
    )
    assert "create_deal" in only_contracts_off
    assert "convert_deal_to_contract" not in only_contracts_off
    assert "preview_deal_contract" not in only_contracts_off

    only_costs_off = await _names(_server(mcp_session, tmp_path, _settings(module_costs=False)))
    assert {"get_deal", "log_time"} <= only_costs_off
    assert "get_deal_pnl" not in only_costs_off

    only_time_off = await _names(_server(mcp_session, tmp_path, _settings(module_time=False)))
    assert "get_deal_time_summary" not in only_time_off
    assert "get_budget_vs_actual" not in only_time_off
    assert "get_deal" in only_time_off


async def test_the_profile_decides_what_a_space_with_no_switch_of_its_own_lists(
    mcp_session: Session, tmp_path: Path
) -> None:
    solo = await _names(_server(mcp_session, tmp_path, _settings(space_profile="solo")))
    company = await _names(_server(mcp_session, tmp_path, _settings(space_profile="company")))
    unchosen = await _names(_server(mcp_session, tmp_path, _settings()))

    assert not solo & _tools_of("deals") and not solo & _tools_of("time")
    assert solo >= {"create_contract", "create_cost", "get_year_overview"}
    assert "convert_deal_to_contract" not in solo
    assert company == unchosen
    # An explicit switch outranks the profile.
    kept = await _names(
        _server(mcp_session, tmp_path, _settings(space_profile="solo", module_deals=True))
    )
    assert {"create_deal", "get_deal", "convert_deal_to_contract"} <= kept
    assert "log_time" not in kept


async def test_resources_and_prompts_of_a_module_that_is_off_are_not_registered(
    mcp_session: Session, tmp_path: Path
) -> None:
    on = _server(mcp_session, tmp_path, _settings())
    off = _server(mcp_session, tmp_path, _settings(module_time=False, module_deals=False))

    assert "deal://{deal_id}" in {t.uri_template for t in await on.list_resource_templates()}
    assert "deal://{deal_id}" not in {t.uri_template for t in await off.list_resource_templates()}
    assert {p.name for p in await on.list_prompts()} >= set(PROMPT_MODULES)
    assert not {p.name for p in await off.list_prompts()} & set(PROMPT_MODULES)


async def test_off_hides_the_deals_and_on_finds_them_as_they_were(
    mcp_session: Session, tmp_path: Path
) -> None:
    PipelineService(mcp_session).seed_defaults(SETUP)
    async with Client(_server(mcp_session, tmp_path, _settings())) as client:
        customer = await client.call_tool("create_customer", {"legal_name": "ACME"})
        customer_id = (customer.structured_content or {})["id"]
        created = await client.call_tool(
            "create_deal", {"name": "Sito", "customer_id": customer_id}
        )
        deal_id = (created.structured_content or {})["id"]

    switched_off = _settings(module_deals=False, module_time=False)
    async with Client(_server(mcp_session, tmp_path, switched_off)) as client:
        refused = await client.call_tool("get_deal", {"deal_id": deal_id})
        assert refused.is_error
        assert refused.content[0].text == "Unknown tool: get_deal"  # type: ignore[union-attr]
        # The customer's own card no longer points into deals.
        card = (await client.read_resource(f"customer://{customer_id}")).contents[0].text
        assert "## Deal" not in card

    async with Client(_server(mcp_session, tmp_path, _settings())) as client:
        found = await client.call_tool("get_deal", {"deal_id": deal_id})
        assert not found.is_error
        assert (found.structured_content or {})["name"] == "Sito"
        card = (await client.read_resource(f"customer://{customer_id}")).contents[0].text
        assert "Sito" in card


# -- who sees and who writes ------------------------------------------------------------


async def test_every_token_reads_the_space_and_only_a_full_access_one_is_shown_the_writes(
    mcp_session: Session, tmp_path: Path
) -> None:
    plain = await _names(_server(mcp_session, tmp_path, _settings(**OPEN), full_access=False))
    full = await _names(_server(mcp_session, tmp_path, _settings(**OPEN), full_access=True))
    collaborator = await _names(
        _server(mcp_session, tmp_path, _settings(), role="collaborator", full_access=False)
    )

    assert "describe_space" in plain and "describe_space" in collaborator
    assert not plain & {"set_space_profile", "set_space_modules"}
    assert {"set_space_profile", "set_space_modules"} <= full


async def test_describe_space_says_what_is_on_and_what_holds_data(
    mcp_session: Session, tmp_path: Path
) -> None:
    async with Client(_server(mcp_session, tmp_path, _settings(space_profile="solo"))) as client:
        space = (await client.call_tool("describe_space", {})).structured_content or {}

    assert space["profile"] == "solo"
    modules = {m["key"]: m for m in space["modules"]}
    assert modules["deals"]["enabled"] is False and modules["contracts"]["enabled"] is True
    assert modules["time"]["requires"] == ["deals"]


async def test_set_space_modules_by_an_admin_token_switches_and_a_collaborators_is_refused(
    mcp_session: Session, tmp_path: Path
) -> None:
    settings = _settings(**OPEN)
    admin = _server(mcp_session, tmp_path, settings, full_access=True)
    collaborator = _server(mcp_session, tmp_path, settings, role="collaborator", full_access=True)

    async with Client(collaborator) as client:
        refused = await client.call_tool("set_space_modules", {"off": ["costs"]})
    assert refused.is_error
    async with Client(admin) as client:
        switched = await client.call_tool("set_space_modules", {"off": ["costs"]})
        assert not switched.is_error, switched.content
        described = (await client.call_tool("describe_space", {})).structured_content or {}
    assert {m["key"]: m["enabled"] for m in described["modules"]}["costs"] is False


async def test_set_space_profile_answers_the_diff_and_applies_nothing_without_confirmation(
    mcp_session: Session, tmp_path: Path
) -> None:
    admin = _server(mcp_session, tmp_path, _settings(**OPEN), full_access=True)

    async with Client(admin) as client:
        proposal = (
            await client.call_tool("set_space_profile", {"profile": "solo"})
        ).structured_content or {}
        unchanged = (await client.call_tool("describe_space", {})).structured_content or {}
        applied = (
            await client.call_tool("set_space_profile", {"profile": "solo", "confirm": True})
        ).structured_content or {}
        after = (await client.call_tool("describe_space", {})).structured_content or {}

    assert proposal["applied"] is False
    assert {c["module"] for c in proposal["changes"]} == {"deals", "time"}
    assert unchanged["profile"] is None
    assert applied["applied"] is True
    assert after["profile"] == "solo"
    assert {m["key"]: m["stored"] for m in after["modules"]}["costs"] is True


async def test_set_space_modules_refuses_a_module_with_data_the_call_does_not_name(
    mcp_session: Session, tmp_path: Path
) -> None:
    PipelineService(mcp_session).seed_defaults(SETUP)
    admin = _server(mcp_session, tmp_path, _settings(**OPEN), full_access=True)
    async with Client(admin) as client:
        customer = await client.call_tool("create_customer", {"legal_name": "ACME"})
        await client.call_tool(
            "create_deal",
            {"name": "Sito", "customer_id": (customer.structured_content or {})["id"]},
        )

        refused = await client.call_tool("set_space_modules", {"off": ["time", "deals"]})
        assert refused.is_error
        assert "confirm_off" in refused.content[0].text  # type: ignore[union-attr]
        allowed = await client.call_tool(
            "set_space_modules", {"off": ["time", "deals"], "confirm_off": ["deals"]}
        )
        assert not allowed.is_error, allowed.content


def test_the_resolver_reads_a_stored_switch_over_the_profile_and_a_dependency_over_both() -> None:
    assert enabled_modules(_settings(space_profile="solo")) == {"contracts", "costs", "taxes"}
    assert enabled_modules(_settings(space_profile="solo", module_deals=True)) == {
        "deals",
        "contracts",
        "costs",
        "taxes",
    }
    # A hand-written row cannot make `time` live without `deals`.
    assert "time" not in enabled_modules(_settings(module_deals=False, module_time=True))


# -- what a core tool leaves out of a module that is off ------------------------------------


async def test_the_period_pnl_tool_carries_no_labour_or_hours_while_time_is_off(
    mcp_session: Session, tmp_path: Path
) -> None:
    arguments = {"date_from": "2026-01-01", "date_to": "2026-01-31"}
    hours = ("accrued_value", "billable_uninvoiced_hours", "hours_without_rate")

    async with Client(_server(mcp_session, tmp_path, _settings())) as client:
        on = (await client.call_tool("get_period_pnl", arguments)).structured_content or {}
    assert on["closed"]["work_cost"] is not None
    assert all(on[field] is not None for field in hours)

    async with Client(_server(mcp_session, tmp_path, _settings(module_time=False))) as client:
        off = (await client.call_tool("get_period_pnl", arguments)).structured_content or {}
    assert off["closed"]["work_cost"] is None
    assert off["in_progress"]["work_cost"] is None
    assert all(off[field] is None for field in hours)


async def test_the_reminder_catalogue_and_preferences_leave_out_a_module_that_is_off(
    mcp_session: Session, tmp_path: Path
) -> None:
    async def listed(settings: Settings) -> tuple[set[str], set[str]]:
        async with Client(_server(mcp_session, tmp_path, settings)) as client:
            types = (await client.call_tool("list_reminder_types", {})).structured_content or {}
            read = (await client.call_tool("get_reminder_preferences", {})).structured_content or {}
        return (
            {item["key"] for item in types["result"]},
            {item["type"] for item in read["effective"]},
        )

    for names in await listed(_settings()):
        assert {"deal_stale", "timer_running_long", "contract_expiring"} <= names

    for names in await listed(_settings(module_deals=False, module_time=False)):
        assert not names & {"deal_stale", "deal_past_close", "timer_running_long"}
        assert {"task_due", "contract_expiring"} <= names
