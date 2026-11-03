"""REB-518 over HTTP: an admin marks a talent vetted, and opens the talent cloud to a
company request's referente from the request's page, who then finds «Talent cloud» on
`/me`; the cloud's own pages are D3's."""

import re
from collections.abc import Iterator
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from rebase_api.deps import get_sender
from rebase_core.mail import RecordingSender
from rebase_core.models import User

PDF = b"%PDF-1.7\n1 0 obj<<>>endobj\n%%EOF\n"
ADMIN_EMAIL = "ivan@rebase.it"


@pytest.fixture
def admin(api_session: Session) -> Iterator[None]:
    api_session.add(User(email=ADMIN_EMAIL, first_name="Ivan", last_name="Sala", role="admin"))
    api_session.commit()
    yield
    api_session.rollback()
    for table in (
        "talent_cloud_grants",
        "admin_actions",
        "notes",
        "sessions",
        "magic_link_tokens",
        "logins",
        "freelancers",
        "companies",
        "users",
        "signups",
    ):
        api_session.execute(text(f"DELETE FROM {table}"))
    api_session.commit()


def _login(client: TestClient, sender: RecordingSender, email: str = ADMIN_EMAIL) -> None:
    """Signs `email` in; the speed bump on the public routes is reset first, since one
    test here signs in four times beside a wizard's request."""
    assert client.post("/api/hub/auth/link", json={"email": email}).status_code == 202
    match = re.search(r"/verify\?t=([A-Za-z0-9_-]+)", sender.sent[-1].text)
    assert match
    assert client.post("/api/hub/auth/enter", json={"token": match.group(1)}).status_code == 200


def _apply(client: TestClient, email: str = "ada@studio.it") -> str:
    response = client.post(
        "/api/hub/freelancers",
        data={
            "first_name": "Ada",
            "last_name": "Lovelace",
            "email": email,
            "daily_rate": "450",
            "job_title": "Backend developer",
            "remote": "remote",
        },
        files={"cv": ("Ada CV.pdf", PDF, "application/pdf")},
    )
    assert response.status_code == 201, response.text
    listed = client.get("/api/hub/freelancers").json()["items"]
    return next(item["id"] for item in listed if item["email"] == email)


def _request_company(client: TestClient, company: str = "Acme S.r.l.") -> str:
    response = client.post(
        "/api/hub/companies",
        json={
            "company_name": company,
            "contact_first_name": "Wile",
            "contact_last_name": "Coyote",
            "email": "wile@acme.it",
            "phone": "+39 345 1234567",
            "requested_role": "Backend developer",
            "project": "Un backend developer per tre mesi.",
            "period_from": "2026-10-01",
            "duration": "3 mesi",
            "daily_budget": "500",
            "remote": "remote",
            "headcount": 1,
            "utm": {"origin": "team-builder"},
        },
    )
    assert response.status_code == 201, response.text
    listed = client.get("/api/hub/companies").json()["items"]
    return next(item["id"] for item in listed if item["company_name"] == company)


# ---- «Verificato» ---------------------------------------------------------------------------


def test_an_admin_marks_a_talent_vetted_and_the_list_reads_it(
    client: TestClient, admin: None, sender: RecordingSender
) -> None:
    _login(client, sender)
    freelancer_id = _apply(client)

    row = next(item for item in client.get("/api/hub/talent").json()["items"])
    assert (row["vetted_at"], row["has_anonymous_card"]) == (None, False)

    marked = client.post(f"/api/hub/freelancers/{freelancer_id}/vetted", json={"vetted": True})
    assert marked.status_code == 200, marked.text
    assert marked.json()["vetted_at"] is not None
    row = next(item for item in client.get("/api/hub/talent").json()["items"])
    assert row["vetted_at"] == marked.json()["vetted_at"]
    assert client.get(f"/api/hub/freelancers/{freelancer_id}").json()["vetted_at"] is not None

    unmarked = client.post(f"/api/hub/freelancers/{freelancer_id}/vetted", json={"vetted": False})
    assert unmarked.status_code == 200 and unmarked.json()["vetted_at"] is None

    trail = client.get(f"/api/hub/freelancers/{freelancer_id}/audit").json()
    assert [(entry["kind"], entry["payload"]) for entry in trail] == [
        ("vetted", {"vetted": False}),
        ("vetted", {"vetted": True}),
    ]

    missing = client.post(f"/api/hub/freelancers/{uuid4()}/vetted", json={"vetted": True})
    assert missing.status_code == 404
    path = f"/api/hub/freelancers/{freelancer_id}/vetted"
    assert client.post(path, json={"vetted": "forse"}).status_code == 422


def test_only_an_admin_marks_a_talent_vetted(
    client: TestClient, admin: None, sender: RecordingSender
) -> None:
    _login(client, sender)
    freelancer_id = _apply(client)
    client.cookies.clear()
    path = f"/api/hub/freelancers/{freelancer_id}/vetted"
    assert client.post(path, json={"vetted": True}).status_code == 401
    _login(client, sender, "ada@studio.it")
    assert client.post(path, json={"vetted": True}).status_code == 403


# ---- the talent cloud's door ----------------------------------------------------------------


def test_an_admin_opens_the_cloud_and_the_contact_person_finds_it_on_me(
    client: TestClient, admin: None, sender: RecordingSender
) -> None:
    _login(client, sender)
    company_id = _request_company(client)
    mailed = len(sender.sent)

    opened = client.post(f"/api/hub/companies/{company_id}/cloud")
    assert opened.status_code == 201, opened.text
    grant = opened.json()
    assert grant["company_id"] == company_id and grant["company"] == "Acme S.r.l."
    assert (grant["contact_person"], grant["email"]) == ("Wile Coyote", "wile@acme.it")
    assert grant["granted_by_name"] == "Ivan" and grant["revoked_at"] is None
    # The mail leaves after the answer, to the referente, with the cloud's link.
    assert len(sender.sent) == mailed + 1
    mail = sender.sent[-1]
    assert mail.to == "wile@acme.it"
    assert mail.subject == "Il talent cloud di rebase è aperto per Acme S.r.l."
    assert "/hub/me/cloud" in mail.text

    # A second «Apri», a double click included: the same grant, 200, no second mail.
    again = client.post(f"/api/hub/companies/{company_id}/cloud")
    assert again.status_code == 200 and again.json()["id"] == grant["id"]
    assert len(sender.sent) == mailed + 1

    page = client.get(f"/api/hub/companies/{company_id}").json()
    assert page["origin"] == "team-builder"
    assert page["talent_cloud_grant"]["id"] == grant["id"]
    listed = client.get("/api/hub/cloud/grants").json()
    assert [item["id"] for item in listed] == [grant["id"]]

    client.cookies.clear()
    _login(client, sender, "wile@acme.it")
    assert client.get("/api/hub/me").json()["talent_cloud"] is True

    client.cookies.clear()
    _login(client, sender)
    closed = client.delete(f"/api/hub/companies/{company_id}/cloud")
    assert closed.status_code == 200, closed.text
    assert closed.json()["id"] == grant["id"] and closed.json()["revoked_at"] is not None
    assert client.get(f"/api/hub/companies/{company_id}").json()["talent_cloud_grant"] is None
    refused = client.delete(f"/api/hub/companies/{company_id}/cloud")
    assert refused.status_code == 409
    assert refused.json()["detail"] == "Il talent cloud non è aperto per questa azienda."

    client.cookies.clear()
    _login(client, sender, "wile@acme.it")
    assert client.get("/api/hub/me").json()["talent_cloud"] is False


def test_the_cloud_opens_without_a_mail_key_and_mails_nobody(
    client: TestClient, admin: None, sender: RecordingSender
) -> None:
    _login(client, sender)
    company_id = _request_company(client)
    mailed = len(sender.sent)
    client.app.dependency_overrides[get_sender] = lambda: None  # type: ignore[attr-defined]
    opened = client.post(f"/api/hub/companies/{company_id}/cloud")
    assert opened.status_code == 201, opened.text
    assert len(sender.sent) == mailed


def test_the_cloud_routes_are_an_admins(
    client: TestClient, admin: None, sender: RecordingSender
) -> None:
    _login(client, sender)
    company_id = _request_company(client)
    assert client.post(f"/api/hub/companies/{uuid4()}/cloud").status_code == 404
    assert client.delete(f"/api/hub/companies/{uuid4()}/cloud").status_code == 404

    client.cookies.clear()
    for method, path in (
        ("POST", f"/api/hub/companies/{company_id}/cloud"),
        ("DELETE", f"/api/hub/companies/{company_id}/cloud"),
        ("GET", "/api/hub/cloud/grants"),
    ):
        assert client.request(method, path).status_code == 401, path
    _login(client, sender, "wile@acme.it")
    for method, path in (
        ("POST", f"/api/hub/companies/{company_id}/cloud"),
        ("DELETE", f"/api/hub/companies/{company_id}/cloud"),
        ("GET", "/api/hub/cloud/grants"),
    ):
        assert client.request(method, path).status_code == 403, path
    assert client.get("/api/hub/me").json()["talent_cloud"] is False
