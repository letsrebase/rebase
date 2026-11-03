from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.exc import SQLAlchemyError

from pigrocrm.core.auth.invitations import InvitationService
from pigrocrm.core.auth.magic_link import MagicLinkService
from pigrocrm.core.auth.refresh_service import RefreshTokenService
from pigrocrm.core.auth.repository import UserRepository
from pigrocrm.core.auth.schemas import InvitationPeek, MeUpdate, UserRead
from pigrocrm.core.auth.service import UserService
from pigrocrm.core.auth.tokens import decode_token
from pigrocrm.core.config import Settings
from pigrocrm.core.errors import DomainError, NotFound, ValidationFailed
from pigrocrm.core.mail import magic_link_mail
from pigrocrm.core.tenants import TenantService
from pigrocrm.core.validation import SafeStr
from pigrocrm_api.deps import (
    ACCESS_COOKIE,
    REFRESH_COOKIE,
    ActorDep,
    SessionDep,
    SettingsDep,
    registry_session_factory,
    space_session_factory,
)
from pigrocrm_api.errors import (
    INVITE_GONE_RESPONSE,
    INVITE_NOT_FOUND_RESPONSE,
    PROBLEM_RESPONSES,
)
from pigrocrm_api.ratelimit import (
    INVITE_PEEK_REQUESTS_PER_MINUTE,
    REFRESH_REQUESTS_PER_MINUTE,
    TOO_MANY_REQUESTS_RESPONSE,
    VERIFY_REQUESTS_PER_MINUTE,
    forget_failures,
    log_unauthorized,
    login_key,
    normalise_address,
    record_failure,
    refuse_if_locked,
    spend_for_recipient,
    spend_one,
    too_many_mails,
)
from pigrocrm_api.sessions import SenderDep, mail_origin, open_session
from pigrocrm_api.tenancy import (
    cookie_paths_to_clear,
    cookie_values,
    first_cookie,
    is_root_name,
    tenant_slug,
)

router = APIRouter(prefix="/api/auth", tags=["auth"], responses=PROBLEM_RESPONSES)

# Two plain `HTTPException` 401s documented per route, because `PROBLEM_RESPONSES` is
# `application/problem+json` and these are `application/json`, and an undocumented 401
# would be typed `unknown` in the generated client. (`STATUS_BY_CODE` maps
# `ValidationFailed` to 422 everywhere else; here `UserService.authenticate` raises it
# for wrong credentials, which is "not authenticated", so the router translates it.)
# FastAPI merges a route's own `responses=` with the router's.
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

# `me`, `refresh` and `verify` reach 401 through `deps.get_actor` or this router's own
# token checks, never a `DomainError`. Every cause (no cookie, an expired or invalid
# token, a replayed refresh token, a deactivated user) means the same thing to a caller:
# the session is gone. It is the routine signal a frontend auth layer branches on (try
# /refresh once, then redirect to /login). `logout` has no entry: it is idempotent and
# cannot answer 401.
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
    # SafeStr, not EmailStr: `get_by_email` binds `email` straight into a SELECT, and
    # psycopg refuses any string parameter containing a NUL byte. Login is the one
    # endpoint anyone can call, so that must be a clean 422 and not a 500.
    email: SafeStr
    password: str


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
    # Two throttles, both before the argon2 verify, which a script could otherwise run at
    # line rate (64 MiB, time cost 3): the client's own budget (REB-270,
    # `settings.login_requests_per_minute`), and a backoff on this client trying this
    # account, so repeated guesses at one password slow down however small each burst is.
    spend_one(request, scope="login", per_minute=settings.login_requests_per_minute)
    key = login_key(request, payload.email)
    refuse_if_locked(request, key)
    # Same message whichever of the three the domain layer detected (unknown email,
    # wrong password, deactivated user): `authenticate` raises one identical
    # `ValidationFailed` for all of them, on purpose.
    try:
        user = UserService(session).authenticate(payload.email, payload.password)
    except ValidationFailed as exc:
        record_failure(key)
        log_unauthorized(request, "login")
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Credenziali non valide") from exc
    forget_failures(key)
    open_session(response, request, settings, session, user)
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


def _verify_url(origin: str, prefix: str, raw: str) -> str:
    return f"{origin}{prefix}/app/verify?t={raw}"


def _owned_slugs(settings: Settings, email: str) -> list[str]:
    """The spaces the registry says this address opened. A registry that cannot be read
    is one with no spaces: an anonymous request must not turn into a 500."""
    try:
        with registry_session_factory(settings)() as registry:
            return TenantService(registry, settings).slugs_for_owner(email)
    except SQLAlchemyError:
        return []


def _space_link(settings: Settings, slug: str, email: str) -> str | None:
    """A token for `email` in the space `slug`, or `None`: no such user, or a space whose
    database cannot be opened (which must not turn the request into a 500 for the one
    address that owns it)."""
    try:
        with space_session_factory(slug, settings)() as space:
            return MagicLinkService(space, settings).request(email)
    except (SQLAlchemyError, NotFound):
        return None


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
    user. At the root, every space the registry says this address owns gets a link, in
    one mail, and the root itself comes first whenever it wears a name
    (`PIGROCRM_ROOT_SLUG`) and the address has a user there: it is nobody's registry
    row, and a space in its admin's own name must not hide it (REB-583). A root with
    no name is tried only when the address owns no space, as before: the chooser does
    not name it either. 202 whether the address is known or not, and the mail leaves
    after the response, so neither the status nor the timing says which; 503 while no
    sender is configured. Unauthenticated by design, like `member` and `signup`, so the
    bucket is what stops a script from mail-bombing a known address (ORB-173's limiter;
    REB-228)."""
    spend_one(request)
    if sender is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, NO_SENDER)
    origin = mail_origin(settings)
    email = normalise_address(payload.email)
    # Per recipient, whoever asks: the per-client bucket above does not stop a script that
    # rotates its source address from filling one inbox. Spent before the lookup, so the
    # answer is the same for an address that is known and one that is not.
    if not spend_for_recipient(email):
        raise too_many_mails()
    links: list[tuple[str, str]] = []
    slug = tenant_slug(request)
    if slug is not None:
        raw = MagicLinkService(session, settings).request(email)
        if raw:
            # Under a prefix `settings` are the space's own, and `deps` already gives
            # them a `public_url` that ends with `/<slug>`: nothing to append.
            links.append((slug, _verify_url(origin, "", raw)))
    else:
        for owned_slug in _owned_slugs(settings, email):
            # A registry row wearing the root's own name is the root, not a space
            # (`is_root_name`): its link, if any, is the root's own below.
            if is_root_name(settings.root_slug, owned_slug):
                continue
            raw = _space_link(settings, owned_slug, email)
            if raw:
                links.append((owned_slug, _verify_url(origin, f"/{owned_slug}", raw)))
        # The token is minted only when the root's link may be mailed: a named root
        # always, an unnamed one as the fallback it always was.
        if settings.root_slug or not links:
            raw = MagicLinkService(session, settings).request(email)
            if raw:
                # The root logs in on the bare page (decision 2026-09-09); its cookies
                # live at `/`, so the entry page is the bare one too.
                links.insert(0, (settings.root_slug or "PigroCRM", _verify_url(origin, "", raw)))
    if links:
        background.add_task(sender.send, magic_link_mail(email, links, settings.magic_link_minutes))
    return Ack()


@router.post(
    "/verify",
    response_model=UserRead,
    responses={401: _UNAUTHENTICATED_RESPONSE, 429: TOO_MANY_REQUESTS_RESPONSE},
)
def enter_with_link(
    payload: LinkToken,
    request: Request,
    response: Response,
    session: SessionDep,
    settings: SettingsDep,
) -> UserRead:
    """Spends the link and opens the session, with the cookies `login` sets."""
    spend_one(request, scope="verify", per_minute=VERIFY_REQUESTS_PER_MINUTE)
    user = MagicLinkService(session, settings).enter(payload.t)
    if user is None:
        log_unauthorized(request, "verify")
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, INVALID_LINK)
    open_session(response, request, settings, session, user)
    return user


# ---- an invitation, accepted (spec 2026-09-17, REB-290) -------------------------------
#
# The public half of the invitation flow, beside the link-by-mail routes it shares a
# shape with: unauthenticated, space-scoped by `TenantPrefixMiddleware`. Unlike
# `INVALID_LINK`'s single sentence -- which folds the magic link's failure states together
# because guessing a password or an email is the oracle there -- an invitation names its
# three dead states apart (410 with `code` = `invitation_expired` / `_revoked` / `_used`):
# the token is 32 random bytes, nobody can guess their way from one outcome to another's.


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
    # `space_settings` reads `space`, with the root's own name where the router knows
    # it (the same fallback `request_link` labels its root link with).
    space = tenant_slug(request) or settings.root_slug or "PigroCRM"
    return InvitationService(session).peek(t, space=space)


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
    open_session(response, request, settings, session, user)
    return user


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    request: Request, response: Response, session: SessionDep, settings: SettingsDep
) -> None:
    # Logging out must kill the session server-side, not just empty the browser's
    # cookie jar -- otherwise a copy of the refresh token taken before logout stays
    # valid for the rest of its life (`refresh_token_days`, six months by default). An
    # already-invalid or already-expired token has nothing left to invalidate, so that
    # is not an error here: the goal state ("no usable session") is already true.
    #
    # Every refresh token the browser presented, not the first (`cookie_values`).
    # Consuming one that was already consumed revokes the rest of this user's tokens as a
    # replay (`RefreshTokenService.consume`), which on a logout is the right outcome too.
    refresh_tokens = RefreshTokenService(session)
    for token in cookie_values(request, REFRESH_COOKIE):
        try:
            payload = decode_token(token, settings, expected_type="refresh")
            if payload.jti is not None:
                refresh_tokens.consume(payload.jti, payload.sub)
        except DomainError:
            pass
    # Deleted at every path this installation ever set them at (`cookie_paths_to_clear`):
    # a cookie is only removed by a Set-Cookie with the same path, and a pair left in
    # another jar would keep the session the person just said they do not want.
    for path in cookie_paths_to_clear(request, settings.root_slug):
        response.delete_cookie(ACCESS_COOKIE, path=path)
        response.delete_cookie(REFRESH_COOKIE, path=path)


@router.post(
    "/refresh",
    response_model=UserRead,
    responses={401: _UNAUTHENTICATED_RESPONSE, 429: TOO_MANY_REQUESTS_RESPONSE},
)
def refresh(
    request: Request, response: Response, session: SessionDep, settings: SettingsDep
) -> UserRead:
    spend_one(request, scope="refresh", per_minute=REFRESH_REQUESTS_PER_MINUTE)
    # The most specific one, like `get_actor`: a space's own before the root's.
    token = first_cookie(request, REFRESH_COOKIE)
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Refresh token assente")

    # A refresh token is a credential, not form data, so an invalid or expired one reads
    # as 401 (like a bad access token in `get_actor`), not as a 422.
    try:
        payload = decode_token(token, settings, expected_type="refresh")
    except DomainError as exc:
        log_unauthorized(request, "refresh")
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Refresh token non valido") from exc
    if payload.jti is None:
        log_unauthorized(request, "refresh")
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Refresh token non valido")

    # Rotated before anything else: this is what makes rotation real. Once this returns,
    # the token just presented can never be used again. If it had already been consumed,
    # this either hands back that request's own successor (two tabs, inside
    # `refresh_service.REFRESH_GRACE_SECONDS`) or raises and revokes every other live
    # refresh token this user holds: a replay. This router does not know, and must not
    # know, which of the two branches answered it (`RefreshTokenService.rotate`).
    try:
        rotation = RefreshTokenService(session).rotate(payload.jti, payload.sub, settings)
    except DomainError as exc:
        log_unauthorized(request, "refresh")
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Refresh token non valido") from exc

    try:
        user = UserRepository(session).get_active(payload.sub)
    except DomainError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Utente non attivo") from exc

    # One cookie-setting path for both branches, deliberately: a grace-window answer that
    # differed from an ordinary rotation in any observable way would tell a caller how
    # long ago some other tab refreshed. No identity cookie: a refresh proves nothing new.
    open_session(response, request, settings, session, user, identity=False, rotation=rotation)
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
        return UserService(session).update_own_digest(actor, data.weekly_digest)
    except NotFound as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Utente non trovato") from exc
