"""Referrals over HTTP (P-REB-44): the member's own code, a signup that carries one,
and the admin's ledger and rates, gated the same way every other admin route is."""

import re
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from rebase_api.deps import get_sender
from rebase_api.ratelimit import reset_rate_limit
from rebase_core.mail import RecordingSender
from rebase_core.models import Referral, User

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


def test_my_referral_states_the_rates_in_force_on_every_call(
    client: TestClient, sender: RecordingSender, admin: None
) -> None:
    """An admin editing the rates on the ledger changes the member's next read: nothing
    is cached and nothing is hard-coded. The admin reads their own panel here, since a
    signed-in admin is a member too."""
    _login(client, sender)

    before = client.get("/api/hub/me/referral").json()
    assert (before["rate_freelancer"], before["rate_company"]) == ("0.1000", "0.3000")

    saved = client.put(
        "/api/hub/referral-settings", json={"rate_freelancer": "0.125", "rate_company": "0.35"}
    )
    assert saved.status_code == 200, saved.text

    after = client.get("/api/hub/me/referral").json()
    assert (after["rate_freelancer"], after["rate_company"]) == ("0.1250", "0.3500")


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


def test_a_malformed_rif_is_no_referral_not_a_422(
    client: TestClient, sender: RecordingSender, api_session: Session, clean: None
) -> None:
    """Regression: `rif` used to be a `Field(pattern=...)`, so punctuation or a code
    over ten characters failed the whole application with a 422. A malformed code is
    muted the same way an unknown one already is (`ReferralService.resolve_referrer`),
    never a reason to refuse someone applying (CodeRabbit)."""
    applied = client.post(
        "/api/hub/freelancers",
        data={
            "nome": "Grace",
            "cognome": "Hopper",
            "email": "grace@studio.it",
            "tariffa_giornaliera": "450",
            "posizione": "Backend developer",
            "remoto": "remoto",
            "rif": "not-a-code!",
        },
        files={"cv": ("Grace CV.pdf", PDF, "application/pdf")},
    )
    assert applied.status_code == 201, applied.text


def _apply(client: TestClient, email: str, rif: str | None = None) -> tuple[int, str]:
    sent = client.post(
        "/api/hub/freelancers",
        data={
            "nome": "Ada",
            "cognome": "Lovelace",
            "email": email,
            "tariffa_giornaliera": "450",
            "posizione": "Backend developer",
            "remoto": "remoto",
            **({"rif": rif} if rif else {}),
        },
        files={"cv": ("Ada CV.pdf", PDF, "application/pdf")},
    )
    return sent.status_code, sent.text


def test_a_squatted_address_is_pending_until_its_owner_logs_in_and_the_route_never_says_so(
    client: TestClient, sender: RecordingSender, api_session: Session, clean: None
) -> None:
    """Someone posts a prospect's address with an accomplice's code. The public answer is
    the same whether the address is new, already attributed or already a card with no
    attribution (REB-658: the routes must not tell a stranger which), and the referral
    stays pending until the owner of the address spends a link mailed to it."""
    _member(api_session, "accomplice@community.it")
    _enter(client, sender, "accomplice@community.it")
    code = client.get("/api/hub/me/referral").json()["code"]
    client.cookies.clear()
    reset_rate_limit()  # the four public posts below are one stranger's, not the member's

    fresh = _apply(client, "prospect@acme.it", code)
    attributed_again = _apply(client, "prospect@acme.it", code)
    plain = _apply(client, "plain@acme.it")
    unattributed_again = _apply(client, "plain@acme.it", code)

    assert fresh == attributed_again == plain == unattributed_again == (201, fresh[1])
    referral = api_session.query(Referral).one()
    assert referral.stato == "da_verificare"

    # The owner of the address clicks the link the repeat application mailed them.
    mailed = next(mail for mail in reversed(sender.sent) if mail.to == "prospect@acme.it")
    token = re.search(r"/entra\?t=([A-Za-z0-9_-]+)", mailed.text)
    assert token
    entered = client.post("/api/hub/auth/enter", json={"token": token.group(1)})
    assert entered.status_code == 200, entered.text

    api_session.expire_all()
    referral = api_session.query(Referral).one()
    assert (referral.stato, referral.verified_via) == ("verificato", "accesso")


def test_a_company_request_answers_the_same_whether_or_not_its_address_is_attributed(
    client: TestClient, sender: RecordingSender, api_session: Session, clean: None
) -> None:
    _member(api_session, "accomplice@community.it")
    _enter(client, sender, "accomplice@community.it")
    code = client.get("/api/hub/me/referral").json()["code"]
    client.cookies.clear()
    reset_rate_limit()
    body = {
        "nome_azienda": "ACME Srl",
        "referente_nome": "Wile",
        "referente_cognome": "E.",
        "email": "wile@acme.it",
        "telefono": "+39 345 1234567",
        "figura_richiesta": "Backend developer",
        "progetto": "Un backend developer per tre mesi.",
        "periodo_da": "2026-10-01",
        "durata": "3 mesi",
        "budget_giornaliero": "500",
        "remoto": "remoto",
        "numero_risorse": 1,
    }

    squatted = client.post("/api/hub/companies", json={**body, "rif": code})
    again = client.post("/api/hub/companies", json=body)
    other = client.post("/api/hub/companies", json={**body, "email": "other@beta.it"})

    assert (squatted.status_code, squatted.text) == (again.status_code, again.text)
    assert (other.status_code, other.text) == (squatted.status_code, squatted.text)
    assert api_session.query(Referral).one().stato == "da_verificare"


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
    assert (defaults.json()["rate_freelancer"], defaults.json()["rate_company"]) == (
        "0.1000",
        "0.3000",
    )

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
