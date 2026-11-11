"""`set_tax_advance_method` over the MCP client (REB-1074): on a `full_access` token's surface
only, an administrator's alone, and the choice moves the legal entity's tax due items."""

import datetime as dt
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from mcp import Client
from sqlalchemy import select
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor, Role
from pigrocrm.core.config import Settings
from pigrocrm.core.fiscal import tax_items
from pigrocrm.core.fiscal.models import FiscalProfile, TaxAdvanceMethod
from pigrocrm.core.legal_entities.models import LegalEntity
from pigrocrm.core.storage import LocalFileStorage
from pigrocrm_mcp.server import build_server

TOOL = "set_tax_advance_method"
ARGUMENTS = {"year": 2026, "method": "forecast"}


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
    entity = LegalEntity(name="acconti", legal_name="Acconti", country="IT")
    session.add(entity)
    session.flush()
    session.add(
        FiscalProfile(
            legal_entity_id=entity.id,
            pack_id="it-standard",
            regime_code="RF01",
            isa=False,
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


async def test_a_full_access_admin_chooses_the_method_and_the_answer_says_what_is_missing(
    mcp_session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(tax_items, "today_local", lambda: dt.date(2026, 5, 15))
    entity = _entity(mcp_session)
    async with Client(_server(mcp_session, tmp_path)) as client:
        chosen = await client.call_tool(TOOL, {**ARGUMENTS, "legal_entity_id": entity})
    assert not chosen.is_error, chosen
    payload = _payload(chosen)
    assert (payload["year"], payload["method"]) == (2026, "forecast")
    assert payload["legal_entity_id"] == entity
    # The forecast method has no IRPEF estimate of 2026 to stand on (the code computes none for a
    # person): the answer lists every payment it could not write, with the reason.
    assert payload["items"]["written"] == 0
    assert any("forecast method" in n["reason"] for n in payload["items"]["not_written"])
    stored = mcp_session.scalars(select(TaxAdvanceMethod)).all()
    assert [(row.tax_year, row.method) for row in stored] == [(2026, "forecast")]


async def test_a_collaborator_with_full_access_is_refused(
    mcp_session: Session, tmp_path: Path
) -> None:
    entity = _entity(mcp_session)
    async with Client(_server(mcp_session, tmp_path, role="collaborator")) as client:
        result = await client.call_tool(TOOL, {**ARGUMENTS, "legal_entity_id": entity})
    assert result.is_error
    assert mcp_session.scalars(select(TaxAdvanceMethod)).all() == []
