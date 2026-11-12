"""`set_tax_loss` and `delete_tax_loss` over the MCP client (REB-1088): on a `full_access` token's
surface only, an administrator's alone, and `get_fiscal_estimate` then lists the loss."""

import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from mcp import Client
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor, Role
from pigrocrm.core.config import Settings
from pigrocrm.core.fiscal.models import FiscalProfile
from pigrocrm.core.legal_entities.models import LegalEntity
from pigrocrm.core.storage import LocalFileStorage
from pigrocrm_mcp.server import build_server

SET = "set_tax_loss"
DELETE = "delete_tax_loss"
ARGUMENTS = {"tax": "ires", "year": 2025, "amount": "50000.00"}


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


def _srl(session: Session) -> str:
    entity = LegalEntity(name="srl", legal_name="Esempio S.r.l.", country="IT")
    session.add(entity)
    session.flush()
    session.add(FiscalProfile(legal_entity_id=entity.id, pack_id="it-company", regime_code="RF01"))
    session.commit()
    return str(entity.id)


async def test_the_tools_are_listed_only_to_a_full_access_token(
    mcp_session: Session, tmp_path: Path
) -> None:
    full = {tool.name for tool in await _server(mcp_session, tmp_path).list_tools()}
    plain = {
        tool.name for tool in await _server(mcp_session, tmp_path, full_access=False).list_tools()
    }
    assert {SET, DELETE} <= full
    assert not ({SET, DELETE} & plain)


async def test_a_token_without_full_access_cannot_call_them_by_name(
    mcp_session: Session, tmp_path: Path
) -> None:
    async with Client(_server(mcp_session, tmp_path, full_access=False)) as client:
        result = await client.call_tool(SET, ARGUMENTS)
    assert result.is_error
    assert "Unknown tool" in str(result.content)


async def test_a_full_access_admin_types_and_removes_a_loss(
    mcp_session: Session, tmp_path: Path
) -> None:
    entity = _srl(mcp_session)
    async with Client(_server(mcp_session, tmp_path)) as client:
        typed = await client.call_tool(
            SET,
            {
                **ARGUMENTS,
                "new_activity": True,
                "activity_start_year": 2024,
                "legal_entity_id": entity,
            },
        )
        assert not typed.is_error, typed
        assert _payload(typed)["remaining"] == "50000.00"
        estimate = _payload(
            await client.call_tool("get_fiscal_estimate", {"year": 2026, "legal_entity_id": entity})
        )
        assert [
            (loss["tax"], loss["year"], loss["new_activity"]) for loss in estimate["losses"]
        ] == [("ires", 2025, True)]
        gone = await client.call_tool(
            DELETE, {"tax": "ires", "year": 2025, "legal_entity_id": entity}
        )
        assert not gone.is_error, gone
        after = _payload(
            await client.call_tool("get_fiscal_estimate", {"year": 2026, "legal_entity_id": entity})
        )
    assert after["losses"] == []


async def test_a_bad_remainder_is_refused_and_a_collaborator_too(
    mcp_session: Session, tmp_path: Path
) -> None:
    entity = _srl(mcp_session)
    async with Client(_server(mcp_session, tmp_path)) as client:
        bad = await client.call_tool(
            SET,
            {
                **ARGUMENTS,
                "remaining": "60000.00",
                "remaining_as_of_year": 2025,
                "legal_entity_id": entity,
            },
        )
    assert bad.is_error
    async with Client(_server(mcp_session, tmp_path, role="collaborator")) as client:
        refused = await client.call_tool(SET, {**ARGUMENTS, "legal_entity_id": entity})
    assert refused.is_error
