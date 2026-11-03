"""The MCP surface of slice 9B's Drive diagnosis, and why it is one tool and not zero.

`describe_drive_account` is registered in the same `if gmail_configured(settings):`
block as the five Gmail tools, right after them -- one Google OAuth client serves both
credentials, so "is Google configured at all" is one fact, not two. Unlike every writing
or fetching operation on this surface, diagnosing the Drive credential is not gated
behind `mcp_full_access`: reading `GoogleDriveAccountService.health` costs no quota and
exercises no consent, exactly the reasoning `describe_gmail_account` already rests on in
`tools/gmail.py`.

The proof that this tool never reaches Google is the repository-root socket guard: it
fails any test that opens one, so a tool that grew a fetch would fail this file rather
than pass it quietly.
"""

import json
from pathlib import Path
from typing import Any

import pytest
from mcp import Client
from sqlalchemy.orm import Session
from test_gmail_tools import gmail_settings

from pigrocrm.core.actor import Actor
from pigrocrm.core.auth.models import User
from pigrocrm.core.drive.models import GoogleDriveAccount
from pigrocrm.core.storage import LocalFileStorage
from pigrocrm_mcp.server import build_server


def _payload(result: Any) -> Any:
    return result.structured_content or json.loads(result.content[0].text)


@pytest.fixture
def drive_actor(mcp_session: Session) -> Actor:
    """Mirrors `test_gmail_tools.gmail_actor`: a real `users.id`, since
    `GoogleDriveAccountService.health` resolves the account through
    `repo.account_for_user(actor.id)`, and `conftest.ADMIN` has `id=None`."""
    user = User(
        email="drive-owner@example.test",
        password_hash="x",
        nome="Owner",
        role="admin",
        active=True,
    )
    mcp_session.add(user)
    mcp_session.flush()
    return Actor(id=user.id, type="mcp", role="admin")


@pytest.fixture
def drive_server(mcp_session: Session, drive_actor: Actor, tmp_path: Path) -> Any:
    return build_server(
        lambda: mcp_session,
        lambda: drive_actor,
        LocalFileStorage(tmp_path),
        settings=gmail_settings(),
    )


async def _tool_names(server: Any) -> set[str]:
    async with Client(server) as client:
        return {tool.name for tool in (await client.list_tools()).tools}


async def test_describe_drive_account_is_present_when_google_is_configured(
    drive_server: Any,
) -> None:
    names = await _tool_names(drive_server)
    assert "describe_drive_account" in names


async def test_describe_drive_account_is_absent_when_google_is_not_configured(
    server: Any,
) -> None:
    """Absent, not broken -- the same rule `test_gmail_tools.py` proves for the other
    five tools. The `server` fixture builds with the unconfigured settings this
    repository actually runs its whole suite under."""
    names = await _tool_names(server)
    assert "describe_drive_account" not in names
    assert "create_customer" in names


async def test_describe_drive_account_reports_no_account_when_nothing_is_connected(
    drive_server: Any,
) -> None:
    """The base case: nobody has connected Drive for this user, so there is nothing to
    report and no banner -- an installation that never switched Drive on has nothing
    wrong with it."""
    async with Client(drive_server) as client:
        result = await client.call_tool("describe_drive_account", {})

    health = _payload(result)
    assert health["account"] is None
    assert health["banner"] is None
    assert "configured" in health
    assert "banner" in health
    assert health["configured"] is True


async def test_describe_drive_account_tells_an_agent_to_stop_retrying(
    drive_server: Any, mcp_session: Session, drive_actor: Actor
) -> None:
    """Mirrors `test_gmail_tools.py::test_describe_gmail_account_tells_an_agent_to_stop_retrying`:
    a revoked credential is reported through `banner`/`banner_text`, not discovered by
    an agent retrying a call that will never succeed."""
    account = GoogleDriveAccount(
        user_id=drive_actor.id,
        google_sub="sub-drive-1",
        email_address="io@example.it",
        refresh_token_ciphertext=b"\x01\x02ciphertext",
        refresh_token_nonce=b"\x03\x04nonce",
        scopes_granted=[
            "https://www.googleapis.com/auth/drive.readonly",
            "https://www.googleapis.com/auth/drive.file",
        ],
        status="revoked",
        last_error="revocato da Google",
    )
    mcp_session.add(account)
    mcp_session.flush()

    async with Client(drive_server) as client:
        result = await client.call_tool("describe_drive_account", {})

    health = _payload(result)
    assert health["banner"] == "revoked"
    assert health["account"]["email_address"] == "io@example.it"
    assert "ricolleg" in health["banner_text"]


async def test_no_drive_tool_answer_carries_a_credential(
    drive_server: Any, mcp_session: Session, drive_actor: Actor
) -> None:
    account = GoogleDriveAccount(
        user_id=drive_actor.id,
        google_sub="sub-drive-2",
        email_address="io@example.it",
        refresh_token_ciphertext=b"\x01\x02ciphertext",
        refresh_token_nonce=b"\x03\x04nonce",
        scopes_granted=["https://www.googleapis.com/auth/drive.readonly"],
        status="active",
    )
    mcp_session.add(account)
    mcp_session.flush()

    async with Client(drive_server) as client:
        result = await client.call_tool("describe_drive_account", {})

    rendered = json.dumps(_payload(result), default=str)
    for forbidden in ("ciphertext", "nonce", "refresh_token"):
        assert forbidden not in rendered
