from decimal import Decimal

from mcp import Client
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.customers.schemas import CustomerCreate
from pigrocrm.core.customers.service import CustomerService
from pigrocrm.core.deals.schemas import DealCreate
from pigrocrm.core.deals.service import DealService
from pigrocrm.core.pipeline.service import PipelineService

SETUP = Actor(id=None, type="system", role="admin")  # setup is not an agent's (agent_actions.py)


def _line(text: str, prefix: str) -> str:
    return next(line for line in text.splitlines() if line.startswith(prefix))


async def test_a_zero_valued_deal_shows_zero_not_a_dash(server, mcp_session: Session) -> None:
    """`Decimal("0.00")` is falsy in Python; a naive `x or '—'` cannot tell a real
    zero (a pro-bono engagement, a full discount) from a field nobody filled in.
    An agent reading "—" concludes the data is missing and may ask a human to
    supply it, when the true answer is already there and is zero."""
    PipelineService(mcp_session).seed_defaults(SETUP)
    customer = CustomerService(mcp_session).create(CustomerCreate(legal_name="Pro Bono Srl"), SETUP)
    deal = DealService(mcp_session).create(
        DealCreate(
            name="Consulenza gratuita",
            customer_id=customer.id,
            expected_value=Decimal("0.00"),
            budgeted_hours=Decimal("0.00"),
            budgeted_value=Decimal("0.00"),
        ),
        SETUP,
    )

    async with Client(server) as client:
        deal_text = (await client.read_resource(f"deal://{deal.id}")).contents[0].text
        customer_text = (await client.read_resource(f"customer://{customer.id}")).contents[0].text

    assert _line(deal_text, "- Expected value:") == "- Expected value: 0.00"
    assert _line(deal_text, "- Budgeted hours:") == "- Budgeted hours: 0.00"
    assert _line(deal_text, "- Budgeted value:") == "- Budgeted value: 0.00"
    assert "expected value 0.00" in customer_text


async def test_address_line_omits_missing_parts_cleanly(server, mcp_session: Session) -> None:
    """A customer with no street/CAP/comune/provincia beyond the country default
    must not render as `- Address: —,   () IT` (a dash, a comma, a double
    space, and empty parentheses): only the parts actually present should
    appear, and the placeholder dash is reserved for when there is truly
    nothing to show."""
    customer = CustomerService(mcp_session).create(
        CustomerCreate(legal_name="Solo Nazione Srl"), SETUP
    )

    async with Client(server) as client:
        text = (await client.read_resource(f"customer://{customer.id}")).contents[0].text

    assert _line(text, "- Address:") == "- Address: IT"
