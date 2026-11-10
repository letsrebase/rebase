"""Who is calling, over HTTP: one actor per request, carried on a ContextVar.

Over stdio the actor is resolved once at start-up and never changes (`__main__.py`).
Over HTTP every request carries its own bearer token, so the actor is resolved per
request by `pigrocrm_mcp.http` and stamped on the ASGI scope; `ActorFromRequest` is
the SDK's context-tier middleware that copies it from `ctx.request.state` into a
`ContextVar` for the duration of the call, and `RequestActorProvider` is the
`ActorProvider` `build_server` reads it back through. A `ContextVar`, like
`ScopedSessionProvider`'s session, because the SDK dispatches tools both on the event
loop and in a thread pool, and a `ContextVar` is the one primitive correct for both.
"""

import contextvars
from typing import Any

from mcp.server.context import CallNext, HandlerResult, ServerRequestContext
from mcp.shared.exceptions import MCPError

from pigrocrm.core.actor import Actor

_CURRENT_ACTOR: contextvars.ContextVar[Actor] = contextvars.ContextVar("mcp_request_actor")

ACTOR_STATE_KEY = "actor"


class RequestActorProvider:
    """`ActorProvider` for the HTTP transport: the actor bound to the current call."""

    def __call__(self) -> Actor:
        try:
            return _CURRENT_ACTOR.get()
        except LookupError as exc:
            raise RuntimeError(
                "no request actor is bound: every tool and resource over HTTP must run "
                "inside ActorFromRequest"
            ) from exc


class ActorFromRequest:
    """`ServerMiddleware`: binds the request's actor for the handler, then unbinds it.

    Refuses a request that carries no actor instead of letting the tool run as nobody:
    the ASGI layer stamps one on every authenticated request, so its absence is a
    programming error, and the message is the product's, because it is what a client
    would show.
    """

    async def __call__(
        self, ctx: ServerRequestContext[Any, Any], call_next: CallNext
    ) -> HandlerResult:
        request = ctx.request
        actor = getattr(getattr(request, "state", None), ACTOR_STATE_KEY, None)
        if not isinstance(actor, Actor):
            raise MCPError(code=-32001, message="no actor associated with the request")
        token = _CURRENT_ACTOR.set(actor)
        try:
            return await call_next(ctx)
        finally:
            _CURRENT_ACTOR.reset(token)
