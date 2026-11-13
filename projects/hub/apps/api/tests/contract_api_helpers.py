"""What the engagement hand-off and member-contract API tests share (REB-387, REB-1105,
REB-1107): the admin and the freelancer signed in through the magic link, and the card and the
request the hand-off pairs. The match tests that used to share it went with the hub's match side
(REB-1108)."""

import re

from fastapi.testclient import TestClient

from rebase_core.mail import RecordingSender

PDF = b"%PDF-1.7\n1 0 obj<<>>endobj\n%%EOF\n"
ADMIN_EMAIL = "ivan@rebase.it"
# What the company would pay rebase a day: it must appear nowhere a freelancer reads.
BUDGET = "777.77"
TABLES = (
    "referral_rewards",
    "referrals",
    "admin_actions",
    "notes",
    "freelancers",
    "companies",
    "users",
    "signups",
)


def login_as(client: TestClient, sender: RecordingSender, email: str) -> None:
    """Signs `email` in through the magic link, as the member area does."""
    assert client.post("/api/hub/auth/link", json={"email": email}).status_code == 202
    match = re.search(r"/verify\?t=([A-Za-z0-9_-]+)", sender.sent[-1].text)
    assert match
    assert client.post("/api/hub/auth/enter", json={"token": match.group(1)}).status_code == 200


def login(client: TestClient, sender: RecordingSender) -> None:
    login_as(client, sender, ADMIN_EMAIL)


def apply_freelancer(client: TestClient) -> str:
    response = client.post(
        "/api/hub/freelancers",
        data={
            "first_name": "Ada",
            "last_name": "Lovelace",
            "email": "ada@studio.it",
            "daily_rate": "450",
            "job_title": "Backend developer",
            "remote": "remote",
        },
        files={"cv": ("Ada CV.pdf", PDF, "application/pdf")},
    )
    assert response.status_code == 201, response.text
    return str(client.get("/api/hub/freelancers").json()["items"][0]["id"])


def request_company(client: TestClient) -> str:
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
            "daily_budget": BUDGET,
            "remote": "remote",
            "headcount": 1,
        },
    )
    assert response.status_code == 201, response.text
    return str(client.get("/api/hub/companies").json()["items"][0]["id"])
