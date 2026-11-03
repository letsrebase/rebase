"""REB-347 over HTTP: an admin overrides, clears, deletes and restores a freelancer or
company record through the admin API, and reads and reverses those actions from the
same rows' own audit trail."""

import re
from collections.abc import Iterator

import pytest
from api_responses import ok
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from rebase_api.deps import get_sender
from rebase_core.mail import RecordingSender
from rebase_core.models import Freelancer, User

PDF = b"%PDF-1.7\n1 0 obj<<>>endobj\n%%EOF\n"
ADMIN_EMAIL = "ivan@rebase.it"


@pytest.fixture
def sender(client: TestClient) -> Iterator[RecordingSender]:
    recording = RecordingSender()
    client.app.dependency_overrides[get_sender] = lambda: recording  # type: ignore[attr-defined]
    yield recording


@pytest.fixture
def admin(api_session: Session) -> Iterator[None]:
    api_session.add(User(email=ADMIN_EMAIL, nome="Ivan", last_name="", role="admin"))
    api_session.commit()
    yield
    api_session.rollback()
    for table in ("admin_actions", "notes", "freelancers", "companies", "users", "signups"):
        api_session.execute(text(f"DELETE FROM {table}"))
    api_session.commit()


def _login(client: TestClient, sender: RecordingSender, email: str = ADMIN_EMAIL) -> None:
    assert client.post("/api/hub/auth/link", json={"email": email}).status_code == 202
    match = re.search(r"/entra\?t=([A-Za-z0-9_-]+)", sender.sent[-1].text)
    assert match
    assert client.post("/api/hub/auth/enter", json={"token": match.group(1)}).status_code == 200


def _apply(client: TestClient, email: str = "ada@studio.it") -> str:
    """The wizard's own answer is `{"ok": true}` (REB-272): the id an admin-only test
    needs comes from the admin's own list, the same lookup `test_admin_api.py` uses."""
    response = client.post(
        "/api/hub/freelancers",
        data={
            "nome": "Ada",
            "last_name": "Lovelace",
            "email": email,
            "daily_rate": "450",
            "posizione": "Backend developer",
            "remote": "remote",
        },
        files={"cv": ("Ada CV.pdf", PDF, "application/pdf")},
    )
    assert response.status_code == 201, response.text
    listed = client.get("/api/hub/freelancers").json()["items"]
    return next(item["id"] for item in listed if item["email"] == email)


def _request_company(client: TestClient) -> str:
    response = client.post(
        "/api/hub/companies",
        json={
            "company_name": "ACME Srl",
            "contact_first_name": "Wile",
            "contact_last_name": "E.",
            "email": "wile@acme.it",
            "phone": "+39 345 1234567",
            "requested_role": "Backend developer",
            "project": "Un backend developer per tre mesi.",
            "period_from": "2026-10-01",
            "duration": "3 mesi",
            "daily_budget": "500",
            "remote": "remote",
            "headcount": 1,
        },
    )
    assert response.status_code == 201, response.text
    listed = client.get("/api/hub/companies").json()["items"]
    return next(item["id"] for item in listed if item["company_name"] == "ACME Srl")


# ---- freelancers ------------------------------------------------------------------------


def test_an_admin_overrides_a_freelancer_field_and_reads_the_audit_trail(
    client: TestClient, admin: None, sender: RecordingSender
) -> None:
    _login(client, sender)
    freelancer_id = _apply(client)

    overridden = client.patch(
        f"/api/hub/freelancers/{freelancer_id}/override",
        json={"posizione": "Tech lead", "daily_rate": "600"},
    )
    assert overridden.status_code == 200, overridden.text
    assert overridden.json()["posizione"] == "Tech lead"

    trail = client.get(f"/api/hub/freelancers/{freelancer_id}/audit")
    assert trail.status_code == 200
    entries = trail.json()
    assert len(entries) == 1
    assert entries[0]["kind"] == "overridden"
    assert entries[0]["admin_name"] == "Ivan"
    assert entries[0]["payload"]["before"]["posizione"] == "Backend developer"


def test_overriding_a_freelancer_identity_field_moves_it_onto_the_linked_user(
    client: TestClient, admin: None, sender: RecordingSender, api_session: Session
) -> None:
    _login(client, sender)
    freelancer_id = _apply(client)

    overridden = client.patch(
        f"/api/hub/freelancers/{freelancer_id}/override", json={"nome": "Grace"}
    )
    assert overridden.status_code == 200 and overridden.json()["nome"] == "Grace"
    user_id = api_session.scalar(select(Freelancer.user_id).where(Freelancer.id == freelancer_id))
    assert api_session.get(User, user_id).nome == "Grace"


def test_overriding_a_required_field_to_null_is_a_422(
    client: TestClient, admin: None, sender: RecordingSender
) -> None:
    _login(client, sender)
    freelancer_id = _apply(client)
    refused = client.patch(f"/api/hub/freelancers/{freelancer_id}/override", json={"status": None})
    assert refused.status_code == 422


def test_an_admin_clears_the_cv_without_the_bytes_reaching_the_audit_trail(
    client: TestClient, admin: None, sender: RecordingSender
) -> None:
    _login(client, sender)
    freelancer_id = _apply(client)

    cleared = client.delete(f"/api/hub/freelancers/{freelancer_id}/cv")
    assert cleared.status_code == 200 and cleared.json()["cv_filename"] is None
    assert client.get(f"/api/hub/freelancers/{freelancer_id}/cv").status_code == 404

    entries = client.get(f"/api/hub/freelancers/{freelancer_id}/audit").json()
    assert entries[0]["kind"] == "cleared"
    assert "cv_bytes" not in str(entries[0]["payload"])


def test_an_admin_deletes_a_freelancer_reversibly(
    client: TestClient, admin: None, sender: RecordingSender
) -> None:
    _login(client, sender)
    freelancer_id = _apply(client)

    deleted = client.delete(f"/api/hub/freelancers/{freelancer_id}")
    assert deleted.status_code == 200 and deleted.json()["deleted_at"] is not None
    assert client.get("/api/hub/freelancers").json()["total"] == 0
    assert client.get(f"/api/hub/freelancers/{freelancer_id}").status_code == 404

    restored = client.post(f"/api/hub/freelancers/{freelancer_id}/restore")
    assert restored.status_code == 200 and restored.json()["deleted_at"] is None
    assert client.get("/api/hub/freelancers").json()["total"] == 1


def test_an_admin_reverts_a_freelancer_override_from_its_audit_entry(
    client: TestClient, admin: None, sender: RecordingSender
) -> None:
    _login(client, sender)
    freelancer_id = _apply(client)
    ok(
        client.patch(
            f"/api/hub/freelancers/{freelancer_id}/override", json={"posizione": "Tech lead"}
        )
    )
    action_id = client.get(f"/api/hub/freelancers/{freelancer_id}/audit").json()[0]["id"]

    reverted = client.post(f"/api/hub/freelancers/{freelancer_id}/audit/{action_id}/revert")
    assert reverted.status_code == 200
    assert reverted.json()["posizione"] == "Backend developer"


def test_reverting_a_delete_entry_is_refused(
    client: TestClient, admin: None, sender: RecordingSender
) -> None:
    _login(client, sender)
    freelancer_id = _apply(client)
    ok(client.delete(f"/api/hub/freelancers/{freelancer_id}"))
    ok(client.post(f"/api/hub/freelancers/{freelancer_id}/restore"))
    delete_entry = next(
        e
        for e in client.get(f"/api/hub/freelancers/{freelancer_id}/audit").json()
        if e["kind"] == "deleted"
    )
    refused = client.post(f"/api/hub/freelancers/{freelancer_id}/audit/{delete_entry['id']}/revert")
    assert refused.status_code == 422


# ---- companies --------------------------------------------------------------------------


def test_an_admin_overrides_deletes_and_restores_a_company_request(
    client: TestClient, admin: None, sender: RecordingSender
) -> None:
    _login(client, sender)
    company_id = _request_company(client)

    overridden = client.patch(
        f"/api/hub/companies/{company_id}/override", json={"daily_budget": "650"}
    )
    assert overridden.status_code == 200
    assert overridden.json()["daily_budget"] == "650"

    deleted = client.delete(f"/api/hub/companies/{company_id}")
    assert deleted.status_code == 200 and deleted.json()["deleted_at"] is not None
    assert client.get("/api/hub/companies").json()["total"] == 0

    restored = client.post(f"/api/hub/companies/{company_id}/restore")
    assert restored.status_code == 200 and restored.json()["deleted_at"] is None

    entries = client.get(f"/api/hub/companies/{company_id}/audit").json()
    assert [e["kind"] for e in entries] == ["restored", "deleted", "overridden"]


def test_without_the_cookie_every_override_route_is_a_401(client: TestClient, admin: None) -> None:
    missing = "00000000-0000-7000-8000-000000000000"
    assert client.patch(f"/api/hub/freelancers/{missing}/override", json={}).status_code == 401
    assert client.delete(f"/api/hub/freelancers/{missing}").status_code == 401
    assert client.post(f"/api/hub/freelancers/{missing}/restore").status_code == 401
    assert client.get(f"/api/hub/freelancers/{missing}/audit").status_code == 401
    assert client.patch(f"/api/hub/companies/{missing}/override", json={}).status_code == 401
    assert client.delete(f"/api/hub/companies/{missing}").status_code == 401
