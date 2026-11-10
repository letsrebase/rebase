"""The staff tools over the MCP client (REB-977): `create_staff_member`, `update_staff_member`,
`get_staff_member` and `list_staff` exist only on a `full_access` token's surface and only while
the `staff` module is on, and even then an administrator's alone (spec 2026-11-09, staff,
payroll and expenses, § 4)."""

import json
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from mcp import Client
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor, Role
from pigrocrm.core.config import Settings
from pigrocrm.core.fiscal.models import FiscalProfile
from pigrocrm.core.legal_entities.models import LegalEntity
from pigrocrm.core.storage import LocalFileStorage
from pigrocrm_mcp.server import build_server

TOOLS = {
    "create_staff_member",
    "update_staff_member",
    "get_staff_member",
    "list_staff",
    "get_payroll_month",
}


def _server(
    session: Session,
    tmp_path: Path,
    *,
    full_access: bool = True,
    role: Role = "admin",
    **settings: Any,
) -> Any:
    return build_server(
        lambda: session,
        lambda: Actor(id=uuid4(), type="mcp", role=role, full_access=full_access),
        LocalFileStorage(tmp_path),
        Settings(_env_file=None, mcp_full_access=full_access, **settings),  # type: ignore[call-arg]
    )


def _payload(result: Any) -> dict[str, Any]:
    return result.structured_content or json.loads(result.content[0].text)


def _entity(session: Session, pack_id: str) -> UUID:
    entity = LegalEntity(name=f"staff {pack_id}", legal_name=f"Staff {pack_id}", country="IT")
    session.add(entity)
    session.flush()
    session.add(FiscalProfile(legal_entity_id=entity.id, pack_id=pack_id, regime_code="RF01"))
    session.commit()
    return entity.id


EMPLOYEE_DETAILS = {"gross_annual_pay": "30000.00", "monthly_payments": 14}


async def test_the_tools_are_listed_only_to_a_full_access_token(
    mcp_session: Session, tmp_path: Path
) -> None:
    full = {tool.name for tool in await _server(mcp_session, tmp_path).list_tools()}
    plain = {
        tool.name for tool in await _server(mcp_session, tmp_path, full_access=False).list_tools()
    }
    assert full >= TOOLS
    assert not TOOLS & plain


async def test_a_token_without_full_access_cannot_call_them_by_name(
    mcp_session: Session, tmp_path: Path
) -> None:
    entity = _entity(mcp_session, "it-standard")
    async with Client(_server(mcp_session, tmp_path, full_access=False)) as client:
        for name, arguments in (
            ("list_staff", {}),
            ("get_staff_member", {"staff_member_id": str(uuid4())}),
            (
                "create_staff_member",
                {
                    "legal_entity_id": str(entity),
                    "kind": "employee",
                    "name": "X",
                    "start_date": "2026-01-01",
                    "details": EMPLOYEE_DETAILS,
                },
            ),
        ):
            result = await client.call_tool(name, arguments)
            assert result.is_error, name
            assert "Unknown tool" in str(result.content), name


async def test_the_tools_leave_with_the_module_and_come_back_with_it(
    mcp_session: Session, tmp_path: Path
) -> None:
    off = {
        tool.name for tool in await _server(mcp_session, tmp_path, module_staff=False).list_tools()
    }
    on = {
        tool.name for tool in await _server(mcp_session, tmp_path, module_staff=True).list_tools()
    }
    assert not TOOLS & off
    assert on >= TOOLS


async def test_a_full_access_admin_creates_reads_updates_and_lists_staff(
    mcp_session: Session, tmp_path: Path
) -> None:
    entity = _entity(mcp_session, "it-standard")
    async with Client(_server(mcp_session, tmp_path)) as client:
        created = await client.call_tool(
            "create_staff_member",
            {
                "legal_entity_id": str(entity),
                "kind": "employee",
                "name": "Giulia Bianchi",
                "start_date": "2026-01-01",
                "details": {**EMPLOYEE_DETAILS, "contract_level": "3"},
            },
        )
        assert not created.is_error, created.content
        member = _payload(created)
        assert (member["kind"], member["gross_annual_pay"], member["contract_level"]) == (
            "employee",
            "30000.00",
            "3",
        )

        updated = _payload(
            await client.call_tool(
                "update_staff_member",
                {"staff_member_id": member["id"], "changes": {"gross_annual_pay": "31000.00"}},
            )
        )
        assert updated["gross_annual_pay"] == "31000.00"
        assert "gross_annual_pay" in updated["changed"]

        empty = await client.call_tool(
            "update_staff_member", {"staff_member_id": member["id"], "changes": {}}
        )
        assert empty.is_error

        read = _payload(
            await client.call_tool("get_staff_member", {"staff_member_id": member["id"]})
        )
        assert read["gross_annual_pay"] == "31000.00"
        listed = _payload(await client.call_tool("list_staff", {"legal_entity_id": str(entity)}))
        assert [m["id"] for m in listed["items"]] == [member["id"]]
        only_directors = _payload(await client.call_tool("list_staff", {"kind": "director"}))
        assert only_directors["items"] == []


async def test_a_director_on_a_legal_entity_that_is_not_a_company_is_refused_in_italian(
    mcp_session: Session, tmp_path: Path
) -> None:
    entity = _entity(mcp_session, "it-standard")
    async with Client(_server(mcp_session, tmp_path)) as client:
        refused = await client.call_tool(
            "create_staff_member",
            {
                "legal_entity_id": str(entity),
                "kind": "director",
                "name": "Marco Rossi",
                "start_date": "2026-01-01",
                "details": {"annual_fee": "24000.00"},
            },
        )
        assert refused.is_error
        assert "società" in str(refused.content)
        assert _payload(await client.call_tool("list_staff", {}))["items"] == []


async def test_a_full_access_token_whose_owner_is_not_an_admin_is_refused(
    mcp_session: Session, tmp_path: Path
) -> None:
    """`full_access` is the credential's half; the owner's role is the other, and the
    collaborator's token reads no pay however it was minted."""
    entity = _entity(mcp_session, "it-standard")
    async with Client(_server(mcp_session, tmp_path, role="collaborator")) as client:
        for name, arguments in (
            ("list_staff", {}),
            ("get_staff_member", {"staff_member_id": str(uuid4())}),
            (
                "create_staff_member",
                {
                    "legal_entity_id": str(entity),
                    "kind": "employee",
                    "name": "X",
                    "start_date": "2026-01-01",
                    "details": EMPLOYEE_DETAILS,
                },
            ),
            ("update_staff_member", {"staff_member_id": str(uuid4()), "changes": {"name": "Y"}}),
        ):
            result = await client.call_tool(name, arguments)
            assert result.is_error, name
            assert "Unknown tool" not in str(result.content), name


async def test_a_full_access_admin_reads_a_months_estimate_and_a_collaborator_cannot(
    mcp_session: Session, tmp_path: Path
) -> None:
    """REB-1008: `get_payroll_month` is the cost and cash estimate; without an accepted payslip
    there is no net pay and no F24 line, and the owner's role is asked as for every pay read."""
    entity = _entity(mcp_session, "it-standard")
    async with Client(_server(mcp_session, tmp_path)) as client:
        created = await client.call_tool(
            "create_staff_member",
            {
                "legal_entity_id": str(entity),
                "kind": "employee",
                "name": "Giulia Bianchi",
                "start_date": "2026-01-01",
                "details": {**EMPLOYEE_DETAILS, "employer_inps_rate": "30.00"},
            },
        )
        assert not created.is_error, created.content
        month = await client.call_tool(
            "get_payroll_month", {"legal_entity_id": str(entity), "month": "2026-03-10"}
        )
        assert not month.is_error, month.content
        row = _payload(month)["members"][0]
        assert row["cost"]["gross"] == "2500.00" and row["cost"]["employer_inps"] == "750.00"
        assert row["cash"] == []
        assert "accepted_payslip" in row["not_estimated"]
    async with Client(_server(mcp_session, tmp_path, role="collaborator")) as client:
        refused = await client.call_tool(
            "get_payroll_month", {"legal_entity_id": str(entity), "month": "2026-03-10"}
        )
        assert refused.is_error
        assert "Unknown tool" not in str(refused.content)
