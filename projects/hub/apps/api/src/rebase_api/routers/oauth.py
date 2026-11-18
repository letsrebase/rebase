"""Signing in with GitHub or Google (REB-1137, spec 2026-11-18).

Three browser navigations and one read, all under `/api/hub/auth/oauth`, none of them
creating anything:

- `GET /{provider}/start` seals `state`, the PKCE verifier, the return path and the campaign
  into a ten-minute cookie (`HttpOnly`, `SameSite=Lax`, scoped to this prefix) and redirects
  to the provider.
- `GET /{provider}/callback` is the redirect URI registered at the provider. It opens the
  cookie, spends it whatever happens (one attempt, one cookie), checks `state`, asks the
  provider which addresses it has verified, and hands them to `UserService.enter_with_provider`.
  A known person gets the magic link's session and a redirect into the SPA; an unknown address
  gets the no-account page; anything else a redirect to the login with `?oauth=<reason>`.
- `GET /no-account` is what that page reads: the provider and the address, from a cookie that
  lives ten minutes in the person's own browser and never reaches the database.

Every answer is a redirect, never a JSON error, because the reader is a browser in the middle
of a sign-in. `start` and `callback` spend from the public rate limit like `/auth/link` does.
The provider is asked nothing the hub does not need, and the redirect URI is built from
`hub_url`, not from the request.
"""

import logging
from typing import Literal

from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse

from rebase_api.deps import HttpCallDep, SessionDep, SettingsDep, set_member_cookie
from rebase_api.ratelimit import spend_one
from rebase_core.config import Settings
from rebase_core.oauth import (
    ATTEMPT_MINUTES,
    AuthProviders,
    MissedAccount,
    OAuthService,
    OAuthUnavailable,
    open_attempt,
    open_miss,
    safe_next,
    same_state,
    seal_attempt,
    seal_miss,
    utm_of,
)
from rebase_core.users import UserService

router = APIRouter(prefix="/api/hub/auth", tags=["hub-member"])

_log = logging.getLogger(__name__)

ATTEMPT_COOKIE = "rebase_oauth"
MISS_COOKIE = "rebase_oauth_miss"
COOKIE_PATH = "/api/hub/auth/oauth"

Provider = Literal["github", "google"]


class HideOAuthQuery(logging.Filter):
    """Keeps the provider's one-time `code` and `state` out of uvicorn's access log: the
    callback's URL carries both, and a container log is read by more people and tools than the
    request ever was. The path stays, the query becomes `?...`. nginx already logs no query."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and len(args) >= 3 and isinstance(args[2], str):
            target = args[2]
            if target.startswith(COOKIE_PATH + "/") and "?" in target:
                record.args = (*args[:2], target.split("?", 1)[0] + "?...", *args[3:])
        return True


# Once, at import: the filter belongs to the logger uvicorn writes its access lines with.
logging.getLogger("uvicorn.access").addFilter(HideOAuthQuery())


def _set_short_cookie(response: Response, settings: Settings, name: str, value: str) -> None:
    response.set_cookie(
        name,
        value,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        max_age=ATTEMPT_MINUTES * 60,
        path=COOKIE_PATH,
    )


def _drop_cookie(response: Response, settings: Settings, name: str) -> None:
    response.delete_cookie(
        name, path=COOKIE_PATH, httponly=True, secure=settings.cookie_secure, samesite="lax"
    )


def _redirect(url: str) -> RedirectResponse:
    """A redirect that no cache keeps and that tells the next page nothing about where it
    came from: the callback's URL carries the provider's one-time code."""
    response = RedirectResponse(url, status_code=status.HTTP_302_FOUND)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


def _to_login(service: OAuthService, reason: str) -> RedirectResponse:
    return _redirect(service.return_url(f"/login?oauth={reason}"))


@router.get("/providers", response_model=AuthProviders)
def providers(settings: SettingsDep, http: HttpCallDep) -> AuthProviders:
    """Which sign-in buttons the login page shows: a provider is on only when its OAuth
    app's id and secret are both configured here. Public, and it says nothing else."""
    return OAuthService(settings, http).providers_on()


@router.get(
    "/oauth/{provider}/start",
    status_code=status.HTTP_302_FOUND,
    response_class=RedirectResponse,
)
def start(
    provider: Provider, request: Request, settings: SettingsDep, http: HttpCallDep
) -> RedirectResponse:
    """Sends the browser to the provider. `next` is the page of the hub to come back to (the
    CLI's `/cli?code=...` is the one that matters) and is kept only when it is one; the
    campaign keys of the login page ride along so the login says where it came from."""
    service = OAuthService(settings, http)
    if not service.is_on(provider):
        return _to_login(service, "off")
    try:
        spend_one(request)
    except HTTPException:
        return _to_login(service, "limited")
    params = dict(request.query_params)
    begun = service.begin(provider, safe_next(params.get("next")), utm_of(params))
    response = _redirect(begun.url)
    _set_short_cookie(response, settings, ATTEMPT_COOKIE, seal_attempt(begun.attempt))
    return response


@router.get(
    "/oauth/{provider}/callback",
    status_code=status.HTTP_302_FOUND,
    response_class=RedirectResponse,
)
def callback(
    provider: Provider,
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    http: HttpCallDep,
) -> RedirectResponse:
    service = OAuthService(settings, http)
    if not service.is_on(provider):
        return _to_login(service, "off")
    try:
        spend_one(request)
    except HTTPException:
        return _to_login(service, "limited")

    # From here on the attempt is spent, whatever the outcome: the cookie is dropped on
    # every answer, so a replayed callback finds none.
    def done(response: RedirectResponse) -> RedirectResponse:
        _drop_cookie(response, settings, ATTEMPT_COOKIE)
        return response

    attempt = open_attempt(request.cookies.get(ATTEMPT_COOKIE))
    state = request.query_params.get("state", "")
    if attempt is None or attempt.provider != provider or not same_state(attempt.state, state):
        return done(_to_login(service, "expired"))
    if request.query_params.get("error"):
        reason = "denied" if request.query_params["error"] == "access_denied" else "failed"
        return done(_to_login(service, reason))
    code = request.query_params.get("code", "")
    if not code or len(code) > 2048:
        return done(_to_login(service, "failed"))

    try:
        addresses = service.verified_addresses(attempt, code)
    except OAuthUnavailable as failure:
        _log.warning("sign-in with %s failed at the %s step", provider, failure)
        return done(_to_login(service, "failed"))
    if not addresses:
        return done(_to_login(service, "unverified"))

    entry = UserService(session, settings).enter_with_provider(addresses, provider, attempt.utm)
    if entry.outcome == "ambiguous":
        _log.warning("sign-in with %s: the verified addresses name more than one person", provider)
        return done(_to_login(service, "failed"))
    if entry.outcome == "no_account" or entry.user is None or entry.raw_session is None:
        response = done(_redirect(service.return_url("/login/no-account")))
        miss = MissedAccount(provider=provider, email=addresses[0])
        _set_short_cookie(response, settings, MISS_COOKIE, seal_miss(miss))
        return response

    landing = attempt.next_path or ("/admin" if entry.user.role == "admin" else "/me")
    response = done(_redirect(service.return_url(landing)))
    _drop_cookie(response, settings, MISS_COOKIE)
    set_member_cookie(response, settings, entry.raw_session)
    return response


@router.get("/oauth/no-account", response_model=MissedAccount)
def no_account(request: Request, response: Response) -> MissedAccount:
    """The provider and the address the no-account page names, from the cookie the callback
    left in this browser; 404 when there is none (a page opened by hand, or an expired one)."""
    response.headers["Cache-Control"] = "no-store"
    miss = open_miss(request.cookies.get(MISS_COOKIE))
    if miss is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "Nessun accesso in sospeso.",
            headers={"Cache-Control": "no-store"},
        )
    return miss
