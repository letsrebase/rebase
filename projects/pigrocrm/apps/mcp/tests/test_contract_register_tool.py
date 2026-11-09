"""`get_contract_register`: the billed days of a contract with the proof behind each, read
by an agent. A read only: the PDF and the CSV are for the page (`test_mcp_surface_coverage`
lists them as bytes the MCP never returns), and nothing here writes."""

from datetime import date
from decimal import Decimal

from mcp import Client
from sqlalchemy.orm import Session

from pigrocrm.core.contracts.models import Contract
from pigrocrm.core.invoices.models import Invoice, InvoiceLine
from pigrocrm.core.organizations.models import Organization
from pigrocrm.core.work_units.models import WorkUnit


def _contract(mcp_session: Session) -> Contract:
    customer = Organization(legal_name="ACME Srl")
    mcp_session.add(customer)
    mcp_session.flush()
    contract = Contract(
        customer_id=customer.id,
        title="Consulenza",
        start=date(2026, 1, 1),
        renewal_type="none",
        termination_notice_days=30,
        billing_cadence="monthly",
        expense_policy={"kind": "not_reimbursable"},
    )
    mcp_session.add(contract)
    mcp_session.flush()
    return contract


async def test_the_register_lists_the_billed_days_and_their_total(server, mcp_session) -> None:
    contract = _contract(mcp_session)
    invoice = Invoice(customer_id=contract.customer_id, type="invoice", status="draft")
    mcp_session.add(invoice)
    mcp_session.flush()
    line = InvoiceLine(
        invoice_id=invoice.id,
        line_number=1,
        description="Attività",
        quantity=Decimal("1.000000"),
        unit_price=Decimal("1.000000"),
        total_price=Decimal("1.00"),
        vat_rate=Decimal("22.00"),
    )
    mcp_session.add(line)
    mcp_session.flush()
    billed = WorkUnit(
        contract_id=contract.id,
        invoice_line_id=line.id,
        date=date(2026, 10, 7),
        quantity=Decimal("1.50"),
        description="Giornata",
        status="worked",
    )
    mcp_session.add(billed)
    mcp_session.flush()
    billed.status = "invoiced"
    mcp_session.add(
        WorkUnit(
            contract_id=contract.id,
            date=date(2026, 10, 8),
            quantity=Decimal("1.00"),
            description="Non ancora fatturata",
            status="worked",
        )
    )
    mcp_session.commit()

    async with Client(server) as client:
        result = await client.call_tool(
            "get_contract_register",
            {"contract_id": str(contract.id), "date_from": "2026-10-01", "date_to": "2026-10-31"},
        )

    body = result.structured_content
    assert body["quantity_total"] == "1.50"
    assert [v["date"] for v in body["entries"]] == ["2026-10-07"]
    assert body["without_approval"] == 1


async def test_a_malformed_date_comes_back_as_guidance(server, mcp_session) -> None:
    contract = _contract(mcp_session)
    mcp_session.commit()

    async with Client(server) as client:
        result = await client.call_tool(
            "get_contract_register",
            {"contract_id": str(contract.id), "date_from": "ottobre", "date_to": "2026-10-31"},
        )

    assert result.is_error
    assert "errors.pydantic.dev" not in result.content[0].text
