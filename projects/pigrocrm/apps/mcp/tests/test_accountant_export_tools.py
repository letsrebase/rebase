"""`build_accountant_export`, `get_export_download_url` (REB-1012) and `get_export_checklist`
(REB-1048) over the MCP client: on a `full_access` token's surface only, an administrator's alone;
the first two answer a REST path, never bytes, and the checklist counts what waits."""

import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from mcp import Client
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor, Role
from pigrocrm.core.config import Settings
from pigrocrm.core.storage import LocalFileStorage
from pigrocrm_mcp.server import build_server

BUILD = "build_accountant_export"
FETCH = "get_export_download_url"
CHECKLIST = "get_export_checklist"
ARGUMENTS = {"date_from": "2026-01-01", "date_to": "2026-03-31"}


def _server(
    session: Session, tmp_path: Path, *, full_access: bool = True, role: Role = "admin"
) -> Any:
    return build_server(
        lambda: session,
        lambda: Actor(id=uuid4(), type="mcp", role=role, full_access=full_access),
        LocalFileStorage(tmp_path),
        Settings(_env_file=None, mcp_full_access=full_access),  # type: ignore[call-arg]
    )


def _payload(result: Any) -> dict[str, Any]:
    return result.structured_content or json.loads(result.content[0].text)


async def test_the_tools_are_listed_only_to_a_full_access_token(
    mcp_session: Session, tmp_path: Path
) -> None:
    full = {tool.name for tool in await _server(mcp_session, tmp_path).list_tools()}
    plain = {
        tool.name for tool in await _server(mcp_session, tmp_path, full_access=False).list_tools()
    }
    assert {BUILD, FETCH, CHECKLIST} <= full
    assert not {BUILD, FETCH, CHECKLIST} & plain


async def test_a_token_without_full_access_cannot_call_them_by_name(
    mcp_session: Session, tmp_path: Path
) -> None:
    async with Client(_server(mcp_session, tmp_path, full_access=False)) as client:
        result = await client.call_tool(BUILD, ARGUMENTS)
    assert result.is_error
    assert "Unknown tool" in str(result.content)


async def test_a_full_access_admin_builds_and_gets_a_path_never_bytes(
    mcp_session: Session, tmp_path: Path
) -> None:
    async with Client(_server(mcp_session, tmp_path)) as client:
        built = await client.call_tool(BUILD, ARGUMENTS)
        assert not built.is_error, built
        export = _payload(built)
        fetched = _payload(await client.call_tool(FETCH, {"export_id": export["id"]}))

    path = f"/api/accountant-export/exports/{export['id']}/parts/1"
    assert export["parts"][0]["download_url"] == path
    assert fetched["parts"][0]["download_url"] == path
    assert "PK" not in json.dumps(fetched)  # no ZIP bytes anywhere in the answer
    assert list(tmp_path.rglob("commercialista-*.zip"))  # the file went to the storage


async def test_a_collaborator_with_full_access_is_refused(
    mcp_session: Session, tmp_path: Path
) -> None:
    async with Client(_server(mcp_session, tmp_path, role="collaborator")) as client:
        result = await client.call_tool(BUILD, ARGUMENTS)
    assert result.is_error


async def test_the_checklist_is_a_full_access_admins_and_answers_counts(
    mcp_session: Session, tmp_path: Path
) -> None:
    async with Client(_server(mcp_session, tmp_path)) as client:
        read = await client.call_tool(CHECKLIST, ARGUMENTS)
        with_pay = await client.call_tool(CHECKLIST, {**ARGUMENTS, "payroll_parts": ["payslips"]})
        changes = await client.call_tool(BUILD, {**ARGUMENTS, "kind": "payroll_changes"})
    assert not read.is_error, read
    checks = {check["key"]: check for check in _payload(read)["checks"]}
    assert checks["inbox"]["state"] in {"ok", "open"} and "payroll" not in checks
    assert "payroll" in {check["key"] for check in _payload(with_pay)["checks"]}
    assert not changes.is_error, changes
    assert _payload(changes)["kind"] == "payroll_changes" and _payload(changes)["payroll"] is True


async def test_the_checklist_refuses_a_collaborator_and_a_token_without_full_access(
    mcp_session: Session, tmp_path: Path
) -> None:
    async with Client(_server(mcp_session, tmp_path, role="collaborator")) as client:
        assert (await client.call_tool(CHECKLIST, ARGUMENTS)).is_error
    async with Client(_server(mcp_session, tmp_path, full_access=False)) as client:
        result = await client.call_tool(CHECKLIST, ARGUMENTS)
    assert result.is_error and "Unknown tool" in str(result.content)
