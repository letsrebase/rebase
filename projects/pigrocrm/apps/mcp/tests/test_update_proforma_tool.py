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
from pigrocrm.core.clock import oggi_in_italia
from pigrocrm.core.customers.schemas import CustomerCreate
from pigrocrm.core.customers.service import CustomerService
from pigrocrm.core.emitter.schemas import AziendaUpsert
from pigrocrm.core.emitter.service import AziendaService
from pigrocrm.core.fiscal.schemas import FiscalProfileUpsert
from pigrocrm.core.fiscal.service import FiscalProfileService
from pigrocrm.core.invoices.schemas import InvoiceCreate, InvoiceIssue, InvoiceLineIn
from pigrocrm.core.invoices.service import InvoiceService
from pigrocrm.core.storage import LocalFileStorage

RIGA = {"descrizione": "Consulenza", "quantita": "2", "prezzo_unitario": "100.00"}
UMANO = Actor(id=None, type="user", role="admin")


def _customer(session: Session) -> str:
    FiscalProfileService(session).upsert(FiscalProfileUpsert(codice_regime="RF19"), Actor.system())
    AziendaService(session).upsert_default(
        AziendaUpsert(
            ragione_sociale="Studio Rossi",
            partita_iva="01234567890",
            indirizzo="Via Vittorio Veneto 12",
            cap="20124",
            comune="Milano",
            provincia="MI",
            email="mario@example.com",
        ),
        Actor(id=None, type="mcp", role="admin"),
    )
    customer = CustomerService(session).create(
        CustomerCreate(
            ragione_sociale="Bozze S.r.l.",
            partita_iva="12345678901",
            codice_sdi="ABCDEFG",
            indirizzo="Corso Italia 5",
            cap="00100",
            comune="Roma",
            provincia="RM",
            nazione="IT",
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
            "create_proforma", {"customer_id": customer_id, "righe": [RIGA]}
        )
        assert not created.is_error, created.content[0].text
        body = created.structured_content
        assert body["data_emissione"] == oggi_in_italia().isoformat()
        assert body["competenza_da"] is None and body["competenza_a"] is None

        dated = await client.call_tool(
            "create_proforma",
            {
                "customer_id": customer_id,
                "righe": [RIGA],
                "causale": "FDE, agosto 2026",
                "data_emissione": "2026-09-05",
                "competenza_da": "2026-08-01",
                "competenza_a": "2026-08-31",
            },
        )
        assert not dated.is_error, dated.content[0].text
        body = dated.structured_content
        assert body["data_emissione"] == "2026-09-05"
        assert (body["competenza_da"], body["competenza_a"]) == ("2026-08-01", "2026-08-31")


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
            {"customer_id": customer_id, "righe": [RIGA], "causale": "Consulenza"},
        )
        assert not created.is_error, created.content[0].text
        invoice_id = created.structured_content["id"]

        updated = await client.call_tool(
            "update_proforma",
            {
                "invoice_id": invoice_id,
                "data_emissione": "2026-09-05",
                "competenza_da": "2026-08-01",
                "competenza_a": "2026-08-31",
            },
        )
        assert not updated.is_error, updated.content[0].text
        body = updated.structured_content
        assert body["causale"] == "Consulenza"
        assert body["data_emissione"] == "2026-09-05"
        assert (body["competenza_da"], body["competenza_a"]) == ("2026-08-01", "2026-08-31")

        moved = await client.call_tool(
            "update_proforma", {"invoice_id": invoice_id, "competenza_a": "2026-09-30"}
        )
        assert not moved.is_error, moved.content[0].text
        assert moved.structured_content["competenza_da"] == "2026-08-01"
        assert moved.structured_content["competenza_a"] == "2026-09-30"

        # The service's own rule, rendered as guidance and not as a raw dump.
        half = await client.call_tool(
            "update_proforma", {"invoice_id": invoice_id, "competenza_da": "2026-10-01"}
        )
        assert half.is_error
        assert "periodo di competenza" in half.content[0].text
        assert "errors.pydantic.dev" not in half.content[0].text

        read = await client.call_tool("get_invoice", {"invoice_id": invoice_id})
        assert read.structured_content["competenza_a"] == "2026-09-30"


async def test_update_proforma_refuses_a_fattura_and_a_consumed_proforma(
    server: Any, mcp_session: Session, tmp_path: Any
) -> None:
    """A fattura, even in draft, is refused by the same guard and with the same wording
    as `replace_proforma_lines`; a proforma consumed by an emission passes the guard on
    its `tipo` and is refused by the service, because its header is frozen with the
    document it became."""
    customer_id = _customer(mcp_session)
    service = InvoiceService(mcp_session, LocalFileStorage(tmp_path / "fatture"))
    fattura = service.create(
        InvoiceCreate(customer_id=UUID(customer_id), tipo="fattura", righe=[InvoiceLineIn(**RIGA)]),
        UMANO,
    )
    async with Client(server) as client:
        refused = await client.call_tool(
            "update_proforma", {"invoice_id": str(fattura.id), "causale": "Altro"}
        )
        assert refused.is_error
        assert "da MCP si modificano solo le proforma" in refused.content[0].text
        read = await client.call_tool("get_invoice", {"invoice_id": str(fattura.id)})
        assert read.structured_content["causale"] is None

        created = await client.call_tool(
            "create_proforma",
            {"customer_id": customer_id, "righe": [RIGA], "data_emissione": "2026-09-05"},
        )
        assert not created.is_error, created.content[0].text
        proforma_id = created.structured_content["id"]

    service.confirm_proforma(UUID(proforma_id), UMANO)
    issued = service.issue(UUID(proforma_id), InvoiceIssue(), UMANO)
    assert issued.numero is not None

    async with Client(server) as client:
        frozen = await client.call_tool(
            "update_proforma", {"invoice_id": proforma_id, "data_emissione": "2026-09-06"}
        )
        assert frozen.is_error
        assert "consumata" in frozen.content[0].text
        read = await client.call_tool("get_invoice", {"invoice_id": proforma_id})
        assert read.structured_content["data_emissione"] == "2026-09-05"
