"""The MCP server's published surface, tool by tool: name, description and input schema.

The tool names, parameters and Italian descriptions are what agents are configured against
(REB-478, REB-892), so a refactor of how the server is built, such as the split by tool family,
must not change them by accident. `tool_surface.json` is the committed surface; this test
fails on any difference and prints which tools differ. A deliberate change (a new tool, a new
parameter, a reworded description) is made by regenerating the file:

    REBASE_UPDATE_TOOL_SURFACE=1 uv run pytest apps/mcp/tests/test_tool_surface.py

and the diff of the JSON is then the review of the change.
"""

import json
import os
from pathlib import Path
from typing import Any

from mcp import Client
from mcp_tool_support import IVAN
from sqlalchemy.orm import Session, sessionmaker

from rebase_mcp.server import build_server

SNAPSHOT = Path(__file__).with_name("tool_surface.json")


async def test_the_published_tool_surface_is_the_committed_one(
    factory: sessionmaker[Session],
) -> None:
    async with Client(build_server(factory, lambda: IVAN)) as client:
        tools = (await client.list_tools()).tools
    surface: dict[str, Any] = {
        tool.name: {"description": tool.description, "inputSchema": tool.input_schema}
        for tool in sorted(tools, key=lambda tool: tool.name)
    }
    if os.environ.get("REBASE_UPDATE_TOOL_SURFACE"):
        SNAPSHOT.write_text(json.dumps(surface, indent=1, ensure_ascii=False) + "\n")
    committed = json.loads(SNAPSHOT.read_text())

    assert sorted(surface) == sorted(committed), "tools added or removed"
    changed = [name for name in surface if surface[name] != committed[name]]
    assert not changed, f"description or parameters changed for: {', '.join(changed)}"
