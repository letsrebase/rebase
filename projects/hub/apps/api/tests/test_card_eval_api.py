"""«Valutazione» over HTTP (REB-836): admin only, a correction must be a `Card`, accepting
needs an expected card, ratings stay inside 1 to 3."""

import re
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
from fakes_cards import text_pdf
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from rebase_api.deps import get_admin
from rebase_core.admin_tokens import AdminRead
from rebase_core.card_eval import CardEvalService
from rebase_core.freelancers import FreelancerService
from rebase_core.mail import RecordingSender
from rebase_core.models import User
from rebase_core.schemas import FreelancerCreate

BASE = "/api/hub/admin/card-eval"
SOME_ID = UUID("00000000-0000-4000-8000-000000000836")
CARD = {
    "ruolo": "Product designer",
    "anni": 6,
    "seniority": "senior",
    "competenze": ["Figma", "Design system"],
    "settori": ["Fintech"],
    "lingue": ["italiano", "inglese"],
    "luogo": "Milano",
    "sintesi": "Disegna prodotti digitali da sei anni. Ha guidato un design system.",
}
CV = text_pdf("Giulia Verdi, product designer a Milano da sei anni: Figma, design system.")
CLEANUP = (
    "DELETE FROM card_eval_results",
    "DELETE FROM card_eval_runs",
    "DELETE FROM card_eval_profiles",
    "DELETE FROM admin_actions",
    "DELETE FROM freelancers",
    "DELETE FROM users",
)


def _clean(session: Session) -> None:
    session.rollback()
    for statement in CLEANUP:
        session.execute(text(statement))
    session.commit()


@pytest.fixture
def admin(client: TestClient, api_session: Session) -> Iterator[User]:
    row = User(email="ivan@rebase.it", nome="Ivan", cognome="Sala", role="admin")
    api_session.add(row)
    api_session.commit()
    client.app.dependency_overrides[get_admin] = lambda: AdminRead(  # type: ignore[attr-defined]
        id=row.id, email=row.email, nome=row.nome, attivo=True, created_at=datetime.now(UTC)
    )
    yield row
    _clean(api_session)


@pytest.fixture
def member(client: TestClient, api_session: Session, sender: RecordingSender) -> Iterator[User]:
    row = User(email="ada.member@studio.it", nome="Ada", cognome="Lovelace", role="member")
    api_session.add(row)
    api_session.commit()
    assert client.post("/api/hub/auth/link", json={"email": row.email}).status_code == 202
    match = re.search(r"/entra\?t=([A-Za-z0-9_-]+)", sender.sent[-1].text)
    assert match
    assert client.post("/api/hub/auth/enter", json={"token": match.group(1)}).status_code == 200
    yield row
    _clean(api_session)


def _profile(session: Session, admin: User) -> UUID:
    """A fiction freelancer on the evaluation set."""
    read, _ = FreelancerService(session).apply(
        FreelancerCreate(
            nome="Giulia",
            cognome="Verdi",
            email=f"{uuid4().hex[:8]}@studio.it",
            tariffa_giornaliera=Decimal("450.00"),
            posizione="Product designer",
            remoto="remoto",
        ),
        CV,
        "cv.pdf",
        "",
    )
    CardEvalService(session).add(admin.id, read.id, "designer")
    session.commit()
    return read.id


ROUTES = [
    ("get", BASE, None),
    ("put", f"{BASE}/{SOME_ID}", CARD),
    ("post", f"{BASE}/{SOME_ID}/accept", {}),
    ("post", f"{BASE}/runs/{SOME_ID}/ratings", {"ratings": {}}),
]


@pytest.mark.parametrize(("method", "path", "body"), ROUTES)
def test_anonymous_gets_401_on_every_route(
    client: TestClient, method: str, path: str, body: Any
) -> None:
    kwargs = {} if body is None else {"json": body}
    assert getattr(client, method)(path, **kwargs).status_code == 401


@pytest.mark.parametrize(("method", "path", "body"), ROUTES)
def test_non_admin_gets_403_on_every_route(
    client: TestClient, member: User, method: str, path: str, body: Any
) -> None:
    kwargs = {} if body is None else {"json": body}
    assert getattr(client, method)(path, **kwargs).status_code == 403


def test_put_invalid_card_422(client: TestClient, admin: User, api_session: Session) -> None:
    fid = _profile(api_session, admin)
    for bad in ({**CARD, "seniority": "guru"}, {**CARD, "extra": 1}, {**CARD, "anni": 99}):
        assert client.put(f"{BASE}/{fid}", json=bad).status_code == 422


def test_put_valid_card_is_bozza_then_accept(
    client: TestClient, admin: User, api_session: Session
) -> None:
    fid = _profile(api_session, admin)
    saved = client.put(f"{BASE}/{fid}", json=CARD)
    assert saved.status_code == 200
    assert saved.json()["status"] == "bozza"
    assert saved.json()["expected"]["ruolo"] == "Product designer"
    accepted = client.post(f"{BASE}/{fid}/accept")
    assert accepted.status_code == 200
    assert accepted.json()["status"] == "accettata"
    listing = client.get(BASE).json()
    assert [p["status"] for p in listing["profiles"]] == ["accettata"]
    assert client.put(f"{BASE}/{fid}", json={**CARD, "anni": 7}).json()["status"] == "bozza"


def test_accept_without_card_409(client: TestClient, admin: User, api_session: Session) -> None:
    fid = _profile(api_session, admin)
    assert client.post(f"{BASE}/{fid}/accept").status_code == 409


def test_unknown_profile_404(client: TestClient, admin: User) -> None:
    assert client.put(f"{BASE}/{uuid4()}", json=CARD).status_code == 404


def test_ratings_outside_one_to_three_422_and_unknown_run_404(
    client: TestClient, admin: User
) -> None:
    body = {"ratings": {str(uuid4()): {"ruolo": 4}}}
    assert client.post(f"{BASE}/runs/{uuid4()}/ratings", json=body).status_code in (404, 422)
    assert client.post(f"{BASE}/runs/{uuid4()}/ratings", json={"ratings": "x"}).status_code == 422
