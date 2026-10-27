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
DEFAULT_BUDGET_BYTES = 90_000
FULL_ACCESS_BUDGET_BYTES = 105_000

# A name starting with one of these reads. The tools that read without such a name are
# listed, so adding one is a decision somebody wrote down.
_READ_PREFIXES = ("get_", "list_", "search_", "describe_", "preview_")
_READS_WITH_ANOTHER_NAME = {
    "propose_azienda",
    "simulate_ceiling",
    "project_contract_revenue",
    "review_invoice_import",
    "read_document_text",
    "read_drive_file",
    "read_gmail_attachment",
    "discover_gmail_correspondents",
    "suggest_customers_from_gmail",
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
}
# A `create_*` that is not additive: `chiudi_precedente` shortens the rate card in force.
_CREATES_THAT_OVERWRITE = {"create_rate_card"}


def _server(tmp_path: Path, **settings: Any) -> Any:
    return build_server(
        lambda: None,
        lambda: Actor(id=None, type="mcp", role="admin"),
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
            "stato": {"enum": ["title", "default"], "default": "title", "type": "string"},
            "righe": {"items": {"title": "Riga", "type": "object"}, "type": "array"},
            "da": {"type": "integer", "default": 0},
        },
        "required": ["title"],
    }
    assert compact_schema(schema) == {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "default": {"anyOf": [{"type": "string"}, {"type": "null"}]},
            "stato": {"enum": ["title", "default"], "default": "title", "type": "string"},
            "righe": {"items": {"type": "object"}, "type": "array"},
            "da": {"type": "integer", "default": 0},
        },
        "required": ["title"],
    }
