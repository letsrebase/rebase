"""The MCP surface of the Slack connection (REB-739): one read, absent without a Slack app,
and the bot token nowhere in an answer.

`describe_slack` is registered under `slack_configured(settings)` and not under Gmail's
condition: the Slack app is the installation's own and has nothing to do with Google. The
workspace in these tests is connected through the real OAuth flow against `FakeSlack`
(the core suite's fake, which this file puts on the path as the API's suite does); the tool
itself never reaches Slack, and the repository-root socket guard would fail a test that
made it.
"""

import json
import sys
from pathlib import Path
from typing import Any

import pytest
from mcp import Client
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "packages" / "core" / "tests"))
from fakes.fake_slack import BOT_TOKEN, CLIENT_SECRET, FakeSlack  # noqa: E402
from fakes.slack_fixtures import connect, make_user, slack_settings  # noqa: E402

from pigrocrm.core.actor import Actor  # noqa: E402
from pigrocrm.core.storage import LocalFileStorage  # noqa: E402
from pigrocrm_mcp.server import build_server  # noqa: E402


def _payload(result: Any) -> Any:
    return result.structured_content or json.loads(result.content[0].text)


@pytest.fixture
def admin(mcp_session: Session):
    return make_user(mcp_session, email="mario@example.it", nome="Mario")


def _server(mcp_session: Session, actor: Actor, tmp_path: Path, **overrides: object) -> Any:
    return build_server(
        lambda: mcp_session,
        lambda: actor,
        LocalFileStorage(tmp_path),
        settings=slack_settings(**overrides),
    )


async def _names(server: Any) -> set[str]:
    async with Client(server) as client:
        return {tool.name for tool in (await client.list_tools()).tools}


async def test_describe_slack_is_present_only_with_a_slack_app(
    mcp_session: Session, admin: Any, tmp_path: Path, server: Any
) -> None:
    actor = Actor(id=admin.id, type="mcp", role="admin")
    assert "describe_slack" in await _names(_server(mcp_session, actor, tmp_path))
    assert "describe_slack" not in await _names(
        _server(mcp_session, actor, tmp_path, slack_client_secret="")
    )
    # The suite's own unconfigured server: absent, not broken.
    assert "describe_slack" not in await _names(server)


async def test_an_agent_reads_who_is_matched_and_never_the_token(
    mcp_session: Session, admin: Any, tmp_path: Path
) -> None:
    ada = make_user(mcp_session, email="ada@example.it", nome="Ada", ruolo="collaboratore")
    fake = FakeSlack()
    fake.add_member("mario@example.it", "UMARIO")
    connect(mcp_session, fake, admin)
    actor = Actor(id=admin.id, type="mcp", role="admin")

    async with Client(_server(mcp_session, actor, tmp_path)) as client:
        raw = await client.call_tool("describe_slack", {})

    health = _payload(raw)
    assert health["configured"] is True
    assert health["workspace"]["team_name"] == "Acme Test"
    assert health["workspace"]["status"] == "active"
    states = {member["nome"]: member["state"] for member in health["members"]}
    assert states == {"Mario": "matched", "Ada": "unmatched"}
    assert str(ada.id) in {member["user_id"] for member in health["members"]}
    everything = json.dumps(health) + str(raw)
    assert BOT_TOKEN not in everything
    assert CLIENT_SECRET not in everything
    assert "ciphertext" not in everything and "nonce" not in everything


async def test_a_workspace_nobody_connected_is_a_state_not_an_error(
    mcp_session: Session, admin: Any, tmp_path: Path
) -> None:
    actor = Actor(id=admin.id, type="mcp", role="admin")
    async with Client(_server(mcp_session, actor, tmp_path)) as client:
        health = _payload(await client.call_tool("describe_slack", {}))
    assert health == {"configured": True, "workspace": None, "members": []}


async def test_a_collaboratore_cannot_read_the_matches_of_everyone(
    mcp_session: Session, tmp_path: Path
) -> None:
    member = make_user(mcp_session, ruolo="collaboratore")
    actor = Actor(id=member.id, type="mcp", role="collaboratore")
    async with Client(_server(mcp_session, actor, tmp_path)) as client:
        result = await client.call_tool("describe_slack", {})
    assert result.is_error is True
