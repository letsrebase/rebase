"""The unified identity over HTTP (REB-278): `GET /api/hub/me`, a signed-in member
hitting an admin route (403, not 401), and the promote/demote pair.

Every address here carries a `.reb278` tag: `api_engine` is one Postgres container
shared by every test file in this suite, and a plain `ada@studio.it` collides with the
same address other files apply under, leaving stale rows (and a stale `completa`) a
`clean` fixture scoped to this file alone cannot see coming.
"""

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


@pytest.fixture
def sender(client: TestClient) -> Iterator[RecordingSender]:
    recording = RecordingSender()
    client.app.dependency_overrides[get_sender] = lambda: recording  # type: ignore[attr-defined]
    yield recording


@pytest.fixture
def clean(api_session: Session) -> Iterator[None]:
    yield
    api_session.rollback()
    for table in (
        "guide_downloads",
        "sessions",
        "magic_link_tokens",
        "logins",
        "admin_actions",
        "notes",
        "admin_tokens",
        "freelancers",
        "companies",
        "users",
        "signups",
    ):
        api_session.execute(text(f"DELETE FROM {table}"))
    api_session.commit()


def _apply(client: TestClient, email: str) -> None:
    response = client.post(
        "/api/hub/freelancers",
        data={
            "nome": "Ada",
            "cognome": "Lovelace",
            "email": email,
            "tariffa_giornaliera": "450",
            "posizione": "Backend developer",
            "remoto": "remoto",
        },
        files={"cv": ("Ada CV.pdf", PDF, "application/pdf")},
    )
    assert response.status_code == 201, response.text


def _enter(client: TestClient, sender: RecordingSender, email: str) -> dict:
    assert client.post("/api/hub/auth/link", json={"email": email}).status_code == 202
    match = re.search(r"/entra\?t=([A-Za-z0-9_-]+)", sender.sent[-1].text)
    assert match, sender.sent[-1].text
    entered = client.post("/api/hub/auth/enter", json={"token": match.group(1)})
    assert entered.status_code == 200, entered.text
    return entered.json()


def _bootstrap_admin(api_session: Session, email: str = "ivan.reb278@rebase.it") -> None:
    """A `users` row already there, `role='admin'`: the shape migration A would leave
    behind for an admin who existed before REB-278 deployed."""
    api_session.add(User(email=email, nome="Ivan", cognome="Fiore", role="admin"))
    api_session.commit()


def test_me_widens_to_role_and_ha_scheda(
    client: TestClient, sender: RecordingSender, clean: None
) -> None:
    _apply(client, "ada.reb278@studio.it")
    entered = _enter(client, sender, "ada.reb278@studio.it")
    assert entered["role"] == "member" and entered["ha_scheda"] is True
    assert entered["completa"] is True and entered["nome"] == "Ada"
    assert client.get("/api/hub/me").json() == entered


def test_a_signed_in_member_hitting_an_admin_route_is_403_not_401(
    client: TestClient, sender: RecordingSender, clean: None
) -> None:
    _apply(client, "ada.reb278@studio.it")
    _enter(client, sender, "ada.reb278@studio.it")
    refused = client.get("/api/hub/admins")
    assert refused.status_code == 403
    # A real identity, just not the right one: /me still answers.
    assert client.get("/api/hub/me").status_code == 200


def test_a_signed_out_caller_is_401_not_403(client: TestClient) -> None:
    assert client.get("/api/hub/admins").status_code == 401
    assert client.get("/api/hub/me").status_code == 401


def test_an_admin_gets_200_where_a_member_gets_403(
    client: TestClient, sender: RecordingSender, clean: None, api_session: Session
) -> None:
    _bootstrap_admin(api_session)
    entered = _enter(client, sender, "ivan.reb278@rebase.it")
    assert entered["role"] == "admin" and entered["ha_scheda"] is False
    assert (entered["cv_filename"], entered["tariffa_giornaliera"], entered["links"]) == (
        None,
        None,
        [],
    )
    assert client.get("/api/hub/admins").status_code == 200


def test_promoting_an_existing_member_opens_the_admin_routes_on_their_own_session(
    client: TestClient, sender: RecordingSender, clean: None, api_session: Session
) -> None:
    _apply(client, "ada.reb278@studio.it")
    _enter(client, sender, "ada.reb278@studio.it")
    assert client.get("/api/hub/admins").status_code == 403

    from rebase_core.config import Settings
    from rebase_core.users import UserService

    UserService(api_session, Settings(_env_file=None)).promote(  # type: ignore[call-arg]
        "ada.reb278@studio.it"
    )
    api_session.commit()

    # No new login: the session already open resolves as an admin on the next request.
    assert client.get("/api/hub/admins").status_code == 200
    assert client.get("/api/hub/me").json()["role"] == "admin"


def test_promote_and_demote_routes(
    client: TestClient, sender: RecordingSender, clean: None, api_session: Session
) -> None:
    _bootstrap_admin(api_session)
    _enter(client, sender, "ivan.reb278@rebase.it")

    missing_name = client.post("/api/hub/admins/promote", json={"email": "nuovo.reb278@rebase.it"})
    assert missing_name.status_code == 422

    promoted = client.post(
        "/api/hub/admins/promote",
        json={"email": "Lorenzo.Reb278@Rebase.it", "nome": "Lorenzo", "cognome": "Fiore"},
    )
    assert promoted.status_code == 200, promoted.text
    body = promoted.json()
    assert body["email"] == "lorenzo.reb278@rebase.it" and body["nome"] == "Lorenzo"
    assert sender.sent and "/entra?t=" in sender.sent[-1].text  # a brand-new row gets a link

    admins = {row["email"] for row in client.get("/api/hub/admins").json()["items"]}
    assert admins == {"ivan.reb278@rebase.it", "lorenzo.reb278@rebase.it"}

    demoted = client.post(f"/api/hub/admins/{body['id']}/demote")
    assert demoted.status_code == 200 and demoted.json()["email"] == "lorenzo.reb278@rebase.it"
    assert [row["email"] for row in client.get("/api/hub/admins").json()["items"]] == [
        "ivan.reb278@rebase.it"
    ]

    # Promoting an address that already has a users row sends nothing new: it is not
    # a brand-new row, so there is no fresh way-in mail to send.
    _apply(client, "grace.reb278@studio.it")
    before = len(sender.sent)
    promoted_existing = client.post(
        "/api/hub/admins/promote", json={"email": "grace.reb278@studio.it"}
    )
    assert promoted_existing.status_code == 200 and promoted_existing.json()["email"] == (
        "grace.reb278@studio.it"
    )
    assert len(sender.sent) == before
