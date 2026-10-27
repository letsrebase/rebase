"""Invoices and proforma, read and prepare; issuing is behind full access."""

from collections.abc import Callable
from typing import Any, cast
from uuid import UUID

from mcp.server import MCPServer

from pigrocrm.core.invoices.schemas import InvoiceListQuery
from pigrocrm_mcp.annotations import ADDITIVE, DESTRUCTIVE, READ_ONLY
from pigrocrm_mcp.context import McpContext
from pigrocrm_mcp.tools import (
    invoices,
)
from pigrocrm_mcp.tools.invoices import parse_legal_entity_id
from pigrocrm_mcp.tools.params import BoundedLimit, IsoDateStr, iso_date


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
        tipo: str | None = None,
        stato: str | None = None,
        anno: int | None = None,
        escludi_consumate: bool = False,
        azienda_id: str | None = None,
        limit: BoundedLimit = 50,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        """Elenca fatture e proforma. `escludi_consumate` lascia fuori le proforma già emesse come
        fattura: la fattura porta il loro numero e `origine_proforma_id` punta alla proforma.
        `azienda_id` filtra per azienda. PDF e XML si scaricano dall'API REST: qui ci sono solo
        identificativi.
        """
        return invoices.search(
            context,
            InvoiceListQuery(
                customer_id=UUID(customer_id) if customer_id else None,
                deal_id=UUID(deal_id) if deal_id else None,
                azienda_id=parse_legal_entity_id(azienda_id),
                tipo=tipo,  # type: ignore[arg-type]
                stato=stato,  # type: ignore[arg-type]
                anno=anno,
                escludi_consumate=escludi_consumate,
                limit=cast(int, limit),
                cursor=UUID(cursor) if cursor else None,
            ),
        )

    @mcp.tool(annotations=READ_ONLY)
    @guard
    def list_invoice_register_gaps(
        anno: int, azienda_id: str | None = None
    ) -> list[dict[str, Any]]:
        """I numeri che il registro di `anno` non porta per dichiarazione esplicita, con il motivo.
        Vuoto se non ci sono buchi dichiarati. `azienda_id` omesso: azienda predefinita.
        """
        return invoices.list_register_gaps(context, anno, azienda_id)

    @mcp.tool(annotations=READ_ONLY)
    @guard
    def get_invoice(invoice_id: str) -> dict[str, Any]:
        """Legge una fattura o una proforma: numero, stato, totali e righe."""
        return invoices.get(context, invoice_id)

    @mcp.tool(annotations=ADDITIVE)
    @guard
    def create_proforma(
        customer_id: str,
        righe: list[dict[str, Any]],
        deal_id: str | None = None,
        causale: str | None = None,
        data_emissione: IsoDateStr = None,
        competenza_da: IsoDateStr = None,
        competenza_a: IsoDateStr = None,
        ordine_numero: str | None = None,
        ordine_data: IsoDateStr = None,
        ordine_cig: str | None = None,
        ordine_cup: str | None = None,
    ) -> dict[str, Any]:
        """Crea una proforma. Non è un documento fiscale: non prende un numero, non produce XML per
        il Sistema di Interscambio e diventa fattura solo quando una persona la emette.

        `data_emissione` (YYYY-MM-DD): oggi se manca; la fattura prenderà la propria data.
        `competenza_da` e `competenza_a` (YYYY-MM-DD, insieme o nessuno): il periodo di competenza
        del lavoro, per esempio il mese. `ordine_numero`, `ordine_data` (YYYY-MM-DD), `ordine_cig`
        (10 caratteri) e `ordine_cup` (15) sono l'ordine d'acquisto del cliente.
        """
        return invoices.create_proforma(
            context,
            {
                "customer_id": UUID(customer_id),
                "deal_id": UUID(deal_id) if deal_id else None,
                "causale": causale,
                "data_emissione": iso_date(data_emissione),
                "competenza_da": iso_date(competenza_da),
                "competenza_a": iso_date(competenza_a),
                "ordine_numero": ordine_numero,
                "ordine_data": iso_date(ordine_data),
                "ordine_cig": ordine_cig,
                "ordine_cup": ordine_cup,
                "righe": righe,
            },
        )

    @mcp.tool(annotations=DESTRUCTIVE)
    @guard
    def update_proforma(
        invoice_id: str,
        causale: str | None = None,
        data_emissione: IsoDateStr = None,
        data_scadenza: IsoDateStr = None,
        competenza_da: IsoDateStr = None,
        competenza_a: IsoDateStr = None,
        ordine_numero: str | None = None,
        ordine_data: IsoDateStr = None,
        ordine_cig: str | None = None,
        ordine_cup: str | None = None,
    ) -> dict[str, Any]:
        """Corregge l'intestazione di una proforma ancora modificabile: causale, data, scadenza,
        competenza e ordine d'acquisto. Cambia solo ciò che passi; un campo omesso resta com'è e
        non si svuota niente. Senza `data_scadenza`, all'emissione la calcolano i termini di
        pagamento del cliente (`scadenza_prevista` su `get_invoice`). Rifiuta una fattura e una
        proforma già consumata da un'emissione. Le righe si cambiano con
        `replace_proforma_lines`.
        """
        return invoices.update_proforma(
            context,
            invoice_id,
            causale=causale,
            data_emissione=iso_date(data_emissione),
            data_scadenza=iso_date(data_scadenza),
            competenza_da=iso_date(competenza_da),
            competenza_a=iso_date(competenza_a),
            ordine_numero=ordine_numero,
            ordine_data=iso_date(ordine_data),
            ordine_cig=ordine_cig,
            ordine_cup=ordine_cup,
        )

    @mcp.tool(annotations=DESTRUCTIVE)
    @guard
    def replace_proforma_lines(invoice_id: str, righe: list[dict[str, Any]]) -> dict[str, Any]:
        """Sostituisce **tutte** le righe di una proforma e ricalcola i totali. Rifiuta una fattura
        emessa.
        """
        return invoices.replace_proforma_lines(context, invoice_id, righe)

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
        invoice_id: str, stato_pagamento: str, data_incasso: str | None = None
    ) -> dict[str, Any]:
        """Registra un incasso o lo annulla. Non cambia lo stato fiscale della fattura, che resta
        emessa.
        """
        return invoices.set_payment_state(context, invoice_id, stato_pagamento, data_incasso)
