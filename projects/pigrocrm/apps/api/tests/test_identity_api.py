"""Cross-space identity over HTTP: the passwordless side effect three existing entry
points gain, and the one new root-scoped route this issue adds (design 2026-09-23,
REB-345/376).

Follows `test_tenants_api.py`'s own fixture shape: the registry and every space this
file creates live in the container `api_engine` starts, `get_settings` is overridden
to that container's URL, and the per-process caches in `deps` are reset around each
test -- so `TenantsRegistryDep` (used by `POST /api/identity/logout`) and the
ephemeral registry engine `_issue_identity_cookie` opens (used by `login`,
`enter_with_link`, `accept_invite`) both land on the same real Postgres.
"""

from collections.abc import Iterator
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.exc import SQLAlchemyError

from pigrocrm.core.config import Settings, get_settings
from pigrocrm.core.db.sidecar import drop_database
from pigrocrm.core.mail import RecordingSender
from pigrocrm.core.tenants import ensure_tenants_database
from pigrocrm.core.tenants.database import tenant_database_name, tenant_database_url
from pigrocrm_api.deps import IDENTITY_COOKIE, reset_session_factories
from pigrocrm_api.main import create_app
from pigrocrm_api.ratelimit import reset_rate_limit
from pigrocrm_api.sessions import get_sender

SLUG = "identita-prova"
SIGNUP = {"slug": SLUG, "nome": "Ada Lovelace", "email": "ada@identita.it"}
WIZARD_SIGNUP = {**SIGNUP, "membro": False}


def _cookie_path(set_cookie_header: str) -> str | None:
    for part in set_cookie_header.split(";"):
        key, _, value = part.strip().partition("=")
        if key.strip().lower() == "path":
            return value.strip()
    return None


def _cookie(headers: list[str], name: str) -> str:
    return next(c for c in headers if c.startswith(f"{name}="))


def _token_from(mail_text: str) -> str:
    return mail_text.split("?t=", 1)[1].split()[0]


@pytest.fixture
def container_settings(api_engine: Engine) -> Settings:
    return Settings(
        database_url=api_engine.url.render_as_string(hide_password=False),
        jwt_secret="test-secret-for-the-api-test-suite-only",
        cookie_secure=True,
        public_url="https://pigro.test",
        hub_url="http://127.0.0.1:9",
        _env_file=None,  # type: ignore[call-arg]
    )


@contextmanager
def _serving(settings: Settings) -> Iterator[TestClient]:
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
            connection.execute(text("DELETE FROM identity_sessions"))
            connection.execute(text("DELETE FROM identity_link_tokens"))
            connection.execute(text("DELETE FROM identities"))
            connection.execute(text("DELETE FROM tenants"))
        registry.dispose()
        reset_session_factories()
        drop_database(settings, tenant_database_url(settings, tenant_database_name(SLUG)))
        get_settings.cache_clear()


@pytest.fixture
def spaces_client(container_settings: Settings) -> Iterator[TestClient]:
    with _serving(container_settings) as client:
        yield client


def _sign_up_and_verify(client: TestClient) -> RecordingSender:
    """Signs up `SLUG` through the wizard and clicks the welcome mail's own link --
    the one call site this issue explicitly excludes (`signup` itself) followed by the
    one it wires (`enter_with_link`). Returns the sender so a test can read whatever
    mail comes after."""
    recording = RecordingSender()
    client.app.dependency_overrides[get_sender] = lambda: recording  # type: ignore[attr-defined]
    created = client.post("/api/tenants/", json=WIZARD_SIGNUP)
    assert created.status_code == 201, created.text
    raw = _token_from(recording.sent[0].text)
    entered = client.post(f"/{SLUG}/api/auth/verify", json={"t": raw})
    assert entered.status_code == 200, entered.text
    recording.sent.clear()
    return recording


# --- signup is excluded, on purpose (design §2) -------------------------------------


def test_signup_itself_does_not_mint_the_identity_cookie(spaces_client: TestClient) -> None:
    created = spaces_client.post("/api/tenants/", json=WIZARD_SIGNUP)
    assert created.status_code == 201, created.text
    cookies = created.headers.get_list("set-cookie")
    assert not any(c.startswith(f"{IDENTITY_COOKIE}=") for c in cookies)


# --- enter_with_link ----------------------------------------------------------------


def test_enter_with_link_mints_the_identity_cookie_at_the_root_path(
    spaces_client: TestClient,
) -> None:
    recording = RecordingSender()
    spaces_client.app.dependency_overrides[get_sender] = lambda: recording  # type: ignore[attr-defined]
    created = spaces_client.post("/api/tenants/", json=WIZARD_SIGNUP)
    assert created.status_code == 201, created.text
    raw = _token_from(recording.sent[0].text)

    entered = spaces_client.post(f"/{SLUG}/api/auth/verify", json={"t": raw})
    assert entered.status_code == 200, entered.text
    cookies = entered.headers.get_list("set-cookie")
    identity_cookie = _cookie(cookies, IDENTITY_COOKIE)
    assert _cookie_path(identity_cookie) == "/"
    assert "httponly" in identity_cookie.lower()
    # The space's own pair is unaffected: still scoped to /{SLUG}/.
    assert any(f"Path=/{SLUG}/" in c for c in cookies if c.startswith("pigrocrm_access="))
    assert any(f"Path=/{SLUG}/" in c for c in cookies if c.startswith("pigrocrm_refresh="))


# --- login ---------------------------------------------------------------------------


def test_login_mints_the_identity_cookie_as_a_side_effect(spaces_client: TestClient) -> None:
    _sign_up_and_verify(spaces_client)
    collaborator = spaces_client.post(
        f"/{SLUG}/api/users",
        json={
            "email": "b@identita.it",
            "password": "lunghissima1",
            "nome": "B",
            "ruolo": "collaboratore",
        },
    )
    assert collaborator.status_code == 201, collaborator.text
    spaces_client.cookies.clear()

    response = spaces_client.post(
        f"/{SLUG}/api/auth/login", json={"email": "b@identita.it", "password": "lunghissima1"}
    )
    assert response.status_code == 200, response.text
    identity_cookie = _cookie(response.headers.get_list("set-cookie"), IDENTITY_COOKIE)
    assert _cookie_path(identity_cookie) == "/"


# --- accept_invite --------------------------------------------------------------------


def test_accept_invite_mints_the_identity_cookie(spaces_client: TestClient) -> None:
    recording = _sign_up_and_verify(spaces_client)
    invite = spaces_client.post(
        f"/{SLUG}/api/users/invites", json={"email": "invitato@identita.it", "nome": "Invitato"}
    )
    assert invite.status_code == 201, invite.text
    invite_token = _token_from(recording.sent[0].text)

    spaces_client.cookies.clear()  # the accept is unauthenticated, like the mail click
    accepted = spaces_client.post(f"/{SLUG}/api/auth/invite", json={"t": invite_token})
    assert accepted.status_code == 200, accepted.text
    identity_cookie = _cookie(accepted.headers.get_list("set-cookie"), IDENTITY_COOKIE)
    assert _cookie_path(identity_cookie) == "/"


# --- one row per address, however many times it proves itself ----------------------


def test_repeated_entries_with_the_same_address_keep_one_identity_row(
    spaces_client: TestClient, container_settings: Settings
) -> None:
    recording = _sign_up_and_verify(spaces_client)  # identity session #1
    assert (
        spaces_client.post(f"/{SLUG}/api/auth/link", json={"email": SIGNUP["email"]}).status_code
        == 202
    )
    raw2 = _token_from(recording.sent[0].text)
    assert spaces_client.post(f"/{SLUG}/api/auth/verify", json={"t": raw2}).status_code == 200

    registry = ensure_tenants_database(container_settings)
    with registry.connect() as connection:
        identities = connection.execute(
            text("SELECT count(*) FROM identities WHERE email = :e"), {"e": SIGNUP["email"]}
        ).scalar_one()
        sessions = connection.execute(
            text(
                "SELECT count(*) FROM identity_sessions s "
                "JOIN identities i ON i.id = s.identity_id WHERE i.email = :e"
            ),
            {"e": SIGNUP["email"]},
        ).scalar_one()
    assert identities == 1
    assert sessions == 2


# --- POST /api/identity/logout -------------------------------------------------------


def test_identity_logout_revokes_every_session_and_clears_the_cookie(
    spaces_client: TestClient, container_settings: Settings
) -> None:
    _sign_up_and_verify(spaces_client)

    logout = spaces_client.post("/api/identity/logout")
    assert logout.status_code == 204
    assert spaces_client.cookies.get(IDENTITY_COOKIE) in (None, "")

    registry = ensure_tenants_database(container_settings)
    with registry.connect() as connection:
        live = connection.execute(
            text(
                "SELECT count(*) FROM identity_sessions s "
                "JOIN identities i ON i.id = s.identity_id "
                "WHERE i.email = :e AND s.revoked_at IS NULL"
            ),
            {"e": SIGNUP["email"]},
        ).scalar_one()
    assert live == 0


def test_identity_logout_is_idempotent_with_no_cookie(spaces_client: TestClient) -> None:
    assert spaces_client.post("/api/identity/logout").status_code == 204


def test_identity_logout_is_idempotent_with_a_garbage_cookie(spaces_client: TestClient) -> None:
    spaces_client.cookies.set(IDENTITY_COOKIE, "not-a-real-token")
    assert spaces_client.post("/api/identity/logout").status_code == 204


# --- the mint must never fail the request it rides on ------------------------------


def test_the_identity_cookie_mint_never_fails_a_login_when_the_registry_is_unreachable(
    spaces_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    import pigrocrm_api.routers.auth as auth_module

    _sign_up_and_verify(spaces_client)
    collaborator = spaces_client.post(
        f"/{SLUG}/api/users",
        json={
            "email": "c@identita.it",
            "password": "lunghissima1",
            "nome": "C",
            "ruolo": "collaboratore",
        },
    )
    assert collaborator.status_code == 201, collaborator.text
    spaces_client.cookies.clear()

    class _BrokenIdentityService:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        def upsert_and_issue(self, email: str) -> str | None:
            raise SQLAlchemyError("registro non raggiungibile")

    monkeypatch.setattr(auth_module, "IdentityService", _BrokenIdentityService)

    response = spaces_client.post(
        f"/{SLUG}/api/auth/login", json={"email": "c@identita.it", "password": "lunghissima1"}
    )
    assert response.status_code == 200, response.text
    cookies = response.headers.get_list("set-cookie")
    assert any(c.startswith("pigrocrm_access=") for c in cookies)
    assert any(c.startswith("pigrocrm_refresh=") for c in cookies)
    assert not any(c.startswith(f"{IDENTITY_COOKIE}=") for c in cookies)
