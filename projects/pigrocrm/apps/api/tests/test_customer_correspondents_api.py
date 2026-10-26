"""Who wrote from a customer's domain, and adding them as people, over real HTTP (REB-766).

`GET /api/gmail/customers/{id}/correspondents` asks the connected mailbox, here the core
suite's `FakeGmail` behind the router's own transport; `POST
/api/customers/{id}/people-from-mail` files one of the addresses it found as a person.
"""

import base64
import sys
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

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


def _mail(fake: FakeGmail, index: int, sender: str, to: str, *, days_ago: int) -> None:
    fake.messages[f"m{index}"] = FakeMessage(
        id=f"m{index}",
        thread_id=f"t{index}",
        headers={"From": sender, "To": to, "Subject": "riservato"},
        body_text="riservato",
        internal_date_ms=int((datetime.now(UTC) - timedelta(days=days_ago)).timestamp() * 1000),
    )


def _acme(session: Session) -> Customer:
    customer = Customer(ragione_sociale="Acme S.r.l.", sito_web="acme.it")
    session.add(customer)
    session.flush()
    return customer


def test_the_addresses_at_the_customers_domain_come_with_their_counts(
    logged_in: TestClient,
    gmail_ready: TestClient,
    google: FakeGmail,
    api_session: Session,
    admin_user: Any,
) -> None:
    customer = _acme(api_session)
    _connect(api_session, admin_user.id)
    _mail(google, 1, "Marco Bianchi <marco@acme.it>", MAILBOX, days_ago=3)
    _mail(google, 2, MAILBOX, "marco@acme.it, Sara Verdi <sara@acme.it>", days_ago=10)
    _mail(google, 3, "amico@gmail.com", MAILBOX, days_ago=4)

    response = logged_in.get(f"/api/gmail/customers/{customer.id}/correspondents")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["dominio"] == "acme.it"
    assert [
        (c["indirizzo"], c["nome"], c["conversazioni"], c["gia_in_anagrafica"])
        for c in body["corrispondenti"]
    ] == [
        ("marco@acme.it", "Marco Bianchi", 2, False),
        ("sara@acme.it", "Sara Verdi", 1, False),
    ]
    assert body["corrispondenti"][0]["ultimo_messaggio"] is not None
    assert "riservato" not in response.text


def test_an_address_in_the_address_book_is_marked_as_known(
    logged_in: TestClient,
    gmail_ready: TestClient,
    google: FakeGmail,
    api_session: Session,
    admin_user: Any,
) -> None:
    customer = _acme(api_session)
    api_session.add(Person(nome="Marco", email="marco@acme.it", customer_id=customer.id))
    api_session.flush()
    _connect(api_session, admin_user.id)
    _mail(google, 1, "marco@acme.it", MAILBOX, days_ago=3)

    body = logged_in.get(f"/api/gmail/customers/{customer.id}/correspondents").json()

    assert [c["gia_in_anagrafica"] for c in body["corrispondenti"]] == [True]


def test_a_customer_without_a_domain_or_a_mailbox_answers_conflict(
    logged_in: TestClient, gmail_ready: TestClient, api_session: Session, admin_user: Any
) -> None:
    bare = Customer(ragione_sociale="Senza sito")
    api_session.add(bare)
    api_session.flush()
    _connect(api_session, admin_user.id)
    response = logged_in.get(f"/api/gmail/customers/{bare.id}/correspondents")
    assert response.status_code == 409, response.text
    assert "dominio" in response.json()["detail"]


def test_an_unknown_customer_is_not_found(
    logged_in: TestClient, gmail_ready: TestClient, api_session: Session, admin_user: Any
) -> None:
    _connect(api_session, admin_user.id)
    response = logged_in.get(f"/api/gmail/customers/{uuid4()}/correspondents")
    assert response.status_code == 404, response.text


def test_gmail_failing_is_a_sentence_to_retry_not_a_server_error(
    logged_in: TestClient,
    gmail_ready: TestClient,
    google: FakeGmail,
    api_session: Session,
    admin_user: Any,
) -> None:
    customer = _acme(api_session)
    _connect(api_session, admin_user.id)
    _mail(google, 1, "marco@acme.it", MAILBOX, days_ago=3)
    google.fail_with = [(503, b"{}", {})] * 10

    response = logged_in.get(f"/api/gmail/customers/{customer.id}/correspondents")

    assert response.status_code == 409, response.text
    assert "Riprova più tardi" in response.json()["detail"]


def test_one_address_becomes_a_person_of_the_customer(
    logged_in: TestClient, api_session: Session
) -> None:
    customer = _acme(api_session)

    response = logged_in.post(
        f"/api/customers/{customer.id}/people-from-mail",
        json={"indirizzo": "Marco@Acme.it", "nome": "Bianchi, Marco"},
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert (body["nome"], body["cognome"], body["email"]) == ("Marco", "Bianchi", "marco@acme.it")
    assert body["customer_id"] == str(customer.id)
    person = api_session.execute(select(Person).where(Person.email == "marco@acme.it")).scalar_one()
    assert person.customer_id == customer.id


def test_an_address_from_another_domain_or_already_filed_is_refused(
    logged_in: TestClient, api_session: Session
) -> None:
    customer = _acme(api_session)
    url = f"/api/customers/{customer.id}/people-from-mail"
    for lookalike in ("x@altro.it", "x@evil-acme.it", "x@mail.acme.it", "x@acme.it.evil.com"):
        assert logged_in.post(url, json={"indirizzo": lookalike}).status_code == 422, lookalike
    assert logged_in.post(url, json={"indirizzo": "marco@acme.it"}).status_code == 201
    again = logged_in.post(url, json={"indirizzo": "marco@acme.it"})
    assert again.status_code == 409, again.text
    assert (
        len(api_session.execute(select(Person).where(Person.email == "marco@acme.it")).all()) == 1
    )


def test_a_missing_or_domainless_customer_cannot_take_a_person(
    logged_in: TestClient, api_session: Session
) -> None:
    body = {"indirizzo": "marco@acme.it"}
    assert (
        logged_in.post(f"/api/customers/{uuid4()}/people-from-mail", json=body).status_code == 404
    )
    bare = Customer(ragione_sociale="Senza sito")
    api_session.add(bare)
    api_session.flush()
    response = logged_in.post(f"/api/customers/{bare.id}/people-from-mail", json=body)
    assert response.status_code == 409, response.text
    assert "dominio" in response.json()["detail"]


def test_a_readonly_person_can_neither_ask_nor_add(
    readonly_client: TestClient, gmail_ready: TestClient
) -> None:
    some = uuid4()
    assert readonly_client.get(f"/api/gmail/customers/{some}/correspondents").status_code == 403
    response = readonly_client.post(
        f"/api/customers/{some}/people-from-mail", json={"indirizzo": "a@acme.it"}
    )
    assert response.status_code == 403, response.text
