"""The tax plan tools over the MCP client (REB-1075): on a `full_access` token's surface only, an
administrator's alone, and each one moves the legal entity's tax due items."""

import datetime as dt
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from mcp import Client
from sqlalchemy import select
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor, Role
from pigrocrm.core.config import Settings
from pigrocrm.core.due_items.models import DueItem
from pigrocrm.core.fiscal.models import FiscalProfile, TaxInstalmentPlan, TaxNoticePlan
from pigrocrm.core.legal_entities.models import LegalEntity
from pigrocrm.core.storage import LocalFileStorage
from pigrocrm_mcp.server import build_server

TOOLS = (
    "set_tax_instalment_plan",
    "create_tax_notice_plan",
    "update_tax_notice_plan",
    "delete_tax_notice_plan",
    "list_tax_plans",
)
NOTICE = {
    "label": "Avviso bonario 2023",
    "tax": "irpef",
    "total": "300.00",
    "instalments": [
        {"due_date": "2031-01-10", "interest": "0.00"},
        {"due_date": "2031-02-10", "interest": "1.50"},
    ],
}


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
    entity = LegalEntity(name="rate", legal_name="Rate", country="IT")
    session.add(entity)
    session.flush()
    session.add(
        FiscalProfile(
            legal_entity_id=entity.id,
            pack_id="it-flat-rate",
            regime_code="RF19",
            profitability_coefficient=78,
            substitute_tax_rate=5,
            social_security_rate=26.07,
        )
    )
    session.commit()
    return str(entity.id)


async def test_the_tools_are_listed_only_to_a_full_access_token(
    mcp_session: Session, tmp_path: Path
) -> None:
    full = {tool.name for tool in await _server(mcp_session, tmp_path).list_tools()}
    plain = {
        tool.name for tool in await _server(mcp_session, tmp_path, full_access=False).list_tools()
    }
    assert set(TOOLS) <= full
    assert not set(TOOLS) & plain


async def test_a_token_without_full_access_cannot_call_them_by_name(
    mcp_session: Session, tmp_path: Path
) -> None:
    async with Client(_server(mcp_session, tmp_path, full_access=False)) as client:
        result = await client.call_tool("set_tax_instalment_plan", {"year": 2026, "instalments": 3})
    assert result.is_error
    assert "Unknown tool" in str(result.content)


async def test_a_full_access_admin_chooses_a_plan_and_reads_it_back(
    mcp_session: Session, tmp_path: Path
) -> None:
    entity = _entity(mcp_session)
    async with Client(_server(mcp_session, tmp_path)) as client:
        chosen = await client.call_tool(
            "set_tax_instalment_plan",
            {"year": 2026, "tax": "substitute_tax", "instalments": 3, "legal_entity_id": entity},
        )
        assert not chosen.is_error, chosen
        assert [(p["tax"], p["tax_year"], p["instalments"]) for p in _payload(chosen)["plans"]] == [
            ("substitute_tax", 2026, 3)
        ]
        listed = await client.call_tool("list_tax_plans", {"legal_entity_id": entity})
        assert [p["instalments"] for p in _payload(listed)["instalment_plans"]] == [3]
        removed = await client.call_tool(
            "set_tax_instalment_plan",
            {"year": 2026, "tax": "substitute_tax", "instalments": 1, "legal_entity_id": entity},
        )
        assert _payload(removed)["plans"] == []
    assert mcp_session.scalars(select(TaxInstalmentPlan)).all() == []


async def test_a_full_access_admin_enters_changes_and_deletes_a_notice_plan(
    mcp_session: Session, tmp_path: Path
) -> None:
    entity = _entity(mcp_session)
    async with Client(_server(mcp_session, tmp_path)) as client:
        created = await client.call_tool(
            "create_tax_notice_plan", {**NOTICE, "legal_entity_id": entity}
        )
        assert not created.is_error, created
        plan = _payload(created)
        assert [i["amount"] for i in plan["instalments"]] == ["150.00", "151.50"]
        items = mcp_session.scalars(
            select(DueItem).where(DueItem.source_kind == "notice_plan", DueItem.state != "dropped")
        ).all()
        assert sorted((i.due_date, i.amount) for i in items) == [
            (dt.date(2031, 1, 10), 150),
            (dt.date(2031, 2, 10), 151.5),
        ]
        changed = await client.call_tool(
            "update_tax_notice_plan", {"plan_id": plan["id"], "total": "400.00"}
        )
        assert not changed.is_error, changed
        assert [i["amount"] for i in _payload(changed)["instalments"]] == ["200.00", "201.50"]
        gone = await client.call_tool("delete_tax_notice_plan", {"plan_id": plan["id"]})
        assert not gone.is_error, gone
    assert mcp_session.scalars(select(TaxNoticePlan)).all() == []


async def test_a_collaborator_with_full_access_is_refused(
    mcp_session: Session, tmp_path: Path
) -> None:
    entity = _entity(mcp_session)
    async with Client(_server(mcp_session, tmp_path, role="collaborator")) as client:
        plan = await client.call_tool(
            "set_tax_instalment_plan",
            {"year": 2026, "instalments": 3, "legal_entity_id": entity},
        )
        notice = await client.call_tool(
            "create_tax_notice_plan", {**NOTICE, "legal_entity_id": entity}
        )
        listed = await client.call_tool("list_tax_plans", {"legal_entity_id": entity})
    assert plan.is_error and notice.is_error and listed.is_error
    assert mcp_session.scalars(select(TaxInstalmentPlan)).all() == []
    assert mcp_session.scalars(select(TaxNoticePlan)).all() == []
