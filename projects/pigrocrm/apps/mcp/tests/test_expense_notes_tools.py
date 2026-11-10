"""The expense-note tools over the MCP client (REB-1046): `submit_expense_note` and
`list_expense_notes` are every token's, for its owner's own staff member; `approve_expense_note`,
`reject_expense_note` and `reimburse_expense_note` exist only on a `full_access` token's surface and
never decide on the owner's own note. All five follow the `staff` and `purchases` modules."""

import datetime as dt
import json
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from mcp import Client
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor, Role
from pigrocrm.core.auth.models import User
from pigrocrm.core.config import Settings
from pigrocrm.core.fiscal.models import FiscalProfile
from pigrocrm.core.legal_entities.models import LegalEntity
from pigrocrm.core.staff.schemas import EmployeeCreate
from pigrocrm.core.staff.service import StaffService
from pigrocrm.core.storage import LocalFileStorage
from pigrocrm.core.timetracking.models import CostCategory
from pigrocrm_mcp.server import build_server

DEFAULT_TOOLS = {"submit_expense_note", "list_expense_notes"}
DECISIONS = {"approve_expense_note", "reject_expense_note", "reimburse_expense_note"}
TODAY = dt.date.today().isoformat()


def _server(
    session: Session,
    tmp_path: Path,
    *,
    user: User | None = None,
    full_access: bool = False,
    role: Role = "collaborator",
    **settings: Any,
) -> Any:
    owner = user.id if user is not None else uuid4()
    return build_server(
        lambda: session,
        lambda: Actor(id=owner, type="mcp", role=role, full_access=full_access),
        LocalFileStorage(tmp_path),
        Settings(_env_file=None, mcp_full_access=full_access, **settings),  # type: ignore[call-arg]
    )


def _payload(result: Any) -> dict[str, Any]:
    return result.structured_content or json.loads(result.content[0].text)


def _user(session: Session, role: str, name: str) -> User:
    user = User(email=f"{name}-{uuid4()}@example.test", password_hash="x", name=name, role=role)
    session.add(user)
    session.flush()
    return user


class World:
    def __init__(self, session: Session) -> None:
        entity = LegalEntity(name="notes mcp", legal_name="Notes MCP S.r.l.", country="IT")
        session.add(entity)
        session.flush()
        session.add(
            FiscalProfile(legal_entity_id=entity.id, pack_id="it-standard", regime_code="RF01")
        )
        category = CostCategory(name=f"Pranzi {uuid4()}", position=0, code=None)
        session.add(category)
        session.flush()
        self.category: UUID = category.id
        self.person = _user(session, "collaborator", "Carla")
        self.admin = _user(session, "admin", "Anna")
        StaffService(session).create_staff_member(
            EmployeeCreate(
                legal_entity_id=entity.id,
                name="Carla Rossi",
                start_date=dt.date(2025, 1, 1),
                gross_annual_pay=Decimal("30000.00"),
                monthly_payments=14,
                user_id=self.person.id,
            ),
            Actor(id=self.admin.id, type="user", role="admin"),
        )
        session.commit()

    def note(self, **overrides: Any) -> dict[str, Any]:
        body: dict[str, Any] = {
            "date": TODAY,
            "description": "Pranzo con il cliente",
            "category_id": str(self.category),
            "amount": "42.50",
        }
        body.update(overrides)
        return body


async def test_the_default_tools_are_every_tokens_and_the_decisions_are_full_access_only(
    mcp_session: Session, tmp_path: Path
) -> None:
    plain = {t.name for t in await _server(mcp_session, tmp_path).list_tools()}
    full = {t.name for t in await _server(mcp_session, tmp_path, full_access=True).list_tools()}
    assert plain >= DEFAULT_TOOLS and not DECISIONS & plain
    assert full >= DEFAULT_TOOLS | DECISIONS


async def test_the_tools_leave_with_either_module(mcp_session: Session, tmp_path: Path) -> None:
    for off in ({"module_staff": False}, {"module_purchases": False}):
        names = {
            t.name
            for t in await _server(mcp_session, tmp_path, full_access=True, **off).list_tools()
        }
        assert not (DEFAULT_TOOLS | DECISIONS) & names, off


async def test_an_agent_submits_for_its_owners_own_staff_member_and_lists_only_theirs(
    mcp_session: Session, tmp_path: Path
) -> None:
    world = World(mcp_session)
    async with Client(_server(mcp_session, tmp_path, user=world.person)) as client:
        submitted = await client.call_tool("submit_expense_note", world.note())
        assert not submitted.is_error, submitted.content
        note = _payload(submitted)
        assert note["status"] == "submitted" and note["staff_member_name"] == "Carla Rossi"
        assert note["submitted_by_type"] == "mcp"
        mileage = await client.call_tool(
            "submit_expense_note",
            {
                "date": TODAY,
                "description": "Visita",
                "category_id": str(world.category),
                "kind": "mileage",
                "kilometres": "100",
                "rate_per_km": "0.42",
                "route_from": "Milano",
                "route_to": "Torino",
            },
        )
        assert not mileage.is_error, mileage.content
        assert _payload(mileage)["amount"] == "42.00"
        listed = _payload(await client.call_tool("list_expense_notes", {"bucket": "to_approve"}))
        assert listed["total_count"] == 2
    # A token whose owner is no staff member has nobody to submit for.
    stranger = _user(mcp_session, "collaborator", "Estranea")
    async with Client(_server(mcp_session, tmp_path, user=stranger)) as client:
        refused = await client.call_tool("submit_expense_note", world.note())
        assert refused.is_error and "Non risulti tra le persone dell'azienda" in str(
            refused.content
        )


async def test_a_full_access_admin_decides_and_reimburses_but_never_on_its_own_note(
    mcp_session: Session, tmp_path: Path
) -> None:
    world = World(mcp_session)
    async with Client(_server(mcp_session, tmp_path, user=world.person)) as client:
        first = _payload(await client.call_tool("submit_expense_note", world.note()))
        second = _payload(await client.call_tool("submit_expense_note", world.note()))
    async with Client(
        _server(mcp_session, tmp_path, user=world.admin, full_access=True, role="admin")
    ) as client:
        approved = await client.call_tool("approve_expense_note", {"note_id": first["id"]})
        assert not approved.is_error, approved.content
        assert _payload(approved)["status"] == "approved"
        paid = await client.call_tool(
            "reimburse_expense_note",
            {"note_id": first["id"], "method": "transfer", "date": TODAY, "paid": True},
        )
        assert not paid.is_error, paid.content
        assert _payload(paid)["status"] == "reimbursed"
        again = await client.call_tool(
            "reimburse_expense_note", {"note_id": first["id"], "method": "payslip"}
        )
        assert again.is_error
        rejected = await client.call_tool(
            "reject_expense_note", {"note_id": second["id"], "reason": "Manca lo scontrino"}
        )
        assert _payload(rejected)["rejection_reason"] == "Manca lo scontrino"
        everything = _payload(await client.call_tool("list_expense_notes", {}))
        assert everything["total_count"] == 2
    # The administrator's token whose owner is the staff member: their own note is refused.
    world.person.role = "admin"
    mcp_session.flush()
    async with Client(
        _server(mcp_session, tmp_path, user=world.person, full_access=True, role="admin")
    ) as client:
        own = _payload(await client.call_tool("submit_expense_note", world.note()))
        refused = await client.call_tool("approve_expense_note", {"note_id": own["id"]})
        assert refused.is_error and "Non puoi approvare una tua nota" in str(refused.content)
