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

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import RedirectResponse

from pigrocrm.core.activities.schemas import ActivityRead
from pigrocrm.core.activities.service import ActivityService
from pigrocrm.core.config import Settings
from pigrocrm.core.errors import DomainError
from pigrocrm.core.slack.client import SlackClient, slack_client_for
from pigrocrm.core.slack.errors import SLACK_ENTITY
from pigrocrm.core.slack.oauth import SlackOAuthService
from pigrocrm.core.slack.repository import SlackRepository
from pigrocrm.core.slack.schemas import (
    SlackChannelRead,
    SlackChannelWrite,
    SlackHealth,
    SlackLinkWrite,
    SlackMine,
)
from pigrocrm.core.slack.service import SlackService
from pigrocrm_api.deps import ActorDep, SessionDep, SettingsDep, get_actor
from pigrocrm_api.errors import PROBLEM_RESPONSES
from pigrocrm_api.oauth_callback import finish_consent
from pigrocrm_api.query_params import PAGE_LIMIT_DEFAULT, PAGE_LIMIT_MAX

router = APIRouter(prefix="/api/slack", tags=["slack"], responses=PROBLEM_RESPONSES)
# The one public route is the OAuth callback: a browser navigation that decides for itself
# what a missing session means (`oauth_callback.py`). Everything else sits behind
# `get_actor`, so a handler added to `authenticated` cannot forget to ask who is calling.
authenticated = APIRouter(dependencies=[Depends(get_actor)])

# Where the SPA renders the outcome of a consent (`oauth_callback.py`).
_SETTINGS_PAGE = "/app/settings/slack"

_CALLBACK_ROUTE = "/oauth/callback"
_CALLBACK_PATH = f"{router.prefix}{_CALLBACK_ROUTE}"


def slack_client(settings: SettingsDep) -> SlackClient:
    return slack_client_for(settings)


SlackClientDep = Annotated[SlackClient, Depends(slack_client)]


def _service(session: SessionDep, settings: Settings, client: SlackClient) -> SlackService:
    return SlackService(session, settings=settings, client=client)


def _oauth(session: SessionDep, settings: Settings, client: SlackClient) -> SlackOAuthService:
    return SlackOAuthService(session, settings=settings, client=client)


@authenticated.get("/account", response_model=SlackHealth)
def read_account(
    session: SessionDep, actor: ActorDep, settings: SettingsDep, client: SlackClientDep
) -> SlackHealth:
    """200 even when Slack is not configured: see the module docstring. Admins only, since
    it lists every user of the space."""
    return _service(session, settings, client).health(actor)


@authenticated.get("/timeline", response_model=list[ActivityRead])
def read_timeline(
    session: SessionDep,
    actor: ActorDep,
    limit: Annotated[int, Query(ge=1, le=PAGE_LIMIT_MAX)] = PAGE_LIMIT_DEFAULT,
) -> list[ActivityRead]:
    """Who connected, disconnected or changed the Slack settings, and when, newest first,
    for the administrators who can make those changes (REB-828). Empty while the space has
    never had a workspace."""
    actor.require_unscoped_admin("read_slack_timeline")
    workspace = SlackRepository(session).workspace()
    if workspace is None:
        return []
    return ActivityService(session).timeline(SLACK_ENTITY, workspace.id, limit)


@authenticated.get("/me", response_model=SlackMine)
def read_mine(
    session: SessionDep, actor: ActorDep, settings: SettingsDep, client: SlackClientDep
) -> SlackMine:
    """Whether the space has a workspace and whether the caller is in it. Anyone's."""
    return _service(session, settings, client).mine(actor)


@authenticated.get("/oauth/start")
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
    # Every refusal `complete` can produce: a `Conflict` (a replayed or expired state, a
    # workspace already connected, a code Slack refused, an app installed with fewer
    # scopes) or a refused role (a scoped or demoted admin must not meet a problem document
    # mid-consent). All end on the settings page, which re-reads `GET /account` and shows
    # the true state.
    return finish_consent(
        request,
        session,
        settings,
        callback_path=_CALLBACK_PATH,
        code=code,
        state=state,
        error=error,
        complete=lambda code, state, actor: _oauth(session, settings, client).complete(
            code=code, state=state, actor=actor
        ),
        page=_SETTINGS_PAGE,
        refusals=(DomainError,),
        lends=bool(settings.slack_client_id),
    )


@authenticated.delete("/account", status_code=204)
def disconnect(
    session: SessionDep, actor: ActorDep, settings: SettingsDep, client: SlackClientDep
) -> None:
    _oauth(session, settings, client).disconnect(actor)


@authenticated.post("/account/rematch", response_model=SlackHealth)
def rematch(
    session: SessionDep, actor: ActorDep, settings: SettingsDep, client: SlackClientDep
) -> SlackHealth:
    """Asks Slack again about every user without a manual match."""
    return _service(session, settings, client).rematch(actor)


@authenticated.put("/users/{user_id}/link", response_model=SlackHealth)
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


@authenticated.delete("/users/{user_id}/link", response_model=SlackHealth)
def unlink_user(
    user_id: UUID,
    session: SessionDep,
    actor: ActorDep,
    settings: SettingsDep,
    client: SlackClientDep,
) -> SlackHealth:
    """The way back from a manual fix to the match by email."""
    return _service(session, settings, client).unlink(actor, user_id)


@authenticated.get("/channels", response_model=list[SlackChannelRead])
def list_channels(
    session: SessionDep, actor: ActorDep, settings: SettingsDep, client: SlackClientDep
) -> list[SlackChannelRead]:
    return _service(session, settings, client).channels(actor)


@authenticated.put("/channel", response_model=SlackHealth)
def set_channel(
    payload: SlackChannelWrite,
    session: SessionDep,
    actor: ActorDep,
    settings: SettingsDep,
    client: SlackClientDep,
) -> SlackHealth:
    """The channel the space-wide reminders are also posted to, or `null` for none."""
    return _service(session, settings, client).set_channel(actor, payload.channel_id)


router.include_router(authenticated)
