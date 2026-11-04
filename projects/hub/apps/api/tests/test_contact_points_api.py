"""REB-944 over HTTP: an admin writes the contact points of a freelancer or of a company's
contact, the person reads and edits their own from the member area, an address that is
somebody else's is refused with a sentence that names it, and a sign-up with an extra
address is the person who holds it."""

import re
from collections.abc import Iterator

import pytest
from api_responses import ok
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from rebase_api.deps import get_sender
from rebase_core.mail import RecordingSender
from rebase_core.models import User

PDF = b"%PDF-1.7\n1 0 obj<<>>endobj\n%%EOF\n"
ADMIN_EMAIL = "ivan@rebase.it"


@pytest.fixture
def sender(client: TestClient) -> Iterator[RecordingSender]:
    recording = RecordingSender()
    client.app.dependency_overrides[get_sender] = lambda: recording  # type: ignore[attr-defined]
    yield recording


@pytest.fixture
def admin(api_session: Session) -> Iterator[None]:
    api_session.add(User(email=ADMIN_EMAIL, first_name="Ivan", last_name="", role="admin"))
    api_session.commit()
    yield
    api_session.rollback()
    for table in ("admin_actions", "notes", "freelancers", "companies", "users", "signups"):
        api_session.execute(text(f"DELETE FROM {table}"))
    api_session.commit()


def _login(client: TestClient, sender: RecordingSender, email: str) -> None:
    assert client.post("/api/hub/auth/link", json={"email": email}).status_code == 202
    match = re.search(r"/verify\?t=([A-Za-z0-9_-]+)", sender.sent[-1].text)
    assert match
    assert client.post("/api/hub/auth/enter", json={"token": match.group(1)}).status_code == 200


def _apply(client: TestClient, email: str = "ada@studio.it", first_name: str = "Ada") -> None:
    response = client.post(
        "/api/hub/freelancers",
        data={
            "first_name": first_name,
            "last_name": "Lovelace",
            "email": email,
            "daily_rate": "450",
            "job_title": "Backend developer",
            "remote": "remote",
        },
        files={"cv": ("Ada CV.pdf", PDF, "application/pdf")},
    )
    assert response.status_code == 201, response.text


def _freelancer_id(client: TestClient, email: str) -> str:
    listed = client.get("/api/hub/freelancers").json()["items"]
    return next(item["id"] for item in listed if item["email"] == email)


def _request_company(client: TestClient) -> str:
    response = client.post(
        "/api/hub/companies",
        json={
            "company_name": "Immobilsistem",
            "contact_first_name": "Marco",
            "contact_last_name": "Crippa",
            "email": "marco@immobilsistem.it",
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
    return next(item["id"] for item in listed if item["company_name"] == "Immobilsistem")


def _point(kind: str, value: str, label: str = "work", **extra: object) -> dict[str, object]:
    return {"kind": kind, "value": value, "label": label, **extra}


def test_an_admin_adds_a_labelled_phone_to_a_company_contact_and_the_contact_reads_it(
    client: TestClient, admin: None, sender: RecordingSender
) -> None:
    _login(client, sender, ADMIN_EMAIL)
    company_id = _request_company(client)
    shared = _point("phone", "+39 02 5550199", "billing", note="il numero dell'agenzia")

    written = ok(
        client.patch(
            f"/api/hub/companies/{company_id}/override",
            json={
                "contact_points": [
                    _point("phone", "+39 345 1234567", "work", is_primary=True),
                    shared,
                ]
            },
        )
    ).json()
    assert [
        (p["kind"], p["value"], p["label"], p["is_primary"]) for p in written["contact_points"]
    ] == [
        ("email", "marco@immobilsistem.it", "work", True),
        ("phone", "+39 345 1234567", "work", True),
        ("phone", "+39 02 5550199", "billing", False),
    ]
    assert written["contact_points"][0]["id"] is None
    assert written["phone"] == "+39 345 1234567"

    # The contact signs in with their own address and reads the same list from `/me`.
    _login(client, sender, "marco@immobilsistem.it")
    mine = ok(client.get("/api/hub/me")).json()
    assert [(p["value"], p["label"], p["note"]) for p in mine["contact_points"]] == [
        ("marco@immobilsistem.it", "work", None),
        ("+39 345 1234567", "work", None),
        ("+39 02 5550199", "billing", "il numero dell'agenzia"),
    ]
    assert mine["phone"] == "+39 345 1234567"


def test_a_person_edits_their_own_list_and_the_admin_sees_a_note_of_it(
    client: TestClient, admin: None, sender: RecordingSender
) -> None:
    _apply(client)
    _login(client, sender, "ada@studio.it")

    me = ok(
        client.put(
            "/api/hub/me/contact-points",
            json={
                "contact_points": [
                    _point("email", "ada.casa@gmail.com", "personal"),
                    _point("phone", "+39 333 123 4567", "personal", note="solo WhatsApp"),
                ]
            },
        )
    ).json()
    assert [p["value"] for p in me["contact_points"]] == [
        "ada@studio.it",
        "ada.casa@gmail.com",
        "+39 333 123 4567",
    ]
    assert me["phone"] == "+39 333 123 4567"

    _login(client, sender, ADMIN_EMAIL)
    detail = ok(
        client.get(f"/api/hub/freelancers/{_freelancer_id(client, 'ada@studio.it')}")
    ).json()
    assert [p["value"] for p in detail["contact_points"]] == [
        "ada@studio.it",
        "ada.casa@gmail.com",
        "+39 333 123 4567",
    ]
    assert "Recapiti aggiornati dalla persona" in [n["text"] for n in detail["note"]]


def test_the_login_address_cannot_be_put_in_the_list_and_a_list_read_can_be_sent_back(
    client: TestClient, admin: None, sender: RecordingSender
) -> None:
    _apply(client)
    _login(client, sender, "ada@studio.it")
    refused = client.put(
        "/api/hub/me/contact-points",
        json={"contact_points": [_point("email", "ADA@studio.it", "personal")]},
    )
    assert refused.status_code == 422
    assert "già mostrato come principale" in refused.text

    ok(
        client.put(
            "/api/hub/me/contact-points", json={"contact_points": [_point("phone", "02 5550199")]}
        )
    )
    read = ok(client.get("/api/hub/me")).json()["contact_points"]
    again = ok(client.put("/api/hub/me/contact-points", json={"contact_points": read})).json()
    assert [p["value"] for p in again["contact_points"]] == ["ada@studio.it", "02 5550199"]


def test_an_address_that_is_somebody_elses_is_refused_and_named(
    client: TestClient, admin: None, sender: RecordingSender
) -> None:
    _apply(client, "ada@studio.it")
    _apply(client, "grace@studio.it", first_name="Grace")
    _login(client, sender, "ada@studio.it")
    ok(
        client.put(
            "/api/hub/me/contact-points",
            json={"contact_points": [_point("email", "ada.casa@gmail.com", "personal")]},
        )
    )

    _login(client, sender, "grace@studio.it")
    login_clash = client.put(
        "/api/hub/me/contact-points", json={"contact_points": [_point("email", "Ada@Studio.it")]}
    )
    extra_clash = client.put(
        "/api/hub/me/contact-points",
        json={"contact_points": [_point("email", "ada.casa@gmail.com")]},
    )
    assert login_clash.status_code == extra_clash.status_code == 422
    assert "«ada@studio.it» è già l'indirizzo con cui un'altra persona accede" in login_clash.text
    assert "«ada.casa@gmail.com» è già tra i recapiti di un'altra persona" in extra_clash.text
    assert ok(client.get("/api/hub/me")).json()["contact_points"][1:] == []


def test_a_sign_up_with_an_extra_address_makes_its_own_person_and_never_logs_in_the_holder(
    client: TestClient, admin: None, sender: RecordingSender, api_session: Session
) -> None:
    """Extra addresses are unverified: the sign-up is its own person (a duplicate an admin
    sees in the duplicates list), and the holder's account is never opened by it."""
    _apply(client, "ada@studio.it")
    _login(client, sender, "ada@studio.it")
    ok(
        client.put(
            "/api/hub/me/contact-points",
            json={"contact_points": [_point("email", "ada.casa@gmail.com", "personal")]},
        )
    )
    sender.sent.clear()

    _apply(client, "Ada.Casa@gmail.com", first_name="Giulia")

    api_session.expire_all()
    members = api_session.scalar(
        select(func.count()).select_from(User).where(User.role == "member")
    )
    assert members == 2
    assert sender.sent == []
    _login(client, sender, "ada.casa@gmail.com")
    me = ok(client.get("/api/hub/me")).json()
    assert me["first_name"] == "Giulia" and me["email"] == "ada.casa@gmail.com"
    assert [p["value"] for p in me["contact_points"]] == ["ada.casa@gmail.com"]


def test_an_admins_change_of_contact_points_is_in_the_trail_and_can_be_put_back(
    client: TestClient, admin: None, sender: RecordingSender
) -> None:
    _login(client, sender, ADMIN_EMAIL)
    _apply(client)
    freelancer_id = _freelancer_id(client, "ada@studio.it")
    ok(
        client.patch(
            f"/api/hub/freelancers/{freelancer_id}/override",
            json={"contact_points": [_point("phone", "+39 333 123 4567", "personal")]},
        )
    )
    ok(
        client.patch(
            f"/api/hub/freelancers/{freelancer_id}/override",
            json={"contact_points": [_point("phone", "02 5550199", "billing")]},
        )
    )
    entries = client.get(f"/api/hub/freelancers/{freelancer_id}/audit").json()
    latest = entries[0]
    assert latest["payload"]["changed"] == ["contact_points"]
    assert latest["payload"]["before"]["contact_points"][0]["value"] == "+39 333 123 4567"

    reverted = ok(
        client.post(f"/api/hub/freelancers/{freelancer_id}/audit/{latest['id']}/revert")
    ).json()
    assert reverted["phone"] == "+39 333 123 4567"

    # `null` is not «clear»: an empty list is.
    assert (
        client.patch(
            f"/api/hub/freelancers/{freelancer_id}/override", json={"contact_points": None}
        ).status_code
        == 422
    )
    cleared = ok(
        client.patch(f"/api/hub/freelancers/{freelancer_id}/override", json={"contact_points": []})
    ).json()
    assert [p["value"] for p in cleared["contact_points"]] == ["ada@studio.it"]


_COMPANY_BODY = {
    "company_name": "Studio Verdi",
    "contact_first_name": "Maria",
    "contact_last_name": "Verdi",
    "email": "maria@studioverdi.it",
    "phone": "+39 345 1234567",
    "requested_role": "Backend developer",
    "project": "Un backend developer per tre mesi.",
    "period_from": "2026-10-01",
    "duration": "3 mesi",
    "daily_budget": "500",
    "remote": "remote",
    "headcount": 1,
}


def test_the_public_wizard_takes_no_contact_points_and_the_admin_route_does(
    client: TestClient, admin: None, sender: RecordingSender, api_session: Session
) -> None:
    """REB-948: `contact_points` is the admin's field. The wizard refuses it (the contact's
    extra points are theirs to add from their own area), the admin route stores it."""
    points = [_point("email", "maria.verdi@gmail.com", "personal")]
    refused = client.post("/api/hub/companies", json={**_COMPANY_BODY, "contact_points": points})
    assert refused.status_code == 422
    assert api_session.scalar(select(func.count()).select_from(User)) == 1  # only the admin

    _login(client, sender, ADMIN_EMAIL)
    created = client.post(
        "/api/hub/admin/companies", json={**_COMPANY_BODY, "contact_points": points}
    )
    assert created.status_code == 201, created.text
    assert [(p["kind"], p["value"], p["is_primary"]) for p in created.json()["contact_points"]] == [
        ("email", "maria@studioverdi.it", True),
        ("phone", "+39 345 1234567", True),
        ("email", "maria.verdi@gmail.com", False),
    ]
