"""The HTTP MCP server does not start on the published placeholder signing key (PCORE-B-05)."""

import pytest

from pigrocrm.core.config import DEFAULT_JWT_SECRET, Settings
from pigrocrm_mcp.http import McpHttpApp


def test_the_http_app_refuses_a_placeholder_secret() -> None:
    placeholder = Settings(_env_file=None, jwt_secret=DEFAULT_JWT_SECRET)  # type: ignore[call-arg]
    with pytest.raises(ValueError, match="PIGROCRM_JWT_SECRET"):
        McpHttpApp(placeholder)
