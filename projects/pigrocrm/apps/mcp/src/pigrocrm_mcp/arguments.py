"""The one place a tool's argument errors are put in the product's words (REB-808).

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
"""

from typing import Any

from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import ValidationError

from pigrocrm.core.errors import DomainError
from pigrocrm_mcp.errors import argument_error, to_agent_message, unknown_argument_error


class GuidedMCPServer(MCPServer):
    async def call_tool(
        self, name: str, arguments: dict[str, Any], context: Context[Any, Any] | None = None
    ) -> Any:
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
