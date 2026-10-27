"""The REST surface of the Slack connection (REB-739), the Slack twin of `routers/drive.py`.

Everything that module's docstring says about the settings page applies unchanged.
`GET /account` answers 200 even when Slack is not configured, because «this installation has
no Slack app» and «nobody connected a workspace yet» are both states with no workspace and
no error to report. The OAuth callback is a browser navigation and always ends on the
settings page with one of four outcome codes, never with Slack's own `error` (English), never
with a `Conflict`'s problem document (which would strand the person outside the SPA at the end
of a consent), and never with a 401 when the session ran out on Slack's screens (REB-446).

The bot token is read by the services and goes nowhere else: no route returns it, in any
form, and none accepts one.

`slack_client` is the dependency that builds the Web API client, so a test replaces it with
one over its fake (`app.dependency_overrides[slack_client]`) and nothing here reaches
`slack.com` from a test.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse

from pigrocrm.core.config import Settings
from pigrocrm.core.errors import DomainError
from pigrocrm.core.slack.client import SlackClient, slack_client_for
from pigrocrm.core.slack.oauth import SlackOAuthService
from pigrocrm.core.slack.schemas import (
    SlackChannelRead,
    SlackChannelWrite,
    SlackHealth,
    SlackLinkWrite,
    SlackMine,
)
from pigrocrm.core.slack.service import SlackService
from pigrocrm_api.deps import ActorDep, SessionDep, SettingsDep, callback_actor
from pigrocrm_api.errors import PROBLEM_RESPONSES
from pigrocrm_api.oauth_relay import relay_to_space
from pigrocrm_api.tenancy import cookie_path

router = APIRouter(prefix="/api/slack", tags=["slack"], responses=PROBLEM_RESPONSES)

# The outcome codes of a consent, rendered in Italian by the SPA: the same fixed table of
# four as Gmail's and Drive's, so nothing an attacker appends to this URL puts words of its
# own on the screen.
_SETTINGS_PAGE = "/app/settings/slack"
_ESITO_COLLEGATO = "collegato"
_ESITO_NEGATO = "negato"
_ESITO_ERRORE = "errore"
_ESITO_SESSIONE = "sessione"

_CALLBACK_ROUTE = "/oauth/callback"
_CALLBACK_PATH = f"{router.prefix}{_CALLBACK_ROUTE}"


def slack_client(settings: SettingsDep) -> SlackClient:
    return slack_client_for(settings)


SlackClientDep = Annotated[SlackClient, Depends(slack_client)]


def _service(session: SessionDep, settings: Settings, client: SlackClient) -> SlackService:
    return SlackService(session, settings=settings, client=client)


def _oauth(session: SessionDep, settings: Settings, client: SlackClient) -> SlackOAuthService:
    return SlackOAuthService(session, settings=settings, client=client)


@router.get("/account", response_model=SlackHealth)
def read_account(
    session: SessionDep, actor: ActorDep, settings: SettingsDep, client: SlackClientDep
) -> SlackHealth:
    """200 even when Slack is not configured: see the module docstring. Admins only, since
    it lists every user of the space."""
    return _service(session, settings, client).health(actor)


@router.get("/me", response_model=SlackMine)
def read_mine(
    session: SessionDep, actor: ActorDep, settings: SettingsDep, client: SlackClientDep
) -> SlackMine:
    """Whether the space has a workspace and whether the caller is in it. Anyone's."""
    return _service(session, settings, client).mine(actor)


@router.get("/oauth/start")
def start_oauth(
    session: SessionDep, actor: ActorDep, settings: SettingsDep, client: SlackClientDep
) -> RedirectResponse:
    # Snyk Code python/OR here is a false positive: the target is the configured Slack
    # origin's authorize page, with every parameter urlencoded.
    return RedirectResponse(_oauth(session, settings, client).start(actor), status_code=307)


@router.get(_CALLBACK_ROUTE)
def finish_oauth(
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    client: SlackClientDep,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> RedirectResponse:
    # Before the actor: a consent a space started through the root's app comes back here
    # carrying that space's cookie only (`oauth_relay.py`).
    relayed = relay_to_space(
        request,
        settings,
        _CALLBACK_PATH,
        code=code,
        state=state,
        error=error,
        lends=bool(settings.slack_client_id),
    )
    if relayed is not None:
        return relayed
    actor = callback_actor(request, session, settings)
    if actor is None:
        return _back_to_settings(request, _ESITO_SESSIONE)
    if error is not None or code is None or state is None:
        return _back_to_settings(request, _ESITO_NEGATO)
    try:
        _oauth(session, settings, client).complete(code=code, state=state, actor=actor)
    except DomainError:
        # Every refusal `complete` can produce: a `Conflict` (a replayed or expired state, a
        # workspace already connected, a code Slack refused, an app installed with fewer
        # scopes) or a refused role (a scoped or demoted admin must not meet a problem
        # document mid-consent). All end on the settings page, which re-reads
        # `GET /account` and shows the true state.
        return _back_to_settings(request, _ESITO_ERRORE)
    return _back_to_settings(request, _ESITO_COLLEGATO)


def _back_to_settings(request: Request, esito: str) -> RedirectResponse:
    # Under the prefix the request wore: a space's consent must end on that space's page.
    prefix = cookie_path(request).rstrip("/")
    # Snyk Code python/OR here is a false positive: a fixed page, under the request's own
    # prefix (a slug that matched SLUG_PATTERN in `tenancy.py`, or none for the root), with
    # one of the four constant esito codes.
    return RedirectResponse(f"{prefix}{_SETTINGS_PAGE}?esito={esito}", status_code=307)


@router.delete("/account", status_code=204)
def disconnect(
    session: SessionDep, actor: ActorDep, settings: SettingsDep, client: SlackClientDep
) -> None:
    _oauth(session, settings, client).disconnect(actor)


@router.post("/account/rematch", response_model=SlackHealth)
def rematch(
    session: SessionDep, actor: ActorDep, settings: SettingsDep, client: SlackClientDep
) -> SlackHealth:
    """Asks Slack again about every user without a manual match."""
    return _service(session, settings, client).rematch(actor)


@router.put("/users/{user_id}/link", response_model=SlackHealth)
def link_user(
    user_id: UUID,
    payload: SlackLinkWrite,
    session: SessionDep,
    actor: ActorDep,
    settings: SettingsDep,
    client: SlackClientDep,
) -> SlackHealth:
    """Says by hand which Slack member a user is: the fix for a mismatch."""
    return _service(session, settings, client).link(actor, user_id, payload.email)


@router.delete("/users/{user_id}/link", response_model=SlackHealth)
def unlink_user(
    user_id: UUID,
    session: SessionDep,
    actor: ActorDep,
    settings: SettingsDep,
    client: SlackClientDep,
) -> SlackHealth:
    """The way back from a manual fix to the match by email."""
    return _service(session, settings, client).unlink(actor, user_id)


@router.get("/channels", response_model=list[SlackChannelRead])
def list_channels(
    session: SessionDep, actor: ActorDep, settings: SettingsDep, client: SlackClientDep
) -> list[SlackChannelRead]:
    return _service(session, settings, client).channels(actor)


@router.put("/channel", response_model=SlackHealth)
def set_channel(
    payload: SlackChannelWrite,
    session: SessionDep,
    actor: ActorDep,
    settings: SettingsDep,
    client: SlackClientDep,
) -> SlackHealth:
    """The channel the space-wide reminders are also posted to, or `null` for none."""
    return _service(session, settings, client).set_channel(actor, payload.channel_id)
