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
        """List invoices and proformas. `exclude_consumed` leaves out proformas already issued as an
        invoice: the invoice carries their number and `origin_proforma_id` points to the proforma.
        `legal_entity_id` filters by legal entity. PDFs and XML are downloaded from the REST API:
        only identifiers are returned here.
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
        """The numbers the `year` register does not carry by explicit declaration, with the reason.
        Empty if no gaps are declared. `legal_entity_id` omitted: default legal entity.
        """
        return invoices.list_register_gaps(context, year, legal_entity_id)

    @mcp.tool(annotations=READ_ONLY)
    @guard
    def get_invoice(invoice_id: str) -> dict[str, Any]:
        """Read an invoice or a proforma: number, status, totals and lines."""
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
        """Create a proforma. It is not a fiscal document: it takes no number, produces no XML for
        the SdI (Sistema di Interscambio) and becomes an invoice only when a person issues it.

        `issue_date` (YYYY-MM-DD): today if missing; the invoice will take its own date.
        `accrual_from` and `accrual_to` (YYYY-MM-DD, both or neither): the accrual period
        of the work, for example the month. `purchase_order_number`, `purchase_order_date`
        (YYYY-MM-DD), `purchase_order_tender_code` (10 characters) and `purchase_order_project_code`
        (15) are the customer's purchase order.
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
        """Correct the header of a proforma that is still editable: memo, date, due date,
        accrual and purchase order. Changes only what you pass; an omitted field stays as it is and
        nothing is cleared. Without `due_date`, it is computed at issue time from the customer's
        payment terms (`expected_due_date` on `get_invoice`). Rejects an invoice and a
        proforma already consumed by an issue. Lines are changed with
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
        """Replace **all** the lines of a proforma and recompute the totals. Rejects an issued
        invoice.
        """
        return invoices.replace_proforma_lines(context, invoice_id, lines)

    @mcp.tool(annotations=ADDITIVE)
    @guard
    def confirm_proforma(invoice_id: str) -> dict[str, Any]:
        """Confirm a draft proforma: the amount is agreed and the document is ready for
        issuing. Consumes no numbers and does not touch the register. Rejects an invoice, a
        proforma already confirmed or consumed, and one with no lines. The next step is
        `issue_invoice`, available only where the installation has enabled it.
        """
        return invoices.confirm_proforma(context, invoice_id)

    @mcp.tool(annotations=DESTRUCTIVE)
    @guard
    def discard_proforma(invoice_id: str) -> dict[str, Any]:
        """Discard a proforma: it disappears from the lists and cannot be restored; recreate it if
        needed. Rejects an invoice and a proforma already consumed by an issue.
        """
        return invoices.discard_proforma(context, invoice_id)

    @mcp.tool(annotations=ADDITIVE)
    @guard
    def render_proforma_pdf(invoice_id: str) -> dict[str, Any]:
        """Generate the PDF of a proforma and return the document identifier. The bytes are
        downloaded from the REST API.
        """
        return invoices.render_proforma_pdf(context, invoice_id)

    @mcp.tool(annotations=READ_ONLY)
    @guard
    def get_invoice_xml_url(invoice_id: str) -> dict[str, Any]:
        """The REST path to download the FatturaPA file of an issued invoice. It does not
        return the bytes.
        """
        return invoices.xml_url(context, invoice_id)

    @mcp.tool(annotations=ADDITIVE)
    @guard
    def set_invoice_payment_state(
        invoice_id: str, payment_status: str, payment_date: str | None = None
    ) -> dict[str, Any]:
        """Record a payment or undo it. Does not change the invoice's fiscal status, which stays
        issued.
        """
        return invoices.set_payment_state(context, invoice_id, payment_status, payment_date)
