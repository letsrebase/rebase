"""The due item tools over the MCP client (REB-967): a payment in instalments, an over-payment,
a reversal, and which token may do what to an incoming, an outgoing and a fiscal item.

Due items are core, so the four tools are there whatever the module switches say.
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
from pigrocrm.core.config import Settings
from pigrocrm.core.due_items.schemas import DueItemCreate
from pigrocrm.core.due_items.service import DueItemService
from pigrocrm.core.legal_entities.repository import LegalEntityRepository
from pigrocrm.core.modules import MODULE_KEYS
from pigrocrm.core.organizations.models import Organization
from pigrocrm.core.storage import LocalFileStorage
from pigrocrm_mcp.server import build_server

D = dt.date
TOOLS = {
    "list_due_items",
    "get_due_item",
    "settle_due_item",
    "reverse_settlement",
    "list_payroll_totals",
}


def _server(session: Session, tmp_path: Path, *, full_access: bool = False, **settings: Any) -> Any:
    return build_server(
        lambda: session,
        lambda: Actor(id=uuid4(), type="mcp", role="admin", full_access=full_access),
        LocalFileStorage(tmp_path),
        Settings(_env_file=None, mcp_full_access=full_access, **settings),  # type: ignore[call-arg]
    )


def _payload(result: Any) -> dict[str, Any]:
    return result.structured_content or json.loads(result.content[0].text)


def _item(session: Session, **overrides: Any) -> str:
    entity = LegalEntityRepository(session).default()
    assert entity is not None
    customer = Organization(legal_name="ACME S.r.l.", legal_entity_id=entity.id)
    session.add(customer)
    session.flush()
    values: dict[str, Any] = {
        "legal_entity_id": entity.id,
        "direction": "in",
        "organization_id": customer.id,
        "amount": Decimal("100.00"),
        "source_kind": "sales_invoice",
        "source_id": uuid4(),
        "own_date": D(2026, 2, 28),
    }
    values.update(overrides)
    created = DueItemService(session).create(DueItemCreate(**values))
    session.commit()
    return str(created.id)


async def test_the_tools_are_there_with_every_optional_module_off(
    mcp_session: Session, tmp_path: Path
) -> None:
    off = {f"module_{key}": False for key in MODULE_KEYS}
    server = _server(mcp_session, tmp_path, **off)
    assert {tool.name for tool in await server.list_tools()} >= TOOLS
    item_id = _item(mcp_session)
    async with Client(server) as client:
        listed = _payload(await client.call_tool("list_due_items", {}))
        paid = await client.call_tool(
            "settle_due_item", {"due_item_id": item_id, "amount": 100, "settled_on": "2026-03-01"}
        )
    assert item_id in {item["id"] for item in listed["items"]}
    assert not paid.is_error, paid.content
    assert _payload(paid)["state"] == "settled"


async def test_a_due_item_settled_in_two_instalments_reads_settled_with_both(
    mcp_session: Session, tmp_path: Path
) -> None:
    item_id = _item(mcp_session)
    async with Client(_server(mcp_session, tmp_path)) as client:
        first = _payload(
            await client.call_tool(
                "settle_due_item",
                {"due_item_id": item_id, "amount": "40.00", "settled_on": "2026-03-01"},
            )
        )
        assert (first["state"], first["remaining"]) == ("expected", "60.00")
        await client.call_tool(
            "settle_due_item",
            {
                "due_item_id": item_id,
                "amount": 60,
                "settled_on": "2026-03-20",
                "note": "bonifico a saldo",
            },
        )
        read = _payload(await client.call_tool("get_due_item", {"due_item_id": item_id}))

    assert read["state"] == "settled"
    assert [(s["amount"], s["settled_on"]) for s in read["settlements"]] == [
        ("40.00", "2026-03-01"),
        ("60.00", "2026-03-20"),
    ]
    assert read["settlements"][1]["note"] == "bonifico a saldo"
    assert read["settlements"][0]["recorded_by_type"] == "mcp"
    # The note is text somebody else wrote: the answer says so.
    assert "provenance" in read


async def test_an_over_payment_and_a_malformed_argument_come_back_as_guidance(
    mcp_session: Session, tmp_path: Path
) -> None:
    item_id = _item(mcp_session)
    async with Client(_server(mcp_session, tmp_path)) as client:
        over = await client.call_tool(
            "settle_due_item",
            {"due_item_id": item_id, "amount": "100.01", "settled_on": "2026-03-01"},
        )
        bad_date = await client.call_tool(
            "settle_due_item", {"due_item_id": item_id, "amount": 1, "settled_on": "ieri"}
        )
        bad_amount = await client.call_tool(
            "settle_due_item",
            {"due_item_id": item_id, "amount": "tanto", "settled_on": "2026-03-01"},
        )
        unknown = await client.call_tool("get_due_item", {"due_item_id": str(uuid4())})
        bad_state = await client.call_tool("list_due_items", {"state": "paid"})

    assert over.is_error and "supera" in over.content[0].text  # type: ignore[union-attr]
    for refused in (bad_date, bad_amount, unknown, bad_state):
        assert refused.is_error
        assert "errors.pydantic.dev" not in refused.content[0].text  # type: ignore[union-attr]


async def test_a_reversal_reopens_the_item(mcp_session: Session, tmp_path: Path) -> None:
    item_id = _item(mcp_session)
    async with Client(_server(mcp_session, tmp_path)) as client:
        paid = _payload(
            await client.call_tool(
                "settle_due_item",
                {"due_item_id": item_id, "amount": 100, "settled_on": "2026-03-01"},
            )
        )
        undone = _payload(
            await client.call_tool(
                "reverse_settlement",
                {"settlement_id": paid["settlements"][0]["id"], "reason": "not received"},
            )
        )
    assert (undone["state"], undone["remaining"]) == ("expected", "100.00")
    assert undone["settlements"][0]["reversal_reason"] == "not received"


async def test_a_token_without_full_access_is_refused_money_going_out_and_the_fiscal_kinds(
    mcp_session: Session, tmp_path: Path
) -> None:
    """The card's «done when» on the MCP surface: the same administrator's token settles a
    client's payment, and is refused an outgoing item, until it carries `full_access`."""
    outgoing = _item(mcp_session, direction="out", source_kind="purchase")
    tax = _item(
        mcp_session,
        direction="out",
        counterparty_type="tax_office",
        organization_id=None,
        tax="vat",
        source_kind="iva_settlement",
        amount=Decimal("50.00"),
    )
    arguments = {"amount": 10, "settled_on": "2026-03-01"}

    async with Client(_server(mcp_session, tmp_path)) as plain:
        refused = await plain.call_tool("settle_due_item", {"due_item_id": outgoing, **arguments})
        listed = _payload(await plain.call_tool("list_due_items", {}))
        fiscal_get = await plain.call_tool("get_due_item", {"due_item_id": tax})
        fiscal_list = await plain.call_tool("list_due_items", {"source_kind": "iva_settlement"})
        untouched = _payload(await plain.call_tool("get_due_item", {"due_item_id": outgoing}))
    assert untouched["settlements"] == []
    assert refused.is_error
    for denied in (refused, fiscal_get, fiscal_list):
        assert denied.is_error
        assert "non è eseguibile da un agente" in denied.content[0].text  # type: ignore[union-attr]
    assert tax not in {item["id"] for item in listed["items"]}

    async with Client(_server(mcp_session, tmp_path, full_access=True)) as full:
        paid = await full.call_tool("settle_due_item", {"due_item_id": outgoing, **arguments})
        read_tax = await full.call_tool("get_due_item", {"due_item_id": tax})
        listed_full = _payload(await full.call_tool("list_due_items", {}))
        paid_tax = await full.call_tool("settle_due_item", {"due_item_id": tax, **arguments})
    assert not paid.is_error, paid.content
    assert not read_tax.is_error and not paid_tax.is_error
    assert tax in {item["id"] for item in listed_full["items"]}


async def test_a_token_without_full_access_reads_payroll_as_one_stipendi_line_per_date(
    mcp_session: Session, tmp_path: Path
) -> None:
    """REB-1008: payroll items are `full_access` reads, and what a default token reads in their
    place is the aggregate: a date and an amount, no kind, no tax and no person."""
    for amount, tax in (("100.00", "payroll_inps"), ("40.00", "payroll_withholding")):
        _item(
            mcp_session,
            direction="out",
            counterparty_type="tax_office",
            organization_id=None,
            tax=tax,
            source_kind="payroll",
            amount=Decimal(amount),
            own_date=D(2026, 4, 16),
        )
    async with Client(_server(mcp_session, tmp_path)) as plain:
        listed = _payload(await plain.call_tool("list_due_items", {}))
        totals = await plain.call_tool("list_payroll_totals", {})
    assert not [i for i in listed["items"] if i["source_kind"] == "payroll"]
    assert not totals.is_error, totals.content
    assert _payload(totals)["items"] == [
        {"label": "Stipendi", "due_date": "2026-04-16", "amount": "140.00", "forecast": False}
    ]
