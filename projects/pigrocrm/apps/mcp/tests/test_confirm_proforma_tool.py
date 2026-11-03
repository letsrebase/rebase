"""`confirm_proforma` (ORB-132): the step between shaping a proforma and issuing it.

Until this existed the surface could create a proforma, rewrite it and discard it, and
`issue_invoice` (behind `mcp_full_access`) refused it: the service issues a *confirmed*
proforma, and nothing here confirmed one. The confirmation itself touches no register
and consumes nothing, and a confirmed proforma stays editable and discardable, which is
why it sits with the other proforma tools and not in `privileged.py`; the irreversible
step stays where it was.
"""

from typing import Any

from mcp import Client
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.customers.schemas import CustomerCreate
from pigrocrm.core.customers.service import CustomerService
from pigrocrm.core.fiscal.schemas import FiscalProfileUpsert
from pigrocrm.core.fiscal.service import FiscalProfileService

LINE = {"description": "Consulenza", "quantity": "2", "unit_price": "100.00"}


def _customer(session: Session) -> str:
    FiscalProfileService(session).upsert(FiscalProfileUpsert(regime_code="RF19"), Actor.system())
    customer = CustomerService(session).create(
        CustomerCreate(legal_name="Bozze S.r.l.", vat_number="12345678901"),
        Actor(id=None, type="mcp", role="admin"),
    )
    return str(customer.id)


async def test_a_draft_proforma_with_lines_is_confirmed(server: Any, mcp_session: Session) -> None:
    customer_id = _customer(mcp_session)
    async with Client(server) as client:
        created = await client.call_tool(
            "create_proforma", {"customer_id": customer_id, "lines": [LINE]}
        )
        assert not created.is_error, created.content[0].text
        invoice_id = created.structured_content["id"]
        assert created.structured_content["status"] == "draft"

        confirmed = await client.call_tool("confirm_proforma", {"invoice_id": invoice_id})
        assert not confirmed.is_error, confirmed.content[0].text
        assert confirmed.structured_content["id"] == invoice_id
        assert confirmed.structured_content["status"] == "confirmed"
        # Still a proforma, still without a number: nothing fiscal happened.
        assert confirmed.structured_content["type"] == "proforma"
        assert confirmed.structured_content["number"] is None

        again = await client.call_tool("get_invoice", {"invoice_id": invoice_id})
        assert again.structured_content["status"] == "confirmed"


async def test_a_proforma_without_lines_is_refused_with_the_field_named(
    server: Any, mcp_session: Session
) -> None:
    customer_id = _customer(mcp_session)
    async with Client(server) as client:
        created = await client.call_tool(
            "create_proforma", {"customer_id": customer_id, "lines": []}
        )
        assert not created.is_error, created.content[0].text
        invoice_id = created.structured_content["id"]
        refused = await client.call_tool("confirm_proforma", {"invoice_id": invoice_id})
        assert refused.is_error
        assert "senza righe" in refused.content[0].text


async def test_a_confirmed_proforma_is_not_confirmed_twice(
    server: Any, mcp_session: Session
) -> None:
    customer_id = _customer(mcp_session)
    async with Client(server) as client:
        created = await client.call_tool(
            "create_proforma", {"customer_id": customer_id, "lines": [LINE]}
        )
        invoice_id = created.structured_content["id"]
        first = await client.call_tool("confirm_proforma", {"invoice_id": invoice_id})
        assert not first.is_error, first.content[0].text
        second = await client.call_tool("confirm_proforma", {"invoice_id": invoice_id})
        assert second.is_error
        assert "non si puo' passare a 'confermata'" in second.content[0].text


async def test_a_confirmed_proforma_is_still_the_agent_s_to_shape(
    server: Any, mcp_session: Session
) -> None:
    """What makes the tool safe on the default surface: confirming forecloses nothing.
    The header and the lines stay editable (`_is_editable`) and the proforma can still be
    discarded; there is no way back to `draft` and none is needed."""
    customer_id = _customer(mcp_session)
    async with Client(server) as client:
        created = await client.call_tool(
            "create_proforma", {"customer_id": customer_id, "lines": [LINE]}
        )
        invoice_id = created.structured_content["id"]
        await client.call_tool("confirm_proforma", {"invoice_id": invoice_id})
        relined = await client.call_tool(
            "replace_proforma_lines",
            {"invoice_id": invoice_id, "lines": [{**LINE, "unit_price": "150.00"}]},
        )
        assert not relined.is_error, relined.content[0].text
        assert relined.structured_content["status"] == "confirmed"
        assert relined.structured_content["total"] == "300.00"
        discarded = await client.call_tool("discard_proforma", {"invoice_id": invoice_id})
        assert not discarded.is_error, discarded.content[0].text


async def test_anything_that_is_not_a_proforma_is_refused_before_the_service_is_reached(
    server: Any, mcp_session: Session
) -> None:
    """The same guard as `replace_proforma_lines` and `discard_proforma`: a fattura is
    refused with the surface's own reason, before `confirm_proforma` in the service gets
    to refuse it for its own."""
    from pigrocrm.core.invoices.schemas import InvoiceCreate, InvoiceLineIn
    from pigrocrm.core.invoices.service import InvoiceService
    from pigrocrm.core.storage import LocalFileStorage

    customer_id = _customer(mcp_session)
    invoice = InvoiceService(mcp_session, LocalFileStorage("/tmp/unused")).create(
        InvoiceCreate(
            customer_id=customer_id,
            lines=[InvoiceLineIn(description="Consulenza", unit_price="100.00")],
        ),
        Actor(id=None, type="mcp", role="admin"),
    )
    async with Client(server) as client:
        refused = await client.call_tool("confirm_proforma", {"invoice_id": str(invoice.id)})
        assert refused.is_error
        assert "over MCP only proformas can be edited" in refused.content[0].text


async def test_the_tool_is_on_the_default_surface_not_behind_the_switch(server: Any) -> None:
    """Confirming consumes nothing and forecloses nothing, so it is not one of the
    operations an installation has to open with `mcp_full_access`; the fixture server is
    a default installation and lists it. Whether `issue_invoice` is absent there is the
    ban test's assertion, not this one's."""
    async with Client(server) as client:
        names = {tool.name for tool in (await client.list_tools()).tools}
        assert "confirm_proforma" in names
