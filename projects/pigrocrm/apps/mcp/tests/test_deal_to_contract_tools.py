"""`preview_deal_contract` and `convert_deal_to_contract` through the MCP client (REB-933)."""

import json
from typing import Any

from mcp import Client
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.pipeline.service import PipelineService

SETUP = Actor(id=None, type="system", role="admin")  # setup is not an agent's (agent_actions.py)

UPFRONT = {"unit": "flat", "delivery_period": "one_off", "unit_price": 20000, "start": "2026-02-01"}
DAY_RATE = {
    "unit": "day",
    "delivery_period": "monthly",
    "unit_price": 600,
    "quantity": 4,
    "start": "2026-02-01",
    "duration_months": 12,
}


def _payload(result: Any) -> dict[str, Any]:
    return result.structured_content or json.loads(result.content[0].text)


async def _won_deal(client: Client, session: Session) -> str:
    customer = _payload(await client.call_tool("create_customer", {"legal_name": "ACME"}))["id"]
    deal = _payload(
        await client.call_tool(
            "create_deal",
            {"name": "Sito", "customer_id": customer, "value_lines": [UPFRONT, DAY_RATE]},
        )
    )
    won = next(s.id for s in PipelineService(session).list() if s.type == "won")
    await client.call_tool("move_deal", {"deal_id": deal["id"], "stage_id": str(won)})
    return deal["id"]


async def test_an_agent_reads_the_proposal_then_converts_the_won_deal(
    server, mcp_session: Session
) -> None:
    PipelineService(mcp_session).seed_defaults(SETUP)
    async with Client(server) as client:
        deal_id = await _won_deal(client, mcp_session)

        proposal = _payload(await client.call_tool("preview_deal_contract", {"deal_id": deal_id}))
        assert (proposal["title"], proposal["start"], proposal["end"]) == (
            "Sito",
            "2026-02-01",
            "2027-01-31",
        )
        assert [card["type"] for card in proposal["cards"]] == ["one_off", "daily"]

        contract = _payload(
            await client.call_tool(
                "convert_deal_to_contract",
                {
                    "deal_id": deal_id,
                    "title": proposal["title"],
                    "start": proposal["start"],
                    "end": proposal["end"],
                    "renewal_type": "none",
                    "termination_notice_days": 30,
                    "billing_cadence": "monthly",
                    "expense_policy": {"kind": "not_reimbursable"},
                },
            )
        )
        assert (contract["status"], contract["deal_id"]) == ("draft", deal_id)

        deal = _payload(await client.call_tool("get_deal", {"deal_id": deal_id}))
        assert deal["contract_id"] == contract["id"]


async def test_a_second_conversion_is_refused_naming_the_contract(
    server, mcp_session: Session
) -> None:
    PipelineService(mcp_session).seed_defaults(SETUP)
    arguments = {
        "title": "Sito",
        "start": "2026-02-01",
        "end": None,
        "renewal_type": "none",
        "termination_notice_days": 30,
        "billing_cadence": "monthly",
        "expense_policy": {"kind": "not_reimbursable"},
    }
    async with Client(server) as client:
        deal_id = await _won_deal(client, mcp_session)
        await client.call_tool("convert_deal_to_contract", {"deal_id": deal_id, **arguments})
        again = await client.call_tool(
            "convert_deal_to_contract", {"deal_id": deal_id, **arguments}
        )

    assert again.is_error
    assert "già diventato il contratto" in again.content[0].text
