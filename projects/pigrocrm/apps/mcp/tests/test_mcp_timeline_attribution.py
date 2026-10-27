"""REB-781: a change made over MCP names the member whose token made it, and says so.

`packages/core/tests/test_activity_coverage.py` sweeps the services for writers that do
not record and proves what a row holds. This is the half only the MCP package can show:
that the actor an agent's tool call carries is the token owner's (`mcp` and their id, not
an anonymous agent), that every write tool of the sample leaves rows with exactly that
attribution and no other, and that `get_timeline` serves the member's name and the channel
side by side with a change a colleague made from the browser.

And the one structural property the sweep cannot see: no tool and no REST route writes
to the database itself. A write that skipped the services would skip the record with them,
so the adapters are held to calling the services and nothing else.
"""

import ast
import json
from pathlib import Path
from typing import Any

import pytest
from mcp import Client
from sqlalchemy import select
from sqlalchemy.orm import Session

from pigrocrm.core.activities.models import Activity
from pigrocrm.core.actor import Actor
from pigrocrm.core.auth.models import User
from pigrocrm.core.config import Settings
from pigrocrm.core.customers.schemas import CustomerCreate
from pigrocrm.core.customers.service import CustomerService
from pigrocrm.core.pipeline.service import PipelineService
from pigrocrm.core.storage import LocalFileStorage
from pigrocrm_mcp.server import build_server

PROJECT = Path(__file__).resolve().parents[3]
ADAPTERS = {
    "tools": PROJECT / "apps" / "mcp" / "src" / "pigrocrm_mcp" / "tools",
    "resources": PROJECT / "apps" / "mcp" / "src" / "pigrocrm_mcp" / "resources",
    "routers": PROJECT / "apps" / "api" / "src" / "pigrocrm_api" / "routers",
}


def _payload(result: Any) -> dict:
    return result.structured_content or json.loads(result.content[0].text)


def _member(session: Session, nome: str, key: str) -> User:
    user = User(email=f"{key}@example.test", password_hash="x", nome=nome, ruolo="admin")
    session.add(user)
    session.flush()
    return user


@pytest.fixture
def token_owner(mcp_session: Session) -> User:
    return _member(mcp_session, "Marco Verdi", "marco")


@pytest.fixture
def owner_server(mcp_session: Session, token_owner: User, tmp_path: Path):
    """The server as `PatService.resolve` hands it its actor: `mcp`, with the owner's id."""
    agent = Actor(id=token_owner.id, type="mcp", role="admin")
    return build_server(
        lambda: mcp_session,
        lambda: agent,
        LocalFileStorage(tmp_path),
        Settings(_env_file=None),  # type: ignore[call-arg]
    )


async def test_every_row_an_agent_writes_carries_the_token_owner_and_the_mcp_channel(
    owner_server, mcp_session: Session, token_owner: User
) -> None:
    PipelineService(mcp_session).seed_defaults(Actor.system())
    stages = None
    async with Client(owner_server) as client:
        stages = _payload(await client.call_tool("list_pipeline_stages", {}))["stages"]
        customer = _payload(await client.call_tool("create_customer", {"ragione_sociale": "ACME"}))
        await client.call_tool(
            "update_customer", {"customer_id": customer["id"], "changes": {"note": "una nota"}}
        )
        person = _payload(
            await client.call_tool("create_person", {"nome": "Anna", "customer_id": customer["id"]})
        )
        await client.call_tool(
            "update_person", {"person_id": person["id"], "changes": {"note": "una nota"}}
        )
        deal = _payload(
            await client.call_tool(
                "create_deal", {"nome": "Progetto", "customer_id": customer["id"]}
            )
        )
        await client.call_tool("move_deal", {"deal_id": deal["id"], "stage_id": stages[-1]["id"]})
        task = _payload(await client.call_tool("create_attivita", {"titolo": "Chiamare"}))
        await client.call_tool("complete_attivita", {"attivita_id": task["id"]})
        await client.call_tool("archive_person", {"person_id": person["id"]})
        await client.call_tool("restore_person", {"person_id": person["id"]})

    rows = list(
        mcp_session.execute(select(Activity).where(Activity.actor_type != "system")).scalars()
    )
    # Ten calls, and every one of them wrote at least one row: a tool that wrote none would
    # be a change nobody is named for.
    assert len(rows) >= 10
    assert {(row.actor_id, row.actor_type) for row in rows} == {(token_owner.id, "mcp")}


async def test_get_timeline_serves_the_name_and_the_channel_of_a_web_and_an_mcp_change(
    owner_server, mcp_session: Session, token_owner: User
) -> None:
    web_member = _member(mcp_session, "Giulia Bianchi", "giulia")
    web = Actor(id=web_member.id, type="user", role="admin")
    created = CustomerService(mcp_session).create(CustomerCreate(ragione_sociale="ACME"), web)

    async with Client(owner_server) as client:
        await client.call_tool(
            "update_customer", {"customer_id": str(created.id), "changes": {"note": "una nota"}}
        )
        entries = _payload(
            await client.call_tool(
                "get_timeline", {"entity_type": "customer", "entity_id": str(created.id)}
            )
        )["entries"]

    by_kind = {entry["kind"]: entry for entry in entries}
    assert (by_kind["created"]["actor_name"], by_kind["created"]["actor_type"]) == (
        "Giulia Bianchi",
        "user",
    )
    assert (by_kind["updated"]["actor_name"], by_kind["updated"]["actor_type"]) == (
        "Marco Verdi",
        "mcp",
    )
    assert all(entry["occurred_at"] for entry in entries)


# --- no write goes around the services ---------------------------------------------

# Calls on a session that change rows. A tool or a route that makes one has written
# without a service, which is a write nothing in `core` knows to record.
_SESSION_WRITES = {"add", "add_all", "delete", "commit", "flush", "merge", "execute"}

# Reviewed exceptions, each a file and the reason. `tenants.py` signs a space up: it
# settles the new space's first admin (`email_verificata_il`) before any member, and so
# any actor, exists in it.
_ALLOWED = {"routers/tenants.py": "provisioning a space: no member exists in it yet"}


def _session_writes(directory: Path) -> list[str]:
    found: list[str] = []
    for path in sorted(directory.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in _SESSION_WRITES
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id in {"session", "space", "root", "registry"}
            ):
                found.append(f"{directory.name}/{path.name}:{node.lineno} .{node.func.attr}()")
    return found


@pytest.mark.parametrize("adapter", sorted(ADAPTERS))
def test_no_adapter_writes_to_the_database_without_a_service(adapter: str) -> None:
    directory = ADAPTERS[adapter]
    assert directory.is_dir(), f"{directory} moved: this guard would check nothing"
    offenders = [line for line in _session_writes(directory) if line.split(":")[0] not in _ALLOWED]
    assert offenders == [], f"a write that skips the services also skips the record: {offenders}"
