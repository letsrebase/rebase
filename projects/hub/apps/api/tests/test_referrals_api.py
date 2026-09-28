"""Referrals over HTTP (P-REB-44): the member's own code, a signup that carries one,
and the admin's ledger and rates, gated the same way every other admin route is."""

import re
from collections.abc import Iterator

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
def sender(client: TestClient) -> Iterator[RecordingSender]:
    recording = RecordingSender()
    client.app.dependency_overrides[get_sender] = lambda: recording  # type: ignore[attr-defined]
    yield recording


@pytest.fixture
def admin(api_session: Session) -> Iterator[None]:
    api_session.add(User(email=ADMIN_EMAIL, nome="Ivan", cognome="", role="admin"))
    api_session.commit()
    yield
    api_session.rollback()
    for table in (
        "referral_rewards",
        "referrals",
        "referral_settings",
        "comments",
        "freelancers",
        "companies",
        "users",
        "signups",
    ):
        api_session.execute(text(f"DELETE FROM {table}"))
    api_session.commit()


@pytest.fixture
def clean(api_session: Session) -> Iterator[None]:
    yield
    api_session.rollback()
    for table in (
        "referral_rewards",
        "referrals",
        "referral_settings",
        "sessions",
        "magic_link_tokens",
        "comments",
        "freelancers",
        "companies",
        "users",
    ):
        api_session.execute(text(f"DELETE FROM {table}"))
    api_session.commit()


def _enter(client: TestClient, sender: RecordingSender, email: str) -> None:
    assert client.post("/api/hub/auth/link", json={"email": email}).status_code == 202
    match = re.search(r"/entra\?t=([A-Za-z0-9_-]+)", sender.sent[-1].text)
    assert match
    entered = client.post("/api/hub/auth/enter", json={"token": match.group(1)})
    assert entered.status_code == 200, entered.text


def _member(api_session: Session, email: str = "mario@community.it") -> None:
    api_session.add(User(email=email, nome="Mario", cognome="Rossi", role="member"))
    api_session.commit()


def test_my_referral_needs_a_session(client: TestClient, clean: None) -> None:
    assert client.get("/api/hub/me/referral").status_code == 401


def test_my_referral_issues_a_stable_code(
    client: TestClient, sender: RecordingSender, api_session: Session, clean: None
) -> None:
    _member(api_session)
    _enter(client, sender, "mario@community.it")

    first = client.get("/api/hub/me/referral")
    second = client.get("/api/hub/me/referral")

    assert first.status_code == second.status_code == 200
    assert first.json()["code"] == second.json()["code"]
    assert first.json()["referred"] == []


def test_a_freelancer_application_with_rif_shows_up_on_the_referrers_own_page(
    client: TestClient, sender: RecordingSender, api_session: Session, clean: None
) -> None:
    _member(api_session)
    _enter(client, sender, "mario@community.it")
    code = client.get("/api/hub/me/referral").json()["code"]

    applied = client.post(
        "/api/hub/freelancers",
        data={
            "nome": "Ada",
            "cognome": "Lovelace",
            "email": "ada@studio.it",
            "tariffa_giornaliera": "450",
            "posizione": "Backend developer",
            "remoto": "remoto",
            "rif": code,
        },
        files={"cv": ("Ada CV.pdf", PDF, "application/pdf")},
    )
    assert applied.status_code == 201, applied.text

    mine = client.get("/api/hub/me/referral").json()
    assert [item["nome"] for item in mine["referred"]] == ["Ada Lovelace"]
    assert mine["referred"][0]["kind"] == "freelancer"


def test_referral_routes_need_the_admin_cookie(client: TestClient, admin: None) -> None:
    for path in ("/api/hub/referrals", "/api/hub/referral-settings"):
        assert client.get(path).status_code == 401, path
    assert client.put("/api/hub/referral-settings", json={}).status_code == 401


def test_the_admin_reads_and_saves_the_two_rates(
    client: TestClient, sender: RecordingSender, admin: None
) -> None:
    _login(client, sender)

    defaults = client.get("/api/hub/referral-settings")
    assert defaults.status_code == 200
    assert (defaults.json()["rate_freelancer"], defaults.json()["rate_company"]) == ("0.10", "0.30")

    saved = client.put(
        "/api/hub/referral-settings", json={"rate_freelancer": "0.12", "rate_company": "0.35"}
    )
    assert saved.status_code == 200
    assert (saved.json()["rate_freelancer"], saved.json()["rate_company"]) == ("0.12", "0.35")
    assert saved.json()["updated_by_nome"] == "Ivan"

    read_back = client.get("/api/hub/referral-settings")
    assert (read_back.json()["rate_freelancer"], read_back.json()["rate_company"]) == (
        "0.1200",
        "0.3500",
    )


def test_the_admin_ledger_starts_empty(
    client: TestClient, sender: RecordingSender, admin: None
) -> None:
    _login(client, sender)

    ledger = client.get("/api/hub/referrals")

    assert ledger.status_code == 200
    assert ledger.json() == {"items": [], "next_cursor": None}


def _login(client: TestClient, sender: RecordingSender, email: str = ADMIN_EMAIL) -> None:
    assert client.post("/api/hub/auth/link", json={"email": email}).status_code == 202
    match = re.search(r"/entra\?t=([A-Za-z0-9_-]+)", sender.sent[-1].text)
    assert match
    assert client.post("/api/hub/auth/enter", json={"token": match.group(1)}).status_code == 200
