"""The bank tools over the MCP client (REB-1025): `list_bank_accounts`, `create_bank_account`,
`update_bank_account`, `archive_bank_account`, `list_statements` and `list_bank_movements` exist
only on a `full_access` token's surface and only while the `cash` module is on, and even then an
administrator's alone. The file upload and the revert are REST only."""

import json
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from mcp import Client
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor, Role
from pigrocrm.core.bank.statements import StatementImportService
from pigrocrm.core.config import Settings
from pigrocrm.core.storage import LocalFileStorage
from pigrocrm_mcp.provenance import PROVENANCE_TEXT_FREE
from pigrocrm_mcp.server import build_server

TOOLS = {
    "list_bank_accounts",
    "create_bank_account",
    "update_bank_account",
    "archive_bank_account",
    "list_statements",
    "list_bank_movements",
}
IBAN = "IT60X0542811101000000123456"
CAMT = (
    Path(__file__).resolve().parents[3]
    / "packages"
    / "core"
    / "tests"
    / "fixtures"
    / "camt053"
    / "statement_001_02.xml"
)


def _server(
    session: Session,
    tmp_path: Path,
    *,
    full_access: bool = True,
    role: Role = "admin",
    **settings: Any,
) -> Any:
    return build_server(
        lambda: session,
        lambda: Actor(id=uuid4(), type="mcp", role=role, full_access=full_access),
        LocalFileStorage(tmp_path),
        Settings(_env_file=None, mcp_full_access=full_access, **settings),  # type: ignore[call-arg]
    )


def _payload(result: Any) -> dict[str, Any]:
    return result.structured_content or json.loads(result.content[0].text)


async def _create(client: Client, **extra: Any) -> dict[str, Any]:
    result = await client.call_tool(
        "create_bank_account",
        {
            "name": "Main",
            "bank": "Banco BPM",
            "iban": IBAN,
            "opening_balance": "1000.00",
            "opening_balance_date": "2026-09-30",
            **extra,
        },
    )
    assert not result.is_error, result.content
    return _payload(result)


async def test_the_tools_are_listed_only_to_a_full_access_token(
    mcp_session: Session, tmp_path: Path
) -> None:
    full = {tool.name for tool in await _server(mcp_session, tmp_path).list_tools()}
    plain = {
        tool.name for tool in await _server(mcp_session, tmp_path, full_access=False).list_tools()
    }
    assert full >= TOOLS
    assert not TOOLS & plain


async def test_the_tools_leave_with_the_module_and_come_back_with_it(
    mcp_session: Session, tmp_path: Path
) -> None:
    off = {
        tool.name for tool in await _server(mcp_session, tmp_path, module_cash=False).list_tools()
    }
    on = {tool.name for tool in await _server(mcp_session, tmp_path, module_cash=True).list_tools()}
    assert not TOOLS & off
    assert on >= TOOLS


async def test_a_token_without_full_access_cannot_call_them_by_name(
    mcp_session: Session, tmp_path: Path
) -> None:
    async with Client(_server(mcp_session, tmp_path, full_access=False)) as client:
        for name, arguments in (
            ("list_bank_accounts", {}),
            ("create_bank_account", {"name": "A", "bank": "B", "iban": IBAN}),
            ("list_bank_movements", {"account_id": str(uuid4())}),
        ):
            result = await client.call_tool(name, arguments)
            assert result.is_error, name
            assert "Unknown tool" in str(result.content), name


async def test_a_full_access_admin_creates_lists_updates_and_archives_an_account(
    mcp_session: Session, tmp_path: Path
) -> None:
    async with Client(_server(mcp_session, tmp_path)) as client:
        made = await _create(client, cadence="quarterly", senders=["Banca@Example.it"])
        assert (made["iban"], made["cadence"], made["senders"], made["balance"]) == (
            IBAN,
            "quarterly",
            ["banca@example.it"],
            "1000.00",
        )
        assert made["provenance"] == PROVENANCE_TEXT_FREE
        listed = _payload(await client.call_tool("list_bank_accounts", {}))
        assert [a["id"] for a in listed["bank_accounts"]] == [made["id"]]
        updated = _payload(
            await client.call_tool(
                "update_bank_account",
                {"account_id": made["id"], "changes": {"name": "Operating"}},
            )
        )
        assert (updated["name"], updated["changed"]) == ("Operating", ["name"])
        archived = _payload(
            await client.call_tool("archive_bank_account", {"account_id": made["id"]})
        )
        assert archived["archived_at"] is not None
        assert _payload(await client.call_tool("list_bank_accounts", {}))["bank_accounts"] == []
        again = _payload(await client.call_tool("list_bank_accounts", {"include_archived": True}))
        assert [a["id"] for a in again["bank_accounts"]] == [made["id"]]


async def test_an_update_with_nothing_to_change_and_a_bad_iban_are_refused_in_words(
    mcp_session: Session, tmp_path: Path
) -> None:
    async with Client(_server(mcp_session, tmp_path)) as client:
        made = await _create(client)
        empty = await client.call_tool(
            "update_bank_account", {"account_id": made["id"], "changes": {}}
        )
        assert empty.is_error and "no field to change" in str(empty.content)
        bad = await client.call_tool(
            "create_bank_account", {"name": "A", "bank": "B", "iban": "IT60X0542811101000000123457"}
        )
        assert bad.is_error and "IBAN non valido" in str(bad.content)
        duplicate = await client.call_tool(
            "create_bank_account", {"name": "A", "bank": "B", "iban": IBAN}
        )
        assert duplicate.is_error and "esiste gia' un conto con questo IBAN" in str(
            duplicate.content
        )


async def test_the_statements_and_the_movements_are_read_with_their_checks_and_flags(
    mcp_session: Session, tmp_path: Path
) -> None:
    async with Client(_server(mcp_session, tmp_path)) as client:
        made = await _create(client)
        StatementImportService(mcp_session).import_file(
            UUID(made["id"]),
            CAMT.read_bytes(),
            Actor(id=None, type="system", role="admin"),
        )
        statements = _payload(await client.call_tool("list_statements", {"account_id": made["id"]}))
        assert statements["total_count"] == 1
        statement = statements["items"][0]
        assert (statement["movement_count"], statement["check"]["ok"]) == (4, True)
        assert statements["provenance"] == PROVENANCE_TEXT_FREE
        movements = _payload(
            await client.call_tool(
                "list_bank_movements",
                {"account_id": made["id"], "state": "unmatched", "limit": 3},
            )
        )
        assert (len(movements["items"]), movements["total_count"]) == (3, 4)
        assert movements["next_cursor"] is not None
        dates = [item["booking_date"] for item in movements["items"]]
        assert dates == sorted(dates, reverse=True)
        assert all(item["amount"].endswith(".00") for item in movements["items"])
        assert movements["provenance"] == PROVENANCE_TEXT_FREE
        refused = await client.call_tool(
            "list_bank_movements", {"account_id": made["id"], "state": "bogus"}
        )
        assert refused.is_error


async def test_a_member_who_is_not_an_administrator_is_refused(
    mcp_session: Session, tmp_path: Path
) -> None:
    async with Client(_server(mcp_session, tmp_path, role="collaborator")) as client:
        result = await client.call_tool("list_bank_accounts", {})
        assert result.is_error
