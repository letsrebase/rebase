import asyncio
import json

import pytest
from mcp import Client
from mcp.shared.exceptions import MCPError
from mcp.types import INVALID_PARAMS
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.errors import Conflict, NotFound
from pigrocrm.core.fields.schemas import FieldDefinitionCreate
from pigrocrm.core.fields.service import FieldDefinitionService
from pigrocrm_mcp.errors import to_agent_message
from pigrocrm_mcp.resources import entities
from pigrocrm_mcp.server import DOMAIN_REFUSAL

ADMIN = Actor(id=None, type="mcp", role="admin")


def _payload(result) -> dict:
    return result.structured_content or json.loads(result.content[0].text)


async def test_describe_schema_lists_native_and_custom_fields(server, mcp_session: Session) -> None:
    FieldDefinitionService(mcp_session).create(
        FieldDefinitionCreate(
            entity_type="customer", key="settore", label="Settore", field_type="text"
        ),
        ADMIN,
    )
    async with Client(server) as client:
        result = await client.call_tool("describe_schema", {"entity_type": "customer"})

    body = _payload(result)
    assert "ragione_sociale" in body["native_fields"]
    assert any(field["key"] == "settore" for field in body["custom_fields"])


async def test_describe_schema_reads_live_so_a_new_field_appears_at_once(
    server, mcp_session: Session
) -> None:
    """Tool schemas are fixed at registration, so describe_schema must not be. This is
    how an agent notices a field added from the web app while the server was running."""
    async with Client(server) as client:
        before = _payload(await client.call_tool("describe_schema", {"entity_type": "deal"}))
        assert before["custom_fields"] == []

        FieldDefinitionService(mcp_session).create(
            FieldDefinitionCreate(
                entity_type="deal", key="rischio", label="Rischio", field_type="text"
            ),
            ADMIN,
        )
        after = _payload(await client.call_tool("describe_schema", {"entity_type": "deal"}))

    assert [f["key"] for f in after["custom_fields"]] == ["rischio"]


async def test_the_tool_list_is_advertised(server) -> None:
    async with Client(server) as client:
        names = {tool.name for tool in (await client.list_tools()).tools}
    assert {"describe_schema", "refresh_schema"} <= names


async def test_refresh_schema_reports_what_it_rebuilt(server, mcp_session: Session) -> None:
    FieldDefinitionService(mcp_session).create(
        FieldDefinitionCreate(
            entity_type="customer", key="settore", label="Settore", field_type="text"
        ),
        ADMIN,
    )
    async with Client(server) as client:
        body = _payload(await client.call_tool("refresh_schema", {}))
    assert body["customer"] == 1


async def test_refresh_schema_notification_is_a_documented_sdk_limitation(server) -> None:
    """`refresh_schema` calls `ctx.session.send_tool_list_changed()` correctly (see
    the comment beside that call in server.py), but the installed SDK (mcp==2.0.0)
    only delivers it to a client that opened a `subscriptions/listen` stream when
    the connection negotiates the modern (2026-07-28) protocol -- the default a
    plain `Client` connection negotiates, and what every other test in this file
    uses. This documents the observed gap (and that the classic handshake
    protocol does not have it) instead of asserting a promise the SDK does not
    keep by default. It is exactly why `refresh_schema`'s own docstring tells the
    caller to call `describe_schema` again rather than wait to be notified.
    """
    modern_messages: list[object] = []

    async def modern_handler(message: object) -> None:
        modern_messages.append(message)

    async with Client(server, message_handler=modern_handler) as client:
        await client.call_tool("refresh_schema", {})
        await asyncio.sleep(0.05)
    assert modern_messages == []

    legacy_messages: list[object] = []

    async def legacy_handler(message: object) -> None:
        legacy_messages.append(message)

    async with Client(server, mode="legacy", message_handler=legacy_handler) as client:
        await client.call_tool("refresh_schema", {})
        await asyncio.sleep(0.05)
    assert any(type(m).__name__ == "ToolListChangedNotification" for m in legacy_messages)


async def test_customer_resource_returns_readable_markdown(server, mcp_session: Session) -> None:
    """Resources exist so an agent can read before it acts."""
    from pigrocrm.core.customers.schemas import CustomerCreate
    from pigrocrm.core.customers.service import CustomerService

    customer = CustomerService(mcp_session).create(
        CustomerCreate(ragione_sociale="ACME Srl", partita_iva="12345678901"), ADMIN
    )
    async with Client(server) as client:
        result = await client.read_resource(f"customer://{customer.id}")

    text = result.contents[0].text
    assert "ACME Srl" in text
    assert "12345678901" in text
    assert "## Timeline" in text


async def test_an_unknown_resource_id_explains_itself(server) -> None:
    from uuid import uuid4

    unknown = uuid4()
    async with Client(server) as client:
        with pytest.raises(MCPError) as exc:
            await client.read_resource(f"customer://{unknown}")
    assert exc.value.message == to_agent_message(NotFound("customer", unknown))
    assert exc.value.code == INVALID_PARAMS


async def test_a_malformed_resource_id_is_a_refusal_and_not_a_crash(server) -> None:
    """`UUID("non-un-id")` is the caller's mistake, which the guard renders as a
    `validation_failed` sentence. Its code is `DOMAIN_REFUSAL`: on mcp 2.2 the SDK would
    have turned the guard's old `ResourceError` into `-32603`, the code it answers a crash
    with, so a client could not tell this refusal from a server failure (REB-451)."""
    async with Client(server) as client:
        with pytest.raises(MCPError) as exc:
            await client.read_resource("customer://non-un-id")
    assert exc.value.code == DOMAIN_REFUSAL
    assert "Correggi il valore indicato" in exc.value.message


async def test_a_conflict_raised_by_a_resource_carries_the_application_code(
    server, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A conflict is an expected refusal, never a crash: the assistant reads the domain
    sentence and the code says so, `DOMAIN_REFUSAL` and not `-32603`."""
    from uuid import uuid4

    refusal = Conflict("customer", "scheda bloccata da un'altra operazione")

    def _refuse(context: object, customer_id: object) -> str:
        raise refusal

    monkeypatch.setattr(entities, "render_customer", _refuse)

    async with Client(server) as client:
        with pytest.raises(MCPError) as exc:
            await client.read_resource(f"customer://{uuid4()}")
    assert exc.value.code == DOMAIN_REFUSAL
    assert exc.value.message == to_agent_message(refusal)


async def test_the_configuration_and_account_timelines_are_not_reachable_from_mcp(
    server,
) -> None:
    """R5 gives users, field definitions, pipeline stages and personal access tokens a
    timeline. None of them is reachable through `get_timeline`, and that is structural,
    not a permission check: `entity_type` is a `Literal` of the three entity domains, so
    the value an agent would have to send is simply not in the tool's schema.

    The reason is the same one that makes an agent's own credential worth auditing at
    all. A PAT inherits its owner's full role (R10), so a check inside a registered
    tool is a check an administrator's token passes -- and the account timeline is
    precisely the record of that token being issued, used and revoked. An agent able to
    read it could see exactly what its own misuse would look like to whoever comes
    looking. Narrowing what the tool accepts is the only form of that guarantee a token
    cannot talk its way past. Asserted against the advertised schema rather than by
    calling the tool, because that is what an agent actually reads.
    """
    async with Client(server) as client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}

    entity_type = tools["get_timeline"].input_schema["properties"]["entity_type"]
    advertised = json.dumps(entity_type)
    for forbidden in ("user", "field_definition", "pipeline_stage", "personal_access_token"):
        assert f'"{forbidden}"' not in advertised, advertised
