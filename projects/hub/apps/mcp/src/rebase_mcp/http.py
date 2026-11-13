"""The MCP server over Streamable HTTP, for admins with a token (REB-213).

Served by the `mcp` compose service (`uvicorn rebase_mcp.http:app`), which the host
vhost proxies at `/api/hub/mcp`: a process of its own rather than a mount in the API,
because `apps/api` may not import `apps/mcp` (each adapter's `ruff.toml`), the same
reason the CRM runs its transport as a service. A pure ASGI application around the
SDK's own Starlette app: every request is first resolved to the admin behind its bearer
(`AdminTokenService.resolve`, in a worker thread since it reads the database), then
handed to the SDK with the admin stamped on the scope, where `AdminFromRequest` binds
it for the tools. A missing, unknown, revoked or deactivated-owner token is one uniform
401 with `WWW-Authenticate: Bearer`, the CRM's wording.

One `MCPServer` for the process: the tool list does not depend on who is calling, only
the author of what the tools write does. Stateless and JSON-only, so nothing about a
session lives in the process and a restart loses nothing.
"""

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable, MutableMapping
from contextlib import AbstractContextManager, asynccontextmanager, closing
from typing import Any

import anyio
import anyio.to_thread
from mcp.server.transport_security import TransportSecuritySettings
from sqlalchemy.orm import Session, sessionmaker
from starlette.types import ASGIApp

from rebase_core.admin_tokens import AdminRead, AdminTokenService, is_token
from rebase_core.config import Settings, get_settings
from rebase_core.crm_link import CrmLeadService
from rebase_core.db import create_engine_from_settings, session_factory
from rebase_core.dependencies import Dependencies, build_dependencies
from rebase_core.errors import DomainError
from rebase_core.http import HttpCall, urllib_call
from rebase_core.ratelimit import UNKNOWN_CLIENT, RateLimiter, client_key
from rebase_mcp.actor import ADMIN_STATE_KEY, AdminFromRequest, request_admin
from rebase_mcp.server import build_server
from rebase_mcp.settings import McpSettings

Scope = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[MutableMapping[str, Any]]]
Send = Callable[[MutableMapping[str, Any]], Awaitable[None]]

MCP_PATH = "/mcp"
HEALTH_PATH = "/health"
NOT_FOUND = "not found"
INTERNAL_ERROR = "internal error"
TOO_MANY_BEARERS = "Too many invalid tokens from here. Try again in a minute."
INVALID_TOKEN_ANSWER = "Invalid token"
BEARER_WINDOW_SECONDS = 60

logger = logging.getLogger(__name__)


class McpHttpApp:
    """`factory` is a callable answering the session factory, so the API can hand the
    one it builds lazily and a test can hand its own. `mcp_settings` carries the Host
    names the transport answers to and the failed-bearer allowance (REB-889)."""

    def __init__(
        self,
        factory: Callable[[], sessionmaker[Session]],
        settings: Settings,
        http: HttpCall,
        mcp_settings: McpSettings | None = None,
    ) -> None:
        self._factory = factory
        self._settings = settings
        self._http = http
        self._mcp_settings = mcp_settings or McpSettings()
        self._bad_bearers = RateLimiter(
            self._mcp_settings.bad_bearers_per_minute, BEARER_WINDOW_SECONDS
        )
        self._dependencies: Dependencies | None = None
        self._app: ASGIApp | None = None

    def _deps(self) -> Dependencies:
        """The collaborators the tools need, built once from the settings
        (`rebase_core.dependencies`, the same set stdio builds)."""
        if self._dependencies is None:
            self._dependencies = build_dependencies(self._settings, self._open, http=self._http)
        return self._dependencies

    def crm_leads(self, session: Session) -> CrmLeadService:
        """The service behind `link_company_to_pigro` (REB-869), asking «Modelli AI»
        for the `crm_lead` draft only when one is needed."""
        return self._deps().crm_leads(session)

    def _open(self) -> AbstractContextManager[Session]:
        """A session for the model seam, which reads «Modelli AI» on each call."""
        return closing(self._factory()())

    def _server(self) -> ASGIApp:
        if self._app is None:
            deps = self._deps()
            server = build_server(
                self._factory(),
                request_admin,
                settings=self._settings,
                http=self._http,
                middleware=[AdminFromRequest()],
                card_llm=deps.card_llm,
                sender=deps.sender,
                crm_leads=deps.crm_leads,
            )
            self._starlette = server.streamable_http_app(
                streamable_http_path=MCP_PATH,
                stateless_http=True,
                json_response=True,
                # nginx binds the public Host (`proxy_set_header Host $host`), so the
                # defaults name the public hosts, the loopback and the compose service;
                # a Host outside them is refused, whatever the bind address is (REB-889).
                transport_security=TransportSecuritySettings(
                    enable_dns_rebinding_protection=True,
                    allowed_hosts=self._mcp_settings.hosts,
                    allowed_origins=self._mcp_settings.origins,
                ),
            )
            self._app = self._starlette
        return self._app

    @asynccontextmanager
    async def lifespan(self) -> AsyncIterator[None]:
        """The SDK's session manager runs inside the Starlette app's lifespan; the API
        enters this from its own, and a test drives it directly.

        The lifespan is entered and left by a task of its own rather than by this
        context manager: anyio refuses to leave a cancel scope in a task other than the
        one that entered it, and `pytest_asyncio`'s fixtures run setup and teardown as
        two separate tasks. The CRM's transport records the same finding.
        """
        self._server()
        running = anyio.Event()
        shutdown = anyio.Event()
        failures: list[BaseException] = []

        async def own() -> None:
            try:
                async with self._starlette.router.lifespan_context(self._starlette):
                    running.set()
                    await shutdown.wait()
            except BaseException as exc:
                failures.append(exc)
                running.set()
                raise

        owner = asyncio.create_task(own())
        await running.wait()
        if failures:
            await asyncio.gather(owner, return_exceptions=True)
            raise failures[0]
        try:
            yield
        finally:
            shutdown.set()
            await owner

    async def _lifespan_protocol(self, receive: Receive, send: Send) -> None:
        """What uvicorn speaks: the session manager runs between startup and shutdown.
        A test that drives the app through `httpx2.ASGITransport`, which has no
        lifespan, enters `lifespan()` itself instead."""
        _expect(await receive(), "lifespan.startup")
        try:
            async with self.lifespan():
                await send({"type": "lifespan.startup.complete"})
                _expect(await receive(), "lifespan.shutdown")
        except Exception as exc:
            logger.exception("the MCP server could not start")
            await send({"type": "lifespan.startup.failed", "message": str(exc)})
            return
        await send({"type": "lifespan.shutdown.complete"})

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "lifespan":
            await self._lifespan_protocol(receive, send)
            return
        if scope["type"] != "http":
            if scope["type"] == "websocket":
                await send({"type": "websocket.close"})
            return
        path: str = scope.get("path", "")
        if path == HEALTH_PATH:
            await _json(send, 200, {"status": "ok"})
            return
        if path.rstrip("/") not in ("", MCP_PATH):
            await _json(send, 404, {"detail": NOT_FOUND})
            return
        client = client_key(_observed_address(scope))
        if not self._bad_bearers.has_room(client):
            await _json(
                send,
                429,
                {"detail": TOO_MANY_BEARERS},
                {"retry-after": str(BEARER_WINDOW_SECONDS)},
            )
            return
        try:
            admin = await anyio.to_thread.run_sync(self._authenticate, _bearer_of(scope))
        except DomainError:
            # Only a failure is charged: a busy agent with a good token never meets this.
            self._bad_bearers.spend(client)
            await _json(send, 401, {"detail": INVALID_TOKEN_ANSWER}, {"www-authenticate": "Bearer"})
            return
        except Exception:
            logger.exception("failed to authenticate an MCP request over HTTP")
            await _json(send, 500, {"detail": INTERNAL_ERROR})
            return
        scope["path"] = MCP_PATH
        scope["raw_path"] = MCP_PATH.encode("utf-8")
        scope.setdefault("state", {})[ADMIN_STATE_KEY] = admin
        await self._server()(scope, receive, send)

    def _authenticate(self, raw: str | None) -> AdminRead:
        session = self._factory()()
        try:
            return AdminTokenService(session).resolve(raw)
        finally:
            session.close()


def _expect(message: MutableMapping[str, Any], kind: str) -> None:
    """The lifespan protocol's next message must be `kind`; anything else is a server
    that does not speak ASGI, and failing loudly beats serving on."""
    if message["type"] != kind:
        raise RuntimeError(f"expected the ASGI message {kind!r}, got {message['type']!r}")


def _observed_address(scope: Scope) -> str:
    """The caller's address as nginx saw it (`X-Real-IP`, else the last hop of
    `X-Forwarded-For`: the first is whatever the caller typed), else the socket's peer."""
    headers: dict[bytes, bytes] = {name.lower(): value for name, value in scope.get("headers", [])}
    real_ip = headers.get(b"x-real-ip", b"").decode("latin-1").strip()
    if real_ip:
        return real_ip
    forwarded = headers.get(b"x-forwarded-for", b"").decode("latin-1")
    if forwarded:
        return forwarded.split(",")[-1].strip()
    client = scope.get("client")
    return client[0] if client else UNKNOWN_CLIENT


def _bearer_of(scope: Scope) -> str | None:
    headers: list[tuple[bytes, bytes]] = scope.get("headers", [])
    for name, value in headers:
        if name.lower() == b"authorization":
            scheme, _, credential = value.decode("latin-1").partition(" ")
            if scheme.lower() == "bearer" and is_token(credential):
                return credential
            return None
    return None


async def _json(
    send: Send, status: int, body: dict[str, Any], extra_headers: dict[str, str] | None = None
) -> None:
    payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
    headers = [
        (b"content-type", b"application/json; charset=utf-8"),
        (b"content-length", str(len(payload)).encode("ascii")),
    ]
    for name, value in (extra_headers or {}).items():
        headers.append((name.encode("latin-1"), value.encode("latin-1")))
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": payload})


def create_app(settings: Settings | None = None) -> McpHttpApp:
    """For uvicorn: one engine for the process, opened on the first request."""
    settings = settings or get_settings()
    factory: sessionmaker[Session] | None = None

    def lazy() -> sessionmaker[Session]:
        nonlocal factory
        if factory is None:
            factory = session_factory(create_engine_from_settings(settings))
        return factory

    return McpHttpApp(lazy, settings, urllib_call)


app = create_app()
