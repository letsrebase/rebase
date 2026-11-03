"""What an agent is told when it sends a wrong argument: schemas that list the real fields,
and guidance instead of a raw pydantic dump."""

import json

from mcp import Client
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.pipeline.service import PipelineService

SETUP = Actor(id=None, type="system", role="admin")  # setup is not an agent's (agent_actions.py)


def _payload(result) -> dict:
    return result.structured_content or json.loads(result.content[0].text)


async def test_update_customer_schema_lists_the_real_modifiable_fields(server) -> None:
    """A model calling list_tools() must see the actual field names it can send in
    `changes`, not an opaque `{"type": "object"}` it has to guess or infer from
    create_customer's sibling schema."""
    async with Client(server) as client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}

    changes_schema = tools["update_customer"].input_schema["properties"]["changes"]
    assert "legal_name" in changes_schema["properties"]
    assert "vat_number" in changes_schema["properties"]


async def test_update_deal_schema_lists_the_real_modifiable_fields(server) -> None:
    async with Client(server) as client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}

    changes_schema = tools["update_deal"].input_schema["properties"]["changes"]
    assert "probability" in changes_schema["properties"]
    assert "expected_value" in changes_schema["properties"]
    # move_deal, not update_deal, is the supported way to change stage -- the
    # schema must not invite an agent to try setting it here instead.
    assert "pipeline_stage_id" not in changes_schema["properties"]


async def test_update_person_schema_documents_what_detach_does(server) -> None:
    """`detach` reads as a bare boolean unless its schema explains the effect --
    the field name alone does not say it unlinks the person from their customer."""
    async with Client(server) as client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}

    changes_schema = tools["update_person"].input_schema["properties"]["changes"]
    detach_schema = changes_schema["properties"]["detach"]
    description = detach_schema.get("description", "").lower()
    assert description
    assert "client" in description or "customer" in description


async def test_create_deal_documents_the_expected_date_format(server) -> None:
    async with Client(server) as client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}

    create_deal_tool = tools["create_deal"]
    assert "YYYY-MM-DD" in (create_deal_tool.description or "")


async def test_a_malformed_identifier_produces_guidance_not_a_stack_trace(
    server, mcp_session: Session
) -> None:
    """§8.2: an error must be actionable by a model. `uuid.UUID("not-a-uuid")`
    raises a bare `ValueError` with an English, unstructured message; the guard
    must render it the same way as every other domain error."""
    async with Client(server) as client:
        result = await client.call_tool("get_customer", {"customer_id": "not-a-uuid"})

    assert result.is_error
    message = result.content[0].text
    assert "errors.pydantic.dev" not in message
    assert "badly formed" not in message


async def test_a_wrong_typed_value_inside_changes_produces_guidance(
    server, mcp_session: Session
) -> None:
    """Fix 1 makes `changes` a real, typed schema; this proves the type-checking
    that now happens when building the update model still comes back through the
    guard's rendering, not as a raw pydantic dump with a errors.pydantic.dev link."""
    PipelineService(mcp_session).seed_defaults(SETUP)
    async with Client(server) as client:
        customer = _payload(await client.call_tool("create_customer", {"legal_name": "ACME"}))
        deal = _payload(
            await client.call_tool("create_deal", {"nome": "X", "customer_id": customer["id"]})
        )
        result = await client.call_tool(
            "update_deal", {"deal_id": deal["id"], "changes": {"probability": "not-a-number"}}
        )

    assert result.is_error
    message = result.content[0].text
    assert "errors.pydantic.dev" not in message
    assert "probability" in message


async def test_a_limit_out_of_range_produces_guidance_naming_the_range(
    server, mcp_session: Session
) -> None:
    async with Client(server) as client:
        result = await client.call_tool("search_customers", {"limit": 500})

    assert result.is_error
    message = result.content[0].text
    assert "errors.pydantic.dev" not in message
    assert "200" in message


# --- Final review item 9 (IMPORTANT): the remaining wrong-TYPE scalars still ---
# --- leaked pydantic internals -- a wrong value on `limit` was already fixed  ---
# --- (Task 17), but a wrong *type* on `limit`/`probability`/the money fields  ---
# --- bypassed the guard entirely, at the SDK's own pre-call argument coercion. --


async def test_a_wrong_typed_limit_produces_guidance_not_a_raw_pydantic_dump(
    server, mcp_session: Session
) -> None:
    """The exact reproduction named in the review: before BoundedLimit existed,
    this came back as a raw, multi-line, English pydantic dump carrying an
    errors.pydantic.dev link, because the SDK's own pre-call coercion for a bare
    `limit: int` parameter rejected "molti" before `_guard` ever ran."""
    async with Client(server) as client:
        result = await client.call_tool("search_customers", {"limit": "molti"})

    assert result.is_error
    message = result.content[0].text
    assert "errors.pydantic.dev" not in message
    assert "limit" in message


async def test_to_wrong_typed_probability_on_create_deal_produces_guidance(
    server, mcp_session: Session
) -> None:
    PipelineService(mcp_session).seed_defaults(SETUP)
    async with Client(server) as client:
        customer = _payload(await client.call_tool("create_customer", {"legal_name": "ACME"}))
        result = await client.call_tool(
            "create_deal",
            {"nome": "X", "customer_id": customer["id"], "probability": "molto alta"},
        )

    assert result.is_error
    message = result.content[0].text
    assert "errors.pydantic.dev" not in message
    assert "probability" in message


async def test_a_wrong_typed_money_field_on_create_deal_produces_guidance(
    server, mcp_session: Session
) -> None:
    PipelineService(mcp_session).seed_defaults(SETUP)
    async with Client(server) as client:
        customer = _payload(await client.call_tool("create_customer", {"legal_name": "ACME"}))
        result = await client.call_tool(
            "create_deal",
            {"nome": "X", "customer_id": customer["id"], "expected_value": "un sacco"},
        )

    assert result.is_error
    message = result.content[0].text
    assert "errors.pydantic.dev" not in message
    assert "expected_value" in message


async def test_create_deal_still_accepts_to_normal_numeric_probability(
    server, mcp_session: Session
) -> None:
    """The permissive `int | str` type must not regress the ordinary path."""
    PipelineService(mcp_session).seed_defaults(SETUP)
    async with Client(server) as client:
        customer = _payload(await client.call_tool("create_customer", {"legal_name": "ACME"}))
        deal = _payload(
            await client.call_tool(
                "create_deal",
                {"nome": "X", "customer_id": customer["id"], "probability": 42},
            )
        )

    assert deal["probability"] == 42


# --- Final review item 5 (CRITICAL): get_timeline's limit was unbounded, unlike --
# --- REST's Query(ge=1, le=200) on all three timeline routes. ---------------------


async def test_get_timeline_rejects_a_negative_limit_with_guidance_not_a_crash(
    server, mcp_session: Session
) -> None:
    """Before ActivityService.timeline bounded its own `limit`, -1 reached Postgres
    raw as `psycopg.errors.InvalidRowCountInLimitClause` -- an exception `_guard`
    did not (and, per item 6, could not fully) recover from cleanly."""
    async with Client(server) as client:
        customer = _payload(await client.call_tool("create_customer", {"legal_name": "ACME"}))
        result = await client.call_tool(
            "get_timeline",
            {"entity_type": "customer", "entity_id": customer["id"], "limit": -1},
        )

    assert result.is_error
    message = result.content[0].text
    assert "errors.pydantic.dev" not in message
    assert "1-200" in message


async def test_get_timeline_rejects_an_oversized_limit_matching_rest_exactly(
    server, mcp_session: Session
) -> None:
    """`10**9` used to succeed here and return everything, where REST would refuse
    the same request outright -- a parity divergence between the two adapters, not
    only a crash risk."""
    async with Client(server) as client:
        customer = _payload(await client.call_tool("create_customer", {"legal_name": "ACME"}))
        result = await client.call_tool(
            "get_timeline",
            {"entity_type": "customer", "entity_id": customer["id"], "limit": 10**9},
        )

    assert result.is_error
    message = result.content[0].text
    assert "1-200" in message


async def test_get_timeline_still_accepts_an_in_range_limit(server, mcp_session: Session) -> None:
    async with Client(server) as client:
        customer = _payload(await client.call_tool("create_customer", {"legal_name": "ACME"}))
        result = _payload(
            await client.call_tool(
                "get_timeline",
                {"entity_type": "customer", "entity_id": customer["id"], "limit": 50},
            )
        )

    assert isinstance(result["entries"], list)


# --- The WithJsonSchema override must not disturb the ordinary, valid path. ---
