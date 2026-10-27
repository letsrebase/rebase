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
        """Le regole di automazione del CRM, se attive, e le ultime esecuzioni con esito (anche
        quando una regola ha deciso di non agire, con il motivo). Leggila prima di concludere
        che un deal è stato spostato a mano. Si configurano solo dall'interfaccia web.
        """
        return automation_tools.describe_automations(context)
