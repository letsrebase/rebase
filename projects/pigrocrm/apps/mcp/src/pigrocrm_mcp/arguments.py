"""The one place a tool's argument errors are put in the product's words (REB-808), and the
one place the privileged tools are offered only to a token that may use them (REB-810).

A tool's arguments are validated twice. Inside the tool, `_guard` turns a bad value into
guidance. But before the body runs, the SDK validates the call against the tool's own
signature (`Tool.run` calls `FuncMetadata.validate_arguments` ahead of the function, so
ahead of `_guard`), and what that rejects, a missing parameter or one of the wrong type,
left as pydantic's English dump with an `errors.pydantic.dev` link, the text the guard's
own comments say must never reach an agent. A parameter the tool does not have was worse:
the SDK drops it without a word, so a misspelt name read as «not given».

`call_tool` is the single entry every tool call passes through, whatever the transport,
so it is overridden here once, not wrapped per tool: a tool added tomorrow is covered
without anyone remembering to. Nothing else about the call changes.

The privileged tools (invoices, rates, period locks, the Google reads) are registered when
the space's `mcp_full_access` is open, the ceiling. Whether a given token may see them is
its own `full_access` flag, which only a request knows, so `list_tools` and `call_tool` are
the two places that read the actor: a token without the flag is shown no privileged tool,
and one called by name answers «Unknown tool», exactly as on a space whose ceiling is
closed. The service refuses the act regardless (`Actor.full_access`); this is the door.
"""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import Tool as MCPTool
from pydantic import ValidationError

from pigrocrm.core.actor import Actor
from pigrocrm.core.errors import DomainError
from pigrocrm_mcp.errors import argument_error, to_agent_message, unknown_argument_error


class GuidedMCPServer(MCPServer):
    def __init__(
        self, *args: Any, actor_provider: Callable[[], Actor] | None = None, **kwargs: Any
    ) -> None:
        super().__init__(*args, **kwargs)
        # Without a provider nothing is gated: a server built with no notion of who is
        # calling has no token to ask.
        self._actor_provider = actor_provider
        self._privileged: frozenset[str] = frozenset()

    @contextmanager
    def privileged_registration(self) -> Iterator[None]:
        """Whatever is registered inside the block is a privileged tool: named once, here,
        by the code that registers it, so a tool added to a privileged module tomorrow is
        gated without anybody remembering to list it."""
        before = {tool.name for tool in self._tool_manager.list_tools()}
        yield
        added = {tool.name for tool in self._tool_manager.list_tools()} - before
        self._privileged = self._privileged | added

    def _may_use_privileged(self) -> bool:
        if not self._privileged or self._actor_provider is None:
            return True
        try:
            return self._actor_provider().full_access
        except DomainError:
            # A token revoked since, its owner deactivated: it is shown the ordinary
            # surface, a privileged tool called by name answers «Unknown tool», and the
            # first ordinary call it makes answers the refusal.
            return False

    async def list_tools(self) -> list[MCPTool]:
        tools = await super().list_tools()
        if self._may_use_privileged():
            return tools
        return [tool for tool in tools if tool.name not in self._privileged]

    async def call_tool(
        self, name: str, arguments: dict[str, Any], context: Context[Any, Any] | None = None
    ) -> Any:
        if name in self._privileged and not self._may_use_privileged():
            raise ToolError(f"Unknown tool: {name}")
        tool = self._tool_manager.get_tool(name)
        # What a client may send is what the tool declares. The analytics wrapper
        # (`analytics.install`) can add parameters to a tool's listing and strips them again
        # below this call, but only if its options are turned on, and `analytics.py` keeps
        # them off by decision (REB-642) with a test that the advertised schemas are exactly
        # the uninstrumented ones. Turning one on means deciding here, too.
        if tool is not None:
            known = tool.parameters.get("properties", {})
            unknown = [argument for argument in arguments if argument not in known]
            if unknown:
                raise _as_tool_error(name, unknown_argument_error(name, unknown, tool.parameters))
        try:
            return await super().call_tool(name, arguments, context)
        except ToolError as exc:
            # The SDK raises its own validation failure `from` the pydantic error. A
            # refusal from inside a tool body has the guard's `ToolError` as its cause,
            # not a `ValidationError`, so this reaches only what happened before the body.
            if tool is None or not isinstance(exc.__cause__, ValidationError):
                raise
            raise _as_tool_error(
                name, argument_error(name, exc.__cause__, tool.parameters)
            ) from exc


def _as_tool_error(name: str, failure: DomainError) -> ToolError:
    """Worded as the SDK words every other tool refusal, «Error executing tool <name>:
    <message>», so a client shows one shape however the call failed."""
    return ToolError(f"Error executing tool {name}: {to_agent_message(failure)}")
