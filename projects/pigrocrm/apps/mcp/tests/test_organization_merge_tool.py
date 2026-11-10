"""`merge_organizations` over the MCP client (REB-995): on a `full_access` administrator's surface
only, a preview unless it is told otherwise."""

import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from mcp import Client
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.config import Settings
from pigrocrm.core.storage import LocalFileStorage
from pigrocrm_mcp.server import build_server


def _server(session: Session, tmp_path: Path, *, full_access: bool = True) -> Any:
    return build_server(
        lambda: session,
        lambda: Actor(id=None, type="mcp", role="admin", full_access=full_access),  # type: ignore[arg-type]
        LocalFileStorage(tmp_path),
        Settings(_env_file=None, mcp_full_access=full_access),  # type: ignore[call-arg]
    )


def _payload(result: Any) -> dict[str, Any]:
    return result.structured_content or json.loads(result.content[0].text)


async def test_the_tool_is_listed_only_to_a_full_access_token(
    mcp_session: Session, tmp_path: Path
) -> None:
    full = {tool.name for tool in await _server(mcp_session, tmp_path).list_tools()}
    plain = {
        tool.name for tool in await _server(mcp_session, tmp_path, full_access=False).list_tools()
    }
    assert "merge_organizations" in full
    assert "merge_organizations" not in plain


async def test_it_previews_by_default_and_merges_when_told_to(
    mcp_session: Session, tmp_path: Path
) -> None:
    async with Client(_server(mcp_session, tmp_path)) as client:
        ids = []
        for name in ("Acme Srl", "ACME s.r.l."):
            created = await client.call_tool("create_organization", {"legal_name": name})
            assert not created.is_error, created.content
            ids.append(_payload(created)["id"])
        survivor, loser = ids

        preview = await client.call_tool(
            "merge_organizations", {"survivor_id": survivor, "loser_id": loser}
        )
        assert not preview.is_error, preview.content
        assert _payload(preview)["dry_run"] is True
        still = await client.call_tool("get_organization", {"organization_id": loser})
        assert not still.is_error

        done = await client.call_tool(
            "merge_organizations",
            {"survivor_id": survivor, "loser_id": loser, "dry_run": False},
        )
        assert not done.is_error, done.content
        assert _payload(done)["dry_run"] is False
        gone = await client.call_tool("get_organization", {"organization_id": loser})
        assert gone.is_error


async def test_a_token_without_full_access_cannot_call_it_by_name(
    mcp_session: Session, tmp_path: Path
) -> None:
    async with Client(_server(mcp_session, tmp_path, full_access=False)) as client:
        result = await client.call_tool(
            "merge_organizations", {"survivor_id": str(uuid4()), "loser_id": str(uuid4())}
        )
        assert result.is_error
        assert "Unknown tool" in str(result.content)
