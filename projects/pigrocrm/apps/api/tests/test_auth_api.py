from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

import pigrocrm.core.auth.refresh_service as refresh_service
from pigrocrm.core.actor import Actor
from pigrocrm.core.auth.schemas import UserCreate, UserUpdate
from pigrocrm.core.auth.service import UserService
from pigrocrm.core.config import Settings, get_settings
from pigrocrm.core.errors import NotFound
from pigrocrm_api.deps import ACCESS_COOKIE, REFRESH_COOKIE, get_session
from pigrocrm_api.main import create_app
from pigrocrm_api.ratelimit import LOGIN_REQUESTS_PER_MINUTE, REQUESTS_PER_MINUTE

CREDENTIALS = {"email": "admin@pigro.it", "password": "supersegreta1"}


def _create_deactivated_user(session: Session, email: str) -> None:
    """A user who authenticated correctly right up until an admin flipped `attivo`
    off -- must fail login exactly like a wrong password or an unknown email, per
    UserService.authenticate's own anti-enumeration contract."""
    users = UserService(session)
    user = users.create(
        UserCreate(email=email, password="supersegreta1", nome="Disattivato", ruolo="admin"),
        Actor.system(),
    )
    users.update(user.id, UserUpdate(attivo=False), Actor.system())


def _present_refresh_cookie(client: TestClient, token: str) -> None:
    """Replaces the jar's refresh cookie with `token`, as a browser would.

    `client.cookies.set` on its own adds a second entry next to the one the last
    Set-Cookie left, and the two then travel in one header; a browser holds one cookie
    per (name, domain, path) and would have overwritten it. The API reads the most
    specific -- the first -- so a stale duplicate here would test the wrong token."""
    # Removed by the exact (domain, path, name) the jar holds -- `http.cookiejar` files
    # a dotless host as `testserver.local`, and `Cookies.delete` without a domain
    # recurses forever in httpx -- then set again under the same domain.
    existing = [c for c in client.cookies.jar if c.name == REFRESH_COOKIE]
    for cookie in existing:
        client.cookies.jar.clear(cookie.domain, cookie.path, cookie.name)
    domain = existing[0].domain if existing else "testserver.local"
    client.cookies.set(REFRESH_COOKIE, token, domain=domain, path="/")


def test_login_sets_httponly_cookies(client: TestClient, admin_user) -> None:
    response = client.post("/api/auth/login", json=CREDENTIALS)
    assert response.status_code == 200
    assert ACCESS_COOKIE in response.cookies
    assert REFRESH_COOKIE in response.cookies
    # The token must never be readable by JavaScript.
    assert "httponly" in response.headers["set-cookie"].lower()
    # Production's default: TLS-only, so a stolen network capture cannot replay it.
    assert "secure" in response.headers["set-cookie"].lower()


def test_cookie_secure_false_omits_the_secure_attribute_for_local_dev_over_http(
    api_session: Session, admin_user
) -> None:
    """Chrome and Firefox treat `localhost` as a secure context and accept `Secure`
    cookies over plain HTTP there, but Safari does not and has no plan to -- and slice
    1B's local dev proxies through http://localhost with no TLS. Without a way to turn
    this off, login on Safari in development would look fine (200) while the browser
    silently discarded the cookie, and every later request would look unauthenticated
    with no visible error anywhere. Production's default stays secure; only an
    explicit override changes this."""
    app = create_app()
    app.dependency_overrides[get_session] = lambda: api_session
    app.dependency_overrides[get_settings] = lambda: Settings(
        jwt_secret="test-secret-for-the-api-test-suite-only", cookie_secure=False
    )
    with TestClient(app, base_url="http://testserver") as insecure_client:
        response = insecure_client.post("/api/auth/login", json=CREDENTIALS)

    assert response.status_code == 200
    assert "secure" not in response.headers["set-cookie"].lower()
    # Still httpOnly -- turning off Secure for local HTTP must not also give up the
    # unrelated protection against JavaScript reading the cookie.
    assert "httponly" in response.headers["set-cookie"].lower()


def test_login_does_not_return_the_token_in_the_body(client: TestClient, admin_user) -> None:
    body = client.post("/api/auth/login", json=CREDENTIALS).json()
    assert "access_token" not in body
    assert body["email"] == "admin@pigro.it"


def test_login_with_wrong_password_is_401(client: TestClient, admin_user) -> None:
    """Authentication failing is "not authenticated" (401), not "the request body was
    unprocessable" (422) -- the same convention an invalid refresh token and an invalid
    access token already follow a few lines below and in deps.get_actor.
    UserService.authenticate raises ValidationFailed here, like it does for every
    caller of this endpoint, but the router now maps that one call site to 401 instead
    of falling through to STATUS_BY_CODE's default (422), which is still correct for
    every other endpoint's ValidationFailed."""
    response = client.post("/api/auth/login", json={**CREDENTIALS, "password": "sbagliata"})
    assert response.status_code == 401


def test_login_with_an_unknown_email_is_401(client: TestClient) -> None:
    response = client.post(
        "/api/auth/login", json={"email": "nessuno@pigro.it", "password": "irrilevante"}
    )
    assert response.status_code == 401


def test_login_by_a_deactivated_user_is_401(client: TestClient, api_session: Session) -> None:
    _create_deactivated_user(api_session, "disattivato@pigro.it")
    response = client.post(
        "/api/auth/login",
        json={"email": "disattivato@pigro.it", "password": "supersegreta1"},
    )
    assert response.status_code == 401


def test_login_is_throttled_per_client_before_the_password_is_checked(
    client: TestClient, admin_user, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`spend_one` runs before `UserService.authenticate`, so the request past the
    budget never pays for the argon2 verify at all -- the whole point of REB-270,
    which exists because that verify is a 64 MiB, time-cost-3 hash anyone could
    trigger at line rate. Wrapping `authenticate` with a counter, rather than trusting
    the 401/429 split alone, is what actually proves that: a limiter placed after the
    verify but before the 401 raise would produce the exact same responses while
    paying for the hash on every one of the eleven attempts. On its own budget, not
    `REQUESTS_PER_MINUTE`'s five: this loop runs `LOGIN_REQUESTS_PER_MINUTE` (ten)
    attempts, which would already be a 429 on a shared bucket with the signup
    routes."""
    calls = 0
    real_authenticate = UserService.authenticate

    def counting_authenticate(self: UserService, email: str, password: str) -> object:
        nonlocal calls
        calls += 1
        return real_authenticate(self, email, password)

    monkeypatch.setattr(UserService, "authenticate", counting_authenticate)

    for _ in range(LOGIN_REQUESTS_PER_MINUTE):
        response = client.post("/api/auth/login", json={**CREDENTIALS, "password": "sbagliata"})
        assert response.status_code == 401, response.text
    refused = client.post("/api/auth/login", json={**CREDENTIALS, "password": "sbagliata"})
    assert refused.status_code == 429, refused.text
    assert refused.headers["Retry-After"] == "60"
    assert calls == LOGIN_REQUESTS_PER_MINUTE
    # Another client has its own bucket, still under budget -- 401 like the rest, not
    # the 429 this client's own bucket would now give it.
    other = client.post(
        "/api/auth/login",
        json={**CREDENTIALS, "password": "sbagliata"},
        headers={"X-Real-IP": "10.0.0.7"},
    )
    assert other.status_code == 401, other.text
    assert calls == LOGIN_REQUESTS_PER_MINUTE + 1


def test_login_failures_are_byte_identical_regardless_of_cause(
    client: TestClient, api_session: Session, admin_user
) -> None:
    """UserService.authenticate deliberately raises one identical error for unknown
    email, wrong password, and a deactivated user -- with a constant-time dummy hash so
    even response timing does not leak which case happened. That property is only worth
    anything if the HTTP layer preserves it all the way out: this pins the three
    responses as byte-identical, not merely "all 401", so a future change that gives
    even one of the three cases its own message would fail this test instead of quietly
    reopening the enumeration gap."""
    _create_deactivated_user(api_session, "disattivato@pigro.it")

    unknown_email = client.post(
        "/api/auth/login", json={"email": "nessuno@pigro.it", "password": "irrilevante"}
    )
    wrong_password = client.post("/api/auth/login", json={**CREDENTIALS, "password": "sbagliata"})
    deactivated_user = client.post(
        "/api/auth/login",
        json={"email": "disattivato@pigro.it", "password": "supersegreta1"},
    )

    for response in (unknown_email, wrong_password, deactivated_user):
        assert response.status_code == 401
    assert unknown_email.content == wrong_password.content == deactivated_user.content


def test_me_requires_authentication(client: TestClient) -> None:
    assert client.get("/api/auth/me").status_code == 401


def test_me_returns_the_current_user(logged_in: TestClient) -> None:
    body = logged_in.get("/api/auth/me").json()
    assert body["email"] == "admin@pigro.it"
    assert body["ruolo"] == "admin"


def test_update_me_requires_authentication(client: TestClient) -> None:
    response = client.patch("/api/auth/me", json={"digest_settimanale": False})
    assert response.status_code == 401


def test_a_non_admin_can_switch_off_their_own_weekly_report(
    collaborator_client: TestClient,
) -> None:
    """Spec 2026-09-16 §3.6: the mail's own opt-out link must work for whoever
    received it -- `PATCH /api/auth/me` (`UserService.update_own_digest`) takes no
    `actor.require_admin`, unlike `PATCH /api/users/{id}`, which a `collaboratore`
    cannot call at all."""
    response = collaborator_client.patch("/api/auth/me", json={"digest_settimanale": False})
    assert response.status_code == 200
    assert response.json()["digest_settimanale"] is False

    assert collaborator_client.get("/api/auth/me").json()["digest_settimanale"] is False


def test_update_me_is_401_when_the_session_outlives_the_row(
    logged_in: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The service owns the row, the router owns the answer.

    `update_own_digest` loads the caller itself and raises `NotFound` when there is no
    row behind the token -- which every other endpoint of this API renders as a 404. On
    *this* router it means «not authenticated», exactly as `GET /me` answers it, and
    that translation is what this endpoint is left holding.
    """

    def sparito(self: UserService, actor: Actor, value: bool) -> None:
        raise NotFound("user", "sparito")

    monkeypatch.setattr(UserService, "update_own_digest", sparito)

    response = logged_in.patch("/api/auth/me", json={"digest_settimanale": False})

    assert response.status_code == 401


def test_logout_clears_the_cookies(logged_in: TestClient) -> None:
    assert logged_in.post("/api/auth/logout").status_code == 204
    assert logged_in.get("/api/auth/me").status_code == 401


def test_refresh_issues_a_new_access_cookie(logged_in: TestClient) -> None:
    response = logged_in.post("/api/auth/refresh")
    assert response.status_code == 200
    assert ACCESS_COOKIE in response.cookies


def test_refresh_with_an_invalid_refresh_token_is_401(client: TestClient) -> None:
    """A refresh token is a credential, not user-submitted form data -- an invalid or
    expired one must read as "not authenticated" (401), the same way an invalid access
    token does in get_actor, not as a generic 422 validation failure."""
    client.cookies.set(REFRESH_COOKIE, "not-a-real-jwt")
    response = client.post("/api/auth/refresh")
    assert response.status_code == 401


def _past_the_grace_window(monkeypatch: pytest.MonkeyPatch) -> None:
    """Move the refresh service's clock beyond REFRESH_GRACE_SECONDS.

    The service reads the wall clock through its module-level `datetime` -- the single
    point `packages/core/tests/test_refresh_tokens.py` already freezes -- and the app
    under `TestClient` runs in this same process, so shifting it here shifts what the
    endpoint sees. The alternative is a test that sleeps for eleven seconds.
    """
    real_datetime = refresh_service.datetime

    class _Shifted(real_datetime):  # type: ignore[valid-type,misc]
        @classmethod
        def now(cls, tz: object = None) -> datetime:
            return real_datetime.now(tz) + timedelta(
                seconds=refresh_service.REFRESH_GRACE_SECONDS + 1
            )

    monkeypatch.setattr(refresh_service, "datetime", _Shifted)


def test_refresh_rotation_makes_the_old_token_unusable(
    logged_in: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole point of rotation: if the old refresh token still worked after being
    used once, a copy of it -- taken at any point before rotation -- would stay usable
    for the rest of its 180-day life no matter how many times the legitimate user
    rotated past it.

    The clock is moved past the grace window first, because inside it the same token
    presented again is answered instead of rejected (see the two tests below): that
    window is measured in seconds and this is the case it deliberately does not
    cover -- a copy resurfacing later, which is the actual shape of theft."""
    old_refresh_token = logged_in.cookies.get(REFRESH_COOKIE)
    assert old_refresh_token is not None

    first = logged_in.post("/api/auth/refresh")
    assert first.status_code == 200

    # Simulate presenting the token again after it has already been rotated away --
    # e.g. a copy an attacker made before rotation happened.
    _past_the_grace_window(monkeypatch)
    _present_refresh_cookie(logged_in, old_refresh_token)
    replay = logged_in.post("/api/auth/refresh")
    assert replay.status_code == 401


def test_a_refresh_replayed_within_the_grace_window_answers_exactly_as_the_first_did(
    logged_in: TestClient,
) -> None:
    """Two tabs, one cookie jar. The refresh cookie belongs to the browser, not to a
    tab, so two tabs waking up past the fifteen-minute access cookie both POST here
    with the same token -- and the second one used to revoke the whole family and drop
    the owner on the login screen, in every tab at once. It reported as "it logs me out
    when I have two tabs open".

    The assertion is on the `Set-Cookie` headers themselves, not merely on the status:
    the second tab has to end up holding *the same* pair as the first, or the two tabs
    disagree about which refresh cookie is current and the next rotation kills one of
    them. Identical headers is also what makes the two branches indistinguishable from
    outside -- a response that advertised "this was a grace answer" would leak how long
    ago another tab refreshed."""
    old_refresh_token = logged_in.cookies.get(REFRESH_COOKIE)
    assert old_refresh_token is not None

    first = logged_in.post("/api/auth/refresh")
    assert first.status_code == 200

    _present_refresh_cookie(logged_in, old_refresh_token)
    second = logged_in.post("/api/auth/refresh")

    assert second.status_code == 200
    assert second.headers.get_list("set-cookie") == first.headers.get_list("set-cookie")
    assert second.json() == first.json()


def test_the_grace_window_leaves_the_rest_of_the_session_alive(logged_in: TestClient) -> None:
    """The damage the window exists to prevent was never the 401 on the replay itself --
    it was `_revoke_all_valid` firing behind it and killing every other token the user
    held. So the tell is the *third* request: the pair the tabs now share still works,
    and the next rotation still rotates."""
    old_refresh_token = logged_in.cookies.get(REFRESH_COOKIE)
    assert old_refresh_token is not None

    assert logged_in.post("/api/auth/refresh").status_code == 200
    _present_refresh_cookie(logged_in, old_refresh_token)
    assert logged_in.post("/api/auth/refresh").status_code == 200

    assert logged_in.get("/api/auth/me").status_code == 200
    assert logged_in.post("/api/auth/refresh").status_code == 200


def test_logout_invalidates_the_refresh_token_server_side(logged_in: TestClient) -> None:
    """logout must do more than empty the browser's cookie jar: the same refresh token,
    presented again after logout, must be dead server-side too -- otherwise a copy
    taken before logout stays valid for the rest of its 30-day life."""
    refresh_token = logged_in.cookies.get(REFRESH_COOKIE)
    assert refresh_token is not None

    assert logged_in.post("/api/auth/logout").status_code == 204

    _present_refresh_cookie(logged_in, refresh_token)
    response = logged_in.post("/api/auth/refresh")
    assert response.status_code == 401


def test_logout_consumes_every_refresh_token_the_browser_sends(
    client: TestClient, admin_user
) -> None:
    """A browser keeps one cookie per path and sends every one that matches: a session
    opened at `/` before the root got its own name and the one opened at `/studiorossi/`
    after it arrive as two `refresh_token=` pairs in a single header. `request.cookies`
    keeps only the last, and a logout that consumed only that one left the other alive
    -- the session the person had just ended came back on the next refresh."""
    first = client.post("/api/auth/login", json=CREDENTIALS).cookies.get(REFRESH_COOKIE)
    second = client.post("/api/auth/login", json=CREDENTIALS).cookies.get(REFRESH_COOKIE)
    assert first and second and first != second

    bare = TestClient(client.app, base_url="https://testserver")
    response = bare.post(
        "/api/auth/logout",
        headers={"Cookie": f"{REFRESH_COOKIE}={second}; {REFRESH_COOKIE}={first}"},
    )
    assert response.status_code == 204

    for token in (first, second):
        again = TestClient(client.app, base_url="https://testserver")
        _present_refresh_cookie(again, token)
        assert again.post("/api/auth/refresh").status_code == 401


def test_a_personal_access_token_authenticates_too(logged_in: TestClient) -> None:
    """The same API serves the browser and the agent; only the credential differs."""
    raw = logged_in.post("/api/tokens", json={"nome": "Claude"}).json()["token"]

    bare = TestClient(logged_in.app)
    response = bare.get("/api/auth/me", headers={"Authorization": f"Bearer {raw}"})
    assert response.status_code == 200
    assert response.json()["email"] == "admin@pigro.it"


def test_an_invalid_bearer_token_is_401(client: TestClient) -> None:
    response = client.get("/api/auth/me", headers={"Authorization": "Bearer pgc_inventato"})
    assert response.status_code == 401


def test_openapi_document_is_served(client: TestClient) -> None:
    schema = client.get("/openapi.json").json()
    assert schema["info"]["title"] == "PigroCRM API"


def test_me_and_refresh_document_401_but_logout_does_not() -> None:
    """`me` and `refresh` both actually return 401 (see test_me_requires_authentication
    and test_refresh_with_an_invalid_refresh_token_is_401 above), but PROBLEM_RESPONSES
    (errors.py, shared by every router) only declares 403/404/409/422 -- a generated
    TypeScript client would type this response as `unknown` for exactly the status code
    a frontend auth layer branches on programmatically (session expired -> try refresh
    -> redirect to login). `logout` must NOT gain the same entry: it has no actor
    dependency and is idempotent by construction (an absent or already-invalid refresh
    cookie is simply nothing left to invalidate, per its own docstring), so it cannot
    structurally produce a 401 the way these two can -- claiming one anyway would
    misdocument a response this route never sends.

    No database fixture: building the app and reading its schema never opens a session,
    same reasoning as test_error_rendering.py's equivalent 422-shape check."""
    app = create_app()
    schema = app.openapi()

    me_responses = schema["paths"]["/api/auth/me"]["get"]["responses"]
    refresh_responses = schema["paths"]["/api/auth/refresh"]["post"]["responses"]
    logout_responses = schema["paths"]["/api/auth/logout"]["post"]["responses"]

    assert "401" not in logout_responses

    for responses in (me_responses, refresh_responses):
        assert "401" in responses
        content = responses["401"]["content"]
        assert set(content) == {"application/json"}
        body_schema = content["application/json"]["schema"]
        assert body_schema["properties"]["detail"]["type"] == "string"
        assert body_schema["required"] == ["detail"]

    # The addition is additive, not a replacement: both routes still inherit the
    # router-wide domain-error responses alongside their own new 401.
    for responses in (me_responses, refresh_responses):
        assert {"403", "404", "409", "422"} <= set(responses)


# --- Coordinator follow-up on final review item 1: the NUL-byte gap was live --
# --- and reachable with zero credentials through /api/auth/login. ------------


def test_login_with_a_nul_byte_in_email_is_422_not_500(client: TestClient) -> None:
    """UserRepository.get_by_email binds `email` straight into a SELECT ... WHERE
    email = :email; psycopg refuses to adapt any string parameter containing a
    NUL byte. Before LoginRequest.email was SafeStr, this reached that query raw
    and came back as an uncaught 500 -- reachable by anyone, no cookie or PAT
    required, since login is the one endpoint that must work with zero
    credentials."""
    response = client.post(
        "/api/auth/login", json={"email": "admin\x00@pigro.it", "password": "supersegreta1"}
    )
    assert response.status_code == 422


def test_login_with_a_nul_byte_does_not_reveal_whether_the_email_exists(
    client: TestClient, admin_user
) -> None:
    """The rejection happens at the schema layer, before authenticate() runs any
    query at all -- so a malformed email that happens to match a real user and
    one that does not must produce indistinguishable responses, the same
    anti-enumeration discipline UserService.authenticate already applies via its
    dummy-hash timing defence for the ordinary wrong-password/unknown-email case."""
    existing = client.post(
        "/api/auth/login", json={"email": f"{CREDENTIALS['email']}\x00", "password": "x"}
    )
    unknown = client.post(
        "/api/auth/login", json={"email": "nobody-at-all\x00@example.it", "password": "x"}
    )
    assert existing.status_code == unknown.status_code == 422
    existing_error, unknown_error = existing.json()["detail"][0], unknown.json()["detail"][0]
    assert existing_error["type"] == unknown_error["type"]
    assert existing_error["msg"] == unknown_error["msg"]
    assert existing_error["loc"] == unknown_error["loc"]


# --- a link by mail (spec 2026-09-12 §6.2) --------------------------------------------

from pigrocrm.core.mail import RecordingSender  # noqa: E402
from pigrocrm_api.routers.auth import get_sender  # noqa: E402

ADMIN_EMAIL = CREDENTIALS["email"]


PUBLIC_URL = "https://pigro.test"


@pytest.fixture
def sender(client: TestClient) -> RecordingSender:
    """A recording sender, and the public origin a link needs: without either the
    endpoint answers 503 by design."""
    recording = RecordingSender()
    client.app.dependency_overrides[get_sender] = lambda: recording  # type: ignore[attr-defined]
    client.app.dependency_overrides[get_settings] = lambda: Settings(  # type: ignore[attr-defined]
        public_url=PUBLIC_URL,
        _env_file=None,  # type: ignore[call-arg]
    )
    return recording


def _token_from(mail_text: str) -> str:
    return mail_text.split("?t=", 1)[1].split()[0]


def test_link_answers_202_and_mails_a_known_address(
    client: TestClient, admin_user, sender: RecordingSender
) -> None:
    response = client.post("/api/auth/link", json={"email": ADMIN_EMAIL.upper()})
    assert response.status_code == 202
    assert len(sender.sent) == 1
    mail = sender.sent[0]
    assert mail.to == ADMIN_EMAIL and "15 minuti" in mail.text
    # The link wears the configured public origin, never the request's Host.
    assert f"{PUBLIC_URL}/app/verify?t=" in mail.text and "testserver" not in mail.text


def test_link_answers_202_and_mails_nothing_for_an_unknown_address(
    client: TestClient, sender: RecordingSender
) -> None:
    response = client.post("/api/auth/link", json={"email": "nessuno@pigro.it"})
    assert response.status_code == 202
    assert sender.sent == []


def test_link_is_503_without_a_sender(client: TestClient, admin_user) -> None:
    response = client.post("/api/auth/link", json={"email": ADMIN_EMAIL})
    assert response.status_code == 503
    assert "non è ancora attivo" in response.json()["detail"]


def test_the_link_request_is_throttled_per_client(client: TestClient, admin_user) -> None:
    """Unauthenticated by design, so the bucket is what stops a script mail-bombing a
    known address (REB-228): the request past the budget is a 429 with a
    `Retry-After`. No `sender` fixture on purpose, unlike the mail-flow tests around
    this one: every request under budget is `test_link_is_503_without_a_sender`'s own
    503, so only the request that trips the limiter is a 429 rather than a 503 -- proof
    that `spend_one` runs before the sender check, not merely consistent with it (the
    prior version of this test ran every request with a sender configured, which is
    equally consistent with the limiter running after that check, or not at all)."""
    for _ in range(REQUESTS_PER_MINUTE):
        response = client.post("/api/auth/link", json={"email": ADMIN_EMAIL})
        assert response.status_code == 503, response.text
    refused = client.post("/api/auth/link", json={"email": ADMIN_EMAIL})
    assert refused.status_code == 429, refused.text
    assert refused.headers["Retry-After"] == "60"
    # Another client has its own bucket, still under budget -- 503 like the rest, not
    # the 429 this client's own bucket would now give it.
    other = client.post(
        "/api/auth/link", json={"email": ADMIN_EMAIL}, headers={"X-Real-IP": "10.0.0.7"}
    )
    assert other.status_code == 503, other.text


def test_entra_sets_the_cookies_and_me_answers(
    client: TestClient, admin_user, sender: RecordingSender
) -> None:
    client.post("/api/auth/link", json={"email": ADMIN_EMAIL})
    token = _token_from(sender.sent[0].text)
    response = client.post("/api/auth/verify", json={"t": token})
    assert response.status_code == 200, response.text
    assert response.json()["email"] == ADMIN_EMAIL
    set_cookie = response.headers.get_list("set-cookie")
    assert any("pigrocrm_access=" in c and "Path=/" in c for c in set_cookie)
    assert client.get("/api/auth/me").status_code == 200
    # Spent: the same link a second time is a 401 with the sentence the page shows.
    again = client.post("/api/auth/verify", json={"t": token})
    assert again.status_code == 401 and "link" in again.json()["detail"]


def test_entra_with_garbage_is_401(client: TestClient) -> None:
    assert client.post("/api/auth/verify", json={"t": "x"}).status_code == 401


def test_link_is_503_without_a_public_url(client: TestClient, admin_user) -> None:
    """A link built from the request's Host would hand a live token to whatever host the
    caller named: no public origin, no link."""
    client.app.dependency_overrides[get_sender] = lambda: RecordingSender()  # type: ignore[attr-defined]
    response = client.post("/api/auth/link", json={"email": ADMIN_EMAIL})
    assert response.status_code == 503
    assert "PIGROCRM_PUBLIC_URL" in response.json()["detail"]


def test_the_first_entry_kills_the_earlier_session_and_keeps_its_own(
    client: TestClient, admin_user, sender: RecordingSender
) -> None:
    """The guarantee the signup will rely on: a session opened before the address was
    proven dies at the first link entry; the session the entry opens refreshes fine."""
    earlier = client.post(
        "/api/auth/login", json={"email": ADMIN_EMAIL, "password": CREDENTIALS["password"]}
    )
    assert earlier.status_code == 200
    earlier_refresh = client.cookies.get(REFRESH_COOKIE)
    assert earlier_refresh
    client.cookies.clear()

    client.post("/api/auth/link", json={"email": ADMIN_EMAIL})
    token = _token_from(sender.sent[0].text)
    entered = client.post("/api/auth/verify", json={"t": token})
    assert entered.status_code == 200, entered.text
    # The new session refreshes.
    assert client.post("/api/auth/refresh").status_code == 200
    # The earlier one does not.
    client.cookies.clear()
    client.cookies.set(REFRESH_COOKIE, earlier_refresh)
    assert client.post("/api/auth/refresh").status_code == 401
