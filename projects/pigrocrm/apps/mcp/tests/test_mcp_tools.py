import json
from typing import Any
from uuid import uuid4

import pytest
from mcp import Client
from sqlalchemy import text
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.config import Settings
from pigrocrm.core.fields.schemas import FieldDefinitionCreate
from pigrocrm.core.fields.service import FieldDefinitionService
from pigrocrm.core.pipeline.service import PipelineService

# Test setup runs as the system, not as an agent: an agent credential may not seed a
# pipeline or define a field (`core/agent_actions.py`).
SETUP = Actor(id=None, type="system", role="admin")  # setup is not an agent's (agent_actions.py)


def _payload(result) -> dict:
    return result.structured_content or json.loads(result.content[0].text)


async def test_every_tool_from_the_spec_is_exposed(server) -> None:
    async with Client(server) as client:
        names = {tool.name for tool in (await client.list_tools()).tools}

    assert {
        "describe_schema",
        "create_customer",
        "update_customer",
        "get_customer",
        "search_customers",
        "archive_customer",
        "create_person",
        "update_person",
        "get_person",
        "search_people",
        "archive_person",
        "create_deal",
        "update_deal",
        "get_deal",
        "search_deals",
        "move_deal",
        "archive_deal",
        "list_pipeline_stages",
        "get_timeline",
    } <= names


# The one tool that starts with `delete_`, and why it is not a way to destroy rows: a note is
# soft-deleted (the row stays, `deleted_at` is set), only for who may edit it, and the parent's
# timeline records who did it (REB-879), like `archive_*` for the records it hangs on.
_SOFT_DELETES = {"delete_note"}


async def test_no_destructive_delete_tool_exists(server) -> None:
    """An agent misreading 'elimina i deal chiusi' must not be able to destroy rows."""
    async with Client(server) as client:
        names = {tool.name for tool in (await client.list_tools()).tools}
    assert not [name for name in names - _SOFT_DELETES if name.startswith("delete_")]


async def test_the_full_journey_an_agent_would_take(server, mcp_session: Session) -> None:
    """Spec success criterion 2: Claude does the whole job through MCP, discovering a
    custom field nobody hardcoded."""
    FieldDefinitionService(mcp_session).create(
        FieldDefinitionCreate(
            entity_type="customer",
            key="settore",
            label="Settore",
            field_type="select",
            options=["IT", "Retail"],
        ),
        SETUP,
    )
    PipelineService(mcp_session).seed_defaults(SETUP)

    async with Client(server) as client:
        schema = _payload(await client.call_tool("describe_schema", {"entity_type": "customer"}))
        assert [f["key"] for f in schema["custom_fields"]] == ["settore"]

        customer = _payload(
            await client.call_tool(
                "create_customer",
                {
                    "ragione_sociale": "ACME Srl",
                    "partita_iva": "12345678901",
                    "custom_fields": {"settore": "IT"},
                },
            )
        )
        assert customer["custom_fields"] == {"settore": "IT"}

        person = _payload(
            await client.call_tool(
                "create_person",
                {"nome": "Mario", "cognome": "Rossi", "customer_id": customer["id"]},
            )
        )
        assert person["customer_id"] == customer["id"]

        deal = _payload(
            await client.call_tool(
                "create_deal", {"nome": "Progetto X", "customer_id": customer["id"]}
            )
        )

        stages = _payload(await client.call_tool("list_pipeline_stages", {}))["stages"]
        offerta = next(s for s in stages if s["nome"] == "Offerta")
        moved = _payload(
            await client.call_tool("move_deal", {"deal_id": deal["id"], "stage_id": offerta["id"]})
        )
        assert moved["pipeline_stage_id"] == offerta["id"]

        timeline = _payload(
            await client.call_tool("get_timeline", {"entity_type": "deal", "entity_id": deal["id"]})
        )["entries"]

    # Criterion 3: the timeline distinguishes the agent from a human.
    assert {entry["actor_type"] for entry in timeline} == {"mcp"}
    assert "stage_changed" in {entry["kind"] for entry in timeline}


async def test_an_invalid_custom_value_returns_guidance_naming_the_options(
    server, mcp_session: Session
) -> None:
    FieldDefinitionService(mcp_session).create(
        FieldDefinitionCreate(
            entity_type="customer",
            key="settore",
            label="Settore",
            field_type="select",
            options=["IT", "Retail"],
        ),
        SETUP,
    )
    async with Client(server) as client:
        result = await client.call_tool(
            "create_customer",
            {"ragione_sociale": "Beta", "custom_fields": {"settore": "Altro"}},
        )

    assert result.is_error
    message = result.content[0].text
    assert "IT" in message and "Retail" in message


async def test_a_permission_error_tells_the_agent_to_ask_the_user(mcp_session: Session) -> None:
    from pigrocrm_mcp.server import build_server

    readonly = Actor(id=None, type="mcp", role="readonly")
    # `_env_file=None`: this test is about the permission refusal, not about whatever
    # document store the worktree's `.env` selects (a Drive backend without a service
    # account would refuse at storage construction before the tool ever ran).
    server = build_server(
        lambda: mcp_session,
        lambda: readonly,
        settings=Settings(_env_file=None),  # type: ignore[call-arg]
    )

    async with Client(server) as client:
        result = await client.call_tool("create_customer", {"ragione_sociale": "ACME"})

    assert result.is_error
    message = result.content[0].text
    assert "readonly" in message


async def test_an_agent_names_a_customers_owner_when_it_creates_it_and_when_it_updates_it(
    server, mcp_session: Session
) -> None:
    """REB-773: `create_customer` takes `owner_id`, and `update_customer` takes it in
    `changes` (its schema is `CustomerUpdate`'s). The agent here is an admin's."""
    from pigrocrm.core.auth.models import User

    first = User(email=f"a-{uuid4()}@example.test", password_hash="x", nome="A", ruolo="admin")
    second = User(email=f"b-{uuid4()}@example.test", password_hash="x", nome="B", ruolo="admin")
    mcp_session.add_all([first, second])
    mcp_session.flush()

    async with Client(server) as client:
        created = _payload(
            await client.call_tool(
                "create_customer", {"ragione_sociale": "ACME", "owner_id": str(first.id)}
            )
        )
        assert created["owner_id"] == str(first.id)
        moved = _payload(
            await client.call_tool(
                "update_customer",
                {"customer_id": created["id"], "changes": {"owner_id": str(second.id)}},
            )
        )
        assert moved["owner_id"] == str(second.id)


async def test_server_survives_a_raw_database_error_and_serves_the_next_call(
    server, mcp_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`__main__.py` builds exactly one `Session` for the whole process and passes
    `lambda: session` to `build_server` -- the same shared session every tool call
    in this test suite's `server` fixture reuses too. Before this fix, `_guard`
    only rolled back a `DomainError`/`ValueError` it already knew how to render;
    any other exception -- a raw DBAPI failure being the realistic case, since
    items 1-5 of this same fix wave closed off the paths that used to produce one
    at the public tool surface -- escaped uncaught, leaving the session's
    transaction failed. Every later call on that session, including an unrelated
    read like `describe_schema`, then failed too (`psycopg.errors.
    InFailedSqlTransaction` under this test's savepoint-based session, the plain
    SQLAlchemy `PendingRollbackError` in `__main__.py`'s real, non-savepoint
    session -- same underlying bug, two renderings of it) until the process was
    restarted.

    Forces a genuine Postgres-level error (`SELECT 1/0`, integer division by
    zero) rather than a bare Python exception: only a real DBAPI failure actually
    leaves the SQLAlchemy transaction in the failed state this bug depends on --
    a plain `RuntimeError` raised in application code never touches the
    connection at all, and would pass even without the fix, proving nothing.
    """

    def _hit_a_real_database_error(self: PipelineService) -> list[Any]:
        self.session.execute(text("SELECT 1/0"))
        return []

    monkeypatch.setattr(PipelineService, "list", _hit_a_real_database_error)

    async with Client(server) as client:
        failing = await client.call_tool("list_pipeline_stages", {})
        assert failing.is_error

        # Undo the fault injection before the next call -- this proves the FIRST
        # call's aftermath (not a second, independent failure) is what is under
        # test: describe_schema never touches PipelineService either way, but
        # this keeps the test's intent unambiguous even if that changes later.
        monkeypatch.undo()

        ok = await client.call_tool("describe_schema", {"entity_type": "customer"})

    assert not ok.is_error, ok.content[0].text


async def test_the_broadened_rollback_guard_still_does_not_disguise_a_programming_error(
    server, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The trailing `except Exception` added for item 6 exists only to add a
    rollback -- it must never also translate the exception, or a real programming
    bug (a `KeyError`/`AttributeError` this codebase never intends to raise on
    purpose) would be misreported to the agent as if it had sent a bad value,
    exactly the property Task 17's review already verified about the two narrower
    `except` clauses above it in `_guard`.

    Re-raised untouched, the crash is then the SDK's to report, and since mcp 2.2 it
    keeps the exception's text on the server: `Tool.run` answers only «Error
    executing tool <name>» for anything that is not a `ToolError`, a `ResourceError`
    or an `MCPError`. That is the intended behaviour (REB-451): the assistant learns
    that the call failed and nothing about the code, where on mcp 2.0 it read the
    `KeyError`'s own text."""

    def _raise_a_programming_error(self: PipelineService) -> list[Any]:
        raise KeyError("boom")

    monkeypatch.setattr(PipelineService, "list", _raise_a_programming_error)

    async with Client(server) as client:
        result = await client.call_tool("list_pipeline_stages", {})

    assert result.is_error
    message = result.content[0].text
    # The call failed and the answer names the tool, never the exception behind it.
    assert "list_pipeline_stages" in message
    assert "KeyError" not in message
    assert "boom" not in message
