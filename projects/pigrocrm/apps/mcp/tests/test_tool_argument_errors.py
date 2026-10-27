"""REB-808: a wrong or missing tool argument is answered in Italian, naming the field,
whatever the tool.

The SDK validates a call against the tool's signature before its body, so before `_guard`,
and used to answer with pydantic's English dump and its `errors.pydantic.dev` link. A
parameter the tool does not have was dropped without a word. Both are now refused by
`GuidedMCPServer.call_tool`, once for every tool.
"""

from uuid import uuid4

import pytest
from mcp import Client
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.pipeline.service import PipelineService

ADMIN = Actor(id=None, type="mcp", role="admin")


def _assert_guidance(message: str) -> None:
    assert "errors.pydantic.dev" not in message
    assert "validation error" not in message
    assert "Field required" not in message
    assert "Correggi il valore indicato e riprova" in message


async def test_a_missing_parameter_names_it_and_lists_what_the_tool_takes(server) -> None:
    async with Client(server) as client:
        result = await client.call_tool("get_customer", {})

    assert result.is_error
    message = result.content[0].text
    _assert_guidance(message)
    assert "customer_id" in message
    assert "campo obbligatorio mancante" in message
    assert "get_customer" in message


async def test_a_parameter_the_tool_does_not_have_is_refused_with_the_closest_name(
    server,
) -> None:
    """`move_deal`'s own parameter is `stage_id`; the deal field is `pipeline_stage_id`,
    the one an agent that has just read a deal reaches for. On a tool without the alias
    the misspelling is named and the right name offered."""
    async with Client(server) as client:
        result = await client.call_tool("get_customer", {"customer_id": str(uuid4()), "id": "x"})

    assert result.is_error
    message = result.content[0].text
    _assert_guidance(message)
    assert "parametro non riconosciuto" in message
    assert "`id`" in message or "input.id" in message
    assert "customer_id" in message


async def test_a_misspelt_search_parameter_is_no_longer_silently_ignored(server) -> None:
    async with Client(server) as client:
        result = await client.call_tool("search_customers", {"limite": 5})

    assert result.is_error
    message = result.content[0].text
    assert "limite" in message
    assert "forse `limit`" in message


async def test_move_deal_with_a_wrong_name_gets_italian_guidance_naming_stage_id(
    server,
) -> None:
    async with Client(server) as client:
        result = await client.call_tool(
            "move_deal", {"deal_id": str(uuid4()), "stage": str(uuid4())}
        )

    assert result.is_error
    message = result.content[0].text
    _assert_guidance(message)
    assert "stage_id" in message
    assert "pipeline_stage_id" in message


async def test_move_deal_without_a_destination_asks_for_one(server) -> None:
    async with Client(server) as client:
        result = await client.call_tool("move_deal", {"deal_id": str(uuid4())})

    assert result.is_error
    message = result.content[0].text
    _assert_guidance(message)
    assert "stage_id" in message
    assert "list_pipeline_stages" in message


async def test_move_deal_accepts_the_deals_own_field_name(server, mcp_session: Session) -> None:
    PipelineService(mcp_session).seed_defaults(ADMIN)
    async with Client(server) as client:
        customer = (
            await client.call_tool("create_customer", {"ragione_sociale": "ACME"})
        ).structured_content
        deal = (
            await client.call_tool("create_deal", {"nome": "X", "customer_id": customer["id"]})
        ).structured_content
        stages = (await client.call_tool("list_pipeline_stages", {})).structured_content["stages"]
        target = next(stage for stage in stages if stage["id"] != deal["pipeline_stage_id"])
        moved = await client.call_tool(
            "move_deal", {"deal_id": deal["id"], "pipeline_stage_id": target["id"]}
        )

    assert not moved.is_error, moved.content[0].text
    assert moved.structured_content["pipeline_stage_id"] == target["id"]


async def test_move_deal_with_two_different_destinations_is_refused(server) -> None:
    async with Client(server) as client:
        result = await client.call_tool(
            "move_deal",
            {
                "deal_id": str(uuid4()),
                "stage_id": str(uuid4()),
                "pipeline_stage_id": str(uuid4()),
            },
        )

    assert result.is_error
    assert "due stati diversi" in result.content[0].text


@pytest.mark.parametrize(
    ("tool", "arguments", "field"),
    [
        ("search_customers", {"cursor": 5}, "cursor"),
        ("describe_schema", {"entity_type": "fattura"}, "entity_type"),
        ("describe_schema", {"entity_type": 7}, "entity_type"),
        ("update_customer", {"customer_id": str(uuid4()), "changes": "note"}, "changes"),
        ("search_everything", {"termine": "ab"}, "termine"),
    ],
)
async def test_a_wrongly_typed_or_out_of_range_argument_names_its_field(
    server, tool: str, arguments: dict, field: str
) -> None:
    async with Client(server) as client:
        result = await client.call_tool(tool, arguments)

    assert result.is_error
    message = result.content[0].text
    _assert_guidance(message)
    assert field in message


async def test_a_wrong_enum_value_lists_the_allowed_ones(server) -> None:
    async with Client(server) as client:
        result = await client.call_tool("describe_schema", {"entity_type": "fattura"})

    message = result.content[0].text
    assert "non è uno dei valori ammessi" in message
    assert "`customer`" in message


async def test_a_short_search_text_is_asked_for_in_italian(server) -> None:
    async with Client(server) as client:
        result = await client.call_tool("search_everything", {"termine": "ab"})

    assert result.is_error
    assert "almeno 3 caratteri" in result.content[0].text
    assert "should have" not in result.content[0].text


async def test_too_many_digits_is_explained_not_called_invalid(
    server, seeded_deal_id, seeded_user_id
) -> None:
    async with Client(server) as client:
        result = await client.call_tool(
            "log_time",
            {
                "deal_id": str(seeded_deal_id),
                "user_id": str(seeded_user_id),
                "data": "2026-03-10",
                "ore": "1.123456789",
                "descrizione": "x",
            },
        )

    assert result.is_error
    message = result.content[0].text
    assert "errors.pydantic.dev" not in message
    assert "ha troppe cifre" in message
    assert "al massimo" in message


async def test_move_deal_with_an_empty_alias_uses_the_other_name(server) -> None:
    async with Client(server) as client:
        result = await client.call_tool(
            "move_deal", {"deal_id": str(uuid4()), "stage_id": "", "pipeline_stage_id": ""}
        )

    assert result.is_error
    assert "campo obbligatorio mancante" in result.content[0].text
