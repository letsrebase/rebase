"""What the space has switched on: its profile and its modules (spec 2026-11-09, B1)."""

from collections.abc import Callable
from typing import Any

from mcp.server import MCPServer

from pigrocrm.core.config import Settings
from pigrocrm.core.modules.service import SpaceModulesService
from pigrocrm_mcp.annotations import READ_ONLY
from pigrocrm_mcp.context import McpContext


def register(
    mcp: MCPServer, context: McpContext, guard: Callable[..., Any], settings: Settings
) -> None:
    @mcp.tool(annotations=READ_ONLY)
    @guard
    def describe_space() -> dict[str, Any]:
        """The space's profile (`solo`, `company`, `null` until chosen), its `onboarding`
        (`pending`, `skipped`, `done`) and its modules: each one's `enabled`, `requires` and
        `records`. A module that is off has no tools: this
        says why.
        """
        return (
            SpaceModulesService(context.session, settings)
            .describe(context.actor)
            .model_dump(mode="json")
        )
