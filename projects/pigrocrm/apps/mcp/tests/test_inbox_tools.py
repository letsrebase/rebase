"""REB-971: the inbox over MCP is reads only and carries no bytes. A file is uploaded over the
web or REST and never through a tool; a tool lists and reads the items (metadata, never the
file, never the storage key), and the access rule of the inbox holds for a token like it does
for a session."""

import json
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from mcp import Client
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.auth.models import User
from pigrocrm.core.config import Settings
from pigrocrm.core.inbox.uploads import InboxUploads
from pigrocrm.core.storage import LocalFileStorage
from pigrocrm_mcp.provenance import PROVENANCE_TEXT_FREE
from pigrocrm_mcp.server import build_server

INVOICE = (
    Path(__file__).resolve().parents[3]
    / "packages"
    / "core"
    / "tests"
    / "fixtures"
    / "fatturapa"
    / "fpr-12-march-consulting.xml"
).read_bytes()
PDF = b"%PDF-1.7\n1 0 obj\n<<>>\nendobj\n"


def _payload(result: Any) -> dict[str, Any]:
    return result.structured_content or json.loads(result.content[0].text)


def _member(session: Session, key: str, role: str) -> User:
    user = User(email=f"{key}@example.test", password_hash="x", name=key.title(), role=role)
    session.add(user)
    session.flush()
    return user


def _server_of(session: Session, user: User, tmp_path: Path) -> Any:
    agent = Actor(id=user.id, type="mcp", role=user.role)  # type: ignore[arg-type]
    return build_server(
        lambda: session,
        lambda: agent,
        LocalFileStorage(tmp_path / "agent"),
        Settings(_env_file=None),  # type: ignore[call-arg]
    )


@pytest.fixture
def anna(mcp_session: Session) -> User:
    return _member(mcp_session, "anna", "admin")


@pytest.fixture
def marco(mcp_session: Session) -> User:
    return _member(mcp_session, "marco", "collaborator")


@pytest.fixture
def giulia(mcp_session: Session) -> User:
    return _member(mcp_session, "giulia", "collaborator")


@pytest.fixture
def uploaded(mcp_session: Session, marco: User, tmp_path: Path) -> list[dict[str, Any]]:
    """What Marco uploaded over REST: an invoice, its duplicate and a PDF."""
    inbox = InboxUploads(mcp_session, LocalFileStorage(tmp_path / "store"))
    items = inbox.upload(
        [("fattura.xml", INVOICE), ("copia.xml", INVOICE), ("estratto.pdf", PDF)],
        Actor(id=marco.id, type="user", role="collaborator"),
    ).items
    return [item.model_dump(mode="json") for item in items]


async def test_the_inbox_has_two_read_only_tools_and_no_upload(server: Any) -> None:
    async with Client(server) as client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}
    assert {"list_inbox_items", "get_inbox_item"} <= set(tools)
    for name in ("list_inbox_items", "get_inbox_item"):
        assert tools[name].annotations is not None
        assert tools[name].annotations.read_only_hint is True
    assert not [name for name in tools if "upload" in name]
    # No tool of the server takes the bytes of a file: the inbox's reads have none to take.
    for name in ("list_inbox_items", "get_inbox_item"):
        properties = tools[name].input_schema.get("properties", {})
        assert not {"content", "data", "file", "bytes", "base64"} & set(properties)


async def test_the_list_answers_metadata_newest_first_and_never_the_file(
    mcp_session: Session, anna: User, uploaded: list[dict[str, Any]], tmp_path: Path
) -> None:
    async with Client(_server_of(mcp_session, anna, tmp_path)) as client:
        result = await client.call_tool("list_inbox_items", {})
    assert not result.is_error, result.content[0].text
    payload = _payload(result)
    assert [i["original_name"] for i in payload["items"]] == [
        "estratto.pdf",
        "copia.xml",
        "fattura.xml",
    ]
    assert payload["next_cursor"] is None
    # A file name is written by whoever sent the file: the answer says so.
    assert payload["provenance"] == PROVENANCE_TEXT_FREE
    for item in payload["items"]:
        assert "storage_key" not in item
        assert not {"content", "data", "bytes"} & set(item)
    duplicate = payload["items"][1]
    assert (duplicate["state"], duplicate["duplicate_of_id"]) == ("duplicate", uploaded[0]["id"])


async def test_the_list_filters_and_pages(
    mcp_session: Session, anna: User, uploaded: list[dict[str, Any]], tmp_path: Path
) -> None:
    async with Client(_server_of(mcp_session, anna, tmp_path)) as client:
        duplicates = _payload(await client.call_tool("list_inbox_items", {"state": "duplicate"}))
        first = _payload(await client.call_tool("list_inbox_items", {"limit": 2}))
        second = _payload(
            await client.call_tool("list_inbox_items", {"limit": 2, "cursor": first["next_cursor"]})
        )
    assert [i["original_name"] for i in duplicates["items"]] == ["copia.xml"]
    assert len(first["items"]) == 2 and first["next_cursor"] is not None
    assert [i["original_name"] for i in second["items"]] == ["fattura.xml"]


async def test_a_state_that_does_not_exist_is_refused_in_words(
    mcp_session: Session, anna: User, tmp_path: Path
) -> None:
    async with Client(_server_of(mcp_session, anna, tmp_path)) as client:
        result = await client.call_tool("list_inbox_items", {"state": "nonsense"})
    assert result.is_error
    assert "new" in result.content[0].text and "duplicate" in result.content[0].text


async def test_one_item_is_read_with_its_hash_and_where_it_came_from(
    mcp_session: Session, anna: User, uploaded: list[dict[str, Any]], tmp_path: Path
) -> None:
    async with Client(_server_of(mcp_session, anna, tmp_path)) as client:
        result = await client.call_tool("get_inbox_item", {"item_id": uploaded[1]["id"]})
    assert not result.is_error, result.content[0].text
    item = _payload(result)
    assert item["sha256"] == uploaded[1]["sha256"]
    assert item["duplicate_of_id"] == uploaded[0]["id"]
    assert item["has_file"] is False
    assert "storage_key" not in item


async def test_an_item_that_does_not_exist_is_not_found(
    mcp_session: Session, anna: User, tmp_path: Path
) -> None:
    async with Client(_server_of(mcp_session, anna, tmp_path)) as client:
        result = await client.call_tool("get_inbox_item", {"item_id": str(uuid4())})
    assert result.is_error
    assert "non trovato" in result.content[0].text


async def test_a_token_sees_the_unclassified_items_of_its_owner_and_an_admins_see_all(
    mcp_session: Session,
    anna: User,
    marco: User,
    giulia: User,
    uploaded: list[dict[str, Any]],
    tmp_path: Path,
) -> None:
    async with Client(_server_of(mcp_session, marco, tmp_path)) as client:
        mine = _payload(await client.call_tool("list_inbox_items", {}))
    async with Client(_server_of(mcp_session, giulia, tmp_path)) as client:
        theirs = _payload(await client.call_tool("list_inbox_items", {}))
        refused = await client.call_tool("get_inbox_item", {"item_id": uploaded[0]["id"]})
    async with Client(_server_of(mcp_session, anna, tmp_path)) as client:
        everything = _payload(await client.call_tool("list_inbox_items", {}))
    assert len(mine["items"]) == 3
    assert theirs["items"] == []
    assert refused.is_error and "non trovato" in refused.content[0].text
    assert len(everything["items"]) == 3
