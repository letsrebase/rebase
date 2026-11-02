"""What a router needs to open a browser session or to mail: the session cookies, the
identity cookie, and the mail sender as a dependency. Shared by `routers/auth.py` (login,
link, invitation, refresh), `routers/identity.py` and `routers/tenants.py` (the signup,
which opens the space's session, spec 2026-09-12 §6.4)."""

from typing import Annotated

from fastapi import Depends, HTTPException, Request, Response, status
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from pigrocrm.core.auth.models import User
from pigrocrm.core.auth.refresh_service import RefreshTokenService, Rotation
from pigrocrm.core.auth.repository import UserRepository
from pigrocrm.core.auth.schemas import UserRead
from pigrocrm.core.auth.tokens import issue_access_token
from pigrocrm.core.config import Settings
from pigrocrm.core.identity.service import IdentityService
from pigrocrm.core.mail import TrackedSender, sender_from_settings
from pigrocrm_api.deps import (
    ACCESS_COOKIE,
    IDENTITY_COOKIE,
    REFRESH_COOKIE,
    SettingsDep,
    registry_session_factory,
)
from pigrocrm_api.tenancy import cookie_path, cookie_paths_to_clear, tenant_slug


def set_session_cookie(
    response: Response, name: str, value: str, max_age: int, *, secure: bool, path: str = "/"
) -> None:
    response.set_cookie(
        name, value, httponly=True, secure=secure, samesite="lax", max_age=max_age, path=path
    )


def set_access_cookie(
    response: Response, token: str, minutes: int, *, secure: bool, path: str
) -> None:
    set_session_cookie(response, ACCESS_COOKIE, token, minutes * 60, secure=secure, path=path)


def set_session_cookies(
    response: Response, settings: Settings, *, path: str, access: str, refresh: str
) -> None:
    """The access and refresh cookies, at one path."""
    set_access_cookie(
        response, access, settings.access_token_minutes, secure=settings.cookie_secure, path=path
    )
    set_session_cookie(
        response,
        REFRESH_COOKIE,
        refresh,
        settings.refresh_token_days * 86400,
        secure=settings.cookie_secure,
        path=path,
    )


def clear_other_jars(response: Response, request: Request, settings: Settings) -> None:
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


def issue_identity_cookie(response: Response, settings: Settings, email: str) -> None:
    """Records that this address has proven itself, somewhere, and mints a fresh
    `pigrocrm_identity` cookie at `path=/` (design 2026-09-23 §2, REB-376). Never for a
    signup: an address nobody has proven anything about must not get a durable,
    cross-space cookie. The registry being briefly unreachable costs the identity cookie
    for this one request, never the space session the caller already has."""
    try:
        with registry_session_factory(settings)() as registry:
            token = IdentityService(registry, settings).upsert_and_issue(email)
    except SQLAlchemyError:
        token = None
    if token:
        set_session_cookie(
            response,
            IDENTITY_COOKIE,
            token,
            settings.identity_token_days * 86400,
            secure=settings.cookie_secure,
            path="/",
        )


def open_session(
    response: Response,
    request: Request,
    settings: Settings,
    session: Session,
    user: UserRead | User,
    *,
    identity: bool = True,
    rotation: Rotation | None = None,
) -> None:
    """Opens the browser session of `user` on this request's own jar: clears the jars at
    other paths, then sets the access and refresh cookies at `cookie_path(request)`.

    `rotation` is `refresh`'s variant: the refresh token is the rotation's own and the
    access token is signed from its instant, so the grace-window answer and the ordinary
    one are the same code and the same two cookies (see `Rotation`). Without it a fresh
    refresh token is issued.

    `identity` adds the durable, cross-space cookie, and only for an address a click has
    proven. A password proves the row's password, not the mailbox, and an admin may set a
    row up with any address and a password of their own: if that login minted the
    identity, one space's admin could enter every other space the address belongs to, the
    root included (REB-583's review). `email_verificata_il` is set only once the mailbox
    has proven itself: by the link, by an accepted invitation, or by a signup whose
    browser already held a live identity for the address (REB-677)."""
    clear_other_jars(response, request, settings)
    space = tenant_slug(request)
    if rotation is None:
        access = issue_access_token(user.id, user.ruolo, settings, space=space)
        refresh = RefreshTokenService(session).issue(user.id, settings)
    else:
        access = issue_access_token(
            user.id, user.ruolo, settings, space=space, issued_at=rotation.issued_at
        )
        refresh = rotation.refresh_token
    set_session_cookies(
        response, settings, path=cookie_path(request), access=access, refresh=refresh
    )
    if identity:
        row = UserRepository(session).get_by_email(user.email)
        if row is not None and row.email_verificata_il is not None:
            issue_identity_cookie(response, settings, user.email)


def get_sender(settings: SettingsDep) -> TrackedSender | None:
    """The mail sender, or `None` without a key: the endpoints that mail answer 503.
    Tests override this one dependency with a `RecordingSender`. A `TrackedSender`
    rather than a bare `EmailSender`, because the invitations show the admin what became
    of the mail (REB-678); every other caller still only calls `send`."""
    return sender_from_settings(settings)


SenderDep = Annotated[TrackedSender | None, Depends(get_sender)]


# The two refusals every mailing endpoint answers with when the installation cannot
# mail: no provider key (checked by each caller, the sentences differ), no public
# origin to build the link from (checked here). Shared by `routers/auth.py` (the link
# by mail) and `routers/users.py` (the invitation), the two surfaces that hand a token
# to somebody else's inbox.
NO_PUBLIC_URL = (
    "L'installazione non ha un indirizzo pubblico configurato (PIGROCRM_PUBLIC_URL), quindi "
    "non può mandare link. Entra con la password."
)


def mail_origin(settings: Settings) -> str:
    """Where a link by mail points: the configured public origin, and nothing else. The
    request's own `Host` is whatever the caller sent, and a link built from it would
    hand a live token to that host; without `PIGROCRM_PUBLIC_URL` this installation
    mails no link (503 with a sentence, like the missing key)."""
    origin = settings.public_url.strip().rstrip("/")
    if not origin:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, NO_PUBLIC_URL)
    return origin
