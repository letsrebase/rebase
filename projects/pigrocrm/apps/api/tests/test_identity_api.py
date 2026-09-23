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
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import SQLAlchemyError

from pigrocrm.core.auth.repository import UserRepository
from pigrocrm.core.config import Settings, get_settings
from pigrocrm.core.db.session import session_factory
from pigrocrm.core.db.sidecar import drop_database
from pigrocrm.core.mail import RecordingSender
from pigrocrm.core.tenants import TenantService, ensure_tenants_database, tenants_database_url
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


def _tenants_with_user_email(settings: Settings, email: str) -> set[str]:
    """The live scan §3/B1 of the design describes for `GET /api/identity/spaces`:
    every tenant's own `users` table, checked for this email -- the same
    `UserRepository.get_by_email` a space's own login already uses, widened across
    every tenant the registry knows the way `_owned_slugs` already widens
    `Tenant.owner_email` for magic links (`routers/auth.py:233-247`). REB-377's own
    route is not merged as of this test; this is the assertion that route will make
    once it exists (design 2026-09-23 §3/§4, REB-378)."""
    registry_engine = create_engine(tenants_database_url(settings), future=True)
    try:
        with session_factory(registry_engine)() as registry_session:
            slugs = [t.slug for t in TenantService(registry_session, settings).list()]
    finally:
        registry_engine.dispose()
    found: set[str] = set()
    for slug in slugs:
        url = tenant_database_url(settings, tenant_database_name(slug))
        engine = create_engine(url, future=True)
        try:
            with session_factory(engine)() as space:
                if UserRepository(space).get_by_email(email) is not None:
                    found.add(slug)
        finally:
            engine.dispose()
    return found


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


def _sign_up_and_verify(
    client: TestClient, *, slug: str = SLUG, signup: dict[str, object] | None = None
) -> RecordingSender:
    """Signs up `slug` through the wizard and clicks the welcome mail's own link --
    the one call site this issue explicitly excludes (`signup` itself) followed by the
    one it wires (`enter_with_link`). Returns the sender so a test can read whatever
    mail comes after. Defaults to the module's own `SLUG`/`WIZARD_SIGNUP`; REB-378's
    own test signs up a second space, under a different owner, to prove an
    invitation composes with an identity a *different* space already minted."""
    payload = signup if signup is not None else WIZARD_SIGNUP
    recording = RecordingSender()
    client.app.dependency_overrides[get_sender] = lambda: recording  # type: ignore[attr-defined]
    created = client.post("/api/tenants/", json=payload)
    assert created.status_code == 201, created.text
    raw = _token_from(recording.sent[0].text)
    entered = client.post(f"/{slug}/api/auth/verify", json={"t": raw})
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


# --- accepting an invitation composes with an identity that already existed
# elsewhere (design 2026-09-23 §4, REB-378) ------------------------------------------


def test_accepting_an_invitation_links_to_an_existing_identity_across_spaces(
    spaces_client: TestClient, container_settings: Settings
) -> None:
    """REB-378's own "needed": one `IdentityService.upsert_and_issue` call in
    `accept_invite`, in the same place REB-376 already added it to `login` and
    `enter_with_link` (`_issue_identity_cookie`, `routers/auth.py:464`). That call
    already existed by the time this issue was opened, so there is no production
    code for this issue to add -- this test is REB-378's entire remaining scope: the
    regression proving that composition, end to end.

    A first space (`SLUG`) mints an identity for `shared_email` through the ordinary
    signup-and-verify path. A second space, owned by someone else entirely, then
    invites that same address and the invitee accepts. Done when (the issue's own
    words): that acceptance shows the second space for the *same* identity, with no
    second row and no separate action taken -- verified the same live-scan way §3/B1
    of the design resolves it, and REB-377's own `GET /api/identity/spaces` (not yet
    merged as of this test) will run."""
    shared_email = "condivisa@identita.it"
    _sign_up_and_verify(spaces_client, signup={**WIZARD_SIGNUP, "email": shared_email})

    registry = ensure_tenants_database(container_settings)
    with registry.connect() as connection:
        identity_id_before = connection.execute(
            text("SELECT id FROM identities WHERE email = :e"), {"e": shared_email}
        ).scalar_one()

    slug_b = "identita-prova-b"
    signup_b = {
        "slug": slug_b,
        "nome": "Titolare B",
        "email": "titolare@identita.it",
        "membro": False,
    }
    try:
        recording_b = _sign_up_and_verify(spaces_client, slug=slug_b, signup=signup_b)

        invite = spaces_client.post(
            f"/{slug_b}/api/users/invites", json={"email": shared_email, "nome": "Condivisa"}
        )
        assert invite.status_code == 201, invite.text
        invite_token = _token_from(recording_b.sent[0].text)

        spaces_client.cookies.clear()  # the accept is unauthenticated, like the mail click
        accepted = spaces_client.post(f"/{slug_b}/api/auth/invite", json={"t": invite_token})
        assert accepted.status_code == 200, accepted.text
        identity_cookie = _cookie(accepted.headers.get_list("set-cookie"), IDENTITY_COOKIE)
        assert _cookie_path(identity_cookie) == "/"

        with registry.connect() as connection:
            identity_count = connection.execute(
                text("SELECT count(*) FROM identities WHERE email = :e"), {"e": shared_email}
            ).scalar_one()
            identity_id_after = connection.execute(
                text("SELECT id FROM identities WHERE email = :e"), {"e": shared_email}
            ).scalar_one()
        assert identity_count == 1  # composed onto the existing identity, never a second row
        assert identity_id_after == identity_id_before

        assert _tenants_with_user_email(container_settings, shared_email) == {SLUG, slug_b}
    finally:
        url = tenant_database_url(container_settings, tenant_database_name(slug_b))
        drop_database(container_settings, url)


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
