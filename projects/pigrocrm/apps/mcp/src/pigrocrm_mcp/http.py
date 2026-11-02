"""The MCP server over Streamable HTTP, one endpoint per space (ORB-170).

`/<slug>/mcp` and `/mcp` (the root), served by the `mcp` compose service behind the
web container's nginx. A pure ASGI application rather than a Starlette one, because it
has to rewrite the path *before* routing and stamp the actor on the scope, exactly as
`pigrocrm_api.tenancy.TenantPrefixMiddleware` does for the API -- and because the SDK's
own Starlette app, one per space, is what it dispatches to.

The three answers every request needs come from `SpaceRegistry` (which database, what
the space's settings are, which rows lay over them); the credential is a personal
access token as a bearer, resolved by `PatService.resolve` on the space's database with
the space's settings applied, so `Actor.full_access` is the space's answer. Everything
is lazy: importing this module opens nothing, and a space's server is built on its
first request and rebuilt when its settings rows change.

Why one `MCPServer` per space: `build_server` registers tools according to the settings
it receives (`mcp_full_access`, whether Google is configured), so a process serving
several spaces cannot share one tool list.

Why an anyio task group of its own: the Starlette app the SDK returns has a lifespan
that starts its session manager, without which every request fails. Space apps are
built inside a request, so their lifespan is entered by a task spawned in a group this
application opens once for the whole process, and left when the process stops --
anyio does not allow entering a scope in one task and leaving it in another.
"""

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable, MutableMapping
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import anyio
import anyio.to_thread
from anyio.abc import TaskGroup, TaskStatus
from mcp.server.transport_security import TransportSecuritySettings
from starlette.types import ASGIApp

from pigrocrm.core.actor import Actor
from pigrocrm.core.auth.pat_service import PAT_PREFIX, PatService
from pigrocrm.core.config import Settings, get_settings, require_production_secrets
from pigrocrm.core.errors import DomainError, ValidationFailed
from pigrocrm.core.storage import DocumentStorage, LocalFileStorage, storage_from_settings
from pigrocrm.core.tenants import (
    MCP_SEGMENTS,
    OVERRIDES_TTL_SECONDS,
    SpaceRegistry,
    split_tenant_prefix,
)
from pigrocrm_mcp import analytics
from pigrocrm_mcp.actor_scope import ACTOR_STATE_KEY, ActorFromRequest, RequestActorProvider
from pigrocrm_mcp.context import ScopedSessionProvider
from pigrocrm_mcp.server import build_server

Scope = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[MutableMapping[str, Any]]]
Send = Callable[[MutableMapping[str, Any]], Awaitable[None]]

MCP_PATH = "/mcp"
HEALTH_PATH = "/health"
_ROOT = ""

# The API's wording, so a client sees the same words whichever door it knocked on.
INVALID_TOKEN = "Token non valido"
SPACE_NOT_FOUND = "spazio non trovato"
NOT_FOUND = "non trovato"
INTERNAL_ERROR = "errore interno"

logger = logging.getLogger(__name__)


@dataclass
class _SpaceApp:
    app: ASGIApp
    # The settings this server was built from: the registry hands out the very same object
    # while the space's rows are unchanged, so `is` asks whether the tools are stale.
    settings: Settings
    stop: anyio.Event


@dataclass(frozen=True)
class _Authenticated:
    actor: Actor
    settings: Settings


class McpHttpApp:
    def __init__(self, settings: Settings, *, overrides_ttl: float = OVERRIDES_TTL_SECONDS) -> None:
        require_production_secrets(settings)
        self._settings = settings
        self._registry = SpaceRegistry(settings, overrides_ttl=overrides_ttl)
        self._spaces: dict[str, _SpaceApp] = {}
        self._build_lock = anyio.Lock()
        self._task_group: TaskGroup | None = None

    # -- lifespan --------------------------------------------------------------------

    @asynccontextmanager
    async def _run(self) -> AsyncIterator[None]:
        """Opens the task group that owns every space's lifespan, and closes it.

        The group is entered by a task of its own rather than by this context manager,
        and that is not decoration: anyio refuses to leave a cancel scope in a task
        other than the one that entered it, and the two ends of this context manager do
        not always run in the same task. `pytest_asyncio`'s async fixtures are the case
        that proves it -- setup and teardown are two separate `run_until_complete`
        calls, so a group entered here and left there raises «Attempted to exit cancel
        scope in a different task». Owning it from `_own` makes both ends that task's,
        whoever enters and leaves this context manager.

        `asyncio.create_task` rather than an anyio primitive because anyio has no
        detached spawn: a task group is its only spawner, and a task group is exactly
        what cannot be opened here. The process is on asyncio either way -- uvicorn
        serves it, and the tests run on `pytest_asyncio`.
        """
        running = anyio.Event()
        shutdown = anyio.Event()
        owner = asyncio.create_task(self._own(running, shutdown))
        await running.wait()
        try:
            yield
        finally:
            shutdown.set()
            try:
                await owner  # re-raises whatever a space's lifespan failed with
            finally:
                # Under the `finally` too: a space whose lifespan failed is exactly the
                # process that must still give its engines back. The PostHog client
                # after the spaces, in a thread: every space's last events are queued
                # by then, and `shutdown` blocks on the network.
                self._registry.dispose()
                await anyio.to_thread.run_sync(analytics.shutdown)

    async def _own(self, running: anyio.Event, shutdown: anyio.Event) -> None:
        async with anyio.create_task_group() as task_group:
            self._task_group = task_group
            running.set()
            try:
                await shutdown.wait()
                for space in self._spaces.values():
                    space.stop.set()
            finally:
                # `finally`, not two plain statements after the wait: a space's lifespan
                # failing cancels this scope, and what must not survive that is a
                # `_task_group` pointing at a group that has exited (`start_soon` then
                # raises «task group is not active») or a cached `_SpaceApp` whose
                # session manager has stopped (every request through it is a 500 from
                # the SDK). Forgetting both makes the next request rebuild instead.
                self._spaces.clear()
                self._task_group = None
        # Left the group, so every space's lifespan has finished: nothing is using the
        # engines any more.

    def lifespan(self) -> AbstractAsyncContextManager[None]:
        """For a test that drives the app through `httpx2.ASGITransport`, which does not
        speak the lifespan protocol. A server (uvicorn) goes through `__call__`."""
        return self._run()

    async def _lifespan_protocol(self, receive: Receive, send: Send) -> None:
        message = await receive()
        assert message["type"] == "lifespan.startup"
        async with self._run():
            await send({"type": "lifespan.startup.complete"})
            message = await receive()
            assert message["type"] == "lifespan.shutdown"
        await send({"type": "lifespan.shutdown.complete"})

    # -- the request -----------------------------------------------------------------

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "lifespan":
            await self._lifespan_protocol(receive, send)
            return
        if scope["type"] != "http":
            if scope["type"] == "websocket":
                # Nothing here speaks WebSocket, and the ASGI spec asks for a close
                # rather than silence: a server given no answer at all holds the
                # handshake open until it times out.
                await send({"type": "websocket.close"})
            return

        path: str = scope["path"]
        if path == HEALTH_PATH:
            await _json(send, 200, {"status": "ok"})
            return

        slug, rest = split_tenant_prefix(path, self._settings.root_slug, MCP_SEGMENTS)
        if rest.rstrip("/") != MCP_PATH:
            await _json(send, 404, {"detail": NOT_FOUND})
            return

        # The boundary of this application: everything up to the hand-over gets an
        # answer of ours, because a pure ASGI app that lets an exception out leaves the
        # server to send a bodyless 500 -- or nothing at all. Only the two codes this
        # path really produces are read as answers; any other `DomainError` is a bug
        # here, not a message for the caller, and is treated as one. Past the hand-over
        # the SDK's stateless handler catches its own per-request failures, so the call
        # below is deliberately outside this block.
        try:
            auth = await anyio.to_thread.run_sync(self._authenticate, slug, _bearer_of(scope))
            space = await self._space_app(slug, auth)
        except DomainError as exc:
            if exc.code == "not_found":
                # No slug in the line: it is whatever the caller typed.
                logger.warning("MCP request refused: unknown space")
                await _json(send, 404, {"detail": SPACE_NOT_FOUND})
            elif exc.code == "validation_failed":
                # The space is a registered one here. Never the token, never the header.
                logger.warning("MCP bearer refused, space=%s", slug or _ROOT)
                await _json(send, 401, {"detail": INVALID_TOKEN}, {"www-authenticate": "Bearer"})
            else:
                # Never the token: the log line carries the exception, never the header.
                logger.exception("unexpected domain error serving MCP over HTTP")
                await _json(send, 500, {"detail": INTERNAL_ERROR})
            return
        except Exception:
            logger.exception("failed to serve an MCP request over HTTP")
            await _json(send, 500, {"detail": INTERNAL_ERROR})
            return

        scope["path"] = MCP_PATH
        scope["raw_path"] = MCP_PATH.encode("utf-8")
        scope.setdefault("state", {})[ACTOR_STATE_KEY] = auth.actor
        await space.app(scope, receive, send)

    def _authenticate(self, slug: str | None, raw_token: str | None) -> _Authenticated:
        """Synchronous, run in a worker thread: two reads on the space's database.

        The slug is resolved first, so an unknown space is 404 whatever the header says,
        the way the API answers. Then the settings rows, so the token is resolved with
        the space's `mcp_full_access`, not the environment's. Any failure of the token
        itself is the same `ValidationFailed`, which the caller turns into one 401.
        """
        factory = self._registry.session_factory(slug)  # NotFound for an unknown slug
        if not raw_token:
            # Before the session opens: a missing or malformed header needs no read at
            # all. After the slug, so an unknown space is still 404. The same class
            # `PatService.resolve` raises, so the caller has one branch.
            raise ValidationFailed("token", "token", INVALID_TOKEN)
        with factory() as session:
            settings = self._registry.effective_settings(slug, session)
            actor = PatService(session, settings=settings).resolve(raw_token)
            session.commit()  # `resolve` may stamp last_used_at
        return _Authenticated(actor=actor, settings=settings)

    async def _space_app(self, slug: str | None, auth: _Authenticated) -> _SpaceApp:
        key = slug or _ROOT
        current = self._spaces.get(key)
        if current is not None and current.settings is auth.settings:
            return current
        async with self._build_lock:
            current = self._spaces.get(key)
            if current is not None and current.settings is auth.settings:
                return current
            if current is not None:
                # Forgotten *before* it is stopped, and that order is the whole point:
                # the settings changed, so this server's tool list is stale, but the
                # build that replaces it can fail (a bad override is exactly what this
                # path exists for). Left in the dictionary it would be handed to every
                # later request with these settings, each one a 500 from a session
                # manager that has stopped. Forgotten, a failed rebuild costs one
                # request and the next one builds again.
                self._spaces.pop(key, None)
                current.stop.set()
            built = await self._build(slug, auth)
            if self._task_group is None:
                # The group went away while this build was waiting on `task_group.start`:
                # `_own` set `stop` on every space it could see and this one was not yet
                # among them, so nobody else will ever set it and the task holding its
                # lifespan would never leave.
                built.stop.set()
            self._spaces[key] = built
            return built

    async def _build(self, slug: str | None, auth: _Authenticated) -> _SpaceApp:
        if self._task_group is None:
            raise RuntimeError("McpHttpApp is not running: enter its lifespan first")
        provider = ScopedSessionProvider(lambda: self._registry.session_factory(slug))
        storage = _storage_for(slug, auth.settings, provider)
        server = build_server(
            provider,
            RequestActorProvider(),
            storage,
            auth.settings,
            middleware=[ActorFromRequest()],
            space=slug,
        )
        starlette = server.streamable_http_app(
            streamable_http_path=MCP_PATH,
            stateless_http=True,
            json_response=True,
            # Behind nginx the Host header is the public domain, which differs between
            # the root, the preview and every self-hosted installation; the service has
            # no host port, so nginx is what binds the name. Off is the honest setting.
            transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
        )
        stop = anyio.Event()

        async def serve(*, task_status: TaskStatus[None]) -> None:
            async with starlette.router.lifespan_context(starlette):
                task_status.started()
                await stop.wait()

        # `start`, not `start_soon` plus an event of our own: this task is not a child
        # of the group, so a lifespan that fails before it is ready would cancel the
        # group without cancelling us, and an event nobody will ever set is a request
        # that hangs forever holding `_build_lock` -- every later build behind it. anyio
        # re-raises a pre-start failure here instead, and the caller answers with it.
        await self._task_group.start(serve)
        return _SpaceApp(app=starlette, settings=auth.settings, stop=stop)


def _storage_for(
    slug: str | None, settings: Settings, provider: ScopedSessionProvider
) -> DocumentStorage | None:
    """A space's documents live on disk under its own folder, or on its own Drive when
    its settings say so -- the same two arms `pigrocrm_api.deps.get_storage` has. The
    root returns None and lets `build_server` do what `__main__.py` does."""
    if slug is None:
        return None
    if settings.storage_backend == "gdrive":
        return storage_from_settings(settings, session_factory=provider.new_session)
    return LocalFileStorage(Path(settings.storage_local_root) / "tenants" / slug)


def _bearer_of(scope: Scope) -> str | None:
    headers: list[tuple[bytes, bytes]] = scope.get("headers", [])
    for name, value in headers:
        if name.lower() == b"authorization":
            scheme, _, credential = value.decode("latin-1").partition(" ")
            # The scheme is case-insensitive (RFC 9110); the token is not, and one that
            # is not a PAT is no credential of ours, so the caller answers the same 401
            # it answers a wrong one with.
            if scheme.lower() == "bearer" and credential.startswith(PAT_PREFIX):
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


def create_app(
    settings: Settings | None = None, *, overrides_ttl: float = OVERRIDES_TTL_SECONDS
) -> McpHttpApp:
    return McpHttpApp(settings or get_settings(), overrides_ttl=overrides_ttl)


app = create_app()
