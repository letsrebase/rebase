"""Signing in with GitHub or Google over HTTP (REB-1137, spec 2026-11-18): the start, the
callback and the no-account read, against a provider that is a fake HTTP seam."""

import base64
import json
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
import pytest
from contract_api_helpers import ADMIN_EMAIL, login_as
from fastapi.testclient import TestClient
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session
from work_profile_api_helpers import CARD_EMAIL, apply_card

from rebase_api.deps import get_http_call
from rebase_core.config import Settings, get_settings
from rebase_core.mail import RecordingSender
from rebase_core.models import Freelancer, Login, User

HUB = "https://preview.letsrebase.com/hub"
CALLBACK = "/api/hub/auth/oauth/{provider}/callback"
START = "/api/hub/auth/oauth/{provider}/start"
CODE = "provider-code-1"


class FakeProviders:
    """GitHub and Google as the hub's HTTP seam sees them. A code works once, as a real
    provider's does; every call is kept for the test to read what would have left."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, str], bytes]] = []
        self.github_emails: list[dict[str, Any]] = [
            {"email": CARD_EMAIL, "verified": True, "primary": True}
        ]
        self.google_info: dict[str, Any] = {"email": CARD_EMAIL, "email_verified": True}
        self.refuse_token = False
        self._spent: set[str] = set()

    def __call__(
        self, method: str, url: str, headers: dict[str, str], body: bytes
    ) -> tuple[int, bytes]:
        self.calls.append((method, url, headers, body))
        if url.endswith("/access_token") or url.endswith("/token"):
            code = parse_qs(body.decode())["code"][0]
            if self.refuse_token or code in self._spent:
                reply: Any = {"error": "bad_verification_code"}
                return (200, json.dumps(reply).encode()) if "github" in url else (400, b"{}")
            self._spent.add(code)
            return 200, json.dumps({"access_token": "tok-secret", "token_type": "bearer"}).encode()
        if url.endswith("/user/emails"):
            return 200, json.dumps(self.github_emails).encode()
        if url.endswith("/userinfo"):
            return 200, json.dumps(self.google_info).encode()
        raise AssertionError(f"unexpected call {method} {url}")

    def token_form(self) -> dict[str, str]:
        body = next(b for _m, u, _h, b in self.calls if u.endswith(("/access_token", "/token")))
        return {key: values[0] for key, values in parse_qs(body.decode()).items()}


@pytest.fixture
def provider(client: TestClient) -> FakeProviders:
    fake = FakeProviders()
    client.app.dependency_overrides[get_http_call] = lambda: fake  # type: ignore[attr-defined]
    return fake


def _configure(client: TestClient) -> None:
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        github_oauth_client_id="gh-id",
        github_oauth_client_secret="gh-secret",
        google_oauth_client_id="g-id",
        google_oauth_client_secret="g-secret",
        hub_url=HUB,
    )
    client.app.dependency_overrides[get_settings] = lambda: settings  # type: ignore[attr-defined]


@pytest.fixture
def configured(client: TestClient) -> None:
    _configure(client)


def _go(client: TestClient, path: str) -> httpx.Response:
    return client.get(path, follow_redirects=False)


def _begin(client: TestClient, name: str, **params: str) -> str:
    """Starts a sign-in and answers its `state`, the cookie now in the client's jar."""
    started = _go(client, START.format(provider=name) + ("?" + urlencode(params) if params else ""))
    assert started.status_code == 302, started.text
    query = parse_qs(urlsplit(started.headers["location"]).query)
    return query["state"][0]


def _finish(client: TestClient, name: str, state: str, code: str = CODE) -> httpx.Response:
    return _go(
        client, CALLBACK.format(provider=name) + "?" + urlencode({"code": code, "state": state})
    )


def _sign_in(client: TestClient, name: str = "github", **params: str) -> httpx.Response:
    return _finish(client, name, _begin(client, name, **params))


def _cookies(response: httpx.Response) -> list[str]:
    return [line.lower() for line in response.headers.get_list("set-cookie")]


def _logins(api_session: Session) -> list[Login]:
    api_session.expire_all()
    return list(api_session.scalars(select(Login)))


def _users(api_session: Session) -> int:
    return api_session.scalar(select(func.count()).select_from(User)) or 0


# ---- which providers are on -----------------------------------------------------------


def test_a_provider_with_no_pair_is_off_and_its_routes_go_back_to_the_login(
    client: TestClient, provider: FakeProviders
) -> None:
    assert client.get("/api/hub/auth/providers").json() == {"github": False, "google": False}
    for route in (START, CALLBACK):
        answer = _go(client, route.format(provider="github"))
        assert answer.status_code == 302
        assert answer.headers["location"] == "https://letsrebase.com/hub/login?oauth=off"
    assert provider.calls == []


def test_half_a_pair_is_off_and_a_whole_one_is_on(client: TestClient) -> None:
    settings = Settings(
        _env_file=None,
        github_oauth_client_id="id-only",
        google_oauth_client_id="g",
        google_oauth_client_secret="s",
    )  # type: ignore[call-arg]
    client.app.dependency_overrides[get_settings] = lambda: settings  # type: ignore[attr-defined]
    assert client.get("/api/hub/auth/providers").json() == {"github": False, "google": True}


def test_an_unknown_provider_is_not_a_route(client: TestClient, configured: None) -> None:
    assert _go(client, START.format(provider="apple")).status_code == 422


# ---- the way out ----------------------------------------------------------------------


def test_the_start_sets_one_short_lived_http_only_lax_cookie_and_goes_to_the_provider(
    client: TestClient, configured: None
) -> None:
    started = _go(client, START.format(provider="github"))
    assert started.status_code == 302
    target = urlsplit(started.headers["location"])
    query = parse_qs(target.query)
    assert (target.netloc, target.path) == ("github.com", "/login/oauth/authorize")
    assert query["client_id"] == ["gh-id"]
    assert query["scope"] == ["user:email"]
    assert query["redirect_uri"] == [
        "https://preview.letsrebase.com/api/hub/auth/oauth/github/callback"
    ]
    assert query["code_challenge_method"] == ["S256"]
    assert "code_challenge" in query and "state" in query
    cookie = next(line for line in _cookies(started) if line.startswith("rebase_oauth="))
    for attribute in (
        "httponly",
        "samesite=lax",
        "max-age=600",
        "path=/api/hub/auth/oauth",
        "secure",
    ):
        assert attribute in cookie
    assert started.headers["cache-control"] == "no-store"


def test_the_redirect_uri_follows_the_configuration_and_never_the_host_header(
    client: TestClient, configured: None
) -> None:
    started = client.get(
        START.format(provider="google"),
        follow_redirects=False,
        headers={"host": "evil.example", "x-forwarded-host": "evil.example"},
    )
    query = parse_qs(urlsplit(started.headers["location"]).query)
    assert query["redirect_uri"] == [
        "https://preview.letsrebase.com/api/hub/auth/oauth/google/callback"
    ]
    assert query["scope"] == ["openid email profile"]


# ---- a known person -------------------------------------------------------------------


def test_github_opens_a_known_freelancer_with_the_same_session_as_the_magic_link(
    client: TestClient, provider: FakeProviders, configured: None, api_session: Session
) -> None:
    apply_card(client)
    answer = _sign_in(client, "github", utm_source="email", utm_campaign="wave-3", origin="home")
    assert answer.status_code == 302
    assert answer.headers["location"] == f"{HUB}/me"
    assert answer.headers["referrer-policy"] == "no-referrer"
    cookies = _cookies(answer)
    session_cookie = next(line for line in cookies if line.startswith("rebase_user="))
    assert "httponly" in session_cookie and "samesite=lax" in session_cookie
    assert "max-age=2592000" in session_cookie
    assert any(line.startswith("rebase_oauth=") and "max-age=0" in line for line in cookies)

    me = client.get("/api/hub/me")
    assert me.status_code == 200 and me.json()["email"] == CARD_EMAIL

    [login] = _logins(api_session)
    assert (login.method, login.utm_source, login.utm_campaign, login.origin) == (
        "github",
        "email",
        "wave-3",
        "home",
    )
    # What left the hub: the code, the verifier and the registered redirect_uri, with the secret.
    form = provider.token_form()
    assert form["code"] == CODE and form["client_id"] == "gh-id"
    assert (
        form["redirect_uri"] == "https://preview.letsrebase.com/api/hub/auth/oauth/github/callback"
    )
    assert len(form["code_verifier"]) >= 43
    assert "tok-secret" not in json.dumps(login.__dict__, default=str)


def test_any_verified_github_address_may_match_not_only_the_primary(
    client: TestClient, provider: FakeProviders, configured: None
) -> None:
    apply_card(client)
    provider.github_emails = [
        {"email": "ada.personal@gmail.com", "verified": True, "primary": True},
        {"email": CARD_EMAIL, "verified": True, "primary": False},
    ]
    assert _sign_in(client).headers["location"] == f"{HUB}/me"
    assert client.get("/api/hub/me").json()["email"] == CARD_EMAIL


def test_google_opens_a_known_freelancer(
    client: TestClient, provider: FakeProviders, configured: None, api_session: Session
) -> None:
    apply_card(client)
    provider.google_info = {"email": "ADA@Studio.it", "email_verified": True, "name": "Ada"}
    answer = _sign_in(client, "google")
    assert answer.headers["location"] == f"{HUB}/me"
    assert client.get("/api/hub/me").json()["email"] == CARD_EMAIL
    assert [login.method for login in _logins(api_session)] == ["google"]
    assert provider.token_form()["grant_type"] == "authorization_code"


def test_an_admin_signs_in_this_way_too_and_lands_in_the_admin(
    client: TestClient, provider: FakeProviders, configured: None, api_session: Session
) -> None:
    api_session.add(User(email=ADMIN_EMAIL, first_name="Ivan", last_name="", role="admin"))
    api_session.commit()
    provider.github_emails = [{"email": ADMIN_EMAIL, "verified": True, "primary": True}]
    assert _sign_in(client).headers["location"] == f"{HUB}/admin"
    assert client.get("/api/hub/freelancers").status_code == 200


def test_the_login_shows_in_the_admins_list_with_its_method(
    client: TestClient,
    provider: FakeProviders,
    configured: None,
    api_session: Session,
    sender: RecordingSender,
) -> None:
    apply_card(client)
    _sign_in(client, "google")
    client.post("/api/hub/me/logout")
    api_session.add(User(email=ADMIN_EMAIL, first_name="Ivan", last_name="", role="admin"))
    api_session.commit()
    login_as(client, sender, ADMIN_EMAIL)
    recent = client.get("/api/hub/logins").json()["recent"]
    assert sorted(row["method"] for row in recent) == ["google", "link"]


def test_an_address_merged_into_somebody_opens_the_person_it_was_merged_into(
    client: TestClient, provider: FakeProviders, configured: None, api_session: Session
) -> None:
    apply_card(client)
    survivor = api_session.scalar(select(User).where(User.email == CARD_EMAIL))
    assert survivor is not None
    api_session.add(
        User(
            email="ada.old@studio.it",
            first_name="Ada",
            last_name="Lovelace",
            merged_into_id=survivor.id,
        )
    )
    api_session.commit()
    provider.google_info = {"email": "ada.old@studio.it", "email_verified": True}
    assert _sign_in(client, "google").headers["location"] == f"{HUB}/me"
    assert client.get("/api/hub/me").json()["id"] == str(survivor.id)


# ---- an address nobody here has -------------------------------------------------------


def test_an_unknown_verified_address_gets_the_no_account_page_and_creates_nothing(
    client: TestClient, provider: FakeProviders, configured: None, api_session: Session
) -> None:
    apply_card(client)
    before = _users(api_session)
    provider.google_info = {"email": "nobody@elsewhere.it", "email_verified": True}
    answer = _sign_in(client, "google")
    assert answer.headers["location"] == f"{HUB}/login/no-account"
    assert not any(line.startswith("rebase_user=") for line in _cookies(answer))
    assert client.get("/api/hub/me").status_code == 401
    assert _users(api_session) == before and _logins(api_session) == []

    shown = client.get("/api/hub/auth/oauth/no-account")
    assert shown.json() == {"provider": "google", "email": "nobody@elsewhere.it"}
    assert shown.headers["cache-control"] == "no-store"


def test_the_no_account_page_has_nothing_to_show_a_browser_that_never_came_from_a_sign_in(
    client: TestClient, configured: None
) -> None:
    assert client.get("/api/hub/auth/oauth/no-account").status_code == 404


def test_an_unverified_address_never_matches_even_when_it_is_a_login_address(
    client: TestClient, provider: FakeProviders, configured: None, api_session: Session
) -> None:
    apply_card(client)
    provider.github_emails = [
        {"email": CARD_EMAIL, "verified": False, "primary": True},
        {"email": "nobody@elsewhere.it", "verified": True, "primary": False},
    ]
    answer = _sign_in(client, "github")
    assert answer.headers["location"] == f"{HUB}/login/no-account"
    assert client.get("/api/hub/me").status_code == 401
    assert _logins(api_session) == []
    assert client.get("/api/hub/auth/oauth/no-account").json()["email"] == "nobody@elsewhere.it"


def test_a_provider_that_verified_no_address_sends_the_person_back_to_the_login(
    client: TestClient, provider: FakeProviders, configured: None
) -> None:
    apply_card(client)
    provider.google_info = {"email": CARD_EMAIL, "email_verified": False}
    assert _sign_in(client, "google").headers["location"] == f"{HUB}/login?oauth=unverified"
    assert client.get("/api/hub/me").status_code == 401


def test_an_extra_contact_point_never_decides_who_somebody_is(
    client: TestClient,
    provider: FakeProviders,
    configured: None,
    sender: RecordingSender,
    api_session: Session,
) -> None:
    apply_card(client)
    login_as(client, sender, CARD_EMAIL)
    point = {"kind": "email", "value": "ada.home@gmail.com", "label": "personal", "note": None}
    assert (
        client.put("/api/hub/me/contact-points", json={"contact_points": [point]}).status_code
        == 200
    )
    client.post("/api/hub/me/logout")
    before = _users(api_session)
    provider.github_emails = [{"email": "ada.home@gmail.com", "verified": True, "primary": True}]
    assert _sign_in(client).headers["location"] == f"{HUB}/login/no-account"
    assert client.get("/api/hub/me").status_code == 401
    assert _users(api_session) == before


def test_a_deleted_person_is_unknown(
    client: TestClient, provider: FakeProviders, configured: None, api_session: Session
) -> None:
    apply_card(client)
    api_session.execute(update(Freelancer).values(deleted_at=func.now()))
    api_session.commit()
    assert _sign_in(client).headers["location"] == f"{HUB}/login/no-account"
    assert client.get("/api/hub/me").status_code == 401
    assert _logins(api_session) == []


def test_two_people_behind_one_provider_account_open_nobody(
    client: TestClient, provider: FakeProviders, configured: None, api_session: Session
) -> None:
    apply_card(client)
    api_session.add(User(email="ada.two@studio.it", first_name="Ada", last_name="Due"))
    api_session.commit()
    provider.github_emails = [
        {"email": CARD_EMAIL, "verified": True, "primary": True},
        {"email": "ada.two@studio.it", "verified": True, "primary": False},
    ]
    assert _sign_in(client).headers["location"].endswith("/login?oauth=failed")
    assert client.get("/api/hub/me").status_code == 401


# ---- state ----------------------------------------------------------------------------


def test_a_callback_with_no_cookie_is_expired(
    client: TestClient, provider: FakeProviders, configured: None
) -> None:
    answer = _finish(client, "github", "anything")
    assert answer.headers["location"].endswith("/login?oauth=expired")
    assert provider.calls == []


def test_a_callback_with_the_wrong_state_is_expired_and_spends_the_attempt(
    client: TestClient, provider: FakeProviders, configured: None
) -> None:
    apply_card(client)
    state = _begin(client, "github")
    wrong = _finish(client, "github", state + "x")
    assert wrong.headers["location"].endswith("/login?oauth=expired")
    assert provider.calls == []
    # The attempt is spent: the right state, now, finds no cookie.
    assert _finish(client, "github", state).headers["location"].endswith("/login?oauth=expired")
    assert client.get("/api/hub/me").status_code == 401


def test_a_callback_without_a_state_is_expired(
    client: TestClient, provider: FakeProviders, configured: None
) -> None:
    _begin(client, "github")
    answer = _go(client, CALLBACK.format(provider="github") + "?code=x")
    assert answer.headers["location"].endswith("/login?oauth=expired")


def test_the_same_callback_cannot_be_used_twice(
    client: TestClient, provider: FakeProviders, configured: None, api_session: Session
) -> None:
    apply_card(client)
    state = _begin(client, "github")
    assert _finish(client, "github", state).headers["location"] == f"{HUB}/me"
    client.post("/api/hub/me/logout")
    replay = _finish(client, "github", state)
    assert replay.headers["location"].endswith("/login?oauth=expired")
    assert len([call for call in provider.calls if call[1].endswith("/access_token")]) == 1
    assert len(_logins(api_session)) == 1
    assert client.get("/api/hub/me").status_code == 401


def test_a_replay_with_the_cookie_put_back_still_stops_at_the_provider(
    client: TestClient, provider: FakeProviders, configured: None, api_session: Session
) -> None:
    apply_card(client)
    state = _begin(client, "github")
    sealed = client.cookies.get("rebase_oauth", path="/api/hub/auth/oauth")
    assert sealed
    assert _finish(client, "github", state).headers["location"] == f"{HUB}/me"
    client.post("/api/hub/me/logout")
    client.cookies.set("rebase_oauth", sealed, path="/api/hub/auth/oauth")
    replay = _finish(client, "github", state)
    assert replay.headers["location"].endswith("/login?oauth=failed")
    assert len(_logins(api_session)) == 1


def test_a_cookie_made_for_one_provider_does_not_open_the_other(
    client: TestClient, provider: FakeProviders, configured: None
) -> None:
    state = _begin(client, "github")
    answer = _finish(client, "google", state)
    assert answer.headers["location"].endswith("/login?oauth=expired")
    assert provider.calls == []


def test_a_person_who_says_no_at_the_provider_is_told_so_and_the_attempt_is_spent(
    client: TestClient, provider: FakeProviders, configured: None
) -> None:
    state = _begin(client, "google")
    answer = _go(
        client,
        CALLBACK.format(provider="google")
        + "?"
        + urlencode({"error": "access_denied", "state": state}),
    )
    assert answer.headers["location"].endswith("/login?oauth=denied")
    assert any("rebase_oauth=" in c and "max-age=0" in c for c in _cookies(answer))
    assert provider.calls == []


def test_a_provider_that_refuses_the_code_is_a_failure_not_a_sign_in(
    client: TestClient, provider: FakeProviders, configured: None
) -> None:
    apply_card(client)
    provider.refuse_token = True
    assert _sign_in(client).headers["location"].endswith("/login?oauth=failed")
    assert client.get("/api/hub/me").status_code == 401


# ---- the return path ------------------------------------------------------------------


@pytest.mark.parametrize(
    "hostile",
    [
        "https://evil.example/",
        "//evil.example",
        "/\\evil.example",
        "javascript:alert(1)",
        "/me/../..",
        "/cli?code=x",
    ],
)
def test_a_return_path_that_leaves_the_hub_lands_in_the_area_instead(
    client: TestClient, provider: FakeProviders, configured: None, hostile: str
) -> None:
    apply_card(client)
    answer = _sign_in(client, "github", next=hostile)
    assert answer.headers["location"] == f"{HUB}/me"


def test_a_return_path_forged_into_the_cookie_is_not_followed_either(
    client: TestClient, provider: FakeProviders, configured: None
) -> None:
    apply_card(client)
    state = _begin(client, "github")
    sealed = client.cookies.get("rebase_oauth", path="/api/hub/auth/oauth")
    assert sealed
    body = json.loads(base64.urlsafe_b64decode(sealed + "=" * (-len(sealed) % 4)))
    body["n"] = "https://evil.example/"
    forged = base64.urlsafe_b64encode(json.dumps(body).encode()).rstrip(b"=").decode()
    client.cookies.set("rebase_oauth", forged, path="/api/hub/auth/oauth")
    assert _finish(client, "github", state).headers["location"] == f"{HUB}/me"


def test_the_device_flow_page_is_reachable_through_a_social_sign_in(
    client: TestClient, provider: FakeProviders, configured: None
) -> None:
    apply_card(client)
    device = client.post("/api/hub/cli/device", json={"scope": "work_profile"}).json()
    code = device["user_code"]
    answer = _sign_in(client, "github", next=f"/cli?code={code}")
    assert answer.headers["location"] == f"{HUB}/cli?code={code}"
    read = client.get(f"/api/hub/cli/authorizations/{code}")
    assert read.status_code == 200 and read.json()["can_authorize"] is True


# ---- the budget -----------------------------------------------------------------------


def test_the_start_and_the_callback_spend_from_the_public_budget(
    client: TestClient, configured: None, strict_rate_limit: None
) -> None:
    for _ in range(5):
        assert (
            _go(client, START.format(provider="github"))
            .headers["location"]
            .startswith("https://github.com/")
        )
    limited = _go(client, START.format(provider="github"))
    assert limited.status_code == 302 and limited.headers["location"].endswith(
        "/login?oauth=limited"
    )
    callback = _go(client, CALLBACK.format(provider="github") + "?code=x&state=y")
    assert callback.headers["location"].endswith("/login?oauth=limited")
