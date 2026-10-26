"""REB-732 over MCP: the reminders' settings and the person's own acknowledge and snooze."""

import json
from uuid import uuid4

from mcp import Client
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.auth.models import User
from pigrocrm.core.config import Settings
from pigrocrm_mcp.server import build_server


def _payload(result):
    return result.structured_content or json.loads(result.content[0].text)


def _server_for(session: Session, role: str = "collaboratore"):
    user = User(email=f"a-{uuid4()}@example.test", password_hash="x", nome="A", ruolo=role)
    session.add(user)
    session.flush()
    actor = Actor(id=user.id, type="mcp", role=role)  # type: ignore[arg-type]
    return build_server(
        lambda: session,
        lambda: actor,
        settings=Settings(_env_file=None),  # type: ignore[call-arg]
    )


async def test_an_agent_reads_the_catalogue_and_a_persons_preferences(mcp_session: Session) -> None:
    server = _server_for(mcp_session)

    async with Client(server) as client:
        types = _payload(await client.call_tool("list_reminder_types", {}))
        preferences = _payload(await client.call_tool("get_reminder_preferences", {}))

    keys = [item["key"] for item in (types if isinstance(types, list) else types["result"])]
    assert "attivita_overdue" in keys
    assert preferences["general"]["summary_hour"] == 8


async def test_an_agent_sets_and_resets_a_persons_preference(mcp_session: Session) -> None:
    server = _server_for(mcp_session)

    async with Client(server) as client:
        set_ = _payload(
            await client.call_tool(
                "set_reminder_preference",
                {"scope": "me", "type": "deal_stale", "changes": {"enabled": False}},
            )
        )
        assert set_["preference"]["enabled"] is False
        read = _payload(await client.call_tool("get_reminder_preferences", {}))
        stale = {item["type"]: item for item in read["effective"]}["deal_stale"]
        assert stale["enabled"] is False
        reset = _payload(
            await client.call_tool(
                "reset_reminder_preference", {"scope": "me", "type": "deal_stale"}
            )
        )
        assert reset["status"] == "ripristinato"


async def test_a_member_agent_cannot_set_the_spaces_defaults(mcp_session: Session) -> None:
    server = _server_for(mcp_session)

    async with Client(server) as client:
        result = await client.call_tool(
            "set_reminder_preference",
            {"scope": "space", "type": "deal_stale", "changes": {"enabled": False}},
        )

    assert result.is_error


async def test_an_agent_acknowledges_and_snoozes_for_its_person(mcp_session: Session) -> None:
    server = _server_for(mcp_session, "readonly")
    key = f"deal_stale:{uuid4()}"

    async with Client(server) as client:
        acked = _payload(
            await client.call_tool("acknowledge_reminder", {"key": key, "severity": "warning"})
        )
        assert acked["acknowledged_severity"] == "warning"
        snoozed = _payload(
            await client.call_tool(
                "snooze_reminder", {"key": key, "severity": "warning", "until": "in_a_week"}
            )
        )
        assert snoozed["snoozed_until"] is not None
        mine = _payload(await client.call_tool("list_my_reminder_state", {}))

    items = mine if isinstance(mine, list) else mine["result"]
    assert [item["key"] for item in items] == [key]
