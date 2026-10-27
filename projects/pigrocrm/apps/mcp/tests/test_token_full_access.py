"""The privileged tools follow the token, not the space (REB-810).

`Settings.mcp_full_access` is the ceiling: with it closed there is no privileged tool at
all. With it open, which tokens are shown them is each token's own `full_access`, stamped
on the actor by `PatService.resolve`. This file builds the server the way every other test
here does and varies only the actor, so the one thing that moves between two assertions is
the flag.

`test_http_transport.py` owns the same promise over the wire with real tokens and a real
space; `test_mcp_invoice_ban.py` owns that nothing privileged is registered without the
ceiling.
"""

from pathlib import Path
from typing import Any

import pytest
from mcp import Client
from sqlalchemy.orm import Session
from test_mcp_invoice_ban import FORBIDDEN, FORBIDDEN_NEEDING_GMAIL

from pigrocrm.core.actor import Actor
from pigrocrm.core.config import Settings
from pigrocrm.core.errors import AgentForbidden
from pigrocrm.core.storage import LocalFileStorage
from pigrocrm_mcp.server import build_server

OPEN = Settings(_env_file=None, mcp_full_access=True)  # type: ignore[call-arg]
CLOSED = Settings(_env_file=None)  # type: ignore[call-arg]
WITHOUT_GMAIL = set(FORBIDDEN) - FORBIDDEN_NEEDING_GMAIL


def _server(session: Session, tmp_path: Path, settings: Settings, *, full_access: bool) -> Any:
    return build_server(
        lambda: session,
        lambda: Actor(id=None, type="mcp", role="admin", full_access=full_access),
        LocalFileStorage(tmp_path),
        settings,
    )


async def _names(server: Any) -> set[str]:
    return {tool.name for tool in await server.list_tools()}


async def test_a_token_without_the_flag_is_shown_the_surface_of_a_closed_space(
    mcp_session: Session, tmp_path: Path
) -> None:
    """On a space whose switch is open, the token that was not opened lists exactly what a
    space that never opened it lists: no more, and no different server."""
    unopened = await _names(_server(mcp_session, tmp_path, OPEN, full_access=False))
    ceiling_closed = await _names(_server(mcp_session, tmp_path, CLOSED, full_access=False))

    assert unopened == ceiling_closed
    assert not unopened & set(FORBIDDEN)


async def test_a_token_with_the_flag_is_shown_every_privileged_tool(
    mcp_session: Session, tmp_path: Path
) -> None:
    opened = await _names(_server(mcp_session, tmp_path, OPEN, full_access=True))
    closed = await _names(_server(mcp_session, tmp_path, OPEN, full_access=False))

    assert opened >= WITHOUT_GMAIL
    assert opened - closed == WITHOUT_GMAIL


async def test_the_flag_cannot_open_a_space_whose_switch_is_closed(
    mcp_session: Session, tmp_path: Path
) -> None:
    """A token carrying the flag on a space that closed the ceiling (an administrator
    closed it after the token was made): `PatService.resolve` would not stamp it, and the
    server has nothing registered to show either way."""
    names = await _names(_server(mcp_session, tmp_path, CLOSED, full_access=True))

    assert not names & set(FORBIDDEN)


@pytest.mark.parametrize("tool", sorted(WITHOUT_GMAIL))
async def test_a_privileged_tool_called_by_name_without_the_flag_is_unknown(
    mcp_session: Session, tmp_path: Path, tool: str
) -> None:
    """Not listed is not enough: a client that remembers the name from another token, or
    guesses it, gets the answer a space that never opened the switch gives, and the call
    never reaches the tool or its service."""
    async with Client(_server(mcp_session, tmp_path, OPEN, full_access=False)) as client:
        result = await client.call_tool(tool, {})

    assert result.is_error
    assert result.content[0].text == f"Unknown tool: {tool}"  # type: ignore[union-attr]


async def test_an_ordinary_tool_is_unaffected_by_the_flag(
    mcp_session: Session, tmp_path: Path
) -> None:
    async with Client(_server(mcp_session, tmp_path, OPEN, full_access=False)) as client:
        result = await client.call_tool("describe_schema", {"entity_type": "customer"})

    assert not result.is_error


async def test_a_revoked_token_on_a_server_that_asks_the_actor_is_shown_the_ordinary_surface(
    mcp_session: Session, tmp_path: Path
) -> None:
    """The actor provider refuses (a stdio token revoked since start-up): the list falls
    back to the ordinary surface and the refusal is the first real call's to make."""

    def refuses() -> Actor:
        raise AgentForbidden("token")

    server = build_server(lambda: mcp_session, refuses, LocalFileStorage(tmp_path), OPEN)

    assert not await _names(server) & set(FORBIDDEN)
