"""`set_declared_tax_figure` over the MCP client (REB-1006): on a `full_access` token's surface
only, an administrator's alone, and `get_fiscal_estimate` then shows the figure beside the
estimate."""

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

TOOL = "set_declared_tax_figure"
ARGUMENTS = {"tax": "substitute_tax", "year": 2025, "balance": "300.00"}


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


def _entity(session: Session) -> str:
    entity = LegalEntity(name="declared", legal_name="Declared", country="IT")
    session.add(entity)
    session.flush()
    session.add(
        FiscalProfile(
            legal_entity_id=entity.id,
            pack_id="it-flat-rate",
            regime_code="RF19",
            profitability_coefficient=1,
            substitute_tax_rate=5,
            social_security_rate=26,
        )
    )
    session.commit()
    return str(entity.id)


async def test_the_tool_is_listed_only_to_a_full_access_token(
    mcp_session: Session, tmp_path: Path
) -> None:
    full = {tool.name for tool in await _server(mcp_session, tmp_path).list_tools()}
    plain = {
        tool.name for tool in await _server(mcp_session, tmp_path, full_access=False).list_tools()
    }
    assert TOOL in full
    assert TOOL not in plain


async def test_a_token_without_full_access_cannot_call_it_by_name(
    mcp_session: Session, tmp_path: Path
) -> None:
    async with Client(_server(mcp_session, tmp_path, full_access=False)) as client:
        result = await client.call_tool(TOOL, ARGUMENTS)
    assert result.is_error
    assert "Unknown tool" in str(result.content)


async def test_a_full_access_admin_types_a_figure_and_the_estimate_shows_it(
    mcp_session: Session, tmp_path: Path
) -> None:
    entity = _entity(mcp_session)
    async with Client(_server(mcp_session, tmp_path)) as client:
        typed = await client.call_tool(TOOL, {**ARGUMENTS, "legal_entity_id": entity})
        assert not typed.is_error, typed
        assert _payload(typed)["reading"] == "declared"
        estimate = _payload(
            await client.call_tool("get_fiscal_estimate", {"year": 2025, "legal_entity_id": entity})
        )
    assert estimate["reading"] == "declared"
    assert estimate["declared"][0]["balance"] == "300.00"


async def test_a_collaborator_with_full_access_is_refused(
    mcp_session: Session, tmp_path: Path
) -> None:
    entity = _entity(mcp_session)
    async with Client(_server(mcp_session, tmp_path, role="collaborator")) as client:
        result = await client.call_tool(TOOL, {**ARGUMENTS, "legal_entity_id": entity})
    assert result.is_error
