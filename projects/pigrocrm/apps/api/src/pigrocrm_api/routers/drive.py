"""The REST surface of slice 9B's Drive credential -- the Drive twin of `routers/gmail.py`.

Everything that module's docstring says about the settings page applies here
unchanged, for the same reasons: `GET /account` answers 200 even when Google is not
configured, because "this installation has no Google" and "you have not connected
Drive yet" are both states with no account and no error to report, and the settings
page has to render the first without the SPA treating the response as a failed query.
The OAuth callback is a browser navigation and always ends on the settings page with
one of four outcome codes, never with Google's own `error` (English, and occasionally
the client id), never with a `Conflict`'s problem document (which would strand the
user outside the SPA at the end of a consent flow), and never with a 401 when the
session ran out on Google's screens (REB-446).

One thing here is *not* a mirror of Gmail: there is no sync, no backfill, and no route
that reads back stored correspondence. Slice 9B is only the credential and its two
configuration fields (`root_folder_ids`, `storage_folder_id`); reading and writing files
through it is 9C/9D's surface, built on the `usable()` gate `drive/account.py` already
exposes.
"""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from pigrocrm.core.config import Settings, gmail_configured
from pigrocrm.core.drive.account import GoogleDriveAccountService
from pigrocrm.core.drive.oauth import GoogleDriveOAuthService
from pigrocrm.core.drive.schemas import (
    DriveHealth,
    DriveRootsUpdate,
    GoogleDriveAccountRead,
    drive_health,
)
from pigrocrm_api.deps import ActorDep, SessionDep, SettingsDep, get_actor
from pigrocrm_api.errors import PROBLEM_RESPONSES
from pigrocrm_api.google_tokens import token_client
from pigrocrm_api.oauth_callback import finish_consent

router = APIRouter(prefix="/api/drive", tags=["drive"], responses=PROBLEM_RESPONSES)
# The one public route is the OAuth callback: a browser navigation that decides for itself
# what a missing session means (`oauth_callback.py`). Everything else sits behind
# `get_actor`, so a handler added to `authenticated` cannot forget to ask who is calling.
authenticated = APIRouter(dependencies=[Depends(get_actor)])

# Where the SPA renders the outcome of a consent flow (`oauth_callback.py`), on Drive's own
# settings tab.
_SETTINGS_PAGE = "/app/settings/drive"

_ACCOUNT_PATH = "/account"
_CALLBACK_ROUTE = "/oauth/callback"
_CALLBACK_PATH = f"{router.prefix}{_CALLBACK_ROUTE}"


def _oauth(session: Session, settings: Settings) -> GoogleDriveOAuthService:
    return GoogleDriveOAuthService(session, settings=settings, tokens=token_client(settings))


@authenticated.get(_ACCOUNT_PATH, response_model=DriveHealth, response_model_exclude_unset=True)
def read_account(session: SessionDep, actor: ActorDep, settings: SettingsDep) -> DriveHealth:
    """200 even when Drive is not configured -- see the module docstring.

    `response_model_exclude_unset=True` is load-bearing, not cosmetic: it is what
    turns `DriveHealth.space_storage` being left unset (by `drive_health`, below, for a
    non-admin actor) into the key being absent from the JSON, rather than `null`. See
    that field's own docstring for why the distinction matters (REB-562 fix round 1).

    `space_storage` is read from `GoogleDriveAccountService.space_storage` even on this
    branch, independent of `gmail_configured`: `DriveRepository.storage_account` --
    what `storage/lazy_drive.py` actually resolves a document write against -- reads
    the row directly and was never gated on whether *this* request's Google
    configuration happens to be complete, so an admin must still learn whether the
    space has a write folder in effect when this installation's own client is (no
    longer, or not yet) fully configured.
    """
    service = GoogleDriveAccountService(session, settings=settings)
    if not gmail_configured(settings):
        return drive_health(
            account=None,
            banner=None,
            banner_text=None,
            missing_scopes=[],
            configured=False,
            space_storage=service.space_storage(actor),
        )
    return service.health(actor)


@authenticated.get("/oauth/start")
def start_oauth(session: SessionDep, actor: ActorDep, settings: SettingsDep) -> RedirectResponse:
    # Snyk Code python/OR here is a false positive: the target is Google's fixed
    # GOOGLE_AUTH_URL, with every parameter urlencoded.
    return RedirectResponse(_oauth(session, settings).start(actor), status_code=307)


@router.get(_CALLBACK_ROUTE)
def finish_oauth(
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> RedirectResponse:
    # Every refusal `complete` can produce is a `Conflict` -- a replayed or expired state, a
    # Drive that points at a different Google identity than the connected mailbox, an
    # account already connected to someone else, a grant Google answered with no identity.
    # `finish_consent` ends all of them on the settings page, which re-reads `GET /account`
    # and shows the true state.
    return finish_consent(
        request,
        session,
        settings,
        callback_path=_CALLBACK_PATH,
        code=code,
        state=state,
        error=error,
        complete=lambda code, state, actor: _oauth(session, settings).complete(
            code=code, state=state, actor=actor
        ),
        page=_SETTINGS_PAGE,
    )


@authenticated.delete(_ACCOUNT_PATH, status_code=204)
def disconnect(session: SessionDep, actor: ActorDep, settings: SettingsDep) -> None:
    """No `elimina_messaggi` flag: Drive stores no correspondence of its own, so there is
    nothing here for one to name -- see `GoogleDriveOAuthService.disconnect`."""
    _oauth(session, settings).disconnect(actor)


@authenticated.patch("/account/roots", response_model=GoogleDriveAccountRead)
def update_roots(
    payload: DriveRootsUpdate, session: SessionDep, actor: ActorDep, settings: SettingsDep
) -> GoogleDriveAccountRead:
    """No `gmail_configured` gate, on purpose: `set_roots` reaches the row through
    `_present`, which lets a revoked or expired credential still be reconfigured -- the
    person most likely to be on this screen is the one trying to recover from exactly
    that state, and the route must not add a gate the service deliberately omits."""
    return GoogleDriveAccountService(session, settings=settings).set_roots(payload, actor)


router.include_router(authenticated)
