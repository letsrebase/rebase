"""REB-1036: `close_iva_period` and `reopen_iva_period` over the MCP client. They exist only on a
`full_access` token's surface while the `purchases` module is on, and while a period is closed the
purchase tools refuse a write dated in it with a sentence that names the period."""

import json
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from mcp import Client
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.auth.models import User
from pigrocrm.core.config import Settings
from pigrocrm.core.fiscal.models import FiscalProfile
from pigrocrm.core.legal_entities.models import LegalEntity
from pigrocrm.core.organizations.models import Organization
from pigrocrm.core.storage import LocalFileStorage
from pigrocrm.core.timetracking.models import CostCategory
from pigrocrm_mcp.server import build_server

TOOLS = ("close_iva_period", "reopen_iva_period")


def _server(
    session: Session, tmp_path: Path, actor: Actor, *, full_access: bool = True, **settings: Any
) -> Any:
    return build_server(
        lambda: session,
        lambda: actor,
        LocalFileStorage(tmp_path),
        Settings(_env_file=None, mcp_full_access=full_access, **settings),  # type: ignore[call-arg]
    )


def _owner(session: Session) -> Actor:
    user = User(email=f"owner-{uuid4()}@example.test", password_hash="x", name="G", role="admin")
    session.add(user)
    session.flush()
    return Actor(id=user.id, type="mcp", role="admin", full_access=True)


def _entity(session: Session) -> UUID:
    entity = LegalEntity(name="iva lock", legal_name="Iva Lock", country="IT")
    session.add(entity)
    session.flush()
    session.add(
        FiscalProfile(
            legal_entity_id=entity.id,
            pack_id="it-standard",
            regime_code="RF01",
            iva_frequency="quarterly",
        )
    )
    session.commit()
    return entity.id


def _payload(result: Any) -> dict[str, Any]:
    return result.structured_content or json.loads(result.content[0].text)


async def test_the_tools_are_listed_only_to_a_full_access_token_and_the_module(
    mcp_session: Session, tmp_path: Path
) -> None:
    owner = _owner(mcp_session)
    full = {tool.name for tool in await _server(mcp_session, tmp_path, owner).list_tools()}
    plain = {
        tool.name
        for tool in await _server(mcp_session, tmp_path, owner, full_access=False).list_tools()
    }
    off = {
        tool.name
        for tool in await _server(mcp_session, tmp_path, owner, module_purchases=False).list_tools()
    }
    for tool in TOOLS:
        assert tool in full
        assert tool not in plain
        assert tool not in off


async def test_a_closed_period_refuses_the_purchase_tools_until_it_is_reopened(
    mcp_session: Session, tmp_path: Path
) -> None:
    owner = _owner(mcp_session)
    entity = _entity(mcp_session)
    category = CostCategory(name=f"Servizi {uuid4()}", position=0, code=None)
    supplier = Organization(legal_name="Vendor Srl", country="IT", roles=["supplier"])
    mcp_session.add_all([category, supplier])
    mcp_session.flush()

    def purchase(day: str) -> dict[str, Any]:
        return {
            "purchase": {
                "kind": "invoice",
                "supplier_id": str(supplier.id),
                "legal_entity_id": str(entity),
                "category_id": str(category.id),
                "date": day,
                "amount": "122.00",
                "description": "Consulenza",
                "vat_lines": [{"rate": "22.00", "taxable_amount": "100.00", "vat_amount": "22.00"}],
            }
        }

    async with Client(_server(mcp_session, tmp_path, owner)) as client:
        closed = _payload(
            await client.call_tool(
                "close_iva_period", {"year": 2026, "number": 1, "legal_entity_id": str(entity)}
            )
        )
        assert (closed["label"], closed["closed_by"]) == ("1° trimestre 2026", str(owner.id))
        periods = _payload(
            await client.call_tool(
                "list_iva_periods", {"year": 2026, "legal_entity_id": str(entity)}
            )
        )["periods"]
        assert [row["closed"] for row in periods][:2] == [True, False]

        refused = await client.call_tool("create_purchase", purchase("2026-02-10"))
        assert refused.is_error
        assert "il periodo IVA 1° trimestre 2026 è chiuso" in str(refused.content)
        # The next quarter is open.
        assert not (await client.call_tool("create_purchase", purchase("2026-05-10"))).is_error

        reopened = await client.call_tool(
            "reopen_iva_period", {"year": 2026, "number": 1, "legal_entity_id": str(entity)}
        )
        assert not reopened.is_error
        assert not (await client.call_tool("create_purchase", purchase("2026-02-10"))).is_error


async def test_a_token_without_full_access_cannot_call_them_by_name(
    mcp_session: Session, tmp_path: Path
) -> None:
    owner = _owner(mcp_session)
    entity = _entity(mcp_session)
    async with Client(_server(mcp_session, tmp_path, owner, full_access=False)) as client:
        for tool in TOOLS:
            result = await client.call_tool(
                tool, {"year": 2026, "number": 1, "legal_entity_id": str(entity)}
            )
            assert result.is_error
            assert "Unknown tool" in str(result.content)
