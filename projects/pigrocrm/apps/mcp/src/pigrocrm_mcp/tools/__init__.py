"""The agent's tools, one module per family under `families/`.

Every tool is a thin call into a core service: a tool that contained business logic would
be logic the web app cannot reach, exactly the failure this architecture exists to prevent.
Custom fields travel as a plain dict validated by the core validator; `describe_schema` says
which keys are legal right now.

`tools/<family>.py` (`customers.py`, `contracts.py`...) holds what a tool calls; the tool
itself, its signature and its description, is `families/<family>.py`. The tools behind a
switch (`privileged.py`, `drive_privileged.py`) and behind a configured integration
(`gmail.py`, `drive.py`, `slack.py`) register from their own module, in `server.py`.
"""

from collections.abc import Callable
from typing import Any

from mcp.server import MCPServer

from pigrocrm_mcp.context import McpContext
from pigrocrm_mcp.tools.families import FAMILIES


def register_entity_tools(mcp: MCPServer, context: McpContext, guard: Callable[..., Any]) -> None:
    for family in FAMILIES:
        family.register(mcp, context, guard)
