"""`create_contract_expense`/`update_contract_expense`/`list_contract_expenses`:
thin calls into `ContractExpenseService`, proved here as real MCP tool round trips
-- `reimbursable` must reach the client as the database computed it, and a
malformed `pre_authorized`/`authorization_reference` pair must come back as
guidance, not a raw pydantic dump (spec §8.2's own convention, `_guard`)."""

from datetime import date

from mcp import Client
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.contracts.models import Contract
from pigrocrm.core.organizations.models import Organization
from pigrocrm.core.timetracking.models import CostCategory

ADMIN = Actor(id=None, type="mcp", role="admin")


def _contract_id(mcp_session: Session, *, expense_policy: dict[str, object]) -> str:
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
        expense_policy=expense_policy,
    )
    mcp_session.add(contract)
    mcp_session.flush()
    mcp_session.commit()
    return str(contract.id)


def _category_id(mcp_session: Session) -> str:
    category = CostCategory(name="Trasferte", position=0)
    mcp_session.add(category)
    mcp_session.flush()
    mcp_session.commit()
    return str(category.id)


async def test_create_reports_the_trigger_computed_reimbursable(server, mcp_session) -> None:
    contract_id = _contract_id(mcp_session, expense_policy={"kind": "reimbursable"})
    category_id = _category_id(mcp_session)

    async with Client(server) as client:
        created = await client.call_tool(
            "create_contract_expense",
            {
                "contract_id": contract_id,
                "category_id": category_id,
                "date": "2026-03-05",
                "amount": "42.00",
                "description": "Biglietto treno",
            },
        )
        expense = created.structured_content
        assert expense["reimbursable"] is True
        assert expense["amount"] == "42.00"


async def test_create_flags_non_reimbursable_without_failing(server, mcp_session) -> None:
    contract_id = _contract_id(
        mcp_session,
        expense_policy={"kind": "reimbursable", "requires_pre_authorization": True},
    )
    category_id = _category_id(mcp_session)

    async with Client(server) as client:
        created = await client.call_tool(
            "create_contract_expense",
            {
                "contract_id": contract_id,
                "category_id": category_id,
                "date": "2026-03-05",
                "amount": "42.00",
                "description": "Biglietto treno",
                "pre_authorized": False,
            },
        )
        assert created.structured_content["reimbursable"] is False


async def test_a_bad_argument_comes_back_as_guidance_not_a_pydantic_dump(
    server, mcp_session
) -> None:
    """Spec §8.2: `pre_authorized=true` with no `authorization_reference` fails
    `ContractExpenseCreate`'s own cross-field check -- `_guard` must turn that into
    a message an LLM can act on, not the SDK's own raw validation error text."""
    contract_id = _contract_id(mcp_session, expense_policy={"kind": "reimbursable"})
    category_id = _category_id(mcp_session)

    async with Client(server) as client:
        result = await client.call_tool(
            "create_contract_expense",
            {
                "contract_id": contract_id,
                "category_id": category_id,
                "date": "2026-03-05",
                "amount": "42.00",
                "description": "Biglietto treno",
                "pre_authorized": True,
            },
        )
        assert result.is_error
        message = result.content[0].text
        assert "errors.pydantic.dev" not in message


async def test_update_recomputes_reimbursable_and_list_reflects_it(server, mcp_session) -> None:
    contract_id = _contract_id(
        mcp_session,
        expense_policy={"kind": "reimbursable", "requires_pre_authorization": True},
    )
    category_id = _category_id(mcp_session)

    async with Client(server) as client:
        created = await client.call_tool(
            "create_contract_expense",
            {
                "contract_id": contract_id,
                "category_id": category_id,
                "date": "2026-03-05",
                "amount": "42.00",
                "description": "Biglietto treno",
                "pre_authorized": False,
            },
        )
        expense_id = created.structured_content["id"]
        assert created.structured_content["reimbursable"] is False

        updated = await client.call_tool(
            "update_contract_expense",
            {
                "contract_id": contract_id,
                "expense_id": expense_id,
                "changes": {
                    "pre_authorized": True,
                    "authorization_reference": "email del 2026-03-02",
                },
            },
        )
        assert updated.structured_content["reimbursable"] is True

        listed = await client.call_tool("list_contract_expenses", {"contract_id": contract_id})
        assert [item["id"] for item in listed.structured_content["items"]] == [expense_id]
