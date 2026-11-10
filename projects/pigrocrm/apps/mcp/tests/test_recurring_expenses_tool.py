"""`list_recurring_expenses`, `create_recurring_expense` and `stop_recurring_expense` over the
MCP client (REB-1022): on every token's surface while the `purchases` module is on."""

import datetime as dt
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from mcp import Client
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.config import Settings
from pigrocrm.core.legal_entities.models import LegalEntity
from pigrocrm.core.organizations.models import Organization
from pigrocrm.core.storage import LocalFileStorage
from pigrocrm.core.timetracking.models import CostCategory
from pigrocrm_mcp.server import build_server

TOOLS = {"list_recurring_expenses", "create_recurring_expense", "stop_recurring_expense"}
NEXT_MONTH = (dt.date.today().replace(day=1) + dt.timedelta(days=32)).replace(day=1)


def _server(session: Session, tmp_path: Path, **settings: Any) -> Any:
    return build_server(
        lambda: session,
        lambda: Actor(id=uuid4(), type="mcp", role="admin"),
        LocalFileStorage(tmp_path),
        Settings(_env_file=None, **settings),  # type: ignore[call-arg]
    )


def _payload(result: Any) -> dict[str, Any]:
    return result.structured_content or json.loads(result.content[0].text)


async def test_the_tools_are_listed_while_the_purchases_module_is_on(
    mcp_session: Session, tmp_path: Path
) -> None:
    on = {tool.name for tool in await _server(mcp_session, tmp_path).list_tools()}
    off = {
        tool.name
        for tool in await _server(mcp_session, tmp_path, module_purchases=False).list_tools()
    }
    assert on >= TOOLS
    assert not TOOLS & off


async def test_an_agent_creates_lists_and_stops_a_rule(
    mcp_session: Session, tmp_path: Path
) -> None:
    mcp_session.add(LegalEntity(name="rec tool", legal_name="Rec Tool", country="IT"))
    supplier = Organization(legal_name="Affitti Srl", country="IT", roles=["supplier"])
    category = CostCategory(name="Affitto", position=0, code=None)
    mcp_session.add_all([supplier, category])
    mcp_session.commit()
    async with Client(_server(mcp_session, tmp_path)) as client:
        created = _payload(
            await client.call_tool(
                "create_recurring_expense",
                {
                    "supplier_id": str(supplier.id),
                    "category_id": str(category.id),
                    "description": "Affitto ufficio",
                    "amount": "800.00",
                    "cadence": "quarterly",
                    "first_due_date": NEXT_MONTH.isoformat(),
                },
            )
        )
        listed = _payload(await client.call_tool("list_recurring_expenses", {}))
        stopped = _payload(
            await client.call_tool(
                "stop_recurring_expense", {"recurring_expense_id": created["id"]}
            )
        )
        after = _payload(await client.call_tool("list_recurring_expenses", {}))
    assert created["cadence"] == "quarterly" and created["active"] is True
    assert [row["id"] for row in listed["recurring_expenses"]] == [created["id"]]
    assert stopped["active"] is False
    assert after["recurring_expenses"] == []
