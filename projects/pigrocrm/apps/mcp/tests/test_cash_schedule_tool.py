"""`get_cash_schedule` over the MCP client (REB-1025): the schedule is core, so the tool is there
whatever the module switches say; what an agent reads of the balances and of the fiscal kinds
depends on its token, and a solo space reads without an outgoing column.
"""

import datetime as dt
import json
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4

from mcp import Client
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.bank.models import BankAccount
from pigrocrm.core.config import Settings
from pigrocrm.core.db import today_local
from pigrocrm.core.due_items.schemas import DueItemCreate
from pigrocrm.core.due_items.service import DueItemService
from pigrocrm.core.legal_entities.repository import LegalEntityRepository
from pigrocrm.core.modules import MODULE_KEYS
from pigrocrm.core.organizations.models import Organization
from pigrocrm.core.storage import LocalFileStorage
from pigrocrm_mcp.server import build_server

TODAY = today_local()
NEXT_MONDAY = TODAY - dt.timedelta(days=TODAY.weekday()) + dt.timedelta(days=7)


def _server(session: Session, tmp_path: Path, *, full_access: bool = False, **settings: Any) -> Any:
    return build_server(
        lambda: session,
        lambda: Actor(id=uuid4(), type="mcp", role="admin", full_access=full_access),
        LocalFileStorage(tmp_path),
        Settings(_env_file=None, mcp_full_access=full_access, **settings),  # type: ignore[call-arg]
    )


def _payload(result: Any) -> dict[str, Any]:
    return result.structured_content or json.loads(result.content[0].text)


def _item(session: Session, amount: str, due: dt.date, **overrides: Any) -> None:
    entity = LegalEntityRepository(session).default()
    assert entity is not None
    customer = Organization(legal_name="ACME S.r.l.", legal_entity_id=entity.id)
    session.add(customer)
    session.flush()
    values: dict[str, Any] = {
        "legal_entity_id": entity.id,
        "direction": "in",
        "organization_id": customer.id,
        "amount": Decimal(amount),
        "source_kind": "sales_invoice",
        "source_id": uuid4(),
        "own_date": due,
    }
    if "tax" in overrides:
        values.update(counterparty_type="tax_office", organization_id=None)
    values.update(overrides)
    DueItemService(session).create(DueItemCreate(**values))
    session.commit()


def _account(session: Session, balance: str) -> None:
    entity = LegalEntityRepository(session).default()
    assert entity is not None
    session.add(
        BankAccount(
            legal_entity_id=entity.id,
            name="Conto operativo",
            bank="Banco BPM",
            iban="IT60X0542811101000000123456",
            opening_balance=Decimal(balance),
            opening_balance_date=TODAY,
            created_at=dt.datetime(2025, 1, 1, tzinfo=dt.UTC),
        )
    )
    session.commit()


def _world(session: Session) -> None:
    _item(session, "100.00", NEXT_MONDAY)
    _item(session, "60.00", NEXT_MONDAY, direction="out", source_kind="purchase")
    _item(
        session,
        "40.00",
        NEXT_MONDAY,
        direction="out",
        source_kind="iva_settlement",
        tax="vat",
    )
    _account(session, "1000")


async def test_the_tool_is_there_with_every_optional_module_off(
    mcp_session: Session, tmp_path: Path
) -> None:
    off = {f"module_{key}": False for key in MODULE_KEYS}
    _world(mcp_session)

    server = _server(mcp_session, tmp_path, **off)
    names = {tool.name for tool in await server.list_tools()}
    async with Client(server) as client:
        body = _payload(await client.call_tool("get_cash_schedule", {}))

    assert "get_cash_schedule" in names
    assert body["cash_enabled"] is False
    assert (body["accounts"], body["total_balance"], body["show_balance"]) == (None, None, False)
    assert body["weeks"][1]["in_expected"] == "100.00"
    # The purchase and the tax are the modules' kinds: with every module off they are in no week.
    assert body["weeks"][1]["out_expected"] == "0.00"
    async with Client(_server(mcp_session, tmp_path, module_cash=False)) as only_cash_off:
        body = _payload(await only_cash_off.call_tool("get_cash_schedule", {}))
    assert body["cash_enabled"] is False
    assert body["weeks"][1]["out_expected"] == "60.00"


async def test_a_default_token_reads_no_balance_and_no_fiscal_kind(
    mcp_session: Session, tmp_path: Path
) -> None:
    _world(mcp_session)

    async with Client(_server(mcp_session, tmp_path)) as client:
        body = _payload(await client.call_tool("get_cash_schedule", {"weeks": 4}))

    assert body["horizon_weeks"] == 4 and len(body["weeks"]) == 4
    assert body["cash_enabled"] is True
    assert (body["accounts"], body["total_balance"], body["alerts"]) == (None, None, [])
    assert all(week["balance_end"] is None for week in body["weeks"])
    assert body["weeks"][1]["out_expected"] == "60.00"  # the tax is not in it
    assert "provenance" in body


async def test_a_full_access_administrator_token_reads_the_balances(
    mcp_session: Session, tmp_path: Path
) -> None:
    _world(mcp_session)

    async with Client(_server(mcp_session, tmp_path, full_access=True)) as client:
        body = _payload(await client.call_tool("get_cash_schedule", {}))

    assert body["total_balance"]["amount"] == "1000.00"
    assert [a["name"] for a in body["accounts"]] == ["Conto operativo"]
    assert body["weeks"][1]["out_expected"] == "100.00"
    assert [week["balance_end"] for week in body["weeks"][:2]] == ["1000.00", "1000.00"]


async def test_a_solo_space_reads_without_an_outgoing_column(
    mcp_session: Session, tmp_path: Path
) -> None:
    _world(mcp_session)

    async with Client(
        _server(mcp_session, tmp_path, full_access=True, space_profile="solo")
    ) as client:
        body = _payload(await client.call_tool("get_cash_schedule", {}))

    assert body["profile"] == "solo" and body["show_outgoing"] is False
    assert body["late_payable"] is None
    assert body["weeks"][1]["out_expected"] is None and body["weeks"][1]["balance_end"] is None
    assert [(t["tax"], t["amount"]) for t in body["taxes"]] == [("vat", "40.00")]


async def test_a_horizon_that_is_not_offered_and_a_bad_id_are_refused(
    mcp_session: Session, tmp_path: Path
) -> None:
    async with Client(_server(mcp_session, tmp_path)) as client:
        horizon = await client.call_tool("get_cash_schedule", {"weeks": 7})
        entity = await client.call_tool("get_cash_schedule", {"legal_entity_id": "not-an-id"})

    assert horizon.is_error and entity.is_error
