"""`issue_invoice` answers the issued row even when the render fails (REB-143).

`issue` commits the number, then the tool renders the PDF and the XML in a second
transaction. A render that raised used to surface as a tool error, and an agent reading
«errore» after an emission that really happened is an agent that tries again and
consumes a second number. The tool lives behind `mcp_full_access`
(`test_mcp_invoice_ban.py` owns that); this file only checks what it answers.
"""

from pathlib import Path
from typing import Any

import pytest
from mcp import Client
from sqlalchemy import text
from sqlalchemy.orm import Session
from test_invoice_import_tools import _payload, _seed_fiscal_and_emitter_profiles, _server

from pigrocrm.core.actor import Actor
from pigrocrm.core.customers.models import Customer
from pigrocrm.core.invoices.schemas import InvoiceCreate, InvoiceLineIn
from pigrocrm.core.invoices.service import InvoiceService
from pigrocrm.core.storage import LocalFileStorage


def _draft(session: Session, tmp_path: Path) -> str:
    _seed_fiscal_and_emitter_profiles(session)
    customer = Customer(
        ragione_sociale="Acme S.r.l.",
        partita_iva="12345678901",
        codice_sdi="ABCDEFG",
        indirizzo="Via Roma 1",
        cap="20154",
        comune="Milano",
        provincia="MI",
        nazione="IT",
    )
    session.add(customer)
    session.flush()
    draft = InvoiceService(session, LocalFileStorage(tmp_path)).create(
        InvoiceCreate(
            customer_id=customer.id,
            righe=[InvoiceLineIn(descrizione="Consulenza", prezzo_unitario="1000.00")],
        ),
        Actor(id=None, type="mcp", role="admin"),
    )
    return str(draft.id)


async def test_a_render_that_fails_after_the_commit_still_answers_the_issued_row(
    mcp_session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The XML export fails with a statement PostgreSQL refuses, after the PDF was
    committed: the transaction is aborted, so the answer also proves the tool rolled it
    back before reading the row again."""

    def aborts_the_transaction(self: InvoiceService, *_args: object, **_kwargs: object) -> None:
        self.session.execute(text("SELECT 1/0"))

    monkeypatch.setattr(InvoiceService, "export_xml", aborts_the_transaction)
    invoice_id = _draft(mcp_session, tmp_path)
    async with Client(_server(mcp_session, tmp_path, full_access=True)) as client:
        result = await client.call_tool("issue_invoice", {"invoice_id": invoice_id})
        assert not result.is_error, result.content[0].text
        issued: dict[str, Any] = _payload(result)
        assert issued["id"] == invoice_id
        assert issued["stato"] == "emessa"
        assert issued["numero"] == 1
        assert issued["pdf_document_id"] is not None
        assert issued["xml_document_id"] is None

        # The next call on the same session works, and the register agrees.
        again = await client.call_tool("get_invoice", {"invoice_id": invoice_id})
        assert not again.is_error, again.content[0].text
        assert _payload(again)["stato"] == "emessa"
