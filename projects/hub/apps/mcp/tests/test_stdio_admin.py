"""Over stdio the token is resolved again as the process lives (REB-803): a revoked or
expired token and a demoted owner stop being the admin within the cache's minute, and
the tools say so in Italian instead of writing as somebody who is no longer one."""

from collections.abc import Iterator
from contextlib import closing
from datetime import UTC, datetime, timedelta

import pytest
from mcp import Client
from mcp.server.mcpserver.exceptions import ToolError
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session, sessionmaker

from rebase_core.admin_tokens import AdminTokenService
from rebase_core.db import session_factory
from rebase_core.errors import ValidationFailed
from rebase_core.models import User
from rebase_mcp.actor import ADMIN_CACHE_SECONDS, TOKEN_NO_LONGER_VALID, CachedAdmin
from rebase_mcp.server import build_server


class Clock:
    """A clock a test moves by hand."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def token(mcp_engine: Engine) -> Iterator[tuple[str, User]]:
    with session_factory(mcp_engine)() as session:
        user = User(email="ivan@rebase.it", first_name="Ivan", last_name="Fiore", role="admin")
        session.add(user)
        session.commit()
        _, raw = AdminTokenService(session).create(user.id, "Claude Code")
        session.refresh(user)
        session.expunge(user)
    try:
        yield raw, user
    finally:
        with session_factory(mcp_engine)() as session:
            for table in ("admin_tokens", "users"):
                session.execute(text(f"DELETE FROM {table}"))
            session.commit()


def _provider(factory: sessionmaker[Session], raw: str | None, clock: Clock) -> CachedAdmin:
    return CachedAdmin(lambda: closing(factory()), raw, clock=clock)


def test_the_admin_is_resolved_once_a_minute_not_on_every_call(
    factory: sessionmaker[Session], token: tuple[str, User]
) -> None:
    raw, owner = token
    clock = Clock()
    admin = _provider(factory, raw, clock)
    assert admin.refresh().id == owner.id
    with factory() as session:
        session.execute(text("UPDATE users SET first_name = 'Ivano'"))
        session.commit()
    assert admin().first_name == "Ivan"  # inside the minute: the cached one
    clock.now += ADMIN_CACHE_SECONDS + 1
    assert admin().first_name == "Ivano"  # past it: asked again


@pytest.mark.parametrize("change", ["revoke", "demote", "deactivate", "expire"])
def test_a_token_that_stops_being_valid_stops_the_admin_within_the_minute(
    factory: sessionmaker[Session], token: tuple[str, User], change: str
) -> None:
    raw, owner = token
    clock = Clock()
    admin = _provider(factory, raw, clock)
    admin.refresh()
    with factory() as session:
        if change == "revoke":
            [record] = AdminTokenService(session).list(owner.id)
            AdminTokenService(session).revoke(owner.id, record.id)
        elif change == "demote":
            session.execute(text("UPDATE users SET role = 'member'"))
        elif change == "deactivate":
            session.execute(text("UPDATE users SET active = false"))
        else:
            session.execute(
                text("UPDATE admin_tokens SET expires_at = :past"),
                {"past": datetime.now(UTC) - timedelta(seconds=1)},
            )
        session.commit()
    assert admin().id == owner.id  # the minute has not passed: the one cached
    clock.now += ADMIN_CACHE_SECONDS + 1
    with pytest.raises(ToolError) as refused:
        admin()
    assert str(refused.value) == TOKEN_NO_LONGER_VALID


def test_a_missing_token_does_not_start_a_process(factory: sessionmaker[Session]) -> None:
    with pytest.raises(ValidationFailed):
        _provider(factory, None, Clock()).refresh()


async def test_a_revoked_token_stops_the_writes_of_a_running_server(
    factory: sessionmaker[Session], token: tuple[str, User]
) -> None:
    """End to end: the server keeps running, the token is revoked, and within the minute
    the next write is the sentence and nothing is written."""
    raw, owner = token
    clock = Clock()
    admin = _provider(factory, raw, clock)
    admin.refresh()
    with factory() as session:
        from rebase_core.service import SignupService
        from rebase_core.signup_schemas import SignupCreate

        signup = SignupService(session).subscribe(
            SignupCreate(email="ada@studio.it", first_name="Ada", last_name="Lovelace")
        )
        signup_id = str(signup.id)
    async with Client(build_server(factory, admin)) as client:
        assert not (await client.call_tool("list_talents", {})).is_error
        with factory() as session:
            [record] = AdminTokenService(session).list(owner.id)
            AdminTokenService(session).revoke(owner.id, record.id)
        clock.now += ADMIN_CACHE_SECONDS + 1
        refused = await client.call_tool(
            "create_freelancer_from_signup",
            {
                "signup_id": signup_id,
                "first_name": "Ada",
                "last_name": "Lovelace",
                "sources": ["https://www.linkedin.com/in/ada"],
            },
        )
    assert refused.is_error
    assert TOKEN_NO_LONGER_VALID in refused.content[0].text  # type: ignore[union-attr]
    with factory() as session:
        assert session.execute(text("SELECT count(*) FROM freelancers")).scalar_one() == 0
        session.execute(text("DELETE FROM signups"))
        session.commit()
