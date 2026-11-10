"""REB-1024: `list_iva_periods` and `get_iva_register` over the MCP client. They read the periods
of a company's year and the registers behind them, and exist only on a `full_access` token's
surface, while the `purchases` module is on."""

import datetime as dt
import json
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from mcp import Client
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.config import Settings
from pigrocrm.core.fiscal.models import FiscalProfile
from pigrocrm.core.invoices.models import Invoice
from pigrocrm.core.legal_entities.models import LegalEntity
from pigrocrm.core.organizations.models import Organization
from pigrocrm.core.storage import LocalFileStorage
from pigrocrm_mcp.server import build_server

TOOLS = ("list_iva_periods", "get_iva_register")


def _server(session: Session, tmp_path: Path, *, full_access: bool = True, **settings: Any) -> Any:
    return build_server(
        lambda: session,
        lambda: Actor(id=uuid4(), type="mcp", role="admin", full_access=full_access),
        LocalFileStorage(tmp_path),
        Settings(_env_file=None, mcp_full_access=full_access, **settings),  # type: ignore[call-arg]
    )


def _entity_with_a_sale(session: Session) -> UUID:
    entity = LegalEntity(name="iva register", legal_name="Iva Register", country="IT")
    session.add(entity)
    session.flush()
    session.add(FiscalProfile(legal_entity_id=entity.id, pack_id="it-standard", regime_code="RF01"))
    customer = Organization(legal_name="Acme S.r.l.", country="IT", roles=["customer"])
    session.add(customer)
    session.flush()
    session.add(
        Invoice(
            customer_id=customer.id,
            legal_entity_id=entity.id,
            type="invoice",
            status="issued",
            year=2026,
            number=1,
            issue_date=dt.date(2026, 2, 3),
            taxable_amount=Decimal("1000.00"),
            vat_amount=Decimal("220.00"),
            total=Decimal("1220.00"),
        )
    )
    session.commit()
    return entity.id


def _payload(result: Any) -> dict[str, Any]:
    return result.structured_content or json.loads(result.content[0].text)


async def test_the_tools_are_listed_only_to_a_full_access_token_and_the_module(
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
    for tool in TOOLS:
        assert tool in full
        assert tool not in plain
        assert tool not in off


async def test_a_token_without_full_access_cannot_call_them_by_name(
    mcp_session: Session, tmp_path: Path
) -> None:
    _entity_with_a_sale(mcp_session)
    async with Client(_server(mcp_session, tmp_path, full_access=False)) as client:
        periods = await client.call_tool("list_iva_periods", {"year": 2026})
        register = await client.call_tool(
            "get_iva_register", {"year": 2026, "number": 1, "register": "sales"}
        )
    for result in (periods, register):
        assert result.is_error
        assert "Unknown tool" in str(result.content)


async def test_they_read_the_periods_and_each_register(
    mcp_session: Session, tmp_path: Path
) -> None:
    entity = _entity_with_a_sale(mcp_session)
    async with Client(_server(mcp_session, tmp_path)) as client:
        periods = _payload(
            await client.call_tool(
                "list_iva_periods", {"year": 2026, "legal_entity_id": str(entity)}
            )
        )
        sales = _payload(
            await client.call_tool(
                "get_iva_register",
                {
                    "year": 2026,
                    "number": 2,
                    "register": "sales",
                    "legal_entity_id": str(entity),
                },
            )
        )
        purchases = _payload(
            await client.call_tool(
                "get_iva_register",
                {
                    "year": 2026,
                    "number": 1,
                    "register": "purchases",
                    "legal_entity_id": str(entity),
                },
            )
        )
    assert [row["number"] for row in periods["periods"]] == list(range(1, 13))
    assert sales["total_count"] == 1 and sales["totals"]["vat_due"] == "220.00"
    assert sales["items"][0]["customer_name"] == "Acme S.r.l."
    assert purchases["items"] == [] and purchases["totals"]["deductible_vat"] == "0.00"
