"""`discard_proforma` (ORB-37): the one removal an agent is allowed on the invoicing
surface, and the line it stops at.

A proforma is the only document an agent shapes from the MCP: `create_proforma` makes
it, `replace_proforma_lines` rewrites it. Removing one it got wrong is the natural end of
that shaping, and leaving an empty or wrong draft in the list was the alternative until
this tool existed. The removal is the same soft delete the application performs, and it
stops exactly where the application stops: at anything that is not a proforma, before
`InvoiceService.soft_delete` and the table CHECK behind it get to refuse a consumed
number for their own reasons.
"""

from typing import Any
from uuid import UUID

from mcp import Client
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
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


def _emitter(session: Session) -> None:
    # The issuer every PDF header prints: `render_proforma_pdf` refuses to render
    # without one, the same way `issue` does.
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


def _customer(session: Session, *, exportable: bool = False) -> str:
    # The regime is the install's own configuration, set as the system and not as the
    # agent, for the reason `test_full_cycle.py` spells out at its own fixture. The
    # exportable variant carries what `issue` checks on the recipient before consuming a
    # number: the four address parts, the P.IVA and the `codice_sdi`.
    FiscalProfileService(session).upsert(FiscalProfileUpsert(codice_regime="RF19"), Actor.system())
    recipient = (
        {
            "codice_sdi": "ABCDEFG",
            "indirizzo": "Corso Italia 5",
            "cap": "00100",
            "comune": "Roma",
            "provincia": "RM",
            "nazione": "IT",
        }
        if exportable
        else {}
    )
    customer = CustomerService(session).create(
        CustomerCreate(ragione_sociale="Bozze S.r.l.", partita_iva="12345678901", **recipient),
        Actor(id=None, type="mcp", role="admin"),
    )
    return str(customer.id)


async def test_a_proforma_draft_is_discarded_and_is_gone_from_the_reads(
    server: Any, mcp_session: Session
) -> None:
    customer_id = _customer(mcp_session)
    async with Client(server) as client:
        created = await client.call_tool(
            "create_proforma", {"customer_id": customer_id, "righe": [RIGA]}
        )
        assert not created.is_error, created.content[0].text
        invoice_id = created.structured_content["id"]
        assert created.structured_content["tipo"] == "proforma"

        discarded = await client.call_tool("discard_proforma", {"invoice_id": invoice_id})
        assert not discarded.is_error, discarded.content[0].text
        assert discarded.structured_content == {"status": "scartata", "invoice_id": invoice_id}

        # Gone from the invoice reads, not flagged: the soft delete is invisible there.
        read = await client.call_tool("get_invoice", {"invoice_id": invoice_id})
        assert read.is_error
        listed = await client.call_tool("list_invoices", {"customer_id": customer_id})
        assert [item["id"] for item in listed.structured_content["items"]] == []


async def test_a_discarded_proforma_takes_its_pdf_out_of_the_customers_documents(
    server: Any, mcp_session: Session
) -> None:
    """ORB-41: `render_proforma_pdf` files the PDF among the customer's documents, and
    until the service archived it together with its owner, `discard_proforma` left
    "Proforma PROV-... (PDF)" listed and downloadable while `get_invoice` answered not
    found. Both tools go through `InvoiceService.soft_delete`, so the MCP path is the
    place to show the two reads agree afterwards."""
    customer_id = _customer(mcp_session)
    _emitter(mcp_session)
    async with Client(server) as client:
        created = await client.call_tool(
            "create_proforma", {"customer_id": customer_id, "righe": [RIGA]}
        )
        assert not created.is_error, created.content[0].text
        invoice_id = created.structured_content["id"]
        rendered = await client.call_tool("render_proforma_pdf", {"invoice_id": invoice_id})
        assert not rendered.is_error, rendered.content[0].text
        document_id = rendered.structured_content["document_id"]

        listed = await client.call_tool("list_documents", {"customer_id": customer_id})
        assert [item["id"] for item in listed.structured_content["items"]] == [document_id]

        discarded = await client.call_tool("discard_proforma", {"invoice_id": invoice_id})
        assert not discarded.is_error, discarded.content[0].text

        listed = await client.call_tool("list_documents", {"customer_id": customer_id})
        assert listed.structured_content["items"] == []
        read = await client.call_tool("get_document", {"document_id": document_id})
        assert read.is_error


async def test_anything_that_is_not_a_proforma_is_refused_before_the_service_is_reached(
    server: Any, mcp_session: Session, tmp_path: Any
) -> None:
    """A fattura, even one still in draft, is not the agent's to remove: the same guard
    `replace_proforma_lines` applies, and the same wording, so the refusal explains
    itself rather than leaking the service's own message about numbers."""
    customer_id = _customer(mcp_session)
    fattura = InvoiceService(mcp_session, LocalFileStorage(tmp_path / "fatture")).create(
        InvoiceCreate(
            customer_id=customer_id,
            tipo="fattura",
            righe=[InvoiceLineIn(**RIGA)],
        ),
        Actor(id=None, type="user", role="admin"),
    )
    async with Client(server) as client:
        refused = await client.call_tool("discard_proforma", {"invoice_id": str(fattura.id)})
        assert refused.is_error
        assert "da MCP si modificano solo le proforma" in refused.content[0].text
        # And the draft is still there.
        read = await client.call_tool("get_invoice", {"invoice_id": str(fattura.id)})
        assert not read.is_error, read.content[0].text
        assert read.structured_content["tipo"] == "fattura"


async def test_a_proforma_consumed_by_an_emission_is_refused_and_stays_consumed(
    server: Any, mcp_session: Session, tmp_path: Any
) -> None:
    """`_require_proforma` lets a `consumata` proforma through, since its tipo is still
    `proforma`: the refusal here is the service's own (`stato == "consumata"`), with the
    table CHECK behind it. Tested on the MCP surface because the docstring promises it
    there, and because nothing else exercised a soft delete on a consumed proforma."""
    customer_id = _customer(mcp_session, exportable=True)
    _emitter(mcp_session)
    umano = Actor(id=None, type="user", role="admin")
    async with Client(server) as client:
        created = await client.call_tool(
            "create_proforma", {"customer_id": customer_id, "righe": [RIGA]}
        )
        assert not created.is_error, created.content[0].text
        proforma_id = created.structured_content["id"]

    # The human half: confirm, then issue from the proforma, which consumes it.
    service = InvoiceService(mcp_session, LocalFileStorage(tmp_path / "fatture"))
    service.confirm_proforma(UUID(proforma_id), umano)
    fattura = service.issue(UUID(proforma_id), InvoiceIssue(), umano)
    assert fattura.numero is not None
    assert str(fattura.origine_proforma_id) == proforma_id

    async with Client(server) as client:
        refused = await client.call_tool("discard_proforma", {"invoice_id": proforma_id})
        assert refused.is_error
        read = await client.call_tool("get_invoice", {"invoice_id": proforma_id})
        assert not read.is_error, read.content[0].text
        assert read.structured_content["stato"] == "consumata"
        # And the fattura born from it is refused one step earlier, by tipo.
        refused_fattura = await client.call_tool(
            "discard_proforma", {"invoice_id": str(fattura.id)}
        )
        assert refused_fattura.is_error
        assert "da MCP si modificano solo le proforma" in refused_fattura.content[0].text
