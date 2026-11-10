"""The deal tools and resources over a deal's value lines, through the MCP client."""

import json
from typing import Any

import pytest
from mcp import Client
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.pipeline.service import PipelineService

SETUP = Actor(id=None, type="system", role="admin")  # setup is not an agent's (agent_actions.py)

UPFRONT = {"unit": "flat", "delivery_period": "one_off", "unit_price": 20000}
MAINTENANCE = {"unit": "flat", "delivery_period": "monthly", "unit_price": 500}


def _payload(result: Any) -> dict[str, Any]:
    return result.structured_content or json.loads(result.content[0].text)


async def _customer(client: Client) -> str:
    return _payload(await client.call_tool("create_organization", {"legal_name": "ACME"}))["id"]


async def test_an_upfront_and_an_open_ended_fee_answer_the_total_and_both_figures(
    server, mcp_session: Session
) -> None:
    PipelineService(mcp_session).seed_defaults(SETUP)
    async with Client(server) as client:
        customer_id = await _customer(client)
        deal = _payload(
            await client.call_tool(
                "create_deal",
                {
                    "name": "Sito e manutenzione",
                    "customer_id": customer_id,
                    "value_lines": [UPFRONT, MAINTENANCE],
                },
            )
        )

    assert deal["expected_value"] == "26000.00"
    assert deal["one_off_value"] == "20000.00"
    assert deal["monthly_recurring_value"] == "500.00"
    assert [line["unit_price"] for line in deal["value_lines"]] == ["20000.00", "500.00"]


async def test_expected_value_is_refused_on_create_naming_the_field(
    server, mcp_session: Session
) -> None:
    PipelineService(mcp_session).seed_defaults(SETUP)
    async with Client(server) as client:
        customer_id = await _customer(client)
        result = await client.call_tool(
            "create_deal",
            {"name": "X", "customer_id": customer_id, "expected_value": "26000"},
        )

    assert result.is_error
    message = result.content[0].text
    assert "expected_value" in message and "value_lines" in message
    assert "errors.pydantic.dev" not in message


async def test_expected_value_is_refused_on_update_naming_the_field(
    server, mcp_session: Session
) -> None:
    PipelineService(mcp_session).seed_defaults(SETUP)
    async with Client(server) as client:
        customer_id = await _customer(client)
        deal = _payload(
            await client.call_tool(
                "create_deal", {"name": "X", "customer_id": customer_id, "value_lines": [UPFRONT]}
            )
        )
        result = await client.call_tool(
            "update_deal", {"deal_id": deal["id"], "changes": {"expected_value": "1"}}
        )
        after = _payload(await client.call_tool("get_deal", {"deal_id": deal["id"]}))

    assert result.is_error
    assert "expected_value" in result.content[0].text and "value_lines" in result.content[0].text
    assert after["expected_value"] == "20000.00"


async def test_a_line_the_service_refuses_is_answered_in_the_products_words(
    server, mcp_session: Session
) -> None:
    PipelineService(mcp_session).seed_defaults(SETUP)
    async with Client(server) as client:
        customer_id = await _customer(client)
        result = await client.call_tool(
            "create_deal",
            {
                "name": "X",
                "customer_id": customer_id,
                "value_lines": [{"unit": "week", "delivery_period": "one_off", "unit_price": 1}],
            },
        )

    assert result.is_error
    assert "errors.pydantic.dev" not in result.content[0].text
    assert "unit" in result.content[0].text


async def test_update_deal_replaces_the_lines_and_get_deal_reads_them_back(
    server, mcp_session: Session
) -> None:
    PipelineService(mcp_session).seed_defaults(SETUP)
    async with Client(server) as client:
        customer_id = await _customer(client)
        deal = _payload(
            await client.call_tool(
                "create_deal",
                {"name": "X", "customer_id": customer_id, "value_lines": [UPFRONT, MAINTENANCE]},
            )
        )
        updated = _payload(
            await client.call_tool(
                "update_deal",
                {"deal_id": deal["id"], "changes": {"value_lines": [MAINTENANCE]}},
            )
        )
        read = _payload(await client.call_tool("get_deal", {"deal_id": deal["id"]}))

    assert updated["expected_value"] == "6000.00"
    assert "value_lines" in updated["changed"]
    assert read["one_off_value"] == "0.00"
    assert len(read["value_lines"]) == 1


async def test_preview_deal_value_computes_without_saving(server, mcp_session: Session) -> None:
    PipelineService(mcp_session).seed_defaults(SETUP)
    async with Client(server) as client:
        preview = _payload(
            await client.call_tool(
                "preview_deal_value",
                {
                    "value_lines": [
                        UPFRONT,
                        {
                            "unit": "day",
                            "delivery_period": "monthly",
                            "unit_price": 600,
                            "quantity": 4,
                            "duration_months": 12,
                        },
                    ],
                    "expected_close_date": "2026-11-01",
                },
            )
        )
        refused = await client.call_tool(
            "preview_deal_value",
            {"value_lines": [{"unit": "day", "delivery_period": "monthly", "unit_price": 600}]},
        )
        deals = _payload(await client.call_tool("search_deals", {}))

    assert preview == {
        "expected_value": "48800.00",
        "one_off_value": "20000.00",
        "monthly_recurring_value": "2400.00",
    }
    assert refused.is_error and "value_lines" in refused.content[0].text
    assert deals["items"] == []


@pytest.mark.parametrize("uri_kind", ["deal", "organization"])
async def test_the_resources_print_the_lines_and_the_figures(
    server, mcp_session: Session, uri_kind: str
) -> None:
    PipelineService(mcp_session).seed_defaults(SETUP)
    async with Client(server) as client:
        customer_id = await _customer(client)
        deal = _payload(
            await client.call_tool(
                "create_deal",
                {
                    "name": "X",
                    "customer_id": customer_id,
                    "value_lines": [{**UPFRONT, "description": "Sviluppo"}, MAINTENANCE],
                },
            )
        )
        uri = f"deal://{deal['id']}" if uri_kind == "deal" else f"organization://{customer_id}"
        text = (await client.read_resource(uri)).contents[0].text

    assert "one-off 20000.00" in text or "One-off value: 20000.00" in text
    assert "500.00" in text
    if uri_kind == "deal":
        assert "Sviluppo: 20000.00 per flat, one_off" in text
        assert "500.00 per flat, monthly open-ended" in text
