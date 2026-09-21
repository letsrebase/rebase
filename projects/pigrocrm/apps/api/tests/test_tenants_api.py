"""Spaces over HTTP: signing up, and then being served from the space's own database.

The registry and every space this file creates live in the same container `api_engine`
starts. `get_settings` is overridden with that container's URL so `TenantService` and
`deps._tenant_session_factory` both derive their databases from it, and the per-process
caches in `deps` are reset around each test so a space provisioned here never outlives
the settings it was built under.
"""

from collections.abc import Iterator
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from pigrocrm.core.config import Settings, get_settings
from pigrocrm.core.db.sidecar import drop_database
from pigrocrm.core.tenants import MemberLookup, ensure_tenants_database
from pigrocrm.core.tenants.database import tenant_database_name, tenant_database_url
from pigrocrm_api.deps import get_session, reset_session_factories
from pigrocrm_api.main import create_app
from pigrocrm_api.ratelimit import (
    DISPONIBILE_REQUESTS_PER_MINUTE,
    REQUESTS_PER_MINUTE,
    reset_rate_limit,
)
from pigrocrm_api.tenancy import split_tenant_prefix

SLUG = "studio-prova"
REGISTRY_TOKEN = "un-token-lungo-solo-per-questa-suite"
SIGNUP = {
    "slug": SLUG,
    "nome": "Ada Lovelace",
    "email": "ada@studio.it",
}


@pytest.fixture
def container_settings(api_engine: Engine) -> Settings:
    return Settings(
        database_url=api_engine.url.render_as_string(hide_password=False),
        jwt_secret="test-secret-for-the-api-test-suite-only",
        cookie_secure=True,
        # Where a link by mail points (ORB-172): required, never the request's Host.
        public_url="https://pigro.test",
        # A loopback port nobody listens on, so no test in this file can ever ask the
        # real hub whatever a developer's `.env` says (ORB-173).
        hub_url="http://127.0.0.1:9",
        _env_file=None,  # type: ignore[call-arg]
    )


@contextmanager
def _serving(settings: Settings) -> Iterator[TestClient]:
    """A client whose root database is the container's CRM database and whose spaces are
    real databases on the same server. `get_session` is deliberately *not* overridden:
    the point is that `deps` picks the database from the request. Every space the test
    created under `SLUG` is dropped on the way out, and the registry emptied."""
    reset_session_factories()
    get_settings.cache_clear()
    # The limiter counts per process: one test's questions must not be the next one's budget.
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
        drop_database(settings, tenant_database_url(settings, tenant_database_name(SLUG)))
        get_settings.cache_clear()


@pytest.fixture
def spaces_client(container_settings: Settings) -> Iterator[TestClient]:
    with _serving(container_settings) as client:
        yield client


@pytest.fixture
def registry_client(container_settings: Settings) -> Iterator[TestClient]:
    """The same installation with `PIGROCRM_REGISTRY_TOKEN` set: the one that lets the
    Orbiters hub read the list of spaces (ORB-142)."""
    with_token = container_settings.model_copy(update={"registry_token": REGISTRY_TOKEN})
    with _serving(with_token) as client:
        yield client


def test_the_prefix_is_split_only_for_a_space_and_only_before_api_or_health() -> None:
    assert split_tenant_prefix("/studio/api/customers") == ("studio", "/api/customers")
    assert split_tenant_prefix("/studio/health") == ("studio", "/health")
    assert split_tenant_prefix("/api/customers") == (None, "/api/customers")
    assert split_tenant_prefix("/app/api/x") == (None, "/app/api/x")  # reserved word
    assert split_tenant_prefix("/studio/app/login") == (None, "/studio/app/login")  # not the API
    assert split_tenant_prefix("/Studio/api/x") == (None, "/Studio/api/x")  # not a slug


def test_a_reserved_or_malformed_name_is_refused_before_touching_the_server(
    spaces_client: TestClient,
) -> None:
    for slug in ("app", "ab", "Studio Rossi"):
        response = spaces_client.post("/api/tenants/", json={**SIGNUP, "slug": slug})
        assert response.status_code == 422, (slug, response.text)


def test_availability_says_why(spaces_client: TestClient) -> None:
    assert spaces_client.get("/api/tenants/app/disponibile").json() == {
        "slug": "app",
        "disponibile": False,
        "motivo": "questo nome è riservato",
    }
    assert spaces_client.get(f"/api/tenants/{SLUG}/disponibile").json()["disponibile"] is True


def test_disponibile_is_throttled_per_client(spaces_client: TestClient) -> None:
    """Unauthenticated by design, like `membro` (REB-228): the request past the budget
    is a 429 with a `Retry-After`. Its own, larger budget (`DISPONIBILE_REQUESTS_PER_MINUTE`),
    separate from `membro`'s and `signup`'s `REQUESTS_PER_MINUTE`: this is the one route
    of the three a person's own typing calls repeatedly, and sharing the signup's tighter
    bucket would let a few hesitations while naming a business starve the tokens the
    actual `POST /` still needs to create the space."""
    for _ in range(DISPONIBILE_REQUESTS_PER_MINUTE):
        assert spaces_client.get(f"/api/tenants/{SLUG}/disponibile").status_code == 200
    refused = spaces_client.get(f"/api/tenants/{SLUG}/disponibile")
    assert refused.status_code == 429, refused.text
    assert refused.headers["Retry-After"] == "60"
    # Another client has its own bucket.
    other = spaces_client.get(f"/api/tenants/{SLUG}/disponibile", headers={"X-Real-IP": "10.0.0.7"})
    assert other.status_code == 200, other.text


def test_signing_up_creates_a_space_that_serves_its_own_data(
    spaces_client: TestClient, container_settings: Settings
) -> None:
    created = spaces_client.post("/api/tenants/", json=SIGNUP)
    assert created.status_code == 201, created.text
    assert created.json()["slug"] == SLUG
    # Registering is entering (spec 2026-09-12 §6.4): the space's home, not its login.
    assert created.headers["Location"] == f"/{SLUG}/app/"

    # The name is gone, and a second signup with it is a 409, not a second space.
    assert spaces_client.get(f"/api/tenants/{SLUG}/disponibile").json()["disponibile"] is False
    again = spaces_client.post("/api/tenants/", json={**SIGNUP, "email": "bob@studio.it"})
    assert again.status_code == 409, again.text

    # The signup opened the space's session (ORB-176): the access cookie is scoped to it
    # and the space's own `me` answers the admin the signup created.
    assert f"Path=/{SLUG}/" in created.headers["set-cookie"]
    assert spaces_client.get(f"/{SLUG}/api/auth/me").json()["email"] == SIGNUP["email"]

    # The space is empty and separate: the root's session is not this one.
    customers = spaces_client.get(f"/{SLUG}/api/customers")
    assert customers.status_code == 200, customers.text
    assert customers.json()["items"] == []

    # Gmail does not exist for a space, whatever the installation has configured: the
    # account read says "nothing connected", and connecting one is refused outright.
    assert spaces_client.get(f"/{SLUG}/api/gmail/account").json()["account"] is None
    assert spaces_client.get(f"/{SLUG}/api/gmail/oauth/start").status_code == 409

    # The root does not know this user, and the admin has no password anywhere.
    assert (
        spaces_client.post(
            "/api/auth/login", json={"email": SIGNUP["email"], "password": "qualunque11"}
        ).status_code
        == 401
    )


def test_an_unknown_space_is_a_404_not_a_server_error(spaces_client: TestClient) -> None:
    response = spaces_client.get("/nessuno-qui/api/customers")
    assert response.status_code == 404, response.text
    assert response.json()["code"] == "not_found"


def test_the_root_still_answers_without_a_prefix(spaces_client: TestClient) -> None:
    assert spaces_client.get("/health").json() == {"status": "ok"}
    assert spaces_client.get("/api/auth/me").status_code == 401
    # The root fixture client still works on its own, overridden session.
    assert get_session is not None


def test_the_root_slug_is_the_root_itself_and_nobody_elses_name(
    container_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`PIGROCRM_ROOT_SLUG=studiorossi`: `/studiorossi/api/...` is the root database with the
    root's settings, not a space, and the name is refused to signups."""
    assert split_tenant_prefix("/studiorossi/api/auth/me", "studiorossi") == (None, "/api/auth/me")
    assert split_tenant_prefix("/studiorossi/health", "studiorossi") == (None, "/health")
    assert split_tenant_prefix("/altro/api/x", "studiorossi") == ("altro", "/api/x")
    # Cookies follow the prefix the request wore, root alias included.
    from fastapi import Request

    from pigrocrm_api.tenancy import cookie_path

    def _request(state: dict[str, str]) -> Request:
        return Request({"type": "http", "path": "/api/x", "headers": [], "state": state})

    assert cookie_path(_request({})) == "/"
    # The root under its own name keeps the root's jar: it logs in at the bare
    # `/app/login` and works under `/studiorossi/app`, and only `/` serves both.
    assert cookie_path(_request({"prefix": "studiorossi"})) == "/"
    assert cookie_path(_request({"prefix": "studio", "tenant": "studio"})) == "/studio/"
    # What login, refresh and logout clear: the root's old jar under its own name is
    # included for the root -- bare or aliased -- and never for a space.
    from pigrocrm_api.tenancy import cookie_paths_to_clear

    assert cookie_paths_to_clear(_request({}), "studiorossi") == ["/studiorossi/", "/"]
    assert cookie_paths_to_clear(_request({"prefix": "studiorossi"}), "studiorossi") == [
        "/studiorossi/",
        "/",
    ]
    assert cookie_paths_to_clear(
        _request({"prefix": "studio", "tenant": "studio"}), "studiorossi"
    ) == [
        "/studio/",
        "/",
    ]

    monkeypatch.setenv("PIGROCRM_ROOT_SLUG", "studiorossi")
    monkeypatch.setenv("PIGROCRM_DATABASE_URL", container_settings.database_url)
    monkeypatch.setenv("PIGROCRM_JWT_SECRET", container_settings.jwt_secret)
    get_settings.cache_clear()
    reset_session_factories()
    rooted = container_settings.model_copy(update={"root_slug": "studiorossi"})
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: rooted
    registry = ensure_tenants_database(rooted)
    try:
        with TestClient(app, base_url="https://testserver") as client:
            assert client.get("/studiorossi/health").json() == {"status": "ok"}
            assert client.get("/studiorossi/api/tenants/root").json() == {"slug": "studiorossi"}
            # The root's own login answers here, and the cookie is the root's (`Path=/`).
            assert client.get("/studiorossi/api/auth/me").status_code == 401
            # A logout under the alias clears the root's pair at `/` *and* the pair that
            # lived at `/studiorossi/` before 2026-09-09: a browser removes a cookie only
            # for a matching path, and a pair left behind kept a session alive that the
            # person had ended.
            logout = client.post("/studiorossi/api/auth/logout")
            assert logout.status_code == 204
            deletions = logout.headers.get_list("set-cookie")
            assert sum("Path=/studiorossi/;" in c for c in deletions) == 2
            assert sum("Path=/;" in c for c in deletions) == 2
            assert client.get("/api/tenants/studiorossi/disponibile").json()["motivo"] == (
                "questo nome è riservato"
            )
            refused = client.post("/api/tenants/", json={**SIGNUP, "slug": "studiorossi"})
            assert refused.status_code == 422, refused.text
    finally:
        registry.dispose()
        reset_session_factories()
        get_settings.cache_clear()


def test_the_root_space_endpoint_names_the_root_or_says_there_is_none(
    spaces_client: TestClient,
) -> None:
    assert spaces_client.get("/api/tenants/root").json() == {"slug": None}


def test_without_a_registry_token_the_list_of_spaces_does_not_exist(
    spaces_client: TestClient,
) -> None:
    """A self-hosted installation that never set the token exposes nothing new: the
    route is a 404 whatever the caller presents, not a 401 that says «there is a door»."""
    assert spaces_client.get("/api/tenants/").status_code == 404
    assert (
        spaces_client.get("/api/tenants/", headers={"Authorization": "Bearer qualcosa"}).status_code
        == 404
    )


def test_the_list_of_spaces_answers_only_to_the_registry_token(
    registry_client: TestClient,
) -> None:
    """ORB-142: the hub's admin area reads which spaces exist and whose they are. The
    token is the whole credential, so a missing or wrong one is a 401; the right one gets
    every registry row, newest first, and nothing about the database behind a row."""
    assert registry_client.get("/api/tenants/").status_code == 401
    wrong = registry_client.get("/api/tenants/", headers={"Authorization": "Bearer sbagliato"})
    assert wrong.status_code == 401
    # Starlette decodes headers as latin-1, and `secrets.compare_digest` refuses a `str`
    # with a non-ASCII character: a stray byte must be a 401 like any wrong token, never a 500.
    odd = registry_client.get("/api/tenants/", headers={"Authorization": b"Bearer t\xe9ken"})
    assert odd.status_code == 401, odd.text
    bearer = {"Authorization": f"Bearer {REGISTRY_TOKEN}"}

    assert registry_client.get("/api/tenants/", headers=bearer).json() == []

    created = registry_client.post("/api/tenants/", json=SIGNUP)
    assert created.status_code == 201, created.text
    listed = registry_client.get("/api/tenants/", headers=bearer)
    assert listed.status_code == 200, listed.text
    rows = listed.json()
    assert [row["slug"] for row in rows] == [SLUG]
    assert rows[0]["owner_email"] == SIGNUP["email"]
    assert set(rows[0]) == {"id", "slug", "owner_email", "created_at"}


def test_a_link_asked_at_the_root_reaches_the_space_that_address_owns(
    spaces_client: TestClient,
) -> None:
    """Spec 2026-09-12 §6.2: the bare login page asks for a link, the registry says which
    space the address owns, and the link enters that space with cookies scoped to it."""
    from pigrocrm.core.mail import RecordingSender
    from pigrocrm_api.routers.auth import get_sender

    recording = RecordingSender()
    spaces_client.app.dependency_overrides[get_sender] = lambda: recording  # type: ignore[attr-defined]
    created = spaces_client.post("/api/tenants/", json=SIGNUP)
    assert created.status_code == 201, created.text

    # The signup mailed the welcome (ORB-176); the link mail is the second one.
    recording.sent.clear()
    response = spaces_client.post("/api/auth/link", json={"email": SIGNUP["email"]})
    assert response.status_code == 202
    assert len(recording.sent) == 1
    assert f"/{SLUG}/app/verify?t=" in recording.sent[0].text
    token = recording.sent[0].text.split("?t=", 1)[1].split()[0]

    entered = spaces_client.post(f"/{SLUG}/api/auth/verify", json={"t": token})
    assert entered.status_code == 200, entered.text
    assert any(f"Path=/{SLUG}/" in c for c in entered.headers.get_list("set-cookie"))
    assert spaces_client.get(f"/{SLUG}/api/auth/me").json()["email"] == SIGNUP["email"]
    # The root itself never had this user: nothing is mailed for it, and the bare `entra`
    # does not know the token.
    assert spaces_client.post("/api/auth/verify", json={"t": token}).status_code == 401


def test_a_link_asked_under_a_space_reaches_that_space_only(spaces_client: TestClient) -> None:
    """Under `/<slug>/api`, the space's own user and the space's own entry page."""
    from pigrocrm.core.mail import RecordingSender
    from pigrocrm_api.routers.auth import get_sender

    recording = RecordingSender()
    spaces_client.app.dependency_overrides[get_sender] = lambda: recording  # type: ignore[attr-defined]
    created = spaces_client.post("/api/tenants/", json=SIGNUP)
    assert created.status_code == 201, created.text

    recording.sent.clear()  # the welcome mail of the signup (ORB-176)
    response = spaces_client.post(f"/{SLUG}/api/auth/link", json={"email": SIGNUP["email"]})
    assert response.status_code == 202
    assert len(recording.sent) == 1
    assert f"https://pigro.test/{SLUG}/app/verify?t=" in recording.sent[0].text
    # An address the space does not know: 202 and no mail, like the root.
    assert (
        spaces_client.post(f"/{SLUG}/api/auth/link", json={"email": "x@studio.it"}).status_code
        == 202
    )
    assert len(recording.sent) == 1


def test_the_signup_learns_whether_an_address_is_a_member_and_how_many_spaces_it_owns(
    spaces_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ORB-173: the hub's answer and the registry's, in one body. The hub is stubbed at
    the client the router calls; `spazi` is what only the registry knows and is read for
    real, from the space this test provisions. A count and never the slugs: the address
    is not proven yet."""
    asked: list[str] = []

    def fake_lookup(settings: Settings, email: str) -> MemberLookup:
        asked.append(email)
        return MemberLookup(membro=True, nome="Ada", cognome="Lovelace")

    monkeypatch.setattr("pigrocrm_api.routers.tenants.lookup_member", fake_lookup)

    before = spaces_client.post("/api/tenants/member", json={"email": "Ada@Studio.it"})
    assert before.status_code == 200, before.text
    assert before.json() == {"membro": True, "nome": "Ada", "cognome": "Lovelace", "spazi": 0}
    assert asked == ["ada@studio.it"]

    created = spaces_client.post("/api/tenants/", json=SIGNUP)
    assert created.status_code == 201, created.text
    after = spaces_client.post("/api/tenants/member", json={"email": SIGNUP["email"]})
    assert after.json()["spazi"] == 1
    assert SLUG not in after.text
    # Somebody else's address owns nothing here, whatever the hub says about them.
    assert (
        spaces_client.post("/api/tenants/member", json={"email": "bob@studio.it"}).json()["spazi"]
        == 0
    )


def test_an_unreachable_hub_still_answers_and_says_not_a_member(
    container_settings: Settings,
) -> None:
    """The real client against a loopback port nobody listens on, with a token set: the
    route answers 200 and `membro: false`, and the signup goes on. The community is the
    fast lane, never a gate."""
    with_hub = container_settings.model_copy(update={"registry_token": REGISTRY_TOKEN})
    with _serving(with_hub) as client:
        answer = client.post("/api/tenants/member", json={"email": "ada@studio.it"})
        assert answer.status_code == 200, answer.text
        assert answer.json() == {"membro": False, "nome": None, "cognome": None, "spazi": 0}


def test_a_malformed_or_missing_address_is_a_422_and_the_query_string_is_not_read(
    spaces_client: TestClient,
) -> None:
    for body in ({"email": "non-una-mail"}, {"email": ""}, {}, None):
        refused = spaces_client.post("/api/tenants/member", json=body)
        assert refused.status_code == 422, (body, refused.text)
    # The address in the URL is refused too: it would sit in every access log.
    assert (
        spaces_client.post("/api/tenants/member", params={"email": "ada@studio.it"}).status_code
        == 422
    )


def test_the_member_question_is_throttled_per_client(
    spaces_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unauthenticated by design, so the bucket is what stops a sweep of a mailing list:
    the request past the budget is a 429 with a `Retry-After`, and a 422 spends nothing
    because the limiter runs inside the route, after validation."""
    monkeypatch.setattr(
        "pigrocrm_api.routers.tenants.lookup_member",
        lambda settings, email: MemberLookup(membro=False),
    )
    question = {"email": "ada@studio.it"}
    for _ in range(REQUESTS_PER_MINUTE):
        assert spaces_client.post("/api/tenants/member", json=question).status_code == 200
    refused = spaces_client.post("/api/tenants/member", json=question)
    assert refused.status_code == 429, refused.text
    assert refused.headers["Retry-After"] == "60"
    # Another client has its own bucket.
    other = spaces_client.post(
        "/api/tenants/member", json={"email": "ada@studio.it"}, headers={"X-Real-IP": "10.0.0.7"}
    )
    assert other.status_code == 200, other.text


WIZARD_SIGNUP = {**SIGNUP, "membro": False}


def test_signing_up_enters_for_a_while_and_the_welcome_link_makes_it_durable(
    spaces_client: TestClient,
) -> None:
    """Spec 2026-09-12 §6.4, the whole chain: the 201 carries the access cookie only; the
    welcome mail carries a link that enters; that click verifies the address, opens the
    refresh cookie and is what makes the session durable. Before it, the admin can
    neither mint a token nor add a user."""
    from pigrocrm.core.mail import RecordingSender
    from pigrocrm_api.sessions import get_sender

    recording = RecordingSender()
    spaces_client.app.dependency_overrides[get_sender] = lambda: recording  # type: ignore[attr-defined]
    created = spaces_client.post("/api/tenants/", json=WIZARD_SIGNUP)
    assert created.status_code == 201, created.text
    assert created.headers["Location"] == f"/{SLUG}/app/"
    cookies = created.headers.get_list("set-cookie")
    assert any("pigrocrm_access=" in c and f"Path=/{SLUG}/" in c for c in cookies)
    assert not any("pigrocrm_refresh=" in c for c in cookies)
    # In, for the access token's life: `me` answers, `refresh` has nothing to refresh.
    me = spaces_client.get(f"/{SLUG}/api/auth/me")
    assert me.status_code == 200 and me.json()["ruolo"] == "admin"
    assert spaces_client.post(f"/{SLUG}/api/auth/refresh").status_code == 401
    # Nothing durable before the address is proven: no token, no second user.
    token = spaces_client.post(f"/{SLUG}/api/tokens", json={"nome": "claude"})
    assert token.status_code == 422, token.text
    assert "conferma il tuo indirizzo" in token.text
    user = spaces_client.post(
        f"/{SLUG}/api/users",
        json={
            "email": "b@studio.it",
            "password": "lunghissima1",
            "nome": "B",
            "ruolo": "collaboratore",
        },
    )
    assert user.status_code == 422, user.text
    # The welcome mail: to the owner, with a link that enters and the Orbiters paragraph
    # for someone who is not a member yet.
    assert len(recording.sent) == 1
    mail = recording.sent[0]
    assert mail.to == "ada@studio.it" and mail.subject == "Il tuo spazio PigroCRM è pronto"
    assert f"https://pigro.test/{SLUG}/app/login" in mail.text and "hub/freelance" in mail.text
    raw = mail.text.split(f"/{SLUG}/app/verify?t=", 1)[1].split()[0]
    entered = spaces_client.post(f"/{SLUG}/api/auth/verify", json={"t": raw})
    assert entered.status_code == 200, entered.text
    assert any(
        "pigrocrm_refresh=" in c and f"Path=/{SLUG}/" in c
        for c in entered.headers.get_list("set-cookie")
    )
    assert spaces_client.post(f"/{SLUG}/api/auth/refresh").status_code == 200
    assert spaces_client.post(f"/{SLUG}/api/tokens", json={"nome": "claude"}).status_code == 201
    # No password was ever set: the password form knows nothing of this admin.
    denied = spaces_client.post(
        f"/{SLUG}/api/auth/login", json={"email": "ada@studio.it", "password": "qualunque11"}
    )
    assert denied.status_code == 401


def test_signing_up_without_a_sender_still_creates_the_space(spaces_client: TestClient) -> None:
    created = spaces_client.post("/api/tenants/", json={**WIZARD_SIGNUP, "membro": True})
    assert created.status_code == 201, created.text
    assert spaces_client.get(f"/{SLUG}/api/auth/me").status_code == 200


def test_the_signup_refuses_a_password(spaces_client: TestClient) -> None:
    refused = spaces_client.post("/api/tenants/", json={**SIGNUP, "password": "lunghissima1"})
    assert refused.status_code == 422, refused.text
