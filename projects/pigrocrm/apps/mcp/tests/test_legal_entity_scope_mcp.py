"""The azienda boundary over MCP (REB-634, spec 2026-10-03 §4, §7).

The server's tool session is opened as the application role the boot creates, and the
actor a PAT resolves carries its owner's scope (`PatService.resolve`): every tool call
binds it on its session before the body runs (`server.py`, the guard). Two tools are
enough to prove the two shapes an agent reads: `search_everything`, one query over five
classes, and `list_invoices`, a repository list. The other file in this tree keeps its
superuser session and sees everything, by Postgres's own rule.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, NamedTuple
from uuid import UUID

import pytest
from mcp import Client
from sqlalchemy import Engine, create_engine, delete, select
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.config import Settings
from pigrocrm.core.customers.models import Customer
from pigrocrm.core.db import session_factory
from pigrocrm.core.db.role import ensure_application_role
from pigrocrm.core.invoices.models import Invoice
from pigrocrm.core.legal_entities.models import LegalEntity
from pigrocrm.core.storage import LocalFileStorage
from pigrocrm_mcp.server import build_server

_PREFIX = "ScopeMcp"
APP_ROLE = "pigrocrm_app_test"
APP_PASSWORD = "pigrocrm_app_test"


class World(NamedTuple):
    app: Engine
    studio: UUID
    ltd: UUID
    studio_customer: UUID
    ltd_customer: UUID
    studio_invoice: UUID
    ltd_invoice: UUID


def _customer_and_invoice(session: Session, legal_entity: UUID, tag: str) -> tuple[UUID, UUID]:
    customer = Customer(
        legal_name=f"{_PREFIX} {tag}", country="IT", custom_fields={}, legal_entity_id=legal_entity
    )
    session.add(customer)
    session.flush()
    invoice = Invoice(
        customer_id=customer.id,
        legal_entity_id=legal_entity,
        type="invoice",
        status="issued",
        payment_status="unpaid",
        year=2026,
        number=1,
        taxable_amount=Decimal("100.00"),
        vat_amount=Decimal("0.00"),
        stamp_duty=Decimal("0.00"),
        total=Decimal("100.00"),
        issue_date=date(2026, 3, 10),
        due_date=date(2026, 3, 10),
        memo=f"{_PREFIX} {tag} fattura",
        sdi_document_type="TD01",
        currency="EUR",
        custom_fields={},
    )
    session.add(invoice)
    session.flush()
    return customer.id, invoice.id


@pytest.fixture
def world(mcp_engine: Engine) -> Iterator[World]:
    factory = session_factory(mcp_engine)
    with factory() as session:
        studio = session.execute(
            select(LegalEntity).where(LegalEntity.predefinita.is_(True))
        ).scalar_one()
        ltd = LegalEntity(nome=f"{_PREFIX} ltd", legal_name=f"{_PREFIX} Ltd", country="GB")
        session.add(ltd)
        session.flush()
        studio_customer, studio_invoice = _customer_and_invoice(session, studio.id, "studio")
        ltd_customer, ltd_invoice = _customer_and_invoice(session, ltd.id, "ltd")
        session.commit()
        app_url = mcp_engine.url.set(username=APP_ROLE, password=APP_PASSWORD)
        assert ensure_application_role(mcp_engine.url, app_url) is True
        ids = World(
            create_engine(app_url, future=True),
            studio.id,
            ltd.id,
            studio_customer,
            ltd_customer,
            studio_invoice,
            ltd_invoice,
        )
    try:
        yield ids
    finally:
        ids.app.dispose()
        with factory() as session:
            session.execute(delete(Invoice).where(Invoice.id.in_([studio_invoice, ltd_invoice])))
            session.execute(
                delete(Customer).where(Customer.id.in_([studio_customer, ltd_customer]))
            )
            session.execute(delete(LegalEntity).where(LegalEntity.id == ltd.id))
            session.commit()


def _payload(result: Any) -> dict[str, Any]:
    return result.structured_content or json.loads(result.content[0].text)


async def _as(world: World, actor: Actor, tmp_path: Path) -> tuple[set[str], set[str]]:
    """What `search_everything` and `list_invoices` answer `actor`, on one session of the
    application role: the ids the search finds under the prefix, and the invoice ids."""
    session = session_factory(world.app)()
    try:
        server = build_server(
            lambda: session,
            lambda: actor,
            LocalFileStorage(tmp_path),
            Settings(_env_file=None),  # type: ignore[call-arg]
        )
        async with Client(server) as client:
            found = await client.call_tool("search_everything", {"deadline": _PREFIX})
            invoices = await client.call_tool("list_invoices", {})
    finally:
        session.close()
    hits = {hit["id"] for group in _payload(found)["gruppi"] for hit in group["hits"]}
    return hits, {item["id"] for item in _payload(invoices)["items"]}


async def test_a_scoped_token_searches_and_lists_inside_its_scope(
    world: World, tmp_path: Path
) -> None:
    scoped = Actor(id=None, type="mcp", role="collaborator", legal_entities=(world.ltd,))
    hits, invoices = await _as(world, scoped, tmp_path)
    assert hits == {str(world.ltd_customer), str(world.ltd_invoice)}
    assert invoices == {str(world.ltd_invoice)}


async def test_an_unscoped_token_sees_both_legal_entities(world: World, tmp_path: Path) -> None:
    hits, invoices = await _as(world, Actor(id=None, type="mcp", role="admin"), tmp_path)
    assert hits == {
        str(world.studio_customer),
        str(world.ltd_customer),
        str(world.studio_invoice),
        str(world.ltd_invoice),
    }
    assert invoices == {str(world.studio_invoice), str(world.ltd_invoice)}


async def test_to_scope_with_no_legal_entity_left_sees_nothing(
    world: World, tmp_path: Path
) -> None:
    """Nothing turns an empty scope into «tutte» (spec §1.11), over MCP as over HTTP."""
    hits, invoices = await _as(
        world, Actor(id=None, type="mcp", role="collaborator", legal_entities=()), tmp_path
    )
    assert hits == set() and invoices == set()
