"""The slice 10 surface: commitments an agent can write, and the month it can read.

The reason these tools exist is that «what falls due this week» is exactly the question
somebody asks an agent -- and the reason they are on the *default* surface is that none
of them consumes a number, touches the register or rewrites a rate. What is asserted
here is that, plus the two properties the tool descriptions promise: closing is two
different operations, and an undated commitment never lands in a day.
"""

import calendar
import json
from datetime import timedelta
from typing import Any
from uuid import uuid4

from mcp import Client

from pigrocrm.core.db.clock import today_local

# Derived, never a literal: a suite with `2026-09` in it is a suite with an expiry date
# on it (see `packages/core/tests/periodo_fiscale.py`).
TODAY = today_local()
Month = TODAY.strftime("%Y-%m")
FIRST = TODAY.replace(day=1)
LAST = TODAY.replace(day=calendar.monthrange(TODAY.year, TODAY.month)[1])


def _payload(result: Any) -> Any:
    return result.structured_content or json.loads(result.content[0].text)


async def test_the_slice_10_tools_are_registered(server) -> None:
    async with Client(server) as client:
        names = {tool.name for tool in (await client.list_tools()).tools}

    assert {
        "create_task",
        "update_task",
        "get_task",
        "complete_task",
        "cancel_task",
        "reopen_task",
        "archive_task",
        "restore_task",
        "list_tasks",
        "get_tasks_today",
        "get_calendar_month",
    } <= names


async def test_a_commitment_needs_no_date(server) -> None:
    """The commonest to-do, and the tool description says so: inventing a date fills
    every «what is due» list with noise."""
    async with Client(server) as client:
        result = await client.call_tool("create_task", {"title": "Chiedere il codice SDI"})

    assert not result.is_error, result.content[0].text
    created = _payload(result)
    assert created["due_date"] is None
    assert created["status"] == "open"
    assert created["origin"] == "manual"


async def test_an_undated_commitment_is_outside_the_grid(server) -> None:
    async with Client(server) as client:
        await client.call_tool("create_task", {"title": "Senza data"})
        result = await client.call_tool("get_calendar_month", {"month": Month})

    month = _payload(result)
    assert month["days"] == []
    assert [item["title"] for item in month["task_without_due_date"]] == ["Senza data"]


async def test_a_dated_commitment_lands_on_its_day(server) -> None:
    async with Client(server) as client:
        await client.call_tool(
            "create_task", {"title": "Sollecitare Rossi", "due_date": FIRST.isoformat()}
        )
        result = await client.call_tool("get_calendar_month", {"month": Month})

    month = _payload(result)
    assert [day["day"] for day in month["days"]] == [FIRST.isoformat()]
    assert [item["title"] for item in month["days"][0]["tasks"]] == ["Sollecitare Rossi"]


async def test_completing_and_cancelling_are_two_different_closures(server) -> None:
    """«Done» carries the day it was done; «no longer needed» carries neither a date nor
    a deletion. An agent that could only delete would destroy the answer to *why*."""
    async with Client(server) as client:
        first = _payload(await client.call_tool("create_task", {"title": "Da completare"}))
        done = _payload(await client.call_tool("complete_task", {"task_id": first["id"]}))

        second = _payload(await client.call_tool("create_task", {"title": "Da annullare"}))
        cancelled = _payload(await client.call_tool("cancel_task", {"task_id": second["id"]}))

    assert (done["status"], done["completed_at"]) == ("completed", TODAY.isoformat())
    assert (cancelled["status"], cancelled["completed_at"]) == ("cancelled", None)


async def test_completing_a_cancelled_commitment_is_guidance_not_a_dump(server) -> None:
    async with Client(server) as client:
        created = _payload(await client.call_tool("create_task", {"title": "Annullata"}))
        await client.call_tool("cancel_task", {"task_id": created["id"]})
        result = await client.call_tool("complete_task", {"task_id": created["id"]})

    assert result.is_error
    message = result.content[0].text
    assert "riaprila" in message
    assert "errors.pydantic.dev" not in message


async def test_two_references_are_refused_with_a_sentence(server, seeded_customer_id: str) -> None:
    """At most one of customer, person, deal and invoice -- an activity that appeared on
    two records would be closed on one and left open on the other."""
    async with Client(server) as client:
        result = await client.call_tool(
            "create_task",
            {
                "title": "Due riferimenti",
                "customer_id": seeded_customer_id,
                "person_id": str(uuid4()),
            },
        )

    assert result.is_error
    assert "un solo" in result.content[0].text


async def test_the_date_filter_never_includes_the_undated_ones(server) -> None:
    """`NULL <= date` is NULL, which is not true -- and a filter that quietly included
    them would answer «due by Friday» with things that are not due at all."""
    async with Client(server) as client:
        await client.call_tool("create_task", {"title": "Senza data"})
        await client.call_tool("create_task", {"title": "Con data", "due_date": FIRST.isoformat()})
        result = await client.call_tool("list_tasks", {"due_within": LAST.isoformat()})

    assert [item["title"] for item in _payload(result)["items"]] == ["Con data"]


async def test_the_state_cannot_be_written_through_update(server) -> None:
    """`update_task` says so, and the schema enforces it: «fatta» has a side effect
    -- the day -- that a caller must not be able to supply."""
    async with Client(server) as client:
        created = _payload(await client.call_tool("create_task", {"title": "Aperta"}))
        result = await client.call_tool(
            "update_task",
            {"task_id": created["id"], "changes": {"status": "completed"}},
        )

    assert result.is_error
    # `extra="forbid"`: a key the schema does not declare is refused rather than ignored,
    # so nobody can believe they closed something.
    assert "errors.pydantic.dev" not in result.content[0].text


async def test_a_month_that_is_not_one_is_refused(server) -> None:
    async with Client(server) as client:
        result = await client.call_tool("get_calendar_month", {"month": "2026-13"})

    assert result.is_error
    assert "month" in result.content[0].text


async def test_the_month_is_read_and_never_written(server) -> None:
    """`get_calendar_month` has no way to log an hour: the tool that writes them is
    `log_time`, and the calendar reading them is a read."""
    async with Client(server) as client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}

    schema = tools["get_calendar_month"].input_schema
    # `legal_entity_id` since REB-624 narrows the hours and the invoices; still no way to write.
    assert set(schema.get("properties", {})) == {"month", "all", "legal_entity_id"}


async def test_the_today_panel_is_one_read_in_four_sections(server) -> None:
    yesterday = (TODAY - timedelta(days=1)).isoformat()
    async with Client(server) as client:
        await client.call_tool("create_task", {"title": "In ritardo", "due_date": yesterday})
        await client.call_tool("create_task", {"title": "Senza data"})
        result = await client.call_tool("get_tasks_today", {})

    panel = _payload(result)
    assert panel["today"] == TODAY.isoformat()
    assert [a["title"] for a in panel["overdue"]["items"]] == ["In ritardo"]
    assert panel["overdue"]["total"] == 1
    assert [a["title"] for a in panel["without_due_date"]["items"]] == ["Senza data"]


async def test_to_bad_today_limit_is_guidance_and_not_to_database_error(server) -> None:
    async with Client(server) as client:
        zero = await client.call_tool("get_tasks_today", {"limit": 0})
        many = await client.call_tool("get_tasks_today", {"limit": "molti"})

    for result in (zero, many):
        assert result.is_error
        assert "errors.pydantic.dev" not in result.content[0].text


async def _owner_server(mcp_session, tmp_path):
    from pigrocrm.core.actor import Actor
    from pigrocrm.core.auth.models import User
    from pigrocrm.core.config import Settings
    from pigrocrm.core.storage import LocalFileStorage
    from pigrocrm_mcp.server import build_server

    def member(key: str) -> User:
        user = User(email=f"{key}@example.test", password_hash="x", name=key, role="admin")
        mcp_session.add(user)
        mcp_session.flush()
        return user

    owner, colleague = member("rossi"), member("bianchi")
    actor = Actor(id=owner.id, type="mcp", role="admin")
    server = build_server(
        lambda: mcp_session,
        lambda: actor,
        LocalFileStorage(tmp_path),
        Settings(_env_file=None),  # type: ignore[call-arg]
    )
    return server, owner, colleague


async def test_create_task_defaults_to_the_token_owner(mcp_session, tmp_path) -> None:
    server, owner, _ = await _owner_server(mcp_session, tmp_path)
    async with Client(server) as client:
        result = await client.call_tool("create_task", {"title": "Da fare"})

    assert not result.is_error, result.content[0].text
    assert _payload(result)["assigned_to"] == str(owner.id)


async def test_create_task_explicit_colleague_wins(mcp_session, tmp_path) -> None:
    server, _, colleague = await _owner_server(mcp_session, tmp_path)
    async with Client(server) as client:
        result = await client.call_tool(
            "create_task", {"title": "Da fare", "assigned_to": str(colleague.id)}
        )

    assert _payload(result)["assigned_to"] == str(colleague.id)


async def test_create_task_none_leaves_it_unassigned(mcp_session, tmp_path) -> None:
    server, _, _ = await _owner_server(mcp_session, tmp_path)
    async with Client(server) as client:
        result = await client.call_tool("create_task", {"title": "Da fare", "assigned_to": "none"})

    assert not result.is_error, result.content[0].text
    assert _payload(result)["assigned_to"] is None


async def test_create_task_refuses_to_garbage_assignee(mcp_session, tmp_path) -> None:
    server, _, _ = await _owner_server(mcp_session, tmp_path)
    async with Client(server) as client:
        result = await client.call_tool(
            "create_task", {"title": "Da fare", "assigned_to": "chiunque"}
        )

    assert result.is_error


async def test_create_task_without_to_person_behind_the_token_stays_unassigned(
    server,
) -> None:
    async with Client(server) as client:
        result = await client.call_tool("create_task", {"title": "Da fare"})

    assert _payload(result)["assigned_to"] is None
