"""REB-1004: `get_tax_calendar` over the MCP client. It lists the dates of the legal entity's
pack and exists only on a `full_access` token's surface, while the `taxes` module is on."""

import json
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from mcp import Client
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.config import Settings
from pigrocrm.core.fiscal.models import FiscalProfile
from pigrocrm.core.legal_entities.models import LegalEntity
from pigrocrm.core.storage import LocalFileStorage
from pigrocrm_mcp.server import build_server


def _server(session: Session, tmp_path: Path, *, full_access: bool = True, **settings: Any) -> Any:
    return build_server(
        lambda: session,
        lambda: Actor(id=uuid4(), type="mcp", role="admin", full_access=full_access),
        LocalFileStorage(tmp_path),
        Settings(_env_file=None, mcp_full_access=full_access, **settings),  # type: ignore[call-arg]
    )


def _entity(session: Session, pack_id: str, regime: str) -> UUID:
    entity = LegalEntity(name=f"cal {pack_id}", legal_name=f"Cal {pack_id}", country="IT")
    session.add(entity)
    session.flush()
    session.add(FiscalProfile(legal_entity_id=entity.id, pack_id=pack_id, regime_code=regime))
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
        tool.name for tool in await _server(mcp_session, tmp_path, module_taxes=False).list_tools()
    }
    assert "get_tax_calendar" in full
    assert "get_tax_calendar" not in plain
    assert "get_tax_calendar" not in off


async def test_a_token_without_full_access_cannot_call_it_by_name(
    mcp_session: Session, tmp_path: Path
) -> None:
    _entity(mcp_session, "it-standard", "RF01")
    async with Client(_server(mcp_session, tmp_path, full_access=False)) as client:
        result = await client.call_tool("get_tax_calendar", {"year": 2026})
    assert result.is_error
    assert "Unknown tool" in str(result.content)


async def test_a_flat_rate_and_an_ordinary_entity_each_get_their_dates_with_sources(
    mcp_session: Session, tmp_path: Path
) -> None:
    flat = _entity(mcp_session, "it-flat-rate", "RF19")
    ordinary = _entity(mcp_session, "it-standard", "RF01")
    async with Client(_server(mcp_session, tmp_path)) as client:
        flat_calendar = _payload(
            await client.call_tool("get_tax_calendar", {"year": 2026, "legal_entity_id": str(flat)})
        )
        ordinary_calendar = _payload(
            await client.call_tool(
                "get_tax_calendar", {"year": 2026, "legal_entity_id": str(ordinary)}
            )
        )
    assert {d["tax"] for d in flat_calendar["dates"]} == {"substitute_tax"}
    assert {d["tax"] for d in ordinary_calendar["dates"]} == {"iva", "income_tax"}
    for entry in flat_calendar["dates"] + ordinary_calendar["dates"]:
        assert entry["source"] and entry["due_date"] >= entry["nominal_date"]
