"""REB-833: `list_ai_config`, the read-only door to «Modelli AI». It answers the hint and
never the key, never the ciphertext or the nonce, and no tool writes a key."""

import base64
import json
import os
from collections.abc import Iterator

import httpx2
import pytest
from mcp import Client
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session, sessionmaker
from test_tools import IVAN, _payload

from rebase_core.admin_tokens import AdminTokenService
from rebase_core.ai_config import AiConfigService
from rebase_core.config import Settings
from rebase_core.db import session_factory
from rebase_core.models import User
from rebase_mcp.http import McpHttpApp
from rebase_mcp.server import build_server

SECRET = "sk-secret-abcd"
SECRETS_KEY = base64.b64encode(os.urandom(32)).decode()


def _settings(**overrides: str) -> Settings:
    return Settings(secrets_key=SECRETS_KEY, _env_file=None, **overrides)  # type: ignore[call-arg]


@pytest.fixture
def admin_with_key(factory: sessionmaker[Session]) -> Iterator[User]:
    """An admin row, and the Anthropic key saved through the service the page uses."""
    with factory() as session:
        user = User(email="ivan@rebase.it", nome="Ivan", cognome="Sala", role="admin")
        session.add(user)
        session.commit()
        AiConfigService(session, _settings()).save_key(user.id, "anthropic", SECRET)
    yield user
    with factory() as session:
        for statement in (
            "DELETE FROM ai_usage",
            "DELETE FROM ai_prices",
            "UPDATE ai_providers SET key_ciphertext = NULL, key_nonce = NULL, "
            "key_last4 = NULL, saved_by = NULL, saved_at = NULL, enabled = false",
            "DELETE FROM admin_tokens",
            "DELETE FROM admin_actions",
            "DELETE FROM users",
        ):
            session.execute(text(statement))
        session.commit()


async def test_list_ai_config_has_no_key_and_no_ciphertext(
    factory: sessionmaker[Session], admin_with_key: User
) -> None:
    async with Client(build_server(factory, lambda: IVAN, settings=_settings())) as client:
        result = await client.call_tool("list_ai_config", {})
    body = _payload(result)
    raw = json.dumps(body)
    assert SECRET not in raw
    assert "key_ciphertext" not in raw
    assert "nonce" not in raw
    assert body["secrets_key_present"] is True
    anthropic = next(p for p in body["providers"] if p["provider"] == "anthropic")
    assert anthropic["key_hint"] == "configurata, finisce con …abcd"
    assert {t["task"] for t in body["tasks"]} == {"card", "team_proposal"}


async def test_no_tool_writes_a_key(factory: sessionmaker[Session]) -> None:
    async with Client(build_server(factory, lambda: IVAN, settings=_settings())) as client:
        names = {tool.name for tool in (await client.list_tools()).tools}
    assert [n for n in names if "key" in n or n.startswith("save_")] == []
    assert "list_ai_config" in names


@pytest.mark.asyncio
async def test_list_ai_config_requires_an_admin_token(
    mcp_engine: Engine, factory: sessionmaker[Session], admin_with_key: User
) -> None:
    app = McpHttpApp(lambda: factory, _settings(), lambda *args: (200, b"[]"))
    async with app.lifespan():
        with session_factory(mcp_engine)() as session:
            _, raw = AdminTokenService(session).create(admin_with_key.id, "Claude Code")
        anonymous = httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), base_url="http://prova"
        )
        refused = await anonymous.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/call"},
            headers={"Accept": "application/json, text/event-stream"},
        )
        assert refused.status_code == 401
        async with (
            httpx2.AsyncClient(
                transport=httpx2.ASGITransport(app=app),
                base_url="http://prova",
                headers={"Authorization": f"Bearer {raw}"},
            ) as authed,
            streamable_http_client("http://prova/mcp", http_client=authed) as streams,
            ClientSession(streams[0], streams[1]) as session,
        ):
            await session.initialize()
            answer = await session.call_tool("list_ai_config", {})
        assert SECRET not in answer.content[0].text  # type: ignore[union-attr]
