"""The two routes of the Gmail customer proposals, over real HTTP (REB-223).

`GET /api/gmail/customer-suggestions` asks the connected mailbox, here the core suite's
`FakeGmail` behind the router's own transport; `POST /api/customers/from-suggestions`
creates what a person ticked, together or not at all.
"""

import base64
import sys
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

# The same fakes the core suite uses, as `test_documents_api.py` imports them.
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "packages" / "core" / "tests"))
from fakes.fake_gmail import FakeGmail, FakeMessage  # noqa: E402

import pigrocrm_api.routers.gmail as gmail_router  # noqa: E402
from pigrocrm.core.config import Settings, get_settings  # noqa: E402
from pigrocrm.core.customers.models import Customer  # noqa: E402
from pigrocrm.core.gmail.crypto import seal  # noqa: E402
from pigrocrm.core.gmail.models import GoogleAccount  # noqa: E402
from pigrocrm.core.gmail.schemas import REQUESTED_SCOPES  # noqa: E402
from pigrocrm.core.gmail.transport import GmailTransport  # noqa: E402
from pigrocrm.core.people.models import Person  # noqa: E402

TOKEN_KEY = b"k" * 32
MAILBOX = "io@example.it"


def _configured() -> Settings:
    """The suite's own settings with Gmail filled in; see `test_gmail_router.py` for why
    they are copied from a bare `Settings` and not from `get_settings()`."""
    return Settings(_env_file=None).model_copy(  # type: ignore[call-arg]
        update={
            "google_client_id": "cid.apps.googleusercontent.com",
            "google_client_secret": "il-segreto-del-client",
            "google_token_key": base64.b64encode(TOKEN_KEY).decode(),
            "public_url": "https://crm.example.it",
        }
    )


@pytest.fixture
def gmail_ready(client: TestClient) -> Iterator[TestClient]:
    client.app.dependency_overrides[get_settings] = _configured  # type: ignore[attr-defined]
    yield client
    client.app.dependency_overrides.pop(get_settings, None)  # type: ignore[attr-defined]


@pytest.fixture
def google(monkeypatch: pytest.MonkeyPatch) -> FakeGmail:
    """The router builds its transport and its token client from the module's own
    `GmailTransport`: pointed at the fake, with an empty token-client cache so no other
    test's client answers."""
    fake = FakeGmail(access_token="ya29.fake")
    monkeypatch.setattr(
        gmail_router, "GmailTransport", lambda: GmailTransport(http=fake, sleep=lambda _: None)
    )
    monkeypatch.setattr(gmail_router, "_token_clients", {})
    return fake


def _connect(session: Session, user_id: Any) -> None:
    ciphertext, nonce = seal("1//0gRefresh", TOKEN_KEY)
    session.add(
        GoogleAccount(
            user_id=user_id,
            google_sub="sub-1",
            email_address=MAILBOX,
            refresh_token_ciphertext=ciphertext,
            refresh_token_nonce=nonce,
            scopes_granted=list(REQUESTED_SCOPES),
            status="active",
        )
    )
    session.flush()


def _sent(fake: FakeGmail, index: int, to: str, *, days_ago: int) -> None:
    fake.messages[f"m{index}"] = FakeMessage(
        id=f"m{index}",
        thread_id=f"t{index}",
        headers={"From": MAILBOX, "To": to, "Subject": "riservato"},
        body_text="riservato",
        internal_date_ms=int((datetime.now(UTC) - timedelta(days=days_ago)).timestamp() * 1000),
    )


def test_the_proposals_answer_what_the_mailbox_wrote_to(
    logged_in: TestClient,
    gmail_ready: TestClient,
    google: FakeGmail,
    api_session: Session,
    admin_user: Any,
) -> None:
    _connect(api_session, admin_user.id)
    _sent(google, 1, "Marco Bianchi <marco@acme.it>", days_ago=3)
    _sent(google, 2, "marco@acme.it", days_ago=10)
    _sent(google, 3, "amico@gmail.com", days_ago=4)

    response = logged_in.get("/api/gmail/customer-suggestions")

    assert response.status_code == 200, response.text
    body = response.json()
    assert [proposal["dominio"] for proposal in body] == ["acme.it"]
    assert body[0]["nome"] == "Acme"
    assert body[0]["conversazioni"] == 2
    assert body[0]["persone"] == [
        {"indirizzo": "marco@acme.it", "nome": "Marco Bianchi", "gia_in_anagrafica": False}
    ]
    # Headers only, and not a word of what was written.
    assert "riservato" not in response.text


def test_the_proposals_answer_conflict_without_a_mailbox_or_a_client(
    logged_in: TestClient, gmail_ready: TestClient
) -> None:
    response = logged_in.get("/api/gmail/customer-suggestions")
    assert response.status_code == 409, response.text
    gmail_ready.app.dependency_overrides.pop(get_settings, None)  # type: ignore[attr-defined]
    response = logged_in.get("/api/gmail/customer-suggestions")
    assert response.status_code == 409, response.text
    assert "non è configurato" in response.json()["detail"]


def test_the_period_is_bounded_before_google_is_asked(
    logged_in: TestClient, gmail_ready: TestClient
) -> None:
    for mesi in (0, 25):
        response = logged_in.get("/api/gmail/customer-suggestions", params={"mesi": mesi})
        assert response.status_code == 422, response.text


def test_ticked_proposals_are_created_with_their_people(
    logged_in: TestClient, api_session: Session
) -> None:
    response = logged_in.post(
        "/api/customers/from-suggestions",
        json={
            "clienti": [
                {
                    "dominio": "acme.it",
                    "ragione_sociale": "Acme S.r.l.",
                    "persone": [{"indirizzo": "marco@acme.it", "nome": "Marco Bianchi"}],
                },
                {"dominio": "studio-rossi.it", "ragione_sociale": "Studio Rossi"},
            ]
        },
    )

    assert response.status_code == 201, response.text
    created = response.json()
    assert [row["ragione_sociale"] for row in created] == ["Acme S.r.l.", "Studio Rossi"]
    person = api_session.execute(select(Person).where(Person.email == "marco@acme.it")).scalar_one()
    assert (person.nome, person.cognome) == ("Marco", "Bianchi")
    assert str(person.customer_id) == created[0]["id"]


def test_a_second_import_of_the_same_domain_is_a_conflict_and_creates_nothing(
    logged_in: TestClient, api_session: Session
) -> None:
    api_session.add(Customer(ragione_sociale="Acme", sito_web="acme.it"))
    api_session.flush()
    response = logged_in.post(
        "/api/customers/from-suggestions",
        json={
            "clienti": [
                {"dominio": "nuovo.it", "ragione_sociale": "Nuovo"},
                {"dominio": "acme.it", "ragione_sociale": "Acme di nuovo"},
            ]
        },
    )
    assert response.status_code == 409, response.text
    assert "acme.it" in response.json()["detail"]
    assert (
        api_session.execute(select(Customer).where(Customer.sito_web == "nuovo.it")).first() is None
    )


def test_an_empty_or_nameless_import_is_refused(logged_in: TestClient) -> None:
    assert (
        logged_in.post("/api/customers/from-suggestions", json={"clienti": []}).status_code == 422
    )
    blank = {"clienti": [{"dominio": "acme.it", "ragione_sociale": "   "}]}
    assert logged_in.post("/api/customers/from-suggestions", json=blank).status_code == 422


def test_a_readonly_person_can_neither_ask_nor_import(
    readonly_client: TestClient, gmail_ready: TestClient
) -> None:
    assert readonly_client is gmail_ready
    assert readonly_client.get("/api/gmail/customer-suggestions").status_code == 403
    response = readonly_client.post(
        "/api/customers/from-suggestions",
        json={"clienti": [{"dominio": "acme.it", "ragione_sociale": "Acme"}]},
    )
    assert response.status_code == 403
