"""A space connects Gmail through the root's Google client, over real HTTP (REB-394).

`PIGROCRM_GOOGLE_SHARED_CLIENT` on: a space signs up, starts a consent, Google sends the
browser back to the root's callback, the root hands it to the space's callback and the
space completes it against its own database. The spaces are real databases on the
test container, as in `test_tenants_api.py`, because which database a request opens is
the whole question. Google's token endpoint is `FakeGmail`, installed as the process's
token client for the root's client id: nothing here opens a socket.
"""

import base64
import json
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, text

# The same fakes the core suite uses, as `test_documents_api.py` imports them.
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "packages" / "core" / "tests"))
from fakes.fake_gmail import FakeGmail  # noqa: E402

import pigrocrm_api.routers.gmail as gmail_router  # noqa: E402
from pigrocrm.core.config import Settings, decode_google_token_key, get_settings  # noqa: E402
from pigrocrm.core.db.sidecar import drop_database  # noqa: E402
from pigrocrm.core.gmail.crypto import unseal  # noqa: E402
from pigrocrm.core.gmail.schemas import REQUESTED_SCOPES  # noqa: E402
from pigrocrm.core.gmail.tokens import GoogleTokenClient  # noqa: E402
from pigrocrm.core.gmail.transport import GmailTransport  # noqa: E402
from pigrocrm.core.tenants import ensure_tenants_database, space_base_settings  # noqa: E402
from pigrocrm.core.tenants.database import tenant_database_name, tenant_database_url  # noqa: E402
from pigrocrm_api.deps import reset_session_factories  # noqa: E402
from pigrocrm_api.main import create_app  # noqa: E402
from pigrocrm_api.ratelimit import reset_rate_limit  # noqa: E402

SLUG = "studio-condiviso"
OTHER = "altro-condiviso"
CLIENT_ID = "cid.apps.googleusercontent.com"
CLIENT_SECRET = "il-segreto-del-client"
TOKEN_KEY_B64 = base64.b64encode(b"k" * 32).decode()
ROOT_CALLBACK = "https://pigro.test/api/gmail/oauth/callback"
REFRESH = "1//0gSharedClientRefresh"


def _settings(api_engine: Engine, *, shared: bool) -> Settings:
    return Settings(
        database_url=api_engine.url.render_as_string(hide_password=False),
        jwt_secret="test-secret-for-the-api-test-suite-only",
        cookie_secure=True,
        public_url="https://pigro.test",
        hub_url="http://127.0.0.1:9",
        google_client_id=CLIENT_ID,
        google_client_secret=CLIENT_SECRET,
        google_token_key=TOKEN_KEY_B64,
        google_shared_client=shared,
        _env_file=None,  # type: ignore[call-arg]
    )


@contextmanager
def _serving(settings: Settings) -> Iterator[TestClient]:
    """`test_tenants_api._serving`, dropping both spaces this file creates."""
    reset_session_factories()
    get_settings.cache_clear()
    reset_rate_limit()
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: settings
    registry = ensure_tenants_database(settings)
    try:
        with TestClient(app, base_url="https://testserver") as client:
            yield client
    finally:
        with registry.begin() as connection:
            connection.execute(text("DELETE FROM tenants"))
        registry.dispose()
        reset_session_factories()
        for slug in (SLUG, OTHER):
            drop_database(settings, tenant_database_url(settings, tenant_database_name(slug)))
        get_settings.cache_clear()


def _id_token(sub: str, email: str) -> str:
    claims = base64.urlsafe_b64encode(json.dumps({"sub": sub, "email": email}).encode())
    return f"header.{claims.decode().rstrip('=')}.signature"


@pytest.fixture
def google(monkeypatch: pytest.MonkeyPatch) -> FakeGmail:
    """Google's token endpoint, behind the one token client the routers keep for the
    root's client id: the one every space borrowing it shares."""
    fake = FakeGmail(granted_scopes=REQUESTED_SCOPES)
    fake.id_token = _id_token("104729", "ada@studio.it")
    fake.refresh_token = REFRESH
    client = GoogleTokenClient(
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET,
        transport=GmailTransport(http=fake, sleep=lambda _: None),
    )
    monkeypatch.setitem(gmail_router._token_clients, (f"{SLUG}.", CLIENT_ID), client)
    return fake


@pytest.fixture
def shared(api_engine: Engine) -> Iterator[TestClient]:
    with _serving(_settings(api_engine, shared=True)) as client:
        yield client


def _sign_up(client: TestClient, slug: str, email: str) -> None:
    created = client.post("/api/tenants/", json={"slug": slug, "nome": "Ada", "email": email})
    assert created.status_code == 201, created.text


def _start(client: TestClient, slug: str) -> dict[str, list[str]]:
    started = client.get(f"/{slug}/api/gmail/oauth/start", follow_redirects=False)
    assert started.status_code == 307, started.text
    location = started.headers["location"]
    assert location.startswith("https://accounts.google.com/")
    assert CLIENT_SECRET not in location
    return parse_qs(urlparse(location).query)


def _exchanges(fake: FakeGmail) -> list[dict[str, list[str]]]:
    bodies = [
        parse_qs((request.body or b"").decode())
        for request in fake.requests
        if request.host == "oauth2.googleapis.com"
    ]
    return [body for body in bodies if body.get("grant_type") == ["authorization_code"]]


def test_a_space_connects_gmail_through_the_root_callback(
    shared: TestClient, google: FakeGmail, api_engine: Engine
) -> None:
    _sign_up(shared, SLUG, "ada@studio.it")
    account = shared.get(f"/{SLUG}/api/gmail/account").json()
    assert account["configured"] is True
    assert account["account"] is None

    query = _start(shared, SLUG)
    assert query["client_id"] == [CLIENT_ID]
    assert query["redirect_uri"] == [ROOT_CALLBACK]
    state = query["state"][0]
    assert state.startswith(f"{SLUG}.")

    # Google comes back to the root, where this browser has no session: the root hands
    # it to the space, parameters untouched.
    back = shared.get(
        "/api/gmail/oauth/callback",
        params={"code": "4/0A-code", "state": state},
        follow_redirects=False,
    )
    assert back.status_code == 307, back.text
    relayed = urlparse(back.headers["location"])
    assert relayed.path == f"/{SLUG}/api/gmail/oauth/callback"
    assert parse_qs(relayed.query) == {"code": ["4/0A-code"], "state": [state]}
    assert _exchanges(google) == []

    # The space completes it, and the space is empty, so the browser lands on its Home
    # beside the door that was pressed (REB-222).
    done = shared.get(back.headers["location"], follow_redirects=False)
    assert done.status_code == 307, done.text
    assert done.headers["location"] == f"/{SLUG}/app/?esito=collegato"
    exchanges = _exchanges(google)
    assert len(exchanges) == 1
    assert exchanges[0]["redirect_uri"] == [ROOT_CALLBACK]

    connected = shared.get(f"/{SLUG}/api/gmail/account").json()["account"]
    assert connected["email_address"] == "ada@studio.it"
    assert connected["status"] == "active"

    # Sealed with the space's own derived key, in the space's database only.
    space_key = decode_google_token_key(
        space_base_settings(_settings(api_engine, shared=True), SLUG)
    )
    url = tenant_database_url(_settings(api_engine, shared=True), tenant_database_name(SLUG))
    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            row = connection.execute(
                text("select refresh_token_ciphertext, refresh_token_nonce from google_accounts")
            ).one()
    finally:
        engine.dispose()
    assert unseal(bytes(row[0]), bytes(row[1]), space_key) == REFRESH


def test_a_refusal_on_googles_side_comes_back_to_the_space_home(
    shared: TestClient, google: FakeGmail
) -> None:
    _sign_up(shared, SLUG, "ada@studio.it")
    state = _start(shared, SLUG)["state"][0]
    back = shared.get(
        "/api/gmail/oauth/callback",
        params={"error": "access_denied", "state": state},
        follow_redirects=False,
    )
    assert urlparse(back.headers["location"]).path == f"/{SLUG}/api/gmail/oauth/callback"
    landed = shared.get(back.headers["location"], follow_redirects=False)
    # Google's own `error` is English and never reaches the page (`routers/gmail.py`).
    assert landed.headers["location"] == f"/{SLUG}/app/?esito=negato"
    assert _exchanges(google) == []


def test_a_state_carried_to_another_space_lands_no_token_there(
    shared: TestClient, google: FakeGmail
) -> None:
    """A consent started in one space, with its slug swapped for another's: the root
    relays it where the state points, and that space has never issued the jti. Signed
    in to both, so the refusal is the state's and not the missing session's."""
    _sign_up(shared, SLUG, "ada@studio.it")
    _sign_up(shared, OTHER, "bob@altro.it")
    state = _start(shared, SLUG)["state"][0]
    swapped = f"{OTHER}.{state.partition('.')[2]}"

    back = shared.get(
        "/api/gmail/oauth/callback",
        params={"code": "4/0A-code", "state": swapped},
        follow_redirects=False,
    )
    assert urlparse(back.headers["location"]).path == f"/{OTHER}/api/gmail/oauth/callback"
    landed = shared.get(back.headers["location"], follow_redirects=False)
    assert landed.headers["location"] == f"/{OTHER}/app/?esito=errore"
    # And handed straight to the space it was not minted for, past the root.
    direct = shared.get(
        f"/{OTHER}/api/gmail/oauth/callback",
        params={"code": "4/0A-code", "state": state},
        follow_redirects=False,
    )
    assert direct.headers["location"] == f"/{OTHER}/app/?esito=errore"
    # Nothing reached Google, and neither space holds a mailbox.
    assert _exchanges(google) == []
    for slug in (SLUG, OTHER):
        assert shared.get(f"/{slug}/api/gmail/account").json()["account"] is None


def test_drive_consent_takes_the_same_way_back(shared: TestClient, google: FakeGmail) -> None:
    _sign_up(shared, SLUG, "ada@studio.it")
    started = shared.get(f"/{SLUG}/api/drive/oauth/start", follow_redirects=False)
    query = parse_qs(urlparse(started.headers["location"]).query)
    assert query["redirect_uri"] == ["https://pigro.test/api/drive/oauth/callback"]
    state = query["state"][0]

    back = shared.get(
        "/api/drive/oauth/callback",
        params={"error": "access_denied", "state": state},
        follow_redirects=False,
    )
    assert urlparse(back.headers["location"]).path == f"/{SLUG}/api/drive/oauth/callback"
    landed = shared.get(back.headers["location"], follow_redirects=False)
    assert landed.headers["location"] == f"/{SLUG}/app/settings/drive?esito=negato"


def test_the_root_relays_only_what_names_a_space_and_only_when_it_lends_its_client(
    api_engine: Engine,
) -> None:
    """The root's own consent is a bare jti and still needs the root's session; a state
    that names no well-formed space is not relayed; and with the setting off nothing
    is, whatever the state says."""
    with _serving(_settings(api_engine, shared=True)) as client:
        for state in ("un-jti-della-radice", "app.jti", "evil.com/x.jti"):
            response = client.get(
                "/api/gmail/oauth/callback",
                params={"code": "c", "state": state},
                follow_redirects=False,
            )
            assert response.status_code == 401, (state, response.text)
    with _serving(_settings(api_engine, shared=False)) as client:
        response = client.get(
            "/api/gmail/oauth/callback",
            params={"code": "c", "state": f"{SLUG}.jti"},
            follow_redirects=False,
        )
        assert response.status_code == 401, response.text
        _sign_up(client, SLUG, "ada@studio.it")
        assert client.get(f"/{SLUG}/api/gmail/account").json()["configured"] is False


def test_each_space_gets_its_own_token_client(
    api_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The access tokens a client caches are keyed by the account's id alone, so two
    spaces borrowing one client id must not share a cache: a row copied into the wrong
    database would pick up the other space's live token without unsealing anything."""
    monkeypatch.setattr(gmail_router, "_token_clients", {})
    root = _settings(api_engine, shared=True)
    studio = gmail_router.token_client(space_base_settings(root, SLUG))
    other = gmail_router.token_client(space_base_settings(root, OTHER))
    own = gmail_router.token_client(root)
    assert len({id(studio), id(other), id(own)}) == 3
    assert gmail_router.token_client(space_base_settings(root, SLUG)) is studio


def test_under_the_roots_own_name_the_callback_still_relays_and_never_to_itself(
    api_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Production's nginx sends `/api/.../oauth/callback` on under the root's name
    (`deploy/nginx/pigro.letsrebase.conf`), so that is the path a relayed consent really
    takes. A state naming the root's own name is not a space's and is not relayed: the
    relay would land on this same callback."""
    monkeypatch.setenv("PIGROCRM_ROOT_SLUG", "studiorossi")
    monkeypatch.setenv("PIGROCRM_DATABASE_URL", _settings(api_engine, shared=True).database_url)
    monkeypatch.setenv("PIGROCRM_JWT_SECRET", _settings(api_engine, shared=True).jwt_secret)
    rooted = _settings(api_engine, shared=True).model_copy(update={"root_slug": "studiorossi"})
    with _serving(rooted) as client:
        get_settings.cache_clear()  # the prefix middleware reads the environment's
        relayed = client.get(
            "/studiorossi/api/gmail/oauth/callback",
            params={"code": "c", "state": f"{SLUG}.jti"},
            follow_redirects=False,
        )
        assert relayed.status_code == 307, relayed.text
        assert urlparse(relayed.headers["location"]).path == f"/{SLUG}/api/gmail/oauth/callback"
        itself = client.get(
            "/studiorossi/api/gmail/oauth/callback",
            params={"code": "c", "state": "studiorossi.jti"},
            follow_redirects=False,
        )
        assert itself.status_code == 401, itself.text
