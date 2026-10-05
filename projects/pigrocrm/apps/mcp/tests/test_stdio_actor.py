"""The stdio server's actor is the token's owner as they are now, not as they were
when the process started (REB-634, CodeRabbit and Greptile on PR #513)."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any, NamedTuple

import pytest
from mcp import Client
from sqlalchemy import Engine, delete, select

from pigrocrm.core.activities.models import Activity
from pigrocrm.core.actor import Actor
from pigrocrm.core.auth.models import User
from pigrocrm.core.auth.pat_models import PersonalAccessToken
from pigrocrm.core.auth.pat_service import PatService
from pigrocrm.core.auth.schemas import UserCreate, UserUpdate
from pigrocrm.core.auth.service import UserService
from pigrocrm.core.config import Settings
from pigrocrm.core.db import session_factory
from pigrocrm.core.storage import LocalFileStorage
from pigrocrm_mcp.context import ScopedSessionProvider, TokenActorProvider
from pigrocrm_mcp.server import build_server

EMAIL = "stdio-actor@pigro.it"


class Owner(NamedTuple):
    engine: Engine
    user_id: Any
    raw: str


@pytest.fixture
def owner(mcp_engine: Engine) -> Iterator[Owner]:
    factory = session_factory(mcp_engine)
    with factory() as session:
        user = UserService(session).create(
            UserCreate(email=EMAIL, password="supersegreta1", nome="Stdio", ruolo="collaboratore"),
            Actor.system(),
        )
        _, raw = PatService(session, settings=Settings(_env_file=None)).create(  # type: ignore[call-arg]
            "prova", Actor(id=user.id, type="user", role="collaboratore")
        )
        session.commit()
    try:
        yield Owner(mcp_engine, user.id, raw)
    finally:
        with factory() as session:
            session.execute(
                delete(PersonalAccessToken).where(PersonalAccessToken.user_id == user.id)
            )
            session.execute(delete(Activity).where(Activity.entity_id == user.id))
            session.execute(delete(Activity).where(Activity.actor_id == user.id))
            session.execute(delete(User).where(User.id == user.id))
            session.commit()


def test_the_actor_is_resolved_inside_a_scope_and_outside_one(owner: Owner) -> None:
    sessions = ScopedSessionProvider(session_factory(owner.engine))
    provider = TokenActorProvider(sessions, owner.raw)
    # Outside any tool's scope, where the analytics' identity callback runs: a session
    # of its own, and an answer rather than a refusal.
    outside = provider()
    assert outside.id == owner.user_id and outside.role == "collaboratore"
    with sessions.scope():
        inside = provider()
    assert inside.id == owner.user_id


def test_a_role_changed_meanwhile_reaches_the_next_call(owner: Owner) -> None:
    sessions = ScopedSessionProvider(session_factory(owner.engine))
    provider = TokenActorProvider(sessions, owner.raw)
    assert provider().role == "collaboratore"
    with session_factory(owner.engine)() as session:
        UserService(session).update(owner.user_id, UserUpdate(ruolo="readonly"), Actor.system())
    assert provider().role == "readonly"


async def test_a_token_refused_meanwhile_is_the_tool_s_answer_not_a_crash(
    owner: Owner, tmp_path: Path
) -> None:
    sessions = ScopedSessionProvider(session_factory(owner.engine))
    server = build_server(
        sessions,
        TokenActorProvider(sessions, owner.raw),
        LocalFileStorage(tmp_path),
        Settings(_env_file=None),  # type: ignore[call-arg]
    )
    async with Client(server) as client:
        fine = await client.call_tool("describe_schema", {"entity_type": "customer"})
        assert not fine.is_error
        with session_factory(owner.engine)() as session:
            row = session.execute(
                select(PersonalAccessToken).where(PersonalAccessToken.user_id == owner.user_id)
            ).scalar_one()
            session.delete(row)
            session.commit()
        refused = await client.call_tool("describe_schema", {"entity_type": "customer"})
    assert refused.is_error
    text = json.dumps([getattr(c, "text", "") for c in refused.content])
    assert "token" in text.lower()
