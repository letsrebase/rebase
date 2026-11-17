"""The CLI's sign-in from the browser (REB-1133, spec 2026-11-17 § 2).

`POST /device` and `POST /token` are the CLI's: unauthenticated by design, RFC 8628's shapes, so
the CLI reads the answer's `error` whatever the status (a refusal is a 400 with `{"error": ...}`).
Everything under `/authorizations/{code}` is the signed-in person's, the calls `/hub/cli` makes.

Two limits, answered 429 with a sentence: 10 starts an hour per client address (`X-Real-IP`, an
IPv6 address as its /64, the rule of every public door here), and 5 wrong codes per person in 10
minutes. Both count in this process like the other limiters (`rebase_core.ratelimit`), the floor
under nginx's.
"""

from collections.abc import Callable
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from rebase_api.deps import MeDep, SessionDep, SettingsDep
from rebase_api.ratelimit import client_key
from rebase_core.cli_auth import (
    CliAuthorizationRead,
    CliAuthService,
    DeviceStart,
    TokenGranted,
    TokenRefused,
)
from rebase_core.errors import CliCodeNotFound
from rebase_core.ratelimit import RateLimiter

router = APIRouter(prefix="/api/hub/cli", tags=["hub"])

STARTS_PER_HOUR = 10
WRONG_CODES_PER_WINDOW = 5
WRONG_CODE_WINDOW_SECONDS = 600

_starts = RateLimiter(STARTS_PER_HOUR, 3600)
_wrong_codes = RateLimiter(WRONG_CODES_PER_WINDOW, WRONG_CODE_WINDOW_SECONDS)


def reset_cli_limits() -> None:
    """Forgets every client and person. For tests."""
    _starts.reset()
    _wrong_codes.reset()


class DeviceRequest(BaseModel):
    scope: Literal["work_profile"] = "work_profile"


class TokenRequest(BaseModel):
    # 43 characters as minted; the bound keeps a hostile value out of the hash and the log.
    device_code: str = Field(max_length=200)


@router.post("/device", response_model=DeviceStart)
def start(
    request: Request, body: DeviceRequest, session: SessionDep, settings: SettingsDep
) -> DeviceStart:
    if not _starts.spend(client_key(request)):
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Troppi accessi avviati da qui. Riprova tra un'ora.",
            headers={"Retry-After": "3600"},
        )
    return CliAuthService(session, settings).start(body.scope)


@router.post("/token", response_model=TokenGranted, responses={400: {"model": TokenRefused}})
def token(body: TokenRequest, session: SessionDep, settings: SettingsDep) -> JSONResponse:
    """Never cached: the 200 holds the access token."""
    polled = CliAuthService(session, settings).poll(body.device_code)
    no_store = {"Cache-Control": "no-store"}
    if polled.error is not None:
        return JSONResponse({"error": polled.error}, status_code=400, headers=no_store)
    granted = TokenGranted(
        access_token=str(polled.access_token), expires_in=int(polled.expires_in or 0)
    )
    return JSONResponse(granted.model_dump(), headers=no_store)


def same_origin_only(sec_fetch_site: Annotated[str | None, Header()] = None) -> None:
    """A decision is made from `/hub/cli` itself: a request a browser says came from another
    site, or only from a sibling subdomain (`same-site`), is refused. Clients that send no
    `Sec-Fetch-Site` (scripts, tests) are not browsers and carry no ambient cookie."""
    if sec_fetch_site is not None and sec_fetch_site != "same-origin":
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "Questa richiesta non viene da questa pagina."
        )


def _counted(me: MeDep, call: Callable[[], CliAuthorizationRead]) -> CliAuthorizationRead:
    """Runs `call`, a wrong code counting against the person's budget."""
    key = str(me.id)
    if not _wrong_codes.has_room(key):
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Troppi codici sbagliati. Riprova tra qualche minuto.",
            headers={"Retry-After": str(WRONG_CODE_WINDOW_SECONDS)},
        )
    try:
        result = call()
    except CliCodeNotFound:
        _wrong_codes.spend(key)
        raise
    return result


@router.get("/authorizations/{code}", response_model=CliAuthorizationRead)
def read(code: str, me: MeDep, session: SessionDep, settings: SettingsDep) -> CliAuthorizationRead:
    return _counted(me, lambda: CliAuthService(session, settings).read(code, me.id))


@router.post(
    "/authorizations/{code}/approve",
    response_model=CliAuthorizationRead,
    dependencies=[Depends(same_origin_only)],
)
def approve(
    code: str, me: MeDep, session: SessionDep, settings: SettingsDep
) -> CliAuthorizationRead:
    return _counted(me, lambda: CliAuthService(session, settings).approve(code, me.id))


@router.post(
    "/authorizations/{code}/deny",
    response_model=CliAuthorizationRead,
    dependencies=[Depends(same_origin_only)],
)
def deny(code: str, me: MeDep, session: SessionDep, settings: SettingsDep) -> CliAuthorizationRead:
    return _counted(me, lambda: CliAuthService(session, settings).deny(code, me.id))
