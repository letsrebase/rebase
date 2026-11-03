"""What the contract API tests share (REB-387, REB-406, HTEST-03): the constants of a
draft match, the admin and the freelancer signed in through the magic link, and the card,
the request and the tax data `ready` leaves behind. `contract_flow` and `test_matches_api`
both build on it, so neither imports the other."""

import re

from fastapi.testclient import TestClient

from rebase_core.mail import RecordingSender

PDF = b"%PDF-1.7\n1 0 obj<<>>endobj\n%%EOF\n"
ADMIN_EMAIL = "ivan@rebase.it"
MISSING = "00000000-0000-7000-8000-000000000000"
# What the company would pay rebase a day: it must appear nowhere this flow answers.
BUDGET = "777.77"
FISCAL = {
    "tax_code": "LVLDAA85T50H501Z",
    "vat_number": "01234567890",
    "domicile": "Via Roma 1, Milano",
    "certified_email": None,
}
CUSTOMER = {
    "customer_legal_name": "ACME S.r.l.",
    "customer_vat_number": "01234567890",
    "customer_registered_office": "Milano",
}
LETTER = {
    "role": "Backend developer",
    "activities": "Le API del prodotto.",
    "start_date": "2026-10-01",
    "mode": "lump_sum",
    "fee": "450",
    "payment_days": 30,
    "month_end": True,
}
# The same letter priced by the day, which is what a reward can project without an estimate.
DAY_RATE_LETTER = {**LETTER, "mode": "to_work_unit", "unit": "a giornata"}
TABLES = (
    "referral_rewards",
    "referrals",
    "admin_actions",
    "contract_documents",
    "matches",
    "contract_letter_counters",
    "freelancer_fiscal",
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


def ready(client: TestClient, sender: RecordingSender) -> tuple[str, str]:
    login(client, sender)
    freelancer_id, company_id = apply_freelancer(client), request_company(client)
    assert (
        client.put(f"/api/hub/freelancers/{freelancer_id}/fiscal", json=FISCAL).status_code == 200
    )
    return freelancer_id, company_id


def apply_member(client: TestClient, email: str) -> None:
    response = client.post(
        "/api/hub/freelancers",
        data={
            "first_name": "Bob",
            "last_name": "Ross",
            "email": email,
            "daily_rate": "300",
            "job_title": "Designer",
            "remote": "remote",
        },
        files={"cv": ("Bob CV.pdf", PDF, "application/pdf")},
    )
    assert response.status_code == 201, response.text
