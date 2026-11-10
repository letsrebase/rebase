"""REB-1021: `list_iva_settlements` over the MCP client. It reads what the daily pass wrote and
exists only on a `full_access` token's surface, while the `purchases` module is on."""

import json
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from mcp import Client
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.config import Settings
from pigrocrm.core.fiscal.iva_settlement import IvaSettlementService
from pigrocrm.core.fiscal.models import FiscalProfile
from pigrocrm.core.fiscal.schemas import IvaPeriod
from pigrocrm.core.legal_entities.models import LegalEntity
from pigrocrm.core.storage import LocalFileStorage
from pigrocrm_mcp.server import build_server

TOOL = "list_iva_settlements"


def _server(session: Session, tmp_path: Path, *, full_access: bool = True, **settings: Any) -> Any:
    return build_server(
        lambda: session,
        lambda: Actor(id=uuid4(), type="mcp", role="admin", full_access=full_access),
        LocalFileStorage(tmp_path),
        Settings(_env_file=None, mcp_full_access=full_access, **settings),  # type: ignore[call-arg]
    )


def _entity(session: Session) -> UUID:
    entity = LegalEntity(name="iva tool", legal_name="Iva Tool", country="IT")
    session.add(entity)
    session.flush()
    session.add(FiscalProfile(legal_entity_id=entity.id, pack_id="it-standard", regime_code="RF01"))
    session.commit()
    return entity.id


def _payload(result: Any) -> dict[str, Any]:
    return result.structured_content or json.loads(result.content[0].text)


async def test_the_tool_is_listed_only_to_a_full_access_token_and_the_module(
    mcp_session: Session, tmp_path: Path
) -> None:
    full = {tool.name for tool in await _server(mcp_session, tmp_path).list_tools()}
    plain = {
        tool.name for tool in await _server(mcp_session, tmp_path, full_access=False).list_tools()
    }
    off = {
        tool.name
        for tool in await _server(mcp_session, tmp_path, module_purchases=False).list_tools()
    }
    assert TOOL in full
    assert TOOL not in plain
    assert TOOL not in off


async def test_a_token_without_full_access_cannot_call_it_by_name(
    mcp_session: Session, tmp_path: Path
) -> None:
    _entity(mcp_session)
    async with Client(_server(mcp_session, tmp_path, full_access=False)) as client:
        result = await client.call_tool(TOOL, {"year": 2026})
    assert result.is_error
    assert "Unknown tool" in str(result.content)


async def test_it_lists_the_settlements_of_the_year(mcp_session: Session, tmp_path: Path) -> None:
    entity = _entity(mcp_session)
    IvaSettlementService(mcp_session).settle_iva_period(
        IvaPeriod(year=2026, number=1),
        Actor(id=uuid4(), type="user", role="admin"),
        legal_entity_id=entity,
    )
    async with Client(_server(mcp_session, tmp_path)) as client:
        read = _payload(
            await client.call_tool(TOOL, {"year": 2026, "legal_entity_id": str(entity)})
        )
        empty = _payload(
            await client.call_tool(TOOL, {"year": 2025, "legal_entity_id": str(entity)})
        )
    assert [(row["year"], row["period_number"]) for row in read["settlements"]] == [(2026, 1)]
    assert read["settlements"][0]["outcome"] == "nil"
    assert empty["settlements"] == []
