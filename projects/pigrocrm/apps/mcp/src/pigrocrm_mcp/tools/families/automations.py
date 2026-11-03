"""What the CRM does by itself."""

from collections.abc import Callable
from typing import Any

from mcp.server import MCPServer

from pigrocrm_mcp.annotations import READ_ONLY
from pigrocrm_mcp.context import McpContext
from pigrocrm_mcp.tools import automations as automation_tools


def register(mcp: MCPServer, context: McpContext, guard: Callable[..., Any]) -> None:
    # ---- automations -------------------------------------------------------
    # One tool, and its counterpart is deliberately absent: `update_automation_config`
    # changes what the system will do to *future* data with nobody in the loop, and
    # `MCP_EXCLUDED_SLICE6` in `packages/core/tests/test_architecture.py` declares it as
    # the slice's one exclusion. Registered here, in `tools/automations.py`, rather than
    # alongside the dashboard: the two answer different questions and share no service.

    @mcp.tool(annotations=READ_ONLY)
    @guard
    def describe_automations() -> dict[str, Any]:
        """The CRM's automation rules, if enabled, and the latest runs with their outcome (also
        when a rule decided not to act, with the reason). Read it before concluding that a deal
        was moved by hand. Rules can only be configured from the web interface.
        """
        return automation_tools.describe_automations(context)
