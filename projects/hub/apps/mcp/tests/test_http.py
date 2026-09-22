"""The MCP server over Streamable HTTP (REB-213): who may call, and as whom.

Driven in-process: `httpx2.ASGITransport` around `McpHttpApp`, and the SDK's own client
on top, so the wire is the real one and no port is opened. The database is the MCP
suite's container; every test cleans the admins and tokens it made.
"""

import json
from collections.abc import AsyncIterator, Iterator
from typing import Any

import httpx2
import pytest
import pytest_asyncio
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session, sessionmaker

from rebase_core.admin_tokens import AdminTokenService
from rebase_core.config import Settings
from rebase_core.db import session_factory
from rebase_core.models import User
from rebase_mcp.http import McpHttpApp

URL = "http://prova/mcp"
INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "prova", "version": "0"},
    },
}
ACCEPT = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


@pytest.fixture
def ivan_token(mcp_engine: Engine) -> Iterator[str]:
    """An admin and a token of theirs; both removed after."""
    with session_factory(mcp_engine)() as session:
        user = User(email="ivan@rebase.it", nome="Ivan", cognome="Fiore", role="admin")
        session.add(user)
        session.commit()
        _, raw = AdminTokenService(session).create(user.id, "Claude Code")
    try:
        yield raw
    finally:
        with session_factory(mcp_engine)() as session:
            for table in ("comments", "freelancers", "signups", "admin_tokens", "users"):
                session.execute(text(f"DELETE FROM {table}"))
            session.commit()


def _settings(mcp_engine: Engine, **overrides: Any) -> Settings:
    return Settings(
        database_url=mcp_engine.url.render_as_string(hide_password=False),
        _env_file=None,  # type: ignore[call-arg]
        **overrides,
    )


@pytest_asyncio.fixture
async def served(
    mcp_engine: Engine, factory: sessionmaker[Session]
) -> AsyncIterator[tuple[McpHttpApp, httpx2.AsyncClient]]:
    registry = json.dumps(
        [
            {
                "id": "1",
                "slug": "studio",
                "owner_email": "ada@studio.it",
                "created_at": "2026-09-10T10:00:00Z",
            }
        ]
    ).encode()
    app = McpHttpApp(
        lambda: factory,
        _settings(mcp_engine, pigro_registry_token="segreto", pigro_api_url="https://pigro.test"),
        lambda method, url, headers, body: (200, registry),
    )
    async with app.lifespan():
        transport = httpx2.ASGITransport(app=app)
        async with httpx2.AsyncClient(transport=transport, base_url="http://prova") as client:
            yield app, client


def _authed(app: McpHttpApp, token: str) -> httpx2.AsyncClient:
    return httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app),
        base_url="http://prova",
        headers={"Authorization": f"Bearer {token}"},
    )


async def _call(app: McpHttpApp, token: str, name: str, arguments: dict[str, Any]) -> Any:
    async with (
        _authed(app, token) as authed,
        streamable_http_client(URL, http_client=authed) as streams,
        ClientSession(streams[0], streams[1]) as session,
    ):
        await session.initialize()
        result = await session.call_tool(name, arguments)
        return json.loads(result.content[0].text)  # type: ignore[union-attr]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Bearer non-un-token"},
        {"Authorization": "Bearer reb_sconosciuto"},
        {"Authorization": "Basic abc"},
    ],
)
async def test_without_a_valid_token_the_answer_is_one_uniform_401(
    served: tuple[McpHttpApp, httpx2.AsyncClient], headers: dict[str, str]
) -> None:
    _, client = served
    response = await client.post("/mcp", json=INITIALIZE, headers={**ACCEPT, **headers})
    assert response.status_code == 401
    assert response.json() == {"detail": "Token non valido"}
    assert response.headers["www-authenticate"] == "Bearer"


@pytest.mark.asyncio
async def test_a_revoked_token_is_a_401_too(
    served: tuple[McpHttpApp, httpx2.AsyncClient], ivan_token: str, mcp_engine: Engine
) -> None:
    with session_factory(mcp_engine)() as session:
        service = AdminTokenService(session)
        user_id = session.execute(text("SELECT user_id FROM admin_tokens")).scalar_one()
        [record] = service.list(user_id)
        service.revoke(user_id, record.id)
    _, client = served
    response = await client.post(
        "/mcp", json=INITIALIZE, headers={**ACCEPT, "Authorization": f"Bearer {ivan_token}"}
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_a_token_lists_the_tools_and_signs_what_it_writes(
    served: tuple[McpHttpApp, httpx2.AsyncClient], ivan_token: str
) -> None:
    app, _ = served
    async with (
        _authed(app, ivan_token) as authed,
        streamable_http_client(URL, http_client=authed) as streams,
        ClientSession(streams[0], streams[1]) as session,
    ):
        await session.initialize()
        names = {tool.name for tool in (await session.list_tools()).tools}
    assert {"read_freelancer_cv", "list_pigro_spaces", "add_freelancer_comment"} <= names

    talenti = await _call(app, ivan_token, "list_talenti", {})
    assert talenti["totale"] == 0

    spaces = await _call(app, ivan_token, "list_pigro_spaces", {})
    assert spaces["totale"] == 1 and spaces["items"][0]["slug"] == "studio"
    assert spaces["items"][0]["url"] == "https://pigro.test/studio/app/"


@pytest.mark.asyncio
async def test_only_the_mcp_path_is_served(
    served: tuple[McpHttpApp, httpx2.AsyncClient], ivan_token: str
) -> None:
    _, client = served
    for path in ["/mcp/x", "/api/hub/freelancers", "/mcpx"]:
        response = await client.post(
            path, json=INITIALIZE, headers={**ACCEPT, "Authorization": f"Bearer {ivan_token}"}
        )
        assert response.status_code == 404, path


@pytest.mark.asyncio
async def test_the_asgi_lifespan_protocol_starts_the_session_manager(
    mcp_engine: Engine, factory: sessionmaker[Session], ivan_token: str
) -> None:
    """What uvicorn does in the compose service: no `lifespan()` of ours, only the ASGI
    lifespan messages. A request between startup and shutdown must reach the SDK."""
    import asyncio

    app = McpHttpApp(lambda: factory, _settings(mcp_engine), lambda *a: (503, b""))
    inbox: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
    sent: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        return await inbox.get()

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    await inbox.put({"type": "lifespan.startup"})
    runner = asyncio.create_task(app({"type": "lifespan"}, receive, send))
    while not sent:
        await asyncio.sleep(0.01)
    assert sent[0]["type"] == "lifespan.startup.complete"

    async with _authed(app, ivan_token) as client:
        response = await client.post("/mcp", json=INITIALIZE, headers=ACCEPT)
    assert response.status_code == 200, response.text

    await inbox.put({"type": "lifespan.shutdown"})
    await runner
    assert sent[-1]["type"] == "lifespan.shutdown.complete"
