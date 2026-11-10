"""Expense notes over REST (REB-1046): a staff member submits, another administrator approves, the
note is a purchase and is reimbursed once; an administrator is refused approval of their own note;
every role sees what it may and a space with `staff` or `purchases` off answers 404."""

import datetime as dt
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.auth.schemas import UserCreate
from pigrocrm.core.auth.service import UserService
from pigrocrm.core.fiscal.models import FiscalProfile
from pigrocrm.core.legal_entities.models import LegalEntity

# The same values the `logged_in` fixture of `conftest.py` signs in with.
ADMIN_EMAIL = "admin@pigro.it"
ADMIN_PASSWORD = "supersegreta1"
TODAY = dt.date.today().isoformat()


def _login(client: TestClient, email: str) -> None:
    response = client.post("/api/auth/login", json={"email": email, "password": ADMIN_PASSWORD})
    assert response.status_code == 200, response.text


def _user(session: Session, email: str, name: str, role: str) -> str:
    created = UserService(session).create(
        UserCreate(email=email, password=ADMIN_PASSWORD, name=name, role=role),  # type: ignore[arg-type]
        Actor.system(),
    )
    session.commit()
    return str(created.id)


def _world(client: TestClient, session: Session) -> dict[str, Any]:
    """The admin of the fixture (logged in), a second administrator, a collaborator linked to a
    staff member of a legal entity, and a cost category."""
    entity = LegalEntity(name="notes api", legal_name="Notes API S.r.l.", country="IT")
    session.add(entity)
    session.flush()
    session.add(FiscalProfile(legal_entity_id=entity.id, pack_id="it-standard", regime_code="RF01"))
    session.commit()
    second = _user(session, "bruno@pigro.it", "Bruno", "admin")
    person = _user(session, "carla@pigro.it", "Carla", "collaborator")
    readonly = _user(session, "dario@pigro.it", "Dario", "readonly")
    member = client.post(
        "/api/staff",
        json={
            "kind": "employee",
            "legal_entity_id": str(entity.id),
            "name": "Carla Rossi",
            "start_date": "2025-01-01",
            "gross_annual_pay": "30000.00",
            "monthly_payments": 14,
            "user_id": person,
        },
    )
    assert member.status_code == 201, member.text
    category = client.post("/api/cost-categories", json={"name": "Pranzi"})
    assert category.status_code == 201, category.text
    return {
        "entity": str(entity.id),
        "member": member.json()["id"],
        "category": category.json()["id"],
        "second": second,
        "readonly": readonly,
    }


def _note(world: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "date": TODAY,
        "description": "Pranzo con il cliente",
        "category_id": world["category"],
        "amount": "42.50",
    }
    body.update(overrides)
    return body


def test_a_staff_member_submits_another_administrator_approves_and_it_is_reimbursed_once(
    logged_in: TestClient, api_session: Session
) -> None:
    world = _world(logged_in, api_session)

    _login(logged_in, "carla@pigro.it")
    summary = logged_in.get("/api/expense-notes/summary").json()
    assert summary["staff_member_id"] == world["member"] and summary["can_submit"] is True
    submitted = logged_in.post("/api/expense-notes", json=_note(world))
    assert submitted.status_code == 201, submitted.text
    note = submitted.json()
    assert note["status"] == "submitted" and note["own"] is True and note["can_decide"] is False
    assert [n["id"] for n in logged_in.get("/api/expense-notes").json()["items"]] == [note["id"]]
    # The staff member decides nothing.
    assert logged_in.post(f"/api/expense-notes/{note['id']}/approve").status_code == 403

    _login(logged_in, "bruno@pigro.it")
    approved = logged_in.post(f"/api/expense-notes/{note['id']}/approve")
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "approved" and approved.json()["self_approved"] is False
    purchases = logged_in.get("/api/purchases", params={"kind": "expense_note"}).json()
    assert [p["id"] for p in purchases["items"]] == [approved.json()["purchase_id"]]
    assert purchases["items"][0]["staff_member_id"] == world["member"]
    # Approving twice is a 409; the purchase cannot be changed from the purchases.
    assert logged_in.post(f"/api/expense-notes/{note['id']}/approve").status_code == 409
    changed = logged_in.patch(
        f"/api/purchases/{purchases['items'][0]['id']}", json={"description": "altro"}
    )
    assert changed.status_code == 409, changed.text

    way = {"method": "transfer", "date": TODAY, "paid": True}
    paid = logged_in.post(f"/api/expense-notes/{note['id']}/reimburse", json=way)
    assert paid.status_code == 200, paid.text
    assert paid.json()["status"] == "reimbursed" and paid.json()["reimbursed_on"] == TODAY
    again = logged_in.post(f"/api/expense-notes/{note['id']}/reimburse", json=way)
    assert again.status_code == 409, again.text
    items = logged_in.get(
        "/api/due-items", params={"source_kind": "expense_note", "state": "settled"}
    ).json()["items"]
    assert [item["source_id"] for item in items] == [note["id"]]
    counts = logged_in.get("/api/expense-notes/summary").json()
    assert (counts["to_approve"], counts["to_reimburse"], counts["closed"]) == (0, 0, 1)


def test_an_administrator_is_refused_approval_of_their_own_note_in_words(
    logged_in: TestClient, api_session: Session
) -> None:
    world = _world(logged_in, api_session)
    # The fixture's administrator is a staff member too.
    me = logged_in.get("/api/auth/me").json()
    admin_member = logged_in.post(
        "/api/staff",
        json={
            "kind": "employee",
            "legal_entity_id": world["entity"],
            "name": "Anna Admin",
            "start_date": "2025-01-01",
            "gross_annual_pay": "40000.00",
            "monthly_payments": 14,
            "user_id": me["id"],
        },
    )
    assert admin_member.status_code == 201, admin_member.text
    mine = logged_in.post("/api/expense-notes", json=_note(world))
    assert mine.status_code == 201, mine.text
    assert mine.json()["own"] is True and mine.json()["can_decide"] is False
    refused = logged_in.post(f"/api/expense-notes/{mine.json()['id']}/approve")
    assert refused.status_code == 403, refused.text
    assert "Non puoi approvare una tua nota" in refused.text
    assert (
        logged_in.post(
            f"/api/expense-notes/{mine.json()['id']}/reject", json={"reason": "no"}
        ).status_code
        == 403
    )
    _login(logged_in, "bruno@pigro.it")
    assert logged_in.post(f"/api/expense-notes/{mine.json()['id']}/approve").status_code == 200


def test_a_rejected_note_keeps_its_reason_for_the_person(
    logged_in: TestClient, api_session: Session
) -> None:
    world = _world(logged_in, api_session)
    _login(logged_in, "carla@pigro.it")
    note = logged_in.post("/api/expense-notes", json=_note(world)).json()
    _login(logged_in, ADMIN_EMAIL)
    rejected = logged_in.post(
        f"/api/expense-notes/{note['id']}/reject", json={"reason": "Manca lo scontrino"}
    )
    assert rejected.status_code == 200, rejected.text
    _login(logged_in, "carla@pigro.it")
    read = logged_in.get(f"/api/expense-notes/{note['id']}").json()
    assert read["status"] == "rejected" and read["rejection_reason"] == "Manca lo scontrino"
    assert logged_in.get("/api/purchases", params={"kind": "expense_note"}).json()["items"] == []


def test_roles_and_links_decide_who_submits_and_who_reads(
    logged_in: TestClient, api_session: Session
) -> None:
    world = _world(logged_in, api_session)
    _login(logged_in, "dario@pigro.it")
    # A readonly member cannot write, and is not a staff member.
    assert logged_in.post("/api/expense-notes", json=_note(world)).status_code == 403
    assert logged_in.get("/api/expense-notes").json()["items"] == []
    summary = logged_in.get("/api/expense-notes/summary").json()
    assert summary["staff_member_id"] is None and summary["can_submit"] is False

    # A collaborator who is not a staff member has nobody to submit for.
    _user(api_session, "estranea@pigro.it", "Estranea", "collaborator")
    _login(logged_in, "estranea@pigro.it")
    refused = logged_in.post("/api/expense-notes", json=_note(world))
    assert refused.status_code == 409 and "Non risulti tra le persone" in refused.text

    # An administrator enters a note for a person who is not a user.
    _login(logged_in, ADMIN_EMAIL)
    other = logged_in.post(
        "/api/staff",
        json={
            "kind": "employee",
            "legal_entity_id": world["entity"],
            "name": "Dario Neri",
            "start_date": "2025-01-01",
            "gross_annual_pay": "20000.00",
            "monthly_payments": 13,
        },
    ).json()
    entered = logged_in.post(
        "/api/expense-notes", json=_note(world, staff_member_id=other["id"], amount="9.90")
    )
    assert entered.status_code == 201, entered.text
    assert entered.json()["staff_member_name"] == "Dario Neri"
    # The staff member's own list never holds another's.
    _login(logged_in, "carla@pigro.it")
    assert logged_in.get("/api/expense-notes").json()["total_count"] == 0
    assert logged_in.get(f"/api/expense-notes/{entered.json()['id']}").status_code == 404
    assert (
        logged_in.post(
            "/api/expense-notes", json=_note(world, staff_member_id=other["id"])
        ).status_code
        == 403
    )


def test_a_mileage_note_over_rest_sends_kilometres_and_the_rate_and_never_an_amount(
    logged_in: TestClient, api_session: Session
) -> None:
    world = _world(logged_in, api_session)
    _login(logged_in, "carla@pigro.it")
    body = _note(world)
    body.pop("amount")
    body.update(
        kind="mileage",
        kilometres="100",
        rate_per_km="0.42",
        route_from="Milano",
        route_to="Torino",
    )
    created = logged_in.post("/api/expense-notes", json=body)
    assert created.status_code == 201, created.text
    assert created.json()["amount"] == "42.00"
    body["amount"] = "1.00"
    assert logged_in.post("/api/expense-notes", json=body).status_code == 422


def test_the_routes_answer_404_while_staff_or_purchases_is_off(
    logged_in: TestClient, api_session: Session
) -> None:
    world = _world(logged_in, api_session)
    note = logged_in.post("/api/expense-notes", json=_note(world, staff_member_id=world["member"]))
    assert note.status_code == 201, note.text
    for module in ("purchases", "staff"):
        off = logged_in.put(
            "/api/settings/modules", json={"off": [module], "confirm_off": [module]}
        )
        assert off.status_code == 200, off.text
        for method, path in (
            ("GET", "/api/expense-notes"),
            ("GET", "/api/expense-notes/summary"),
            ("POST", f"/api/expense-notes/{note.json()['id']}/approve"),
        ):
            response = logged_in.request(method, path)
            assert response.status_code == 404, (module, path, response.text)
            assert response.json()["code"] == "module_off"
        assert logged_in.put("/api/settings/modules", json={"on": [module]}).status_code == 200
    assert logged_in.get("/api/expense-notes").json()["total_count"] == 1
