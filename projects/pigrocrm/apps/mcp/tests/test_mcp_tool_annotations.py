"""What an agent reads before its first call: every tool says whether it writes, and the
whole list stays small enough to read.

REB-795's audit found no read-only or destructive hint on any of 138 tools, so a client could
not auto-approve a read, and a tool list of 131 KB (about 35k tokens) paid for before the first
call. This file is the guard for both: a new tool without hints fails the first test, a name
whose hints contradict what it does fails the second, and a list that grows past its budget
fails the last two, with the biggest offenders named.
"""

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from mcp import Client

from pigrocrm.core.actor import Actor
from pigrocrm.core.config import Settings
from pigrocrm.core.storage import LocalFileStorage
from pigrocrm_mcp.listing import compact_schema
from pigrocrm_mcp.server import build_server

# The three installations the tool list differs on: nothing configured, the invoice and
# configuration tools opened (`mcp_full_access`), and Google and Slack on top of that.
_GOOGLE_AND_SLACK: dict[str, Any] = {
    "google_client_id": "cid.apps.googleusercontent.com",
    "google_client_secret": "the-secret",
    "google_token_key": "k",
    "public_url": "https://crm.example.it",
    "slack_client_id": "slack-id",
    "slack_client_secret": "slack-secret",
    "slack_token_key": "k",
}
SURFACES: dict[str, dict[str, Any]] = {
    "default": {},
    "full_access": {"mcp_full_access": True},
    "full_access_google_slack": {"mcp_full_access": True, **_GOOGLE_AND_SLACK},
}

# The card's done-when: the default list under 90 KB. Full access opens 19 more tools and is
# allowed to cost what they cost; both numbers are measured by `_listing_bytes`.
# REB-879: 91.5 KB, up from 90 KB, for `add_note`, `update_note` and `delete_note`.
# REB-933: 94 KB and 106 KB, up from 91.5 KB and 105 KB, for `convert_deal_to_contract` and
# `preview_deal_contract`: the first takes the eight fields of a contract and cannot say less,
# the second is how an agent reads what to pass for three of them.
# REB-953: 94.5 KB and 108 KB, up from 94 KB and 106 KB, for `describe_space` (every token) and
# `set_space_profile` and `set_space_modules` (a full-access administrator's): the three have
# no shorter form, the first is how an agent learns why a module's tools are not there.
# REB-971: 95.5 KB and 109 KB, up from 94.5 KB and 108 KB, for `list_inbox_items` and
# `get_inbox_item` (every token's): the list takes four filters and a cursor and says what an
# item's state is, and neither can say less.
# REB-967: 98.2 KB and 111.6 KB, up from 95.5 KB and 109 KB, for `list_due_items`,
# `get_due_item`, `settle_due_item` and `reverse_settlement` (2.6 KB): the four are core and on
# every token's surface, the settlement takes the item's amount, its date and the account's own
# figures, and the list its five filters.
# REB-970: 99.4 KB and 113.4 KB, up from 98.2 KB and 111.9 KB, for `list_duplicate_organizations`
# and the organization fields of design § 1 (roles, the supplier terms and kinds, the customer kind
# and the withholding flag), measured at 99,358 bytes. The fields are in `update_organization`'s
# `changes` schema; `create_organization` takes only `roles`, and the rest is said once in
# `INSTRUCTIONS`. The full-access list also carries `share_organization` on
# `confirm_invoice_import` (measured at 113,377 bytes).
# REB-972: 100.2 KB, up from 99.4 KB, for `propose_inbox_item` and the `classified` filter of
# `list_inbox_items` (an agent's way to propose what a PDF in the inbox says), measured at 100,168.
# REB-984: 101.7 KB, up from 100.2 KB, for `set_inbox_item_kind`, `discard_inbox_item` and
# `mark_inbox_item_duplicate` (every token's, 1.4 KB): the three acts design § 6 classifies as a
# collaborator's an agent may do, measured at 101,612 bytes. The kinds are listed once.
# REB-976: 101.75 KB, up from 101.7 KB: `hub_freelancer_id` in `update_organization`'s `changes`
# schema, +82 bytes. The `collaborators` tools are in neither list: the module starts off.
# REB-993: 104.8 KB, up from 101.75 KB, for `create_purchase`, `update_purchase`,
# `cancel_purchase`, `archive_purchase`, `restore_purchase`, `get_purchase` and `list_purchases`
# in place of the six cost tools (measured at 104,700 bytes): a purchase has a kind, a supplier, a
# VAT recap and the currency, and the two writes list them by name and type through a compact
# schema (no `null` branches, no patterns), which is how an agent learns them.
# REB-1008: 105.25 KB, up from 104.8 KB, for `list_payroll_totals` (every token's, 444 bytes): the
# «Stipendi» line a token that may not read payroll reads in its place, measured at 105,244.
# REB-1022: 106.8 KB, up from 105.25 KB, for `list_recurring_expenses`, `create_recurring_expense`
# and `stop_recurring_expense` (every token's, 1.5 KB): a rule has a supplier, a category, an
# amount, a cadence and two dates, measured at 106,747.
# REB-1032: 107.65 KB, up from 106.8 KB, for `list_fee_occurrences` (every token's, 854 bytes): the
# occurrences of a contract's recurring fee with the date `bill_contract_fee` takes, measured at
# 107,601.
# REB-1035: 107.68 KB, up from 107.65 KB, for `source_id` on `list_due_items` (every token's, 65
# bytes), measured at 107,672.
DEFAULT_BUDGET_BYTES = 107_680
# REB-957: 111.9 KB, up from 111.6 KB, for `skip_space_onboarding` (a full-access
# administrator's), measured at 111,786 bytes; `describe_space`'s `onboarding` fits the default.
# REB-977: 116.2 KB, up from 113.4 KB, for `create_staff_member`, `update_staff_member`,
# `get_staff_member` and `list_staff` (a full-access administrator's, 2.8 KB): pay has a dozen
# fields, and `update_staff_member` lists them by name and type, which is how an agent learns them.
# REB-972: 117.0 KB, up from 116.2 KB, the same tool, measured at 116,956 bytes.
# REB-984: 118.5 KB, up from 117.0 KB, the same three tools, measured at 118,400 bytes.
# REB-976: 118.6 KB, up from 118.5 KB, the same field.
# REB-995: 119.7 KB, up from 118.6 KB, for `merge_organizations` (a full-access administrator's,
# 1.0 KB): the survivor, the loser and the preview switch, and a docstring that says what it
# rewrites, which is the only warning an agent reads.
# REB-1004: 120.2 KB, up from 119.7 KB, for `get_tax_calendar` (a full-access administrator\'s,
# 477 bytes).
# REB-993: 123.3 KB, up from 120.2 KB, the seven purchase tools (+3.0 KB net of the cost tools).
# REB-1006: 124.1 KB, up from 123.3 KB, for `set_declared_tax_figure` (a full-access
# administrator's, three amounts, a tax and a year).
# REB-1008: 125.1 KB, up from 124.1 KB, for `list_payroll_totals` and `get_payroll_month` (a
# full-access administrator's) and the three staff fields `update_staff_member` lists by name and
# type, measured at 125,098.
# REB-1012: 126.4 KB, up from 125.1 KB, for `build_accountant_export` and
# `get_export_download_url` (a full-access administrator's), measured at 126,346.
# REB-1021: 127.0 KB, up from 126.4 KB, for `list_iva_settlements` (a full-access
# administrator's, a year and a company) and the profile's `iva_frequency`, measured at 126,947.
# REB-1022: 128.5 KB, up from 127.0 KB, the same three tools, measured at 128,444.
# REB-1024: 129.8 KB, up from 128.5 KB, for `list_iva_periods` and `get_iva_register` (a full-access
# administrator's: a year, a period, a register and a page), measured at 129,771.
# REB-1032: 131.75 KB, up from 129.8 KB, for `bill_contract_fee` (a full-access administrator's) and
# `list_fee_occurrences` above, measured at 131,724.
# REB-1035: 131.8 KB, up from 131.75 KB, the same parameter and `bucket` on `list_engagements`,
# measured at 131,795.
FULL_ACCESS_BUDGET_BYTES = 131_800

# A name starting with one of these reads. The tools that read without such a name are
# listed, so adding one is a decision somebody wrote down.
_READ_PREFIXES = ("get_", "list_", "search_", "describe_", "preview_")
_READS_WITH_ANOTHER_NAME = {
    "suggest_legal_entity",
    "simulate_ceiling",
    "project_contract_revenue",
    "review_invoice_import",
    "read_document_text",
    "read_drive_file",
    "read_gmail_attachment",
    "discover_gmail_correspondents",
    "suggest_organizations_from_gmail",
}
# What cannot be taken back from the surface: a numbered invoice never leaves the register,
# a discarded proforma is not restored, a recalculation rewrites hours already counted, an
# accepted or refused offer is final.
_IRREVERSIBLE = {
    "issue_invoice",
    "annul_invoice",
    "mark_invoice_transmitted",
    "import_issued_invoice",
    "confirm_invoice_import",
    "declare_invoice_register_gaps",
    "recalculate_rates",
    "discard_proforma",
    "set_offer_state",
    # A won deal becomes one contract and its lines are locked for good: no tool turns it back.
    "convert_deal_to_contract",
    # An inbox item put aside, or marked as a copy: it stays readable, and nothing on this
    # surface takes either back.
    "discard_inbox_item",
    "mark_inbox_item_duplicate",
}
# A `create_*` that is not additive: `close_previous` shortens the rate card in force.
_CREATES_THAT_OVERWRITE = {"create_rate_card"}


def _server(tmp_path: Path, **settings: Any) -> Any:
    return build_server(
        lambda: None,
        # The token carries the flag too (REB-810): a surface that opens the space's switch is
        # measured as a token that was given it sees it.
        lambda: Actor(
            id=None, type="mcp", role="admin", full_access=settings.get("mcp_full_access", False)
        ),
        LocalFileStorage(tmp_path),
        Settings(_env_file=None, **settings),  # type: ignore[call-arg]
    )


@pytest.fixture(params=sorted(SURFACES))
def surface(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[Any]:
    yield _server(tmp_path, **SURFACES[request.param])


async def _tools(server: Any) -> list[Any]:
    async with Client(server) as client:
        return list((await client.list_tools()).tools)


def _listing_bytes(tools: list[Any]) -> int:
    """The tool list as it crosses the wire: compact JSON, UTF-8."""
    payload = [tool.model_dump(by_alias=True, exclude_none=True, mode="json") for tool in tools]
    return len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode())


async def test_every_tool_says_whether_it_reads_and_whether_it_destroys(surface: Any) -> None:
    tools = await _tools(surface)
    assert tools
    unhinted = [
        tool.name
        for tool in tools
        if tool.annotations is None
        or tool.annotations.read_only_hint is None
        or tool.annotations.destructive_hint is None
    ]
    assert not unhinted, (
        f"{unhinted}: add `annotations=READ_ONLY`, `ADDITIVE` or `DESTRUCTIVE` "
        "(pigrocrm_mcp/annotations.py) to the tool's @mcp.tool()"
    )


async def test_the_hints_match_what_the_name_promises(surface: Any) -> None:
    for tool in await _tools(surface):
        hints = tool.annotations
        assert hints is not None
        reads = tool.name.startswith(_READ_PREFIXES) or tool.name in _READS_WITH_ANOTHER_NAME
        assert hints.read_only_hint is reads, f"{tool.name}: read_only_hint={hints.read_only_hint}"
        if reads:
            assert hints.destructive_hint is False, tool.name
        elif tool.name.startswith("update_"):
            assert hints.destructive_hint is True, f"{tool.name} overwrites what was there"
        elif tool.name in _CREATES_THAT_OVERWRITE:
            assert hints.destructive_hint is True, f"{tool.name} can overwrite"
        elif tool.name.startswith(("create_", "archive_", "restore_")):
            assert hints.destructive_hint is False, tool.name


async def test_what_cannot_be_taken_back_is_marked_destructive(tmp_path: Path) -> None:
    tools = {tool.name: tool for tool in await _tools(_server(tmp_path, **SURFACES["full_access"]))}
    assert set(tools) >= _IRREVERSIBLE
    for name in sorted(_IRREVERSIBLE):
        hints = tools[name].annotations
        assert hints is not None and hints.destructive_hint is True, name


def _heaviest(tools: list[Any]) -> str:
    sizes = sorted(((_listing_bytes([tool]), tool.name) for tool in tools), reverse=True)
    return ", ".join(f"{name} {size}" for size, name in sizes[:6])


async def test_the_default_tool_list_fits_its_budget(tmp_path: Path) -> None:
    tools = await _tools(_server(tmp_path))
    size = _listing_bytes(tools)
    assert size < DEFAULT_BUDGET_BYTES, (
        f"{len(tools)} tools cost {size} bytes, budget {DEFAULT_BUDGET_BYTES}; "
        f"heaviest: {_heaviest(tools)}. Shorten a description, or say it once in INSTRUCTIONS."
    )


async def test_the_full_access_tool_list_fits_its_budget(tmp_path: Path) -> None:
    tools = await _tools(_server(tmp_path, **SURFACES["full_access"]))
    size = _listing_bytes(tools)
    assert size < FULL_ACCESS_BUDGET_BYTES, (
        f"{len(tools)} tools cost {size} bytes, budget {FULL_ACCESS_BUDGET_BYTES}; "
        f"heaviest: {_heaviest(tools)}."
    )


async def test_the_listing_carries_no_empty_promises(surface: Any) -> None:
    """`listing.py` removes what no client reads. A `title` is the property name in Title
    Case, an `outputSchema` of any object says nothing, and `default: null` repeats
    `required`; none of them may come back through a new tool. Checked with the same
    schema-aware walk that removes them, so a property *called* `title` is not a finding."""
    for tool in await _tools(surface):
        assert compact_schema(tool.input_schema) == tool.input_schema, tool.name
        assert compact_schema(tool.output_schema) == tool.output_schema, tool.name
        assert tool.output_schema != {"type": "object", "additionalProperties": True}, tool.name


def test_compact_schema_drops_the_keywords_and_keeps_the_values() -> None:
    schema = {
        "title": "get_customerArguments",
        "type": "object",
        "properties": {
            # a property called `title` and one called `default` are names, not keywords
            "title": {"title": "Title", "type": "string"},
            "default": {"anyOf": [{"type": "string"}, {"type": "null"}], "default": None},
            "status": {"enum": ["title", "default"], "default": "title", "type": "string"},
            "lines": {"items": {"title": "Riga", "type": "object"}, "type": "array"},
            "date_from": {"type": "integer", "default": 0},
        },
        "required": ["title"],
    }
    assert compact_schema(schema) == {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "default": {"anyOf": [{"type": "string"}, {"type": "null"}]},
            "status": {"enum": ["title", "default"], "default": "title", "type": "string"},
            "lines": {"items": {"type": "object"}, "type": "array"},
            "date_from": {"type": "integer", "default": 0},
        },
        "required": ["title"],
    }
