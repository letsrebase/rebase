"""The REST surface of the Slack connection, over real HTTP (REB-739), against `FakeSlack`.

Two claims no service test can make, because they are properties of the adapter: an
installation with no Slack app answers «assente», not «rotto», with a 200; and the consent
comes back to a page of the SPA with one of four outcome codes and never to a problem
document. The space half is `test_google_shared_client_api.py`'s: a space borrows the root's
Slack app, Slack sends the browser to the root's callback with the slug in the `state`, the
root hands it to the space and the space completes it against its own database.

Slack's Web API is `FakeSlack`, installed through the `slack_client` dependency; nothing
here opens a socket.
"""

import base64
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, text

# The same fakes the core suite uses (`test_google_shared_client_api.py` does likewise).
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "packages" / "core" / "tests"))
from fakes.fake_slack import (  # noqa: E402
    BOT_TOKEN,
    CLIENT_ID,
    CLIENT_SECRET,
    SLACK_URL,
    FakeSlack,
)

from pigrocrm.core.config import Settings, decode_slack_token_key, get_settings  # noqa: E402
from pigrocrm.core.db.sidecar import drop_database  # noqa: E402
from pigrocrm.core.gmail.crypto import unseal  # noqa: E402
from pigrocrm.core.slack.client import SlackClient  # noqa: E402
from pigrocrm.core.slack.schemas import SLACK_BOT_SCOPES  # noqa: E402
from pigrocrm.core.tenants import ensure_tenants_database, space_base_settings  # noqa: E402
from pigrocrm.core.tenants.database import tenant_database_name, tenant_database_url  # noqa: E402
from pigrocrm_api.deps import reset_session_factories  # noqa: E402
from pigrocrm_api.main import create_app  # noqa: E402
from pigrocrm_api.ratelimit import reset_rate_limit  # noqa: E402
from pigrocrm_api.routers.slack import slack_client  # noqa: E402

SLUG = "studio-slack"
SLACK_KEY_B64 = base64.b64encode(b"s" * 32).decode()
ROOT_CALLBACK = "https://pigro.test/api/slack/oauth/callback"


def _configured() -> Settings:
    """The suite's settings with the Slack app configured, a copy of what `client`
    installed so the login cookie still verifies (`test_drive_api._configured`)."""
    return Settings(_env_file=None).model_copy(  # type: ignore[call-arg]
        update={
            "slack_client_id": CLIENT_ID,
            "slack_client_secret": CLIENT_SECRET,
            "slack_token_key": SLACK_KEY_B64,
            "slack_url": SLACK_URL,
            "public_url": "https://crm.example.it",
        }
    )


@pytest.fixture
def slack() -> FakeSlack:
    fake = FakeSlack()
    fake.add_member("admin@pigro.it", "UADMIN")
    fake.add_channel("C1", "promemoria")
    return fake


@pytest.fixture
def slack_ready(client: TestClient, slack: FakeSlack) -> Iterator[TestClient]:
    from pigrocrm_api.deps import get_session  # noqa: F401  (kept installed by `client`)

    client.app.dependency_overrides[get_settings] = _configured
    client.app.dependency_overrides[slack_client] = lambda: SlackClient(
        SLACK_URL, http=slack.http, sleep=lambda _: None
    )
    yield client
    client.app.dependency_overrides.pop(get_settings, None)
    client.app.dependency_overrides.pop(slack_client, None)


def _consent(client: TestClient) -> str:
    started = client.get("/api/slack/oauth/start", follow_redirects=False)
    assert started.status_code == 307, started.text
    location = started.headers["location"]
    assert location.startswith(f"{SLACK_URL}/oauth/v2/authorize?")
    assert CLIENT_SECRET not in location
    return parse_qs(urlparse(location).query)["state"][0]


def _connect(client: TestClient) -> None:
    state = _consent(client)
    done = client.get(
        "/api/slack/oauth/callback",
        params={"code": "good-code", "state": state},
        follow_redirects=False,
    )
    assert done.headers["location"] == "/app/settings/slack?esito=collegato", done.text


# --- absent, not broken -------------------------------------------------------------------


def test_the_account_is_readable_with_slack_off_and_says_so(logged_in: TestClient) -> None:
    response = logged_in.get("/api/slack/account")
    assert response.status_code == 200, response.text
    assert response.json() == {"configured": False, "workspace": None, "members": []}
    assert logged_in.get("/api/slack/me").json() == {
        "configured": False,
        "connected": False,
        "team_name": None,
        "matched": False,
    }


def test_starting_without_a_slack_app_is_a_sentence(logged_in: TestClient) -> None:
    response = logged_in.get("/api/slack/oauth/start", follow_redirects=False)
    assert response.status_code == 409
    assert "Slack non è configurato" in response.json()["detail"]


# --- the flow -------------------------------------------------------------------------------


def test_an_admin_connects_sees_who_was_matched_and_disconnects(
    slack_ready: TestClient, logged_in: TestClient, slack: FakeSlack
) -> None:
    _connect(logged_in)

    account = logged_in.get("/api/slack/account").json()
    assert account["configured"] is True
    assert account["workspace"]["team_name"] == "Acme Test"
    assert account["workspace"]["status"] == "active"
    assert [(m["email"], m["state"]) for m in account["members"]] == [("admin@pigro.it", "matched")]
    # Nothing of the token, in any form, in anything the page reads.
    body = logged_in.get("/api/slack/account").text
    assert BOT_TOKEN not in body
    assert "ciphertext" not in body and "nonce" not in body

    assert logged_in.get("/api/slack/me").json() == {
        "configured": True,
        "connected": True,
        "team_name": "Acme Test",
        "matched": True,
    }

    assert logged_in.delete("/api/slack/account").status_code == 204
    after = logged_in.get("/api/slack/account").json()
    assert after["workspace"]["status"] == "disconnected"
    assert after["members"] == []
    assert logged_in.delete("/api/slack/account").status_code == 409


def test_a_replayed_callback_ends_on_the_page_with_an_error_code(
    slack_ready: TestClient, logged_in: TestClient
) -> None:
    state = _consent(logged_in)
    params = {"code": "good-code", "state": state}
    first = logged_in.get("/api/slack/oauth/callback", params=params, follow_redirects=False)
    assert first.headers["location"] == "/app/settings/slack?esito=collegato"
    again = logged_in.get("/api/slack/oauth/callback", params=params, follow_redirects=False)
    assert again.status_code == 307
    assert again.headers["location"] == "/app/settings/slack?esito=errore"


def test_a_refusal_on_slacks_side_is_a_code_never_slacks_own_error(
    slack_ready: TestClient, logged_in: TestClient
) -> None:
    state = _consent(logged_in)
    denied = logged_in.get(
        "/api/slack/oauth/callback",
        params={"error": "access_denied", "state": state},
        follow_redirects=False,
    )
    assert denied.headers["location"] == "/app/settings/slack?esito=negato"


def test_a_callback_with_no_session_lands_on_the_page_with_a_code(
    slack_ready: TestClient, logged_in: TestClient
) -> None:
    state = _consent(logged_in)
    logged_in.cookies.clear()
    response = logged_in.get(
        "/api/slack/oauth/callback",
        params={"code": "good-code", "state": state},
        follow_redirects=False,
    )
    assert response.headers["location"] == "/app/settings/slack?esito=sessione"


def _collaborator(admin: TestClient) -> TestClient:
    """A genuinely independent session: `collaborator_client` shares `logged_in`'s cookie
    jar, so the two fixtures cannot be used in one test (`test_drive_api._second_actor`)."""
    created = admin.post(
        "/api/users",
        json={
            "email": "collab@pigro.it",
            "password": "supersegreta1",
            "nome": "Collab",
            "ruolo": "collaboratore",
        },
    )
    assert created.status_code == 201, created.text
    other = TestClient(admin.app, base_url="https://testserver")
    login = other.post(
        "/api/auth/login", json={"email": "collab@pigro.it", "password": "supersegreta1"}
    )
    assert login.status_code == 200, login.text
    return other


def test_only_an_admin_manages_slack_and_anyone_reads_their_own_state(
    slack_ready: TestClient, logged_in: TestClient
) -> None:
    _connect(logged_in)
    member = _collaborator(logged_in)
    for method, path in (
        ("get", "/api/slack/account"),
        ("get", "/api/slack/oauth/start"),
        ("delete", "/api/slack/account"),
        ("post", "/api/slack/account/rematch"),
        ("get", "/api/slack/channels"),
        ("put", "/api/slack/channel"),
    ):
        response = getattr(member, method)(path, follow_redirects=False)
        assert response.status_code in (403, 422), (path, response.text)
    refused = member.put("/api/slack/channel", json={"channel_id": None})
    assert refused.status_code == 403
    mine = member.get("/api/slack/me")
    assert mine.status_code == 200
    assert mine.json() == {
        "configured": True,
        "connected": True,
        "team_name": "Acme Test",
        "matched": False,
    }


def test_a_mismatch_is_fixed_by_hand_and_undone(
    slack_ready: TestClient, logged_in: TestClient, slack: FakeSlack
) -> None:
    created = logged_in.post(
        "/api/users",
        json={
            "email": "ada@studio.example",
            "password": "supersegreta1",
            "nome": "Ada",
            "ruolo": "collaboratore",
        },
    )
    ada_id = created.json()["id"]
    slack.add_member("ada.bianchi@acme.example", "UADA")
    _connect(logged_in)
    members = {m["email"]: m for m in logged_in.get("/api/slack/account").json()["members"]}
    assert members["ada@studio.example"]["state"] == "unmatched"

    fixed = logged_in.put(
        f"/api/slack/users/{ada_id}/link", json={"email": "ada.bianchi@acme.example"}
    )
    assert fixed.status_code == 200, fixed.text
    members = {m["email"]: m for m in fixed.json()["members"]}
    assert members["ada@studio.example"]["state"] == "manual"
    assert members["ada@studio.example"]["slack_email"] == "ada.bianchi@acme.example"

    nobody = logged_in.put(f"/api/slack/users/{ada_id}/link", json={"email": "no@acme.example"})
    assert nobody.status_code == 409
    assert "nessuno nel workspace Acme Test" in nobody.json()["detail"]

    undone = logged_in.delete(f"/api/slack/users/{ada_id}/link")
    assert undone.status_code == 200
    members = {m["email"]: m for m in undone.json()["members"]}
    assert members["ada@studio.example"]["state"] == "unmatched"

    again = logged_in.post("/api/slack/account/rematch")
    assert again.status_code == 200


def test_the_channel_for_space_wide_reminders_is_picked_and_cleared(
    slack_ready: TestClient, logged_in: TestClient
) -> None:
    _connect(logged_in)
    channels = logged_in.get("/api/slack/channels").json()
    assert channels == [{"id": "C1", "name": "promemoria", "is_private": False, "is_member": False}]

    chosen = logged_in.put("/api/slack/channel", json={"channel_id": "C1"})
    assert chosen.json()["workspace"]["channel_name"] == "promemoria"
    missing = logged_in.put("/api/slack/channel", json={"channel_id": "CNOPE"})
    assert missing.status_code == 409
    junk = logged_in.put("/api/slack/channel", json={"channel_id": "c1; drop"})
    assert junk.status_code == 422
    cleared = logged_in.put("/api/slack/channel", json={"channel_id": None})
    assert cleared.json()["workspace"]["channel_id"] is None


def test_a_token_slack_revokes_turns_the_page_into_a_reconnect(
    slack_ready: TestClient, logged_in: TestClient, slack: FakeSlack
) -> None:
    _connect(logged_in)
    slack.revoked = True
    failed = logged_in.post("/api/slack/account/rematch")
    assert failed.status_code == 409
    assert "ricollega Slack" in failed.json()["detail"]
    workspace = logged_in.get("/api/slack/account").json()["workspace"]
    assert workspace["status"] == "revoked"
    assert "Acme Test" in workspace["last_error"]


def test_the_openapi_document_has_no_route_that_takes_or_returns_a_token() -> None:
    schema = create_app().openapi()
    slack_paths = {path: spec for path, spec in schema["paths"].items() if "/slack/" in path}
    assert slack_paths
    text_of_schemas = str(
        {name: s for name, s in schema["components"]["schemas"].items() if name.startswith("Slack")}
    )
    for forbidden in ("token", "secret", "ciphertext", "nonce"):
        assert forbidden not in text_of_schemas.lower().replace("slackmember", "")
    assert set(SLACK_BOT_SCOPES) >= {"chat:write", "im:write"}


# --- a space borrows the root's app ------------------------------------------------------


def _root_settings(api_engine: Engine) -> Settings:
    return Settings(
        database_url=api_engine.url.render_as_string(hide_password=False),
        jwt_secret="test-secret-for-the-api-test-suite-only",
        cookie_secure=True,
        public_url="https://pigro.test",
        hub_url="http://127.0.0.1:9",
        slack_client_id=CLIENT_ID,
        slack_client_secret=CLIENT_SECRET,
        slack_token_key=SLACK_KEY_B64,
        slack_url=SLACK_URL,
        _env_file=None,  # type: ignore[call-arg]
    )


@contextmanager
def _serving(settings: Settings, fake: FakeSlack) -> Iterator[TestClient]:
    """`test_google_shared_client_api._serving`, with Slack's fake installed."""
    reset_session_factories()
    get_settings.cache_clear()
    reset_rate_limit()
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[slack_client] = lambda: SlackClient(
        SLACK_URL, http=fake.http, sleep=lambda _: None
    )
    registry = ensure_tenants_database(settings)
    try:
        with TestClient(app, base_url="https://testserver") as client:
            yield client
    finally:
        with registry.begin() as connection:
            connection.execute(text("DELETE FROM tenants"))
        registry.dispose()
        reset_session_factories()
        drop_database(settings, tenant_database_url(settings, tenant_database_name(SLUG)))
        get_settings.cache_clear()


def test_a_space_connects_slack_through_the_root_callback(api_engine: Engine) -> None:
    fake = FakeSlack()
    fake.add_member("ada@studio.it", "UADA")
    settings = _root_settings(api_engine)
    with _serving(settings, fake) as client:
        created = client.post(
            "/api/tenants/", json={"slug": SLUG, "nome": "Ada", "email": "ada@studio.it"}
        )
        assert created.status_code == 201, created.text
        assert client.get(f"/{SLUG}/api/slack/account").json()["configured"] is True

        started = client.get(f"/{SLUG}/api/slack/oauth/start", follow_redirects=False)
        query = parse_qs(urlparse(started.headers["location"]).query)
        assert query["redirect_uri"] == [ROOT_CALLBACK]
        state = query["state"][0]
        assert state.startswith(f"{SLUG}.")

        # Slack comes back to the root, where this browser has no session of the root's.
        back = client.get(
            "/api/slack/oauth/callback",
            params={"code": "good-code", "state": state},
            follow_redirects=False,
        )
        relayed = urlparse(back.headers["location"])
        assert relayed.path == f"/{SLUG}/api/slack/oauth/callback"
        assert parse_qs(relayed.query) == {"code": ["good-code"], "state": [state]}
        assert fake.calls("oauth.v2.access") == []

        done = client.get(back.headers["location"], follow_redirects=False)
        assert done.headers["location"] == f"/{SLUG}/app/settings/slack?esito=collegato"
        (exchange,) = fake.calls("oauth.v2.access")
        assert exchange.form["redirect_uri"] == ROOT_CALLBACK

        account = client.get(f"/{SLUG}/api/slack/account").json()
        assert account["workspace"]["team_name"] == "Acme Test"
        assert [(m["email"], m["state"]) for m in account["members"]] == [
            ("ada@studio.it", "matched")
        ]

        # Sealed with the space's own derived key, in the space's database only.
        space_key = decode_slack_token_key(space_base_settings(settings, SLUG))
        engine = create_engine(tenant_database_url(settings, tenant_database_name(SLUG)))
        try:
            with engine.connect() as connection:
                row = connection.execute(
                    text("select bot_token_ciphertext, bot_token_nonce from slack_workspaces")
                ).one()
        finally:
            engine.dispose()
        assert unseal(bytes(row[0]), bytes(row[1]), space_key) == BOT_TOKEN


def test_a_state_carried_to_a_space_that_never_issued_it_lands_no_token(
    api_engine: Engine,
) -> None:
    fake = FakeSlack()
    settings = _root_settings(api_engine)
    with _serving(settings, fake) as client:
        client.post("/api/tenants/", json={"slug": SLUG, "nome": "Ada", "email": "ada@studio.it"})
        state = parse_qs(
            urlparse(
                client.get(f"/{SLUG}/api/slack/oauth/start", follow_redirects=False).headers[
                    "location"
                ]
            ).query
        )["state"][0]
        swapped = f"altro-spazio.{state.partition('.')[2]}"
        back = client.get(
            "/api/slack/oauth/callback",
            params={"code": "good-code", "state": swapped},
            follow_redirects=False,
        )
        assert urlparse(back.headers["location"]).path == "/altro-spazio/api/slack/oauth/callback"
        assert fake.calls("oauth.v2.access") == []
        # Handed directly to the space with a prefix that is not its own: refused by the state.
        direct = client.get(
            f"/{SLUG}/api/slack/oauth/callback",
            params={"code": "good-code", "state": swapped},
            follow_redirects=False,
        )
        assert direct.headers["location"] == f"/{SLUG}/app/settings/slack?esito=errore"
        assert fake.calls("oauth.v2.access") == []
