"""Invoices and proforma, read and prepare; issuing is behind full access."""

from collections.abc import Callable
from typing import Any, cast
from uuid import UUID

from mcp.server import MCPServer

from pigrocrm.core.invoices.schemas import InvoiceListQuery, InvoiceStatus, InvoiceType
from pigrocrm_mcp.annotations import ADDITIVE, DESTRUCTIVE, READ_ONLY
from pigrocrm_mcp.context import McpContext
from pigrocrm_mcp.tools import (
    invoices,
)
from pigrocrm_mcp.tools.params import (
    BoundedLimit,
    IsoDateStr,
    iso_date,
    literal_arg,
    parse_legal_entity_id,
)


def register(mcp: MCPServer, context: McpContext, guard: Callable[..., Any]) -> None:
    # -- Invoices -------------------------------------------------------------
    #
    # Reads, the proforma, and the two identity writes. `issue`, `annul`,
    # `mark_transmitted_externally` and `export_xml` are deliberately absent, and the
    # absence is the mechanism: a personal access token inherits its owner's full role
    # and never expires, so a permission check inside a registered tool would be a check
    # an admin's token passes. A tool that does not exist cannot be called by anyone.
    #
    # `test_mcp_invoice_ban.py` reads every module under `tools/` and fails if any of the four
    # names above appears as a registered tool, so the guarantee survives someone adding
    # one later without reading this comment.

    @mcp.tool(annotations=READ_ONLY)
    @guard
    def list_invoices(
        customer_id: str | None = None,
        deal_id: str | None = None,
        type: str | None = None,
        status: str | None = None,
        year: int | None = None,
        exclude_consumed: bool = False,
        legal_entity_id: str | None = None,
        limit: BoundedLimit = 50,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        """Elenca fatture e proforma. `exclude_consumed` lascia fuori le proforma già emesse come
        fattura: la fattura porta il loro numero e `origin_proforma_id` punta alla proforma.
        `legal_entity_id` filtra per azienda. PDF e XML si scaricano dall'API REST: qui ci sono solo
        identificativi.
        """
        return invoices.search(
            context,
            InvoiceListQuery(
                customer_id=UUID(customer_id) if customer_id else None,
                deal_id=UUID(deal_id) if deal_id else None,
                legal_entity_id=parse_legal_entity_id(legal_entity_id),
                type=literal_arg("type", type, InvoiceType),
                status=literal_arg("status", status, InvoiceStatus),
                year=year,
                exclude_consumed=exclude_consumed,
                limit=cast(int, limit),
                cursor=UUID(cursor) if cursor else None,
            ),
        )

    @mcp.tool(annotations=READ_ONLY)
    @guard
    def list_invoice_register_gaps(
        year: int, legal_entity_id: str | None = None
    ) -> list[dict[str, Any]]:
        """I numeri che il registro di `year` non porta per dichiarazione esplicita, con il motivo.
        Vuoto se non ci sono buchi dichiarati. `legal_entity_id` omesso: azienda predefinita.
        """
        return invoices.list_register_gaps(context, year, legal_entity_id)

    @mcp.tool(annotations=READ_ONLY)
    @guard
    def get_invoice(invoice_id: str) -> dict[str, Any]:
        """Legge una fattura o una proforma: numero, stato, totali e righe."""
        return invoices.get(context, invoice_id)

    @mcp.tool(annotations=ADDITIVE)
    @guard
    def create_proforma(
        customer_id: str,
        lines: list[dict[str, Any]],
        deal_id: str | None = None,
        memo: str | None = None,
        issue_date: IsoDateStr = None,
        accrual_from: IsoDateStr = None,
        accrual_to: IsoDateStr = None,
        purchase_order_number: str | None = None,
        purchase_order_date: IsoDateStr = None,
        purchase_order_tender_code: str | None = None,
        purchase_order_project_code: str | None = None,
    ) -> dict[str, Any]:
        """Crea una proforma. Non è un documento fiscale: non prende un numero, non produce XML per
        il Sistema di Interscambio e diventa fattura solo quando una persona la emette.

        `issue_date` (YYYY-MM-DD): oggi se manca; la fattura prenderà la propria data.
        `accrual_from` e `accrual_to` (YYYY-MM-DD, insieme o nessuno): il periodo di competenza
        del lavoro, per esempio il mese. `purchase_order_number`, `purchase_order_date` (YYYY-MM-DD), `purchase_order_tender_code`
        (10 caratteri) e `purchase_order_project_code` (15) sono l'ordine d'acquisto del cliente.
        """
        return invoices.create_proforma(
            context,
            {
                "customer_id": UUID(customer_id),
                "deal_id": UUID(deal_id) if deal_id else None,
                "memo": memo,
                "issue_date": iso_date(issue_date),
                "accrual_from": iso_date(accrual_from),
                "accrual_to": iso_date(accrual_to),
                "purchase_order_number": purchase_order_number,
                "purchase_order_date": iso_date(purchase_order_date),
                "purchase_order_tender_code": purchase_order_tender_code,
                "purchase_order_project_code": purchase_order_project_code,
                "lines": lines,
            },
        )

    @mcp.tool(annotations=DESTRUCTIVE)
    @guard
    def update_proforma(
        invoice_id: str,
        memo: str | None = None,
        issue_date: IsoDateStr = None,
        due_date: IsoDateStr = None,
        accrual_from: IsoDateStr = None,
        accrual_to: IsoDateStr = None,
        purchase_order_number: str | None = None,
        purchase_order_date: IsoDateStr = None,
        purchase_order_tender_code: str | None = None,
        purchase_order_project_code: str | None = None,
    ) -> dict[str, Any]:
        """Corregge l'intestazione di una proforma ancora modificabile: causale, data, scadenza,
        competenza e ordine d'acquisto. Cambia solo ciò che passi; un campo omesso resta com'è e
        non si svuota niente. Senza `due_date`, all'emissione la calcolano i termini di
        pagamento del cliente (`expected_due_date` su `get_invoice`). Rifiuta una fattura e una
        proforma già consumata da un'emissione. Le righe si cambiano con
        `replace_proforma_lines`.
        """
        return invoices.update_proforma(
            context,
            invoice_id,
            memo=memo,
            issue_date=iso_date(issue_date),
            due_date=iso_date(due_date),
            accrual_from=iso_date(accrual_from),
            accrual_to=iso_date(accrual_to),
            purchase_order_number=purchase_order_number,
            purchase_order_date=iso_date(purchase_order_date),
            purchase_order_tender_code=purchase_order_tender_code,
            purchase_order_project_code=purchase_order_project_code,
        )

    @mcp.tool(annotations=DESTRUCTIVE)
    @guard
    def replace_proforma_lines(invoice_id: str, lines: list[dict[str, Any]]) -> dict[str, Any]:
        """Sostituisce **tutte** le righe di una proforma e ricalcola i totali. Rifiuta una fattura
        emessa.
        """
        return invoices.replace_proforma_lines(context, invoice_id, lines)

    @mcp.tool(annotations=ADDITIVE)
    @guard
    def confirm_proforma(invoice_id: str) -> dict[str, Any]:
        """Conferma una proforma in bozza: l'importo è concordato e il documento è pronto per
        l'emissione. Non consuma numeri e non tocca il registro. Rifiuta una fattura, una
        proforma già confermata o consumata e una senza righe. Il passo successivo è
        `issue_invoice`, disponibile solo dove l'installazione l'ha aperto.
        """
        return invoices.confirm_proforma(context, invoice_id)

    @mcp.tool(annotations=DESTRUCTIVE)
    @guard
    def discard_proforma(invoice_id: str) -> dict[str, Any]:
        """Scarta una proforma: sparisce dagli elenchi e non si ripristina, se serve si ricrea.
        Rifiuta una fattura e una proforma già consumata da un'emissione.
        """
        return invoices.discard_proforma(context, invoice_id)

    @mcp.tool(annotations=ADDITIVE)
    @guard
    def render_proforma_pdf(invoice_id: str) -> dict[str, Any]:
        """Genera il PDF di una proforma e restituisce l'identificativo del documento. I byte si
        scaricano dall'API REST.
        """
        return invoices.render_proforma_pdf(context, invoice_id)

    @mcp.tool(annotations=READ_ONLY)
    @guard
    def get_invoice_xml_url(invoice_id: str) -> dict[str, Any]:
        """Il percorso REST da cui scaricare il file FatturaPA di una fattura emessa. Non
        restituisce i byte.
        """
        return invoices.xml_url(context, invoice_id)

    @mcp.tool(annotations=ADDITIVE)
    @guard
    def set_invoice_payment_state(
        invoice_id: str, payment_status: str, payment_date: str | None = None
    ) -> dict[str, Any]:
        """Registra un incasso o lo annulla. Non cambia lo stato fiscale della fattura, che resta
        emessa.
        """
        return invoices.set_payment_state(context, invoice_id, payment_status, payment_date)
