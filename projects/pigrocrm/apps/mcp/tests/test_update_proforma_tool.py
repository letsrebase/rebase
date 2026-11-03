"""`update_proforma` (ORB-61, ORB-63): the header of the one document an agent shapes.

`create_proforma` makes a proforma and `replace_proforma_lines` rewrites its lines;
until this tool existed nothing on the surface could move the document's own date or
state the period the work belongs to, so "la proforma di agosto" was a causale and
nothing the XML or the P&L could read. The tool stops where the others stop: at anything
that is not a proforma, before the service's own `ImmutableField` refuses a consumed one.
"""

from typing import Any
from uuid import UUID

from mcp import Client
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.customers.schemas import CustomerCreate
from pigrocrm.core.customers.service import CustomerService
from pigrocrm.core.db.clock import today_local
from pigrocrm.core.fiscal.schemas import FiscalProfileUpsert
from pigrocrm.core.fiscal.service import FiscalProfileService
from pigrocrm.core.invoices.schemas import InvoiceCreate, InvoiceIssue, InvoiceLineIn
from pigrocrm.core.invoices.service import InvoiceService
from pigrocrm.core.legal_entities.schemas import LegalEntityUpsert
from pigrocrm.core.legal_entities.service import LegalEntityService
from pigrocrm.core.storage import LocalFileStorage

LINE = {"description": "Consulenza", "quantity": "2", "unit_price": "100.00"}
HUMAN_ACTOR = Actor(id=None, type="user", role="admin")


def _customer(session: Session) -> str:
    FiscalProfileService(session).upsert(FiscalProfileUpsert(regime_code="RF19"), Actor.system())
    LegalEntityService(session).upsert_default(
        LegalEntityUpsert(
            legal_name="Studio Rossi",
            vat_number="01234567890",
            address="Via Vittorio Veneto 12",
            postal_code="20124",
            city="Milano",
            province="MI",
            email="mario@example.com",
        ),
        Actor(id=None, type="mcp", role="admin"),
    )
    customer = CustomerService(session).create(
        CustomerCreate(
            legal_name="Bozze S.r.l.",
            vat_number="12345678901",
            sdi_code="ABCDEFG",
            address="Corso Italia 5",
            postal_code="00100",
            city="Roma",
            province="RM",
            country="IT",
        ),
        Actor(id=None, type="mcp", role="admin"),
    )
    return str(customer.id)


async def test_a_proforma_is_created_dated_today_and_takes_a_period_and_a_date(
    server: Any, mcp_session: Session
) -> None:
    customer_id = _customer(mcp_session)
    async with Client(server) as client:
        created = await client.call_tool(
            "create_proforma", {"customer_id": customer_id, "lines": [LINE]}
        )
        assert not created.is_error, created.content[0].text
        body = created.structured_content
        assert body["issue_date"] == today_local().isoformat()
        assert body["accrual_from"] is None and body["accrual_to"] is None

        dated = await client.call_tool(
            "create_proforma",
            {
                "customer_id": customer_id,
                "lines": [LINE],
                "memo": "FDE, agosto 2026",
                "issue_date": "2026-09-05",
                "accrual_from": "2026-08-01",
                "accrual_to": "2026-08-31",
            },
        )
        assert not dated.is_error, dated.content[0].text
        body = dated.structured_content
        assert body["issue_date"] == "2026-09-05"
        assert (body["accrual_from"], body["accrual_to"]) == ("2026-08-01", "2026-08-31")


async def test_update_proforma_moves_the_date_and_sets_the_period_on_a_draft(
    server: Any, mcp_session: Session
) -> None:
    """Only what is passed changes: the causale set at creation survives an update that
    names the date and the period alone, and a later call that names one end of the
    period moves that end while the other stays."""
    customer_id = _customer(mcp_session)
    async with Client(server) as client:
        created = await client.call_tool(
            "create_proforma",
            {"customer_id": customer_id, "lines": [LINE], "memo": "Consulenza"},
        )
        assert not created.is_error, created.content[0].text
        invoice_id = created.structured_content["id"]

        updated = await client.call_tool(
            "update_proforma",
            {
                "invoice_id": invoice_id,
                "issue_date": "2026-09-05",
                "accrual_from": "2026-08-01",
                "accrual_to": "2026-08-31",
            },
        )
        assert not updated.is_error, updated.content[0].text
        body = updated.structured_content
        assert body["memo"] == "Consulenza"
        assert body["issue_date"] == "2026-09-05"
        assert (body["accrual_from"], body["accrual_to"]) == ("2026-08-01", "2026-08-31")

        moved = await client.call_tool(
            "update_proforma", {"invoice_id": invoice_id, "accrual_to": "2026-09-30"}
        )
        assert not moved.is_error, moved.content[0].text
        assert moved.structured_content["accrual_from"] == "2026-08-01"
        assert moved.structured_content["accrual_to"] == "2026-09-30"

        # The service's own rule, rendered as guidance and not as a raw dump.
        half = await client.call_tool(
            "update_proforma", {"invoice_id": invoice_id, "accrual_from": "2026-10-01"}
        )
        assert half.is_error
        assert "periodo di competenza" in half.content[0].text
        assert "errors.pydantic.dev" not in half.content[0].text

        read = await client.call_tool("get_invoice", {"invoice_id": invoice_id})
        assert read.structured_content["accrual_to"] == "2026-09-30"


async def test_update_proforma_refuses_to_invoice_and_to_consumed_proforma(
    server: Any, mcp_session: Session, tmp_path: Any
) -> None:
    """A fattura, even in draft, is refused by the same guard and with the same wording
    as `replace_proforma_lines`; a proforma consumed by an emission passes the guard on
    its `type` and is refused by the service, because its header is frozen with the
    document it became."""
    customer_id = _customer(mcp_session)
    service = InvoiceService(mcp_session, LocalFileStorage(tmp_path / "invoices"))
    invoice = service.create(
        InvoiceCreate(customer_id=UUID(customer_id), type="invoice", lines=[InvoiceLineIn(**LINE)]),
        HUMAN_ACTOR,
    )
    async with Client(server) as client:
        refused = await client.call_tool(
            "update_proforma", {"invoice_id": str(invoice.id), "memo": "Altro"}
        )
        assert refused.is_error
        assert "da MCP si modificano solo le proforma" in refused.content[0].text
        read = await client.call_tool("get_invoice", {"invoice_id": str(invoice.id)})
        assert read.structured_content["memo"] is None

        created = await client.call_tool(
            "create_proforma",
            {"customer_id": customer_id, "lines": [LINE], "issue_date": "2026-09-05"},
        )
        assert not created.is_error, created.content[0].text
        proforma_id = created.structured_content["id"]

    service.confirm_proforma(UUID(proforma_id), HUMAN_ACTOR)
    issued = service.issue(UUID(proforma_id), InvoiceIssue(), HUMAN_ACTOR)
    assert issued.number is not None

    async with Client(server) as client:
        frozen = await client.call_tool(
            "update_proforma", {"invoice_id": proforma_id, "issue_date": "2026-09-06"}
        )
        assert frozen.is_error
        assert "consumata" in frozen.content[0].text
        read = await client.call_tool("get_invoice", {"invoice_id": proforma_id})
        assert read.structured_content["issue_date"] == "2026-09-05"


async def test_a_proforma_takes_its_purchase_order_at_creation_and_on_update(
    server: Any, mcp_session: Session
) -> None:
    """REB-693: the order goes in on `create_proforma`, a tender code (CIG) is added on
    `update_proforma` without touching what was stored, `get_invoice` reads it back,
    and a CIG with no number comes back as the service's sentence, not a raw dump."""
    customer_id = _customer(mcp_session)
    async with Client(server) as client:
        created = await client.call_tool(
            "create_proforma",
            {
                "customer_id": customer_id,
                "lines": [LINE],
                "purchase_order_number": "ODA-14",
                "purchase_order_date": "2026-10-09",
            },
        )
        assert not created.is_error, created.content[0].text
        invoice_id = created.structured_content["id"]
        assert created.structured_content["purchase_order_number"] == "ODA-14"

        updated = await client.call_tool(
            "update_proforma",
            {"invoice_id": invoice_id, "purchase_order_tender_code": "z0a1b2c3d4"},
        )
        assert not updated.is_error, updated.content[0].text
        assert updated.structured_content["purchase_order_tender_code"] == "Z0A1B2C3D4"
        assert updated.structured_content["purchase_order_date"] == "2026-10-09"

        read = await client.call_tool("get_invoice", {"invoice_id": invoice_id})
        assert read.structured_content["purchase_order_number"] == "ODA-14"

        orphan = await client.call_tool(
            "create_proforma",
            {
                "customer_id": customer_id,
                "lines": [LINE],
                "purchase_order_tender_code": "Z0A1B2C3D4",
            },
        )
        assert orphan.is_error
        assert "numero dell'ordine" in orphan.content[0].text
        assert "errors.pydantic.dev" not in orphan.content[0].text
