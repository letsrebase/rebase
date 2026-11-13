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
from rebase_core.http import urllib_call, urllib_engagements_call
from rebase_core.models import User
from rebase_mcp.http import McpHttpApp
from rebase_mcp.settings import McpSettings

URL = "http://localhost/mcp"
LIFESPAN_TIMEOUT = 10.0
INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "test", "version": "0"},
    },
}
ACCEPT = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


@pytest.fixture
def ivan_token(mcp_engine: Engine) -> Iterator[str]:
    """An admin and a token of theirs; both removed after."""
    with session_factory(mcp_engine)() as session:
        user = User(email="ivan@rebase.it", first_name="Ivan", last_name="Fiore", role="admin")
        session.add(user)
        session.commit()
        _, raw = AdminTokenService(session).create(user.id, "Claude Code")
    try:
        yield raw
    finally:
        with session_factory(mcp_engine)() as session:
            for table in (
                "admin_actions",
                "notes",
                "freelancers",
                "signups",
                "admin_tokens",
                "users",
            ):
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
        async with httpx2.AsyncClient(transport=transport, base_url="http://localhost") as client:
            yield app, client


def _authed(app: McpHttpApp, token: str) -> httpx2.AsyncClient:
    return httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app),
        base_url="http://localhost",
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
    assert response.json() == {"detail": "Invalid token"}
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
    assert {"read_freelancer_cv", "list_pigro_spaces", "add_freelancer_note"} <= names

    talents = await _call(app, ivan_token, "list_talents", {})
    assert talents["total"] == 0

    spaces = await _call(app, ivan_token, "list_pigro_spaces", {})
    assert spaces["total"] == 1 and spaces["items"][0]["slug"] == "studio"
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

    first_sent = asyncio.Event()

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)
        first_sent.set()

    await inbox.put({"type": "lifespan.startup"})
    runner = asyncio.create_task(app({"type": "lifespan"}, receive, send))
    # A server that never answers the startup message fails the test in seconds, not never.
    await asyncio.wait_for(first_sent.wait(), timeout=LIFESPAN_TIMEOUT)
    assert sent[0]["type"] == "lifespan.startup.complete"

    async with _authed(app, ivan_token) as client:
        response = await client.post("/mcp", json=INITIALIZE, headers=ACCEPT)
    assert response.status_code == 200, response.text

    await inbox.put({"type": "lifespan.shutdown"})
    await asyncio.wait_for(runner, timeout=LIFESPAN_TIMEOUT)
    assert sent[-1]["type"] == "lifespan.shutdown.complete"


def test_the_crm_link_tool_reads_through_the_injected_client(
    mcp_engine: Engine, factory: sessionmaker[Session]
) -> None:
    """`link_company_to_pigro` reaches the CRM through the client the app was given, so a
    test's fake stands in for the CRM there too; only production's ten-second `urllib_call`
    becomes `urllib_engagements_call`, for a link that opens a new space."""

    def fake(method: str, url: str, headers: dict[str, str], body: bytes) -> tuple[int, bytes]:
        return 503, b""

    with factory() as session:
        for http, expected in ((fake, fake), (urllib_call, urllib_engagements_call)):
            app = McpHttpApp(lambda: factory, _settings(mcp_engine), http)
            assert app.crm_leads(session).door.http is expected


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("host", "status"),
    [
        ("letsrebase.com", 200),
        ("preview.letsrebase.com", 200),
        ("127.0.0.1:8001", 200),
        ("mcp:8001", 200),
        ("evil.example", 421),
        ("letsrebase.com.evil.example", 421),
    ],
)
async def test_only_the_hosts_the_deployment_answers_to_reach_the_sdk(
    served: tuple[McpHttpApp, httpx2.AsyncClient], ivan_token: str, host: str, status: int
) -> None:
    """REB-889: DNS rebinding protection is on. nginx hands the public name as the Host
    (`proxy_set_header Host $host`), and the bearer is no help to a page in a browser."""
    _, client = served
    response = await client.post(
        "/mcp",
        json=INITIALIZE,
        headers={**ACCEPT, "Authorization": f"Bearer {ivan_token}", "Host": host},
    )
    assert response.status_code == status, response.text


@pytest.mark.asyncio
async def test_a_browser_origin_that_is_not_ours_is_refused(
    served: tuple[McpHttpApp, httpx2.AsyncClient], ivan_token: str
) -> None:
    _, client = served
    headers = {**ACCEPT, "Authorization": f"Bearer {ivan_token}", "Host": "letsrebase.com"}
    foreign = await client.post(
        "/mcp", json=INITIALIZE, headers={**headers, "Origin": "https://evil.example"}
    )
    ours = await client.post(
        "/mcp", json=INITIALIZE, headers={**headers, "Origin": "https://letsrebase.com"}
    )
    assert foreign.status_code == 403
    assert ours.status_code == 200


@pytest.mark.asyncio
async def test_a_stack_under_another_name_lists_its_host_in_the_settings(
    mcp_engine: Engine, factory: sessionmaker[Session], ivan_token: str
) -> None:
    app = McpHttpApp(
        lambda: factory,
        _settings(mcp_engine),
        lambda *a: (503, b""),
        McpSettings(allowed_hosts="mcp.example.org,localhost"),
    )
    async with app.lifespan():
        transport = httpx2.ASGITransport(app=app)
        async with httpx2.AsyncClient(transport=transport, base_url="http://localhost") as client:
            headers = {**ACCEPT, "Authorization": f"Bearer {ivan_token}"}
            named = await client.post(
                "/mcp", json=INITIALIZE, headers={**headers, "Host": "mcp.example.org"}
            )
            default = await client.post(
                "/mcp", json=INITIALIZE, headers={**headers, "Host": "letsrebase.com"}
            )
    assert named.status_code == 200
    assert default.status_code == 421


@pytest.mark.asyncio
async def test_failed_bearers_are_counted_per_client_and_a_good_token_is_not(
    mcp_engine: Engine, factory: sessionmaker[Session], ivan_token: str
) -> None:
    """REB-889: the third wrong bearer from one address in a minute (the allowance here)
    is answered 429 with a `Retry-After`, without the database being asked; another
    address is unaffected, and a busy agent with a good token never spends any."""
    app = McpHttpApp(
        lambda: factory,
        _settings(mcp_engine),
        lambda *a: (503, b""),
        McpSettings(bad_bearers_per_minute=3),
    )
    async with app.lifespan():
        transport = httpx2.ASGITransport(app=app)
        async with httpx2.AsyncClient(transport=transport, base_url="http://localhost") as client:

            def post(token: str, address: str) -> Any:
                return client.post(
                    "/mcp",
                    json=INITIALIZE,
                    headers={
                        **ACCEPT,
                        "Authorization": f"Bearer {token}",
                        "X-Real-IP": address,
                    },
                )

            for _ in range(5):
                assert (await post(ivan_token, "203.0.113.9")).status_code == 200
            wrong = [(await post("reb_sbagliato", "198.51.100.7")).status_code for _ in range(3)]
            limited = await post("reb_sbagliato", "198.51.100.7")
            limited_good = await post(ivan_token, "198.51.100.7")
            elsewhere = await post(ivan_token, "203.0.113.9")
            health = await client.get("/health", headers={"X-Real-IP": "198.51.100.7"})
    assert wrong == [401, 401, 401]
    assert limited.status_code == 429 and limited.headers["retry-after"] == "60"
    assert limited_good.status_code == 429
    assert elsewhere.status_code == 200
    assert health.status_code == 200


@pytest.mark.asyncio
async def test_an_ipv6_client_is_one_client_per_its_64(
    mcp_engine: Engine, factory: sessionmaker[Session]
) -> None:
    app = McpHttpApp(
        lambda: factory,
        _settings(mcp_engine),
        lambda *a: (503, b""),
        McpSettings(bad_bearers_per_minute=2),
    )
    async with app.lifespan():
        transport = httpx2.ASGITransport(app=app)
        async with httpx2.AsyncClient(transport=transport, base_url="http://localhost") as client:
            statuses = []
            for suffix in ("1", "2", "3"):
                response = await client.post(
                    "/mcp",
                    json=INITIALIZE,
                    headers={
                        **ACCEPT,
                        "Authorization": "Bearer reb_sbagliato",
                        "X-Real-IP": f"2001:db8:0:1::{suffix}",
                    },
                )
                statuses.append(response.status_code)
    assert statuses == [401, 401, 429]
