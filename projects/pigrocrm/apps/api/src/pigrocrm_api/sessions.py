"""What a router needs to open a browser session or to mail: the two cookies at a path,
and the mail sender as a dependency. Shared by `routers/auth.py` (login, link, entra)
and `routers/tenants.py` (the signup, which opens the space's session, spec 2026-09-12
§6.4). Not in `deps.py`, which ORB-170 is reshaping at the same time."""

from typing import Annotated

from fastapi import Depends, HTTPException, Response, status

from pigrocrm.core.config import Settings
from pigrocrm.core.mail import EmailSender, sender_from_settings
from pigrocrm_api.deps import ACCESS_COOKIE, REFRESH_COOKIE, SettingsDep


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


def set_refresh_cookie(
    response: Response, token: str, days: int, *, secure: bool, path: str
) -> None:
    set_session_cookie(response, REFRESH_COOKIE, token, days * 86400, secure=secure, path=path)


def get_sender(settings: SettingsDep) -> EmailSender | None:
    """The mail sender, or `None` without a key: the endpoints that mail answer 503.
    Tests override this one dependency with a `RecordingSender`."""
    return sender_from_settings(settings)


SenderDep = Annotated[EmailSender | None, Depends(get_sender)]


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
