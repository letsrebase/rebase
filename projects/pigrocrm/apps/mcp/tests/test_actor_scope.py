"""The actor of an HTTP request reaches the tool through a ContextVar (ORB-170).

Verified in-process against the installed SDK, not assumed: under stateless Streamable
HTTP a `ServerMiddleware` receives the Starlette `Request` as `ctx.request`, and a
`ContextVar` set there is visible to the tool function `call_next` reaches (the SDK
dispatches the call inside the same task tree). This test is what pins that.
"""

from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any

import httpx2
import pytest
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.server import MCPServer
from mcp.server.transport_security import TransportSecuritySettings

from pigrocrm.core.actor import Actor
from pigrocrm_mcp.actor_scope import ActorFromRequest, RequestActorProvider

Scope = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[MutableMapping[str, Any]]]
Send = Callable[[MutableMapping[str, Any]], Awaitable[None]]

ADA = Actor(id=None, type="mcp", role="collaborator")


def _server(provider: RequestActorProvider) -> MCPServer:
    mcp = MCPServer("prova", middleware=[ActorFromRequest()])

    @mcp.tool()
    def who_are() -> str:
        actor = provider()
        return f"{actor.type}:{actor.role}"

    return mcp


def _stamping(inner: Any, actor: Actor | None) -> Callable[[Scope, Receive, Send], Awaitable[None]]:
    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and actor is not None:
            scope.setdefault("state", {})["actor"] = actor
        await inner(scope, receive, send)

    return app


async def _call(app: Any, starlette_app: Any) -> str:
    async with starlette_app.router.lifespan_context(starlette_app):
        transport = httpx2.ASGITransport(app=app)
        async with (
            httpx2.AsyncClient(transport=transport, base_url="http://prova") as client,
            streamable_http_client("http://prova/mcp", http_client=client) as streams,
        ):
            read, write = streams[0], streams[1]
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool("who_are", {})
                return result.content[0].text  # type: ignore[union-attr]


def _http_app(mcp: MCPServer) -> Any:
    return mcp.streamable_http_app(
        streamable_http_path="/mcp",
        stateless_http=True,
        json_response=True,
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )


def _messages(exc: BaseException) -> list[str]:
    """Every message reachable from `exc`, including anything an ExceptionGroup wraps.

    Verified against the installed SDK, not assumed: the transport does not turn
    `ActorFromRequest`'s `MCPError` into a JSON-RPC error the client re-raises as a
    plain `McpError` -- under the ASGI transport it surfaces buried inside nested
    `ExceptionGroup`s from the server's own task groups, and `str(ExceptionGroup(...))`
    does not include what it wraps. Walking `.exceptions` is what actually finds it.
    """
    messages = [str(exc)]
    if isinstance(exc, BaseExceptionGroup):
        for sub in exc.exceptions:
            messages.extend(_messages(sub))
    return messages


@pytest.mark.asyncio
async def test_the_actor_stamped_on_the_request_is_what_the_tool_sees() -> None:
    provider = RequestActorProvider()
    starlette = _http_app(_server(provider))
    assert await _call(_stamping(starlette, ADA), starlette) == "mcp:collaborator"


@pytest.mark.asyncio
async def test_a_request_without_an_actor_is_refused_before_the_tool_runs() -> None:
    provider = RequestActorProvider()
    starlette = _http_app(_server(provider))
    with pytest.raises(BaseException) as excinfo:
        await _call(_stamping(starlette, None), starlette)
    assert any("no actor" in message for message in _messages(excinfo.value))


def test_the_provider_refuses_outside_a_request() -> None:
    with pytest.raises(RuntimeError, match="no request actor is bound"):
        RequestActorProvider()()
