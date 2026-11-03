"""REB-809: `list_members` is how an agent turns a name into the id `owner_id` and
`assigned_to` take. Same people as the web pickers, with the role for everybody and the
email for an administrator only."""

from pathlib import Path
from typing import Any
from uuid import uuid4

from mcp import Client
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.auth.models import User
from pigrocrm.core.auth.service import UserService
from pigrocrm.core.config import Settings
from pigrocrm.core.storage import LocalFileStorage
from pigrocrm_mcp.server import build_server


def _user(session: Session, name: str, *, role: str = "collaborator", active: bool = True) -> User:
    user = User(
        email=f"{name.lower()}-{uuid4()}@example.test",
        password_hash="x",
        name=name,
        role=role,
        active=active,
    )
    session.add(user)
    session.flush()
    return user


def _server_as(session: Session, tmp_path: Path, role: str) -> Any:
    return build_server(
        lambda: session,
        lambda: Actor(id=None, type="mcp", role=role),  # type: ignore[arg-type]
        LocalFileStorage(tmp_path),
        Settings(_env_file=None),  # type: ignore[call-arg]
    )


async def test_an_admin_lists_the_active_members_with_role_and_email(
    server, mcp_session: Session
) -> None:
    ada = _user(mcp_session, "Ada", role="admin")
    gone = _user(mcp_session, "Andata", active=False)

    async with Client(server) as client:
        result = await client.call_tool("list_members", {})

    members = {m["id"]: m for m in result.structured_content["result"]}
    assert str(gone.id) not in members
    assert members[str(ada.id)] == {
        "id": str(ada.id),
        "name": "Ada",
        "role": "admin",
        "email": ada.email,
    }


async def test_anybody_else_gets_the_role_and_no_email(
    mcp_session: Session, tmp_path: Path
) -> None:
    ada = _user(mcp_session, "Ada", role="admin")

    for role in ("collaborator", "readonly"):
        async with Client(_server_as(mcp_session, tmp_path, role)) as client:
            result = await client.call_tool("list_members", {})
        members = {m["id"]: m for m in result.structured_content["result"]}
        assert members[str(ada.id)]["role"] == "admin"
        assert members[str(ada.id)]["email"] is None
        assert all(m["email"] is None for m in members.values())


async def test_it_lists_the_people_the_web_pickers_list(server, mcp_session: Session) -> None:
    _user(mcp_session, "Ada", role="admin")
    _user(mcp_session, "Bruno")
    _user(mcp_session, "Andata", active=False)

    members = UserService(mcp_session).members()
    # What the REST picker answers stays id and name only: the role and the email are the
    # MCP's `directory`, not something `members` quietly grew.
    assert all(set(m.model_dump()) == {"id", "name"} for m in members)
    async with Client(server) as client:
        result = await client.call_tool("list_members", {})

    listed = result.structured_content["result"]
    assert [(m["id"], m["name"]) for m in listed] == [(str(m.id), m.name) for m in members]
    assert [m["name"] for m in listed] == sorted(m["name"] for m in listed)


async def test_an_agent_resolves_to_name_to_an_id_and_assigns_an_task_with_it(
    server, mcp_session: Session
) -> None:
    bruno = _user(mcp_session, "Bruno")

    async with Client(server) as client:
        members = (await client.call_tool("list_members", {})).structured_content["result"]
        found = next(m["id"] for m in members if m["name"] == "Bruno")
        task = await client.call_tool(
            "create_task", {"title": "Chiamare il cliente", "assigned_to": found}
        )
        reassigned = await client.call_tool(
            "update_task",
            {"task_id": task.structured_content["id"], "changes": {"assigned_to": found}},
        )

    assert found == str(bruno.id)
    assert task.structured_content["assigned_to"] == found
    assert reassigned.structured_content["changed"] == []


async def test_a_scoped_admin_sees_the_emails_like_on_the_team_page(
    mcp_session: Session, tmp_path: Path
) -> None:
    ada = _user(mcp_session, "Ada", role="admin")
    scoped = build_server(
        lambda: mcp_session,
        lambda: Actor(id=None, type="mcp", role="admin", legal_entities=(uuid4(),)),
        LocalFileStorage(tmp_path),
        Settings(_env_file=None),  # type: ignore[call-arg]
    )

    async with Client(scoped) as client:
        result = await client.call_tool("list_members", {})

    members = {m["id"]: m for m in result.structured_content["result"]}
    assert members[str(ada.id)]["email"] == ada.email
