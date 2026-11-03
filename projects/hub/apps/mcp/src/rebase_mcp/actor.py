"""Who is calling: the admin behind a stdio token, and over HTTP one admin per request.

Over stdio `CachedAdmin` resolves the process's token again every minute, so a token
revoked or expired, or an owner demoted, stops writing within that minute and not at the
next restart (REB-803). Over HTTP every request
carries its own bearer, resolved by `rebase_mcp.http` and stamped on the ASGI scope;
`AdminFromRequest` is the SDK's middleware that copies it from `ctx.request.state` into
a `ContextVar` for the duration of the call, and `request_admin` is what `build_server`
reads it back through. A `ContextVar`, because the SDK dispatches tools both on the
event loop and in a thread pool, and it is the one primitive correct for both.
"""

import contextvars
import threading
import time
from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Any

from mcp.server.context import CallNext, HandlerResult, ServerRequestContext
from mcp.server.mcpserver.exceptions import ToolError
from mcp.shared.exceptions import MCPError
from sqlalchemy.orm import Session

from rebase_core.admin_tokens import AdminRead, AdminTokenService
from rebase_core.errors import DomainError

_CURRENT_ADMIN: contextvars.ContextVar[AdminRead] = contextvars.ContextVar("mcp_request_admin")

ADMIN_STATE_KEY = "admin"

# How long a stdio process trusts the token it resolved: a minute is the longest a
# revoked token keeps writing, and the database is asked once per minute, not per call.
ADMIN_CACHE_SECONDS = 60.0
TOKEN_NO_LONGER_VALID = (
    "This server's token is no longer valid: it was revoked, it expired or its admin is no "
    "longer an admin. Create a new token from the admin area («Agenti»)."
)


class CachedAdmin:
    """`AdminProvider` for stdio: the admin behind a personal token, resolved through
    `AdminTokenService` and kept for `ttl` seconds. `refresh` resolves at once and
    raises the `DomainError` (the start-up check: a process nobody has a token for does
    not start); a call after the cache has expired resolves again, and a token that no
    longer resolves is a `ToolError` saying so, never the previous admin."""

    def __init__(
        self,
        open_session: Callable[[], AbstractContextManager[Session]],
        raw_token: str | None,
        *,
        ttl: float = ADMIN_CACHE_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._open_session = open_session
        self._raw_token = raw_token
        self._ttl = ttl
        self._clock = clock
        self._lock = threading.Lock()
        self._admin: AdminRead | None = None
        self._resolved_at = 0.0

    def refresh(self) -> AdminRead:
        with self._open_session() as session:
            admin = AdminTokenService(session).resolve(self._raw_token)
        with self._lock:
            self._admin = admin
            self._resolved_at = self._clock()
        return admin

    def __call__(self) -> AdminRead:
        with self._lock:
            admin = self._admin
            fresh = admin is not None and self._clock() - self._resolved_at < self._ttl
        if admin is not None and fresh:
            return admin
        try:
            return self.refresh()
        except DomainError:
            with self._lock:
                self._admin = None
            raise ToolError(TOKEN_NO_LONGER_VALID) from None


def request_admin() -> AdminRead:
    """`AdminProvider` for the HTTP transport: the admin bound to the current call."""
    try:
        return _CURRENT_ADMIN.get()
    except LookupError as exc:
        raise RuntimeError(
            "no request admin is bound: every tool over HTTP must run inside AdminFromRequest"
        ) from exc


class AdminFromRequest:
    """`ServerMiddleware`: binds the request's admin for the handler, then unbinds it.
    A request without one is refused rather than run as nobody: the ASGI layer stamps
    an admin on every authenticated request, so its absence is a programming error."""

    async def __call__(
        self, ctx: ServerRequestContext[Any, Any], call_next: CallNext
    ) -> HandlerResult:
        request = ctx.request
        admin = getattr(getattr(request, "state", None), ADMIN_STATE_KEY, None)
        if not isinstance(admin, AdminRead):
            raise MCPError(code=-32001, message="no admin behind the request")
        token = _CURRENT_ADMIN.set(admin)
        try:
            return await call_next(ctx)
        finally:
            _CURRENT_ADMIN.reset(token)
