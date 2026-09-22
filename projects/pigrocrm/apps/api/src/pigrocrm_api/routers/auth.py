from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import create_engine
from sqlalchemy.exc import SQLAlchemyError

from pigrocrm.core.auth.invitations import InvitationService
from pigrocrm.core.auth.magic_link import MagicLinkService
from pigrocrm.core.auth.refresh_service import RefreshTokenService
from pigrocrm.core.auth.repository import UserRepository
from pigrocrm.core.auth.schemas import InvitationPeek, MeUpdate, UserRead
from pigrocrm.core.auth.service import UserService
from pigrocrm.core.auth.tokens import decode_token, issue_access_token
from pigrocrm.core.config import Settings
from pigrocrm.core.db.session import session_factory
from pigrocrm.core.errors import DomainError, NotFound, ValidationFailed
from pigrocrm.core.mail import magic_link_mail
from pigrocrm.core.tenants import TenantService
from pigrocrm.core.tenants.database import (
    tenant_database_name,
    tenant_database_url,
    tenants_database_url,
)
from pigrocrm.core.validation import SafeStr
from pigrocrm_api.deps import ACCESS_COOKIE, REFRESH_COOKIE, ActorDep, SessionDep, SettingsDep
from pigrocrm_api.errors import (
    INVITE_GONE_RESPONSE,
    INVITE_NOT_FOUND_RESPONSE,
    PROBLEM_RESPONSES,
)
from pigrocrm_api.ratelimit import (
    INVITE_PEEK_REQUESTS_PER_MINUTE,
    LOGIN_REQUESTS_PER_MINUTE,
    TOO_MANY_REQUESTS_RESPONSE,
    spend_one,
)
from pigrocrm_api.sessions import (  # noqa: F401 - get_sender is the override seam
    SenderDep,
    get_sender,
    mail_origin,
    set_session_cookie,
)
from pigrocrm_api.tenancy import cookie_path, cookie_paths_to_clear, first_cookie, tenant_slug

router = APIRouter(prefix="/api/auth", tags=["auth"], responses=PROBLEM_RESPONSES)

# STATUS_BY_CODE (pigrocrm_api/errors.py) maps ValidationFailed to 422 for every other
# endpoint, correctly -- there it means "the body was well-formed but violated a
# domain rule." Here it would mean something else entirely: UserService.authenticate
# raises this same exception class for wrong credentials, which is "not authenticated,"
# the same category refresh (below) and deps.get_actor already answer with 401 for an
# invalid refresh/access token. Router-level, not a STATUS_BY_CODE change, because this
# is specific to what ValidationFailed means at this one call site, not a
# reclassification of the error code everywhere else it is raised.
#
# PROBLEM_RESPONSES (attached to the whole router above) has no 401 entry, so
# without a route-level addition login's OpenAPI documentation would stay silent
# about a status code it can now actually return, and slice 1B's generated
# TypeScript client would type this response as `unknown` -- exactly the kind of
# silent client-generation gap _domain_and_request_validation_response above already
# had to fix once for 422. FastAPI merges a route's own `responses=` with the
# router's (see APIRouter.post/_combined_responses in fastapi/routing.py), so this
# only adds 401 here without touching the shared dict every other route relies on.
# (`me` and `refresh` need the same treatment, for a different, more load-bearing
# reason -- see `_UNAUTHENTICATED_RESPONSE` just below.)
_LOGIN_UNAUTHORIZED_RESPONSE: dict[str, Any] = {
    "description": (
        "Email o password non corrette, oppure l'utente è disattivato -- lo stesso "
        "messaggio identico in tutti e tre i casi, così la risposta stessa non "
        "rivela quale sia la causa reale."
    ),
    "content": {
        "application/json": {
            "schema": {
                "type": "object",
                "properties": {"detail": {"type": "string"}},
                "required": ["detail"],
            }
        }
    },
}

# `me` and `refresh` both reach 401 the same way login does above -- deps.get_actor or
# this router's own token checks raising a plain HTTPException, never a DomainError --
# so PROBLEM_RESPONSES (application/problem+json) would misdocument this exactly as
# _LOGIN_UNAUTHORIZED_RESPONSE above had to fix once already. One shared entry, not one
# per route: unlike login's single fixed message, these two cover several distinct
# causes each (get_actor: no cookie at all, an expired/invalid access token, a user
# deactivated after the token was issued, an unrecognised bearer PAT; refresh: no
# refresh cookie, an invalid/expired refresh token, a replayed/already-consumed token,
# a now-inactive user) -- but every one of them means the same thing to a caller: the
# session is gone, not "you sent something malformed." That is *more* load-bearing for
# a generated client than login's own 401: this is the routine "session expired" signal
# a frontend auth layer branches on programmatically (try /refresh once, then redirect
# to /login), where login's error is hand-written UX regardless of how precisely it is
# typed. `logout` deliberately has no entry here -- it has no actor dependency and is
# idempotent by design (see its own docstring), so it cannot structurally produce a 401
# the way these two can; documenting one anyway would claim a response this route can
# never send.
_UNAUTHENTICATED_RESPONSE: dict[str, Any] = {
    "description": (
        "Non autenticato: il cookie di sessione è assente, scaduto o non valido, "
        "oppure l'utente non è più attivo. Il client deve trattarlo come sessione "
        "terminata (ritentare /api/auth/refresh e poi reindirizzare al login), non "
        "come un errore da ripetere."
    ),
    "content": {
        "application/json": {
            "schema": {
                "type": "object",
                "properties": {"detail": {"type": "string"}},
                "required": ["detail"],
            }
        }
    },
}


class LoginRequest(BaseModel):
    # SafeStr, not EmailStr: UserRepository.get_by_email binds `email` straight
    # into a SELECT ... WHERE email = :email, and psycopg refuses to adapt any
    # string parameter containing a NUL byte, insert or not -- the same defect
    # class as the list routers' search/stato/custom, but reachable with zero
    # credentials, since login is the one endpoint anyone can call. This is a
    # request-body field (like every Create schema's own fields), so FastAPI's
    # normal request-body validation already turns a rejection here into a clean
    # 422 with no further change needed -- unlike the query-parameter case, where
    # the parameter itself has to carry the annotation (see routers/customers.py).
    email: SafeStr
    password: str


# The cookie writer and the sender dependency live in `pigrocrm_api.sessions`, shared
# with the signup; the names here are what this module and its tests always used.
_set_cookie = set_session_cookie


def _clear_other_jars(response: Response, request: Request, settings: Settings) -> None:
    """Deletes the session pair at every path this installation ever set it at, except
    the one this response is about to set. A stale, more specific pair -- the root's old
    `/studiorossi/` jar -- would otherwise be sent ahead of the fresh one and shadow it on
    every request (`cookie_paths_to_clear`). Deleting what is not there is a no-op."""
    keep = cookie_path(request)
    for path in cookie_paths_to_clear(request, settings.root_slug):
        if path == keep:
            continue
        response.delete_cookie(ACCESS_COOKIE, path=path)
        response.delete_cookie(REFRESH_COOKIE, path=path)


@router.post(
    "/login",
    response_model=UserRead,
    responses={401: _LOGIN_UNAUTHORIZED_RESPONSE, 429: TOO_MANY_REQUESTS_RESPONSE},
)
def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    session: SessionDep,
    settings: SettingsDep,
) -> UserRead:
    # On its own budget (`LOGIN_REQUESTS_PER_MINUTE`), before the argon2 verify below,
    # not after: unauthenticated and unthrottled otherwise, so a script could run the
    # library's own 64 MiB, time-cost-3 hash at line rate. Running this first is what
    # stops that cost from being paid at all past the budget, not merely what
    # attaches a message to a request already paid for (REB-270).
    spend_one(request, scope="login", per_minute=LOGIN_REQUESTS_PER_MINUTE)
    # Same message regardless of which of the three the domain layer detected (unknown
    # email, wrong password, deactivated user) -- UserService.authenticate already
    # raises one identical ValidationFailed for all three, on purpose, so there is
    # nothing here that could distinguish them even if this wanted to.
    try:
        user = UserService(session).authenticate(payload.email, payload.password)
    except ValidationFailed as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Credenziali non valide") from exc
    _clear_other_jars(response, request, settings)
    _set_cookie(
        response,
        ACCESS_COOKIE,
        issue_access_token(user.id, user.ruolo, settings),
        settings.access_token_minutes * 60,
        secure=settings.cookie_secure,
        path=cookie_path(request),
    )
    _set_cookie(
        response,
        REFRESH_COOKIE,
        RefreshTokenService(session).issue(user.id, settings),
        settings.refresh_token_days * 86400,
        secure=settings.cookie_secure,
        path=cookie_path(request),
    )
    return user


# ---- a link by mail (spec 2026-09-12 §6.2) ----------------------------------------------


NO_SENDER = (
    "L'accesso via email non è ancora attivo su questa installazione. Entra con la password."
)
INVALID_LINK = "Questo link non è valido o è scaduto. Chiedine un altro."


class LinkRequest(BaseModel):
    email: SafeStr


class LinkToken(BaseModel):
    # `token_urlsafe(32)` is 43 characters; anything much longer is not ours.
    t: SafeStr = Field(max_length=128)


class Ack(BaseModel):
    ok: bool = True


def _entra_url(origin: str, prefix: str, raw: str) -> str:
    return f"{origin}{prefix}/app/verify?t={raw}"


def _owned_slugs(settings: Settings, email: str) -> list[str]:
    """The spaces the registry says this address opened. A plain read on its own engine,
    opened and disposed here rather than through `deps`' cached registry: a link
    request is rare and ORB-170 is moving that registry. No DDL: an installation whose
    registry database does not exist (a self-hosted CRM that never had a signup) is one
    with no spaces, and an anonymous request must not create it."""
    engine = create_engine(tenants_database_url(settings), future=True)
    try:
        with session_factory(engine)() as session:
            rows = TenantService(session, settings).list()
            return [t.slug for t in rows if t.owner_email == email]
    except SQLAlchemyError:
        return []
    finally:
        engine.dispose()


def _space_link(settings: Settings, slug: str, email: str) -> str | None:
    """A token for `email` in the space `slug`, or `None`: no such user, or a space whose
    database cannot be opened (which must not turn the request into a 500 for the one
    address that owns it)."""
    engine = create_engine(tenant_database_url(settings, tenant_database_name(slug)), future=True)
    try:
        with session_factory(engine)() as space:
            return MagicLinkService(space, settings).request(email)
    except SQLAlchemyError:
        return None
    finally:
        engine.dispose()


@router.post(
    "/link",
    response_model=Ack,
    status_code=status.HTTP_202_ACCEPTED,
    responses={429: TOO_MANY_REQUESTS_RESPONSE},
)
def request_link(
    payload: LinkRequest,
    request: Request,
    background: BackgroundTasks,
    session: SessionDep,
    settings: SettingsDep,
    sender: SenderDep,
) -> Ack:
    """A link by mail (spec 2026-09-12 §6.2). Under a space's prefix, the space's own
    user. At the root, every space the registry says this address owns gets a link in
    one mail, and the root itself is tried when none does. 202 whether the address is
    known or not, and the mail leaves after the response, so neither the status nor the
    timing says which; 503 while no sender is configured. Unauthenticated by design, like
    `member` and `signup`, so the bucket is what stops a script from mail-bombing a known
    address (ORB-173's limiter; REB-228)."""
    spend_one(request)
    if sender is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, NO_SENDER)
    origin = mail_origin(settings)
    email = payload.email.strip().lower()
    links: list[tuple[str, str]] = []
    slug = tenant_slug(request)
    if slug is not None:
        raw = MagicLinkService(session, settings).request(email)
        if raw:
            # Under a prefix `settings` are the space's own, and `deps` already gives
            # them a `public_url` that ends with `/<slug>`: nothing to append.
            links.append((slug, _entra_url(origin, "", raw)))
    else:
        for owned_slug in _owned_slugs(settings, email):
            raw = _space_link(settings, owned_slug, email)
            if raw:
                links.append((owned_slug, _entra_url(origin, f"/{owned_slug}", raw)))
        if not links:
            raw = MagicLinkService(session, settings).request(email)
            if raw:
                # The root logs in on the bare page (decision 2026-09-09); its cookies
                # live at `/`, so the entry page is the bare one too.
                links.append((settings.root_slug or "PigroCRM", _entra_url(origin, "", raw)))
    if links:
        background.add_task(sender.send, magic_link_mail(email, links, settings.magic_link_minutes))
    return Ack()


@router.post("/verify", response_model=UserRead, responses={401: _UNAUTHENTICATED_RESPONSE})
def enter_with_link(
    payload: LinkToken,
    request: Request,
    response: Response,
    session: SessionDep,
    settings: SettingsDep,
) -> UserRead:
    """Spends the link and opens the session, with the cookies `login` sets."""
    user = MagicLinkService(session, settings).enter(payload.t)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, INVALID_LINK)
    _clear_other_jars(response, request, settings)
    _set_cookie(
        response,
        ACCESS_COOKIE,
        issue_access_token(user.id, user.ruolo, settings),
        settings.access_token_minutes * 60,
        secure=settings.cookie_secure,
        path=cookie_path(request),
    )
    _set_cookie(
        response,
        REFRESH_COOKIE,
        RefreshTokenService(session).issue(user.id, settings),
        settings.refresh_token_days * 86400,
        secure=settings.cookie_secure,
        path=cookie_path(request),
    )
    return user


# ---- an invitation, accepted (spec 2026-09-17, REB-290) -------------------------------
#
# The public half of the invitation flow, beside the link-by-mail routes it shares a
# shape with: unauthenticated, space-scoped by `TenantPrefixMiddleware` so neither
# route knows it is under a prefix. Unlike `INVALID_LINK`'s single sentence -- which
# folds the magic link's failure states together because guessing a password or an
# email is the oracle there -- an invitation names its three dead states apart (410
# with `code` = `invitation_expired` / `_revoked` / `_used`): the token is 32 random
# bytes, nobody can guess their way from one outcome to another's.


class InviteAccept(BaseModel):
    """The token, plus `nome` when the peek answered none. Whether a name is needed
    is the service's knowledge (it reads the row), so that is where it is enforced --
    a schema constraint here could only duplicate the check or get it wrong."""

    t: SafeStr = Field(max_length=128)
    nome: SafeStr | None = Field(default=None, max_length=200)


@router.get(
    "/invite",
    response_model=InvitationPeek,
    responses={
        404: INVITE_NOT_FOUND_RESPONSE,
        410: INVITE_GONE_RESPONSE,
        429: TOO_MANY_REQUESTS_RESPONSE,
    },
)
def peek_invite(
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    t: Annotated[SafeStr, Query(max_length=128)],
) -> InvitationPeek:
    """Reads an invitation without spending it: the space, the inviter, the name the
    admin carried (or none). Its own generous bucket -- the page calls it on mount,
    and a reload must not lock a person out of their own invitation. Reading is not
    spending: `accepted_at` is untouched here, and only the POST below can end an
    invitation."""
    spend_one(request, scope="invite_peek", per_minute=INVITE_PEEK_REQUESTS_PER_MINUTE)
    # The space as the page shows it: the prefix it is served under, the way
    # `space_settings` reads `spazio`, with the root's own name where the router knows
    # it (the same fallback `request_link` labels its root link with).
    spazio = tenant_slug(request) or settings.root_slug or "PigroCRM"
    return InvitationService(session).peek(t, spazio=spazio)


@router.post(
    "/invite",
    response_model=UserRead,
    responses={
        404: INVITE_NOT_FOUND_RESPONSE,
        410: INVITE_GONE_RESPONSE,
        429: TOO_MANY_REQUESTS_RESPONSE,
    },
)
def accept_invite(
    payload: InviteAccept,
    request: Request,
    response: Response,
    session: SessionDep,
    settings: SettingsDep,
) -> UserRead:
    """Spends the invitation, creates the person, opens the session with the cookies
    `login` sets -- the `enter_with_link` shape, answered with `UserRead` so the SPA
    lands through the same `homeAfterEntry()` the verify page uses. Its own scope at
    the default five a minute: the same posture as `/api/auth/link`, kept in a bucket
    apart so neither credential can starve the other's."""
    spend_one(request, scope="invite_accept")
    user = InvitationService(session).accept(payload.t, payload.nome)
    _clear_other_jars(response, request, settings)
    _set_cookie(
        response,
        ACCESS_COOKIE,
        issue_access_token(user.id, user.ruolo, settings),
        settings.access_token_minutes * 60,
        secure=settings.cookie_secure,
        path=cookie_path(request),
    )
    _set_cookie(
        response,
        REFRESH_COOKIE,
        RefreshTokenService(session).issue(user.id, settings),
        settings.refresh_token_days * 86400,
        secure=settings.cookie_secure,
        path=cookie_path(request),
    )
    return user


def _every_cookie_value(request: Request, name: str) -> list[str]:
    """Every value the browser sent under `name`, not only the one `request.cookies`
    keeps.

    A browser holds one cookie per (name, domain, path), and it sends all of them that
    match: a session opened at `/` and one opened at `/studiorossi/` arrive as two
    `refresh_token=` pairs in one header. The `SimpleCookie` parser behind
    `request.cookies` keeps the last of them, so a logout that read only that one left
    the other alive. `tenancy.first_cookie` is the single-value sibling. Parsed by hand
    because the values are JWTs -- no `;`, no `=` beyond the first, nothing to quote."""
    header = request.headers.get("cookie", "")
    values: list[str] = []
    for pair in header.split(";"):
        key, sep, value = pair.strip().partition("=")
        if sep and key.strip() == name and value:
            values.append(value.strip())
    return values


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    request: Request, response: Response, session: SessionDep, settings: SettingsDep
) -> None:
    # Logging out must kill the session server-side, not just empty the browser's
    # cookie jar -- otherwise a copy of the refresh token taken before logout stays
    # valid for the rest of its life, which `refresh_token_days` puts at six months by
    # default (T1 raised it from thirty days). An already-invalid or already-expired
    # token has nothing left to invalidate, so that case is not an error here: the
    # goal state ("no usable session") is already true.
    #
    # Every refresh token the browser presented, not the first: see
    # `_every_cookie_value`. Consuming one that was already consumed revokes the rest
    # of this user's tokens as a replay (`RefreshTokenService.consume`), which on a
    # logout is the right outcome too -- the person asked for no usable session.
    refresh_tokens = RefreshTokenService(session)
    for token in _every_cookie_value(request, REFRESH_COOKIE):
        try:
            payload = decode_token(token, settings, expected_type="refresh")
            if payload.jti is not None:
                refresh_tokens.consume(payload.jti, payload.sub)
        except DomainError:
            pass
    # Deleted at every path this installation ever set them at -- see
    # `cookie_paths_to_clear`: a cookie is only ever removed by a Set-Cookie with the
    # same path, and a pair left in another jar would keep the session the person just
    # said they do not want.
    for path in cookie_paths_to_clear(request, settings.root_slug):
        response.delete_cookie(ACCESS_COOKIE, path=path)
        response.delete_cookie(REFRESH_COOKIE, path=path)


@router.post("/refresh", response_model=UserRead, responses={401: _UNAUTHENTICATED_RESPONSE})
def refresh(
    request: Request, response: Response, session: SessionDep, settings: SettingsDep
) -> UserRead:
    # The most specific one, like `get_actor`: a space's own before the root's.
    token = first_cookie(request, REFRESH_COOKIE)
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Refresh token assente")

    # A refresh token is a credential, like the access token get_actor checks -- not
    # user-submitted form data -- so an invalid or expired one reads as 401, the same
    # way get_actor treats a bad access token, not as a generic 422 validation failure.
    try:
        payload = decode_token(token, settings, expected_type="refresh")
    except DomainError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Refresh token non valido") from exc
    if payload.jti is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Refresh token non valido")

    refresh_tokens = RefreshTokenService(session)
    # Rotated before anything else: this is what makes rotation real. Once this call
    # returns, the token just presented can never be used again -- if it had already
    # been consumed by an earlier request, this either hands back that request's own
    # successor (the two-tabs case, inside refresh_service.REFRESH_GRACE_SECONDS)
    # or raises and, as a side effect, revokes every other still-valid refresh token
    # this user holds: that earlier request was the legitimate rotation, so this one
    # presenting the same token again long afterwards is a replay. See
    # `RefreshTokenService.rotate` for where that line is drawn -- this router does
    # not know, and must not know, which of the two branches answered it.
    try:
        rotation = refresh_tokens.rotate(payload.jti, payload.sub, settings)
    except DomainError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Refresh token non valido") from exc

    try:
        user = UserRepository(session).get_active(payload.sub)
    except DomainError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Utente non attivo") from exc

    _clear_other_jars(response, request, settings)
    # One cookie-setting path for both branches, deliberately: a grace-window answer
    # that differed from an ordinary rotation in any observable way -- a header, a
    # max-age, an order -- would tell a caller how long ago some other tab refreshed,
    # and would be a second code path on the endpoint that must never fail.
    #
    # `issued_at` is the rotation's own instant, not "now": signing the access token
    # from it is what makes the second tab's pair identical to the first's rather than
    # merely equivalent (see `issue_access_token`). For an ordinary rotation the two
    # are the same instant anyway, microseconds apart at worst.
    _set_cookie(
        response,
        ACCESS_COOKIE,
        issue_access_token(user.id, user.ruolo, settings, issued_at=rotation.issued_at),
        settings.access_token_minutes * 60,
        secure=settings.cookie_secure,
        path=cookie_path(request),
    )
    # A fresh token with its own row -- not a re-signing of the same claims -- because
    # the one just rotated above can never be honoured again.
    _set_cookie(
        response,
        REFRESH_COOKIE,
        rotation.refresh_token,
        settings.refresh_token_days * 86400,
        secure=settings.cookie_secure,
        path=cookie_path(request),
    )
    return UserRead.model_validate(user)


@router.get("/me", response_model=UserRead, responses={401: _UNAUTHENTICATED_RESPONSE})
def me(actor: ActorDep, session: SessionDep) -> UserRead:
    user = UserRepository(session).get(actor.id) if actor.id else None
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Utente non trovato")
    return UserRead.model_validate(user)


@router.patch("/me", response_model=UserRead, responses={401: _UNAUTHENTICATED_RESPONSE})
def update_me(data: MeUpdate, actor: ActorDep, session: SessionDep) -> UserRead:
    """The one write on this router with no `actor.require_admin` behind it,
    deliberately: `PATCH /api/users/{id}` (`UserService.update`) is for an
    administrator changing someone else's account, but this is a person changing
    their own weekly-digest preference, and the mail's own opt-out link (spec
    2026-09-16 §3.6) must work whatever role received it.

    The row is loaded once, by the service. `update_own_digest` already refuses an
    actor with no id and an id with no row, both as `NotFound`, so a check here would
    be the same query asked twice and a second place deciding who exists. What stays
    the router's own is the *answer*: a session whose user row is gone is "not
    authenticated," not "not found," here as everywhere else on this router, so the
    domain error is translated to the same 401 `me` just above gives.
    """
    try:
        return UserService(session).update_own_digest(actor, data.digest_settimanale)
    except NotFound as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Utente non trovato") from exc
