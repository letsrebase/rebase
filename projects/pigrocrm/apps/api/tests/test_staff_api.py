"""Staff and directors over REST (REB-977): an administrator keeps them, and a collaborator, a
readonly member and a token without `full_access` are refused every pay read and write."""

import datetime as _dt
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.fiscal.models import FiscalProfile
from pigrocrm.core.legal_entities.models import LegalEntity
from pigrocrm.core.legal_entities.repository import LegalEntityRepository
from pigrocrm.core.staff.schemas import EmployeeCreate
from pigrocrm.core.staff.service import StaffService
from pigrocrm_api.deps import invalidate_space_settings


def _entity(session: Session, pack_id: str, tag: str) -> str:
    entity = LegalEntity(name=f"staff {tag}", legal_name=f"Staff {tag}", country="IT")
    session.add(entity)
    session.flush()
    session.add(FiscalProfile(legal_entity_id=entity.id, pack_id=pack_id, regime_code="RF01"))
    session.commit()
    return str(entity.id)


def _employee(entity: str, **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "kind": "employee",
        "legal_entity_id": entity,
        "name": "Giulia Bianchi",
        "start_date": "2026-01-01",
        "gross_annual_pay": "30000.00",
        "monthly_payments": 14,
        "part_time_percent": "80",
    }
    body.update(overrides)
    return body


def _director(entity: str) -> dict[str, Any]:
    return {
        "kind": "director",
        "legal_entity_id": entity,
        "name": "Marco Rossi",
        "start_date": "2026-01-01",
        "annual_fee": "24000.00",
        "accrues_tfm": True,
        "tfm_annual_amount": "2000.00",
    }


def _every_route(entity: str, member: str) -> list[tuple[str, str, dict[str, Any] | None]]:
    """Every staff route, read and write, as (method, path, body)."""
    return [
        ("POST", "/api/staff", _employee(entity, name="Nuovo")),
        ("GET", "/api/staff", None),
        ("GET", f"/api/staff/{member}", None),
        ("PATCH", f"/api/staff/{member}", {"gross_annual_pay": "1.00"}),
        ("GET", f"/api/staff/pay-days/{entity}", None),
        ("PUT", f"/api/staff/pay-days/{entity}", {"pay_day": 27, "pay_month_offset": 0}),
        ("GET", f"/api/staff/payroll/{entity}?month=2026-03-01", None),
        (
            "POST",
            f"/api/staff/{member}/payslips",
            {"period": "2026-03-01", "gross": "2000.00", "net": "1500.00", "withholding": "300.00"},
        ),
    ]


def test_an_administrator_keeps_an_employee_and_a_director(
    logged_in: TestClient, api_session: Session
) -> None:
    standard = _entity(api_session, "it-standard", "std")
    company = _entity(api_session, "it-company", "srl")

    created = logged_in.post("/api/staff", json=_employee(standard))
    assert created.status_code == 201, created.text
    body = created.json()
    assert (body["kind"], body["gross_annual_pay"], body["part_time_percent"]) == (
        "employee",
        "30000.00",
        "80.00",
    )
    director = logged_in.post("/api/staff", json=_director(company))
    assert director.status_code == 201, director.text
    assert director.json()["tfm_annual_amount"] == "2000.00"

    assert logged_in.get(f"/api/staff/{body['id']}").json()["name"] == "Giulia Bianchi"
    patched = logged_in.patch(f"/api/staff/{body['id']}", json={"contract_level": "4"})
    assert patched.status_code == 200, patched.text
    assert patched.json()["contract_level"] == "4"
    listed = logged_in.get("/api/staff", params={"legal_entity_id": standard}).json()
    assert [m["id"] for m in listed["items"]] == [body["id"]]

    day = logged_in.put(
        f"/api/staff/pay-days/{standard}", json={"pay_day": 27, "pay_month_offset": 0}
    )
    assert day.status_code == 200, day.text
    read = logged_in.get(f"/api/staff/{body['id']}").json()
    assert read["effective_pay_day"] == {
        "pay_day": 27,
        "pay_month_offset": 0,
        "source": "legal_entity",
    }


def test_a_director_on_another_pack_is_refused_in_italian_and_nothing_is_written(
    logged_in: TestClient, api_session: Session
) -> None:
    for pack in ("it-standard", "it-flat-rate", "non-it"):
        entity = _entity(api_session, pack, pack)
        refused = logged_in.post("/api/staff", json=_director(entity))
        assert refused.status_code == 422, refused.text
        assert "amministratore con compenso" in refused.json()["detail"]
    assert logged_in.get("/api/staff").json()["items"] == []


def test_an_employee_on_a_legal_entity_abroad_is_refused(
    logged_in: TestClient, api_session: Session
) -> None:
    entity = _entity(api_session, "non-it", "abroad")
    refused = logged_in.post("/api/staff", json=_employee(entity))
    assert refused.status_code == 422, refused.text
    assert "dipendenti" in refused.json()["detail"]


def test_a_bad_body_is_a_422_before_the_service(
    logged_in: TestClient, api_session: Session
) -> None:
    entity = _entity(api_session, "it-standard", "std")
    assert (
        logged_in.post("/api/staff", json=_employee(entity, monthly_payments=12)).status_code == 422
    )
    assert (
        logged_in.post("/api/staff", json={**_employee(entity), "annual_fee": "1"}).status_code
        == 422
    )
    assert (
        logged_in.put(
            f"/api/staff/pay-days/{entity}", json={"pay_day": 31, "pay_month_offset": 0}
        ).status_code
        == 422
    )
    assert logged_in.get(f"/api/staff/{uuid4()}").status_code == 404


def _member(session: Session, entity: str) -> str:
    """A member written through the service: the role under test cannot create one."""
    made = StaffService(session).create_staff_member(
        EmployeeCreate(**{k: v for k, v in _employee(entity).items() if k != "kind"}),
        Actor.system(),
    )
    return str(made.id)


def _assert_refused_everywhere(client: TestClient, entity: str, member: str, role: str) -> None:
    for method, path, body in _every_route(entity, member):
        response = client.request(method, path, json=body)
        assert response.status_code == 403, (role, method, path, response.text)
        assert response.json()["code"] == "permission_denied", (role, method, path)


def test_a_collaborator_is_refused_every_staff_route(
    collaborator_client: TestClient, api_session: Session
) -> None:
    """The card's «done when» over HTTP."""
    entity = _entity(api_session, "it-standard", "std")
    member = _member(api_session, entity)
    _assert_refused_everywhere(collaborator_client, entity, member, "collaborator")
    assert StaffService(api_session).get_staff_member(
        UUID(member), Actor.system()
    ).gross_annual_pay == Decimal("30000.00")


def test_a_readonly_member_is_refused_every_staff_route(
    readonly_client: TestClient, api_session: Session
) -> None:
    entity = _entity(api_session, "it-standard", "std")
    member = _member(api_session, entity)
    _assert_refused_everywhere(readonly_client, entity, member, "readonly")


@pytest.fixture
def open_space(logged_in: TestClient) -> TestClient:
    invalidate_space_settings(None)
    response = logged_in.put("/api/settings/space", json={"mcp_full_access": True})
    assert response.status_code == 200, response.text
    return logged_in


def _bearer(client: TestClient, **body: Any) -> TestClient:
    made = client.post("/api/tokens", json={"name": "agente", **body})
    assert made.status_code == 201, made.text
    agent = TestClient(client.app, base_url="https://testserver")
    agent.headers["Authorization"] = f"Bearer {made.json()['token']}"
    return agent


def test_a_token_without_full_access_is_refused_every_staff_route(
    open_space: TestClient, api_session: Session
) -> None:
    entity = _entity(api_session, "it-standard", "std")
    member = open_space.post("/api/staff", json=_employee(entity)).json()["id"]
    plain = _bearer(open_space)
    for method, path, body in _every_route(entity, member):
        response = plain.request(method, path, json=body)
        assert response.status_code == 403, (method, path, response.text)
        assert response.json()["code"] == "agent_forbidden", (method, path)
    assert open_space.get(f"/api/staff/{member}").json()["gross_annual_pay"] == "30000.00"


def test_a_full_access_token_keeps_staff_but_never_sets_the_legal_entitys_pay_day(
    open_space: TestClient, api_session: Session
) -> None:
    entity = _entity(api_session, "it-standard", "std")
    full = _bearer(open_space, full_access=True)
    created = full.post("/api/staff", json=_employee(entity))
    assert created.status_code == 201, created.text
    member = created.json()["id"]
    assert full.get(f"/api/staff/{member}").status_code == 200
    assert full.patch(f"/api/staff/{member}", json={"contract_level": "5"}).status_code == 200
    assert full.get(f"/api/staff/pay-days/{entity}").status_code == 200
    refused = full.put(f"/api/staff/pay-days/{entity}", json={"pay_day": 27, "pay_month_offset": 0})
    assert refused.status_code == 403, refused.text
    assert refused.json()["code"] == "agent_forbidden"


def test_a_space_with_the_staff_module_off_answers_404_and_keeps_the_data(
    logged_in: TestClient, api_session: Session
) -> None:
    entity = _entity(api_session, "it-standard", "std")
    member = logged_in.post("/api/staff", json=_employee(entity)).json()["id"]

    off = logged_in.put("/api/settings/modules", json={"off": ["staff"], "confirm_off": ["staff"]})
    assert off.status_code == 200, off.text
    for method, path, body in _every_route(entity, member):
        response = logged_in.request(method, path, json=body)
        assert response.status_code == 404, (method, path, response.text)
        assert response.json()["code"] == "module_off"

    assert logged_in.put("/api/settings/modules", json={"on": ["staff"]}).status_code == 200
    assert logged_in.get(f"/api/staff/{member}").json()["name"] == "Giulia Bianchi"


def test_the_default_legal_entity_without_a_fiscal_profile_is_refused(
    logged_in: TestClient, api_session: Session
) -> None:
    default = LegalEntityRepository(api_session).default()
    assert default is not None
    refused = logged_in.post("/api/staff", json=_employee(str(default.id)))
    assert refused.status_code == 422, refused.text
    assert "profilo fiscale" in refused.json()["detail"]
    assert isinstance(UUID(str(default.id)), UUID)


def test_an_administrator_records_a_payslip_and_reads_the_months_estimate(
    logged_in: TestClient, api_session: Session
) -> None:
    """REB-1008 over HTTP: cost and cash apart, with their dates; no net pay before a payslip."""
    entity = _entity(api_session, "it-standard", "std")
    member = logged_in.post(
        "/api/staff",
        json=_employee(
            entity,
            part_time_percent="100",
            employer_inps_rate="30.00",
            employee_inps_rate="9.19",
            employer_inail_rate="1.00",
            thirteenth_month=12,
            fourteenth_month=6,
        ),
    ).json()["id"]
    assert (
        logged_in.put(
            f"/api/staff/pay-days/{entity}", json={"pay_day": 27, "pay_month_offset": 0}
        ).status_code
        == 200
    )
    before = logged_in.get(f"/api/staff/payroll/{entity}", params={"month": "2026-03-10"})
    assert before.status_code == 200, before.text
    row = before.json()["members"][0]
    assert row["cost"]["total"] == "3275.00"
    assert row["cash"] == [] and "accepted_payslip" in row["not_estimated"]

    recorded = logged_in.post(
        f"/api/staff/{member}/payslips",
        json={
            "period": "2026-01-15",
            "gross": "2142.86",
            "net": "1607.15",
            "withholding": "321.43",
        },
    )
    assert recorded.status_code == 201, recorded.text
    assert recorded.json()["period"] == "2026-01-01"
    duplicate = logged_in.post(
        f"/api/staff/{member}/payslips",
        json={"period": "2026-01-01", "gross": "1.00", "net": "1.00", "withholding": "0.00"},
    )
    assert duplicate.status_code == 409, duplicate.text
    bad = logged_in.post(
        f"/api/staff/{member}/payslips",
        json={"period": "2026-02-01", "gross": "100.00", "net": "90.00", "withholding": "20.00"},
    )
    assert bad.status_code == 422, bad.text

    after = logged_in.get(f"/api/staff/payroll/{entity}", params={"month": "2026-03-01"}).json()
    cash = {item["kind"]: item for item in after["members"][0]["cash"]}
    assert cash["net_pay"]["due_date"] == "2026-03-27"
    assert cash["withholding"]["due_date"] == "2026-04-16"
    assert after["members"][0]["not_estimated"] == []


def test_a_collaborator_reads_the_stipendi_aggregate_and_not_the_payroll_items(
    collaborator_client: TestClient, api_session: Session
) -> None:
    from pigrocrm.core.db import today_local
    from pigrocrm.core.due_items.schemas import DueItemListQuery
    from pigrocrm.core.due_items.service import DueItemService
    from pigrocrm.core.staff.schemas import PayDayWrite, PayslipFigures

    entity = _entity(api_session, "it-standard", "std")
    staff = StaffService(api_session)
    system = Actor.system()
    staff.set_payroll_pay_day(UUID(entity), PayDayWrite(pay_day=27, pay_month_offset=0), system)
    body = _employee(
        entity,
        part_time_percent="100",
        employer_inps_rate="30.00",
        employee_inps_rate="9.19",
        employer_inail_rate="1.00",
        thirteenth_month=12,
        fourteenth_month=6,
    )
    member = staff.create_staff_member(
        EmployeeCreate(**{k: v for k, v in body.items() if k != "kind"}), system
    )
    staff.record_staff_payslip(
        member.id,
        PayslipFigures(
            period=max(today_local().replace(day=1), _dt.date(2026, 1, 1)),
            gross=Decimal("2142.86"),
            net=Decimal("1607.15"),
            withholding=Decimal("321.43"),
        ),
        system,
    )
    assert (
        DueItemService(api_session)
        .list(DueItemListQuery(source_kind="payroll", limit=200), system)
        .items
    )

    assert (
        collaborator_client.get("/api/due-items", params={"source_kind": "payroll"}).status_code
        == 403
    )
    page = collaborator_client.get("/api/due-items", params={"limit": 200}).json()
    assert not [i for i in page["items"] if i["source_kind"] == "payroll"]
    totals = collaborator_client.get(
        "/api/due-items/payroll-totals", params={"legal_entity_id": entity}
    )
    assert totals.status_code == 200, totals.text
    lines = totals.json()["items"]
    assert lines and all(set(line) == {"label", "due_date", "amount", "forecast"} for line in lines)
    assert {line["label"] for line in lines} == {"Stipendi"}
