"""A module's switch reaches the MCP server within the settings cache's window (spec
2026-11-09, B2 and B8): not on the next request, and not never.

Space settings are read through a cache of `OVERRIDES_TTL_SECONDS` that the server shares with
the API, and the server rebuilds a space's tool list when what it reads changes. So a switch
made by an admin (here straight on the space's database, as the API would) is invisible
inside the window and visible once it has passed, with no restart and nobody reconnecting.
"""

from collections.abc import AsyncIterator
from uuid import UUID

import pytest
import pytest_asyncio
from sqlalchemy import create_engine
from test_http_transport import (  # noqa: F401
    _tool_names,
    http_settings,
    spaces,
)

from pigrocrm.core.actor import Actor
from pigrocrm.core.config import Settings
from pigrocrm.core.db import session_factory
from pigrocrm.core.modules.schemas import SpaceModulesSet
from pigrocrm.core.modules.service import SpaceModulesService
from pigrocrm.core.tenants import ensure_tenants_database
from pigrocrm.core.tenants import registry as registry_module
from pigrocrm.core.tenants.database import tenant_database_name, tenant_database_url
from pigrocrm.core.tenants.registry import OVERRIDES_TTL_SECONDS
from pigrocrm_mcp.http import McpHttpApp, create_app


@pytest_asyncio.fixture
async def cached(http_settings: Settings) -> AsyncIterator[McpHttpApp]:  # noqa: F811
    ensure_tenants_database(http_settings).dispose()
    app = create_app(http_settings, overrides_ttl=OVERRIDES_TTL_SECONDS)
    async with app.lifespan():
        yield app


def _switch(http_settings: Settings, user_id: UUID, **change: list[str]) -> None:  # noqa: F811
    engine = create_engine(
        tenant_database_url(http_settings, tenant_database_name("spazio-uno")), future=True
    )
    try:
        with session_factory(engine)() as session:
            SpaceModulesService(session, http_settings).set_modules(
                SpaceModulesSet(**change),  # type: ignore[arg-type]
                Actor(id=user_id, type="user", role="admin"),
            )
    finally:
        engine.dispose()


@pytest.mark.asyncio
async def test_a_switch_reaches_the_tool_list_within_the_cache_window_and_back(
    cached: McpHttpApp,
    spaces: dict[str, tuple[str, UUID]],  # noqa: F811
    http_settings: Settings,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw, user_id = spaces["spazio-uno"]
    url = "http://prova/spazio-uno/mcp"
    clock = {"now": 1000.0}
    monkeypatch.setattr(registry_module.time, "monotonic", lambda: clock["now"])

    before = await _tool_names(cached, url, raw)
    assert {"create_deal", "log_time", "convert_deal_to_contract"} <= set(before)

    _switch(http_settings, user_id, off=["time", "deals"])
    # Inside the window the cached settings are served: the switch is not seen yet.
    clock["now"] += OVERRIDES_TTL_SECONDS - 1
    assert await _tool_names(cached, url, raw) == before

    clock["now"] += 2
    after = await _tool_names(cached, url, raw)
    assert not {"create_deal", "get_deal", "log_time", "convert_deal_to_contract"} & set(after)
    assert {"create_organization", "create_contract", "describe_space"} <= set(after)
    # Other spaces are untouched by it.
    assert "create_deal" in await _tool_names(
        cached, "http://prova/spazio-due/mcp", spaces["spazio-due"][0]
    )

    _switch(http_settings, user_id, on=["deals", "time"])
    clock["now"] += OVERRIDES_TTL_SECONDS + 1
    assert await _tool_names(cached, url, raw) == before
