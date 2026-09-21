"""The REST surface of slice 5B, over real HTTP.

Three claims are asserted here that no service-level test can make, because each is a
property of the *adapter* rather than of the domain:

  * an installation with no Google credentials answers "assente", not "rotto" -- and
    `GET /api/gmail/account` answers **200** while saying so, because the settings page
    has to render "non disponibile" without the SPA treating the response as a failed
    query;
  * no endpoint on this surface accepts a Gmail search expression (spec 12), checked
    against the generated OpenAPI document so a parameter added later cannot slip in
    unnoticed;
  * nothing that reaches the wire carries a credential -- not the refresh token, not
    its ciphertext, not the PKCE verifier, not the client secret. Asserted both
    structurally (over every schema the Gmail operations reference) and at runtime, on
    the one response that is built out of the OAuth client configuration.

`gmail_ready` overrides the `Settings` dependency rather than the environment: the rest
of this suite runs with Gmail unconfigured (there is no `.env` in the repository), which
is the state the first two tests need, and a per-test override is the only way to have
both without an env var leaking across tests.
"""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from pigrocrm.core.config import Settings, get_settings
from pigrocrm.core.gmail.models import GmailMessage, GmailMessageLink, GoogleAccount
from pigrocrm.core.gmail.schemas import REQUESTED_SCOPES

# Deliberately literal rather than imported from `fakes.gmail_fixtures`: that package
# lives under `packages/core/tests`, which is only on `sys.path` once a test from that
# root has been collected -- so importing it here would make this file pass in a full
# run and fail when run on its own.
TOKEN_KEY_B64 = "a2tra2tra2tra2tra2tra2tra2tra2tra2tra2tra2s="  # 32 bytes of "k"
CLIENT_SECRET = "il-segreto-del-client"


def _configured() -> Settings:
    """The suite's own settings with the four Gmail variables filled in.

    A copy rather than a fresh `Settings(...)`: `jwt_secret` is what the login cookie
    was signed with, and an override that invented its own would make every
    authenticated request in this file answer 401 "Sessione scaduta" -- a failure that
    looks like a broken auth dependency and is nothing of the sort.
    """
    return _base_settings().model_copy(
        update={
            "google_client_id": "cid.apps.googleusercontent.com",
            "google_client_secret": CLIENT_SECRET,
            "google_token_key": TOKEN_KEY_B64,
            "public_url": "https://crm.example.it",
        }
    )


def _base_settings() -> Settings:
    """The settings the `client` fixture already installed, not `get_settings()`.

    The distinction is the one the docstring above turns on. `client` overrides
    `get_settings` with `Settings(_env_file=None)`, so that whether Gmail is configured
    is something these tests declare rather than something they inherit from whatever
    `.env` the developer wrote for their own instance. Copying from `get_settings()`
    here would reintroduce the ambient file through the back door -- and worse, its
    `jwt_secret` would differ from the one the login cookie was signed with, so every
    authenticated request in this file would answer 401 "Sessione scaduta": a failure
    that looks like a broken auth dependency and is nothing of the sort.
    """
    return Settings(_env_file=None)  # type: ignore[call-arg]


@pytest.fixture
def gmail_ready(client: TestClient) -> Iterator[TestClient]:
    """The same client, with Gmail configured. Yields so the override is removed even
    when the test fails: `client` builds a fresh app per test, but leaving global state
    behind in a fixture is how a suite acquires an ordering dependency."""
    client.app.dependency_overrides[get_settings] = _configured
    yield client
    client.app.dependency_overrides.pop(get_settings, None)


def _connected_account(session: Session, user_id: Any, **overrides: Any) -> GoogleAccount:
    """A row in the state a user reaches after consenting. The token bytes are opaque
    on purpose: nothing in this file decrypts them, and every assertion below is about
    them *not* appearing on the wire."""
    account = GoogleAccount(
        user_id=user_id,
        google_sub="sub-123",
        email_address="io@example.it",
        refresh_token_ciphertext=b"\x01\x02ciphertext",
        refresh_token_nonce=b"\x03\x04nonce",
        scopes_granted=list(REQUESTED_SCOPES),
        **{"status": "active", **overrides},
    )
    session.add(account)
    session.flush()
    return account


# --- absent, not broken --------------------------------------------------------------


def test_an_unconfigured_installation_answers_conflict_not_five_hundred(
    logged_in: TestClient,
) -> None:
    """Absent, not broken. Every endpoint that would have to talk to Google says the
    same sentence, and none of them raises."""
    calls: list[tuple[str, str, dict[str, Any] | None]] = [
        ("GET", "/api/gmail/oauth/start", None),
        ("POST", "/api/gmail/sync", None),
        (
            "POST",
            "/api/gmail/backfill",
            {"entity_type": "customer", "entity_id": str(uuid4())},
        ),
    ]
    for method, path, body in calls:
        response = logged_in.request(method, path, json=body)
        assert response.status_code == 409, f"{path}: {response.text}"
        assert "non è configurato" in response.json()["detail"], path


def test_the_account_endpoint_is_readable_even_with_gmail_off(logged_in: TestClient) -> None:
    """The settings page has to be able to render "non configurato" without a failed
    query in the console: a query in `isError` renders QueryErrorBanner, and this is not
    an error."""
    response = logged_in.get("/api/gmail/account")
    assert response.status_code == 200, response.text
    assert response.json() == {
        "account": None,
        "banner": None,
        "banner_text": None,
        "missing_scopes": [],
        # Not the same fact as "no mailbox connected", and the settings page renders two
        # different screens for the two.
        "configured": False,
    }


def test_a_configured_installation_with_no_mailbox_says_so_differently(
    logged_in: TestClient, gmail_ready: TestClient
) -> None:
    """The other half of the same response, and the reason `configured` exists at all.
    "Gmail non esiste qui" and "non hai ancora collegato la casella" both answer with no
    account, and the second one has a button under it."""
    payload = logged_in.get("/api/gmail/account").json()
    assert payload["configured"] is True
    assert payload["account"] is None
    assert payload["banner"] is None


# --- who may call it -----------------------------------------------------------------


def test_every_gmail_endpoint_requires_authentication(client: TestClient) -> None:
    for method, path in [
        ("GET", "/api/gmail/account"),
        ("GET", "/api/gmail/oauth/start"),
        ("GET", "/api/gmail/oauth/callback"),
        ("POST", "/api/gmail/sync"),
        ("POST", "/api/gmail/backfill"),
        ("GET", "/api/gmail/messages"),
        ("DELETE", "/api/gmail/account"),
        ("PATCH", "/api/gmail/account"),
    ]:
        assert client.request(method, path).status_code == 401, path


def test_a_readonly_actor_cannot_sync(readonly_client: TestClient, gmail_ready: TestClient) -> None:
    """403, and specifically not the 409 an unconfigured installation gives: the two
    refusals are different facts, and a role check that only appeared to work because
    Gmail was switched off would be no check at all."""
    assert readonly_client is gmail_ready
    response = readonly_client.post("/api/gmail/sync")
    assert response.status_code == 403, response.text


# --- the OAuth round trip ------------------------------------------------------------


def test_the_start_endpoint_hands_the_browser_to_google_without_the_client_secret(
    logged_in: TestClient, gmail_ready: TestClient, api_session: Session
) -> None:
    """The one response on this surface built out of the OAuth client configuration, so
    the one place a credential could reach a browser. `client_secret` belongs to the
    back-channel token exchange and to nothing else; a copy-paste of the token request's
    parameter set into the authorisation URL is the classic way it escapes."""
    response = logged_in.get("/api/gmail/oauth/start", follow_redirects=False)
    assert response.status_code == 307, response.text

    location = response.headers["location"]
    assert location.startswith("https://accounts.google.com/")
    assert CLIENT_SECRET not in location
    assert "code_challenge_method=S256" in location
    # The PKCE verifier stays server-side; only its S256 digest travels.
    verifiers = api_session.execute(text("select code_verifier from google_oauth_states")).scalars()
    for verifier in verifiers:
        assert verifier not in location


def test_the_callback_never_renders_the_upstream_error_to_the_browser(
    logged_in: TestClient, gmail_ready: TestClient
) -> None:
    """Google appends `?error=access_denied` when the user declines. The redirect
    carries a code the SPA turns into Italian; it does not echo Google's own text, which
    is English and occasionally contains the client id."""
    response = logged_in.get(
        "/api/gmail/oauth/callback?error=access_denied", follow_redirects=False
    )
    assert response.status_code == 307
    location = response.headers["location"]
    assert location.startswith("/app/settings/gmail?esito=")
    assert "access_denied" not in location


def test_a_callback_with_a_state_nobody_issued_never_reports_a_connection(
    logged_in: TestClient, gmail_ready: TestClient
) -> None:
    """A forged or replayed `state` is refused before the token exchange -- and the
    proof it never got there is that this test runs at all: the repository-root socket
    guard fails any test that opens one, and the exchange is the first thing `complete`
    would do after redeeming the state.

    It comes back as `esito=errore` and not `esito=collegato`, and not as a problem
    document either: this is a browser navigation, and the three outcome codes are the
    whole vocabulary the settings page renders. `errore` and `negato` stay distinct
    because "Google said no" and "this authorisation is not valid here" are different
    things to read after pressing Consenti.
    """
    response = logged_in.get(
        "/api/gmail/oauth/callback?code=abc&state=mai-emesso", follow_redirects=False
    )
    assert response.status_code == 307, response.text
    assert response.headers["location"] == "/app/settings/gmail?esito=errore"


# --- reading what is already stored ---------------------------------------------------


def test_the_messages_endpoint_returns_what_is_filed_against_an_entity(
    logged_in: TestClient, api_session: Session, admin_user: Any
) -> None:
    """Reads the CRM's own copy. No credential is needed and none is used -- this route
    is the reason an agent, and a user with Gmail disconnected, can still read the
    history."""
    account = _connected_account(api_session, admin_user.id)
    customer_id = uuid4()
    message = GmailMessage(
        google_account_id=account.id,
        gmail_message_id="m-1",
        gmail_thread_id="t-1",
        direction="inbound",
        from_address="ada@acme.it",
        to_addresses=["io@example.it"],
        cc_addresses=[],
        subject="Preventivo",
        snippet="Ciao,",
        internal_date=datetime.now(UTC) - timedelta(days=1),
        body_text="Ciao, ti mando il preventivo.",
        attachments=[],
    )
    api_session.add(message)
    api_session.flush()
    api_session.add(
        GmailMessageLink(gmail_message_id=message.id, entity_type="customer", entity_id=customer_id)
    )
    api_session.flush()

    response = logged_in.get(
        "/api/gmail/messages", params={"entity_type": "customer", "entity_id": str(customer_id)}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert [row["gmail_message_id"] for row in body] == ["m-1"]
    assert body[0]["subject"] == "Preventivo"
    # The stored copy is the point of the route; the credential that fetched it is not.
    assert "ciphertext" not in response.text
    assert "nonce" not in response.text


def test_the_messages_endpoint_bounds_its_limit(logged_in: TestClient) -> None:
    response = logged_in.get(
        "/api/gmail/messages",
        params={
            "entity_type": "customer",
            "entity_id": "00000000-0000-7000-8000-000000000000",
            "limit": 5_000,
        },
    )
    assert response.status_code == 422, response.text


def test_the_messages_endpoint_refuses_an_entity_type_it_does_not_file_against(
    logged_in: TestClient,
) -> None:
    """`gmail_message_links.entity_type` is written by `links.py` from a closed set. An
    endpoint that accepted any string would answer 200 with an empty list for a typo,
    which reads as "no correspondence" rather than "wrong question"."""
    response = logged_in.get(
        "/api/gmail/messages",
        params={"entity_type": "invoice", "entity_id": str(uuid4())},
    )
    assert response.status_code == 422, response.text


# --- the account, as the settings page sees it ---------------------------------------


def test_a_revoked_credential_is_reported_with_its_own_sentence_and_no_token(
    logged_in: TestClient, gmail_ready: TestClient, api_session: Session, admin_user: Any
) -> None:
    """B1-12 made `revoked` and `disconnected` two different states. The settings page
    renders whichever `banner` says, so the adapter has to carry the distinction out
    intact -- and carry nothing else out with it."""
    _connected_account(api_session, admin_user.id, status="revoked", last_error="revocato")

    response = logged_in.get("/api/gmail/account")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["banner"] == "revoked"
    assert "revocato" in payload["banner_text"]
    assert payload["account"]["email_address"] == "io@example.it"
    assert "refresh_token" not in response.text
    assert "ciphertext" not in response.text


def test_a_mailbox_the_user_disconnected_raises_no_banner(
    logged_in: TestClient, gmail_ready: TestClient, api_session: Session, admin_user: Any
) -> None:
    """The other half of the same B1-12 decision, and the one that would silently
    regress: `disconnected` used to be stored as `revoked`, which told somebody who had
    just unhooked their own mailbox that Google had cut them off."""
    _connected_account(
        api_session,
        admin_user.id,
        status="disconnected",
        disconnected_at=datetime.now(UTC),
    )

    payload = logged_in.get("/api/gmail/account").json()
    assert payload["banner"] is None
    assert payload["banner_text"] is None
    assert payload["account"]["status"] == "disconnected"


def test_disconnecting_keeps_the_stored_messages_unless_the_caller_asks(
    logged_in: TestClient, gmail_ready: TestClient, api_session: Session, admin_user: Any
) -> None:
    """`elimina_messaggi` defaults to false and is really plumbed through. Deleting a
    customer's correspondence because a token expired would be a disaster, so the
    default has to be the safe one and the flag has to be the only way to the other."""
    account = _connected_account(api_session, admin_user.id)
    customer_id = uuid4()
    message = GmailMessage(
        google_account_id=account.id,
        gmail_message_id="m-2",
        gmail_thread_id="t-2",
        direction="outbound",
        from_address="io@example.it",
        to_addresses=["ada@acme.it"],
        cc_addresses=[],
        subject="Re: Preventivo",
        snippet="Eccolo",
        internal_date=datetime.now(UTC),
        body_text="Eccolo.",
        attachments=[],
    )
    api_session.add(message)
    api_session.flush()
    api_session.add(
        GmailMessageLink(gmail_message_id=message.id, entity_type="customer", entity_id=customer_id)
    )
    api_session.flush()
    params = {"entity_type": "customer", "entity_id": str(customer_id)}

    assert logged_in.delete("/api/gmail/account").status_code == 204
    assert len(logged_in.get("/api/gmail/messages", params=params).json()) == 1

    assert logged_in.delete("/api/gmail/account?elimina_messaggi=true").status_code == 204
    assert logged_in.get("/api/gmail/messages", params=params).json() == []


def test_the_body_store_can_be_switched_off_without_a_healthy_credential(
    logged_in: TestClient, gmail_ready: TestClient, api_session: Session, admin_user: Any
) -> None:
    """The moment a user most wants the CRM to stop keeping their mail is the moment the
    credential is broken. `set_store_bodies` goes through `_present`, not `usable`, and
    the route must not add a gate of its own on top."""
    _connected_account(api_session, admin_user.id, status="revoked")

    response = logged_in.patch("/api/gmail/account", json={"gmail_store_bodies": False})
    assert response.status_code == 200, response.text
    assert response.json()["gmail_store_bodies"] is False


# --- the surface as documented --------------------------------------------------------


def _gmail_operations(schema: dict[str, Any]) -> list[tuple[str, str, dict[str, Any]]]:
    found = []
    for path, operations in schema["paths"].items():
        if not path.startswith("/api/gmail"):
            continue
        for method, operation in operations.items():
            found.append((path, method, operation))
    return found


def test_no_endpoint_accepts_a_gmail_search_string(client: TestClient) -> None:
    """Spec 12: no surface in this slice accepts a Gmail search expression. Checked
    against the generated OpenAPI document, so a future parameter cannot slip in."""
    schema = client.get("/openapi.json").json()
    operations = _gmail_operations(schema)
    # A scan over an empty list is a scan that cannot fail.
    assert len(operations) >= 8, operations
    for path, _method, operation in operations:
        names = {parameter["name"] for parameter in operation.get("parameters", [])}
        assert not names & {"q", "query", "search", "ricerca"}, (
            f"{path} espone un parametro di ricerca Gmail"
        )


def test_no_gmail_schema_on_the_wire_carries_a_credential(client: TestClient) -> None:
    """Structural, over every schema the Gmail operations reference. `GoogleAccount` and
    `GoogleOAuthState` both hold secrets as columns, and the read schemas exist
    precisely so that no route can return the row; this fails the moment somebody
    returns the ORM object or widens `GoogleAccountRead`."""
    schema = client.get("/openapi.json").json()
    components = schema["components"]["schemas"]
    forbidden = {
        "refresh_token",
        "refresh_token_ciphertext",
        "refresh_token_nonce",
        "code_verifier",
        "access_token",
        "client_secret",
        "google_token_key",
    }

    referenced: set[str] = set()

    def collect(node: Any) -> None:
        if isinstance(node, dict):
            ref = node.get("$ref")
            if isinstance(ref, str) and ref.startswith("#/components/schemas/"):
                name = ref.rsplit("/", 1)[1]
                if name not in referenced:
                    referenced.add(name)
                    collect(components.get(name, {}))
            for value in node.values():
                collect(value)
        elif isinstance(node, list):
            for value in node:
                collect(value)

    for _path, _method, operation in _gmail_operations(schema):
        collect(operation)

    assert {"GmailHealth", "GmailMessageRead", "SyncReport"} <= referenced, referenced
    for name in referenced:
        properties = components.get(name, {}).get("properties", {})
        assert not set(properties) & forbidden, f"{name} espone una credenziale"
