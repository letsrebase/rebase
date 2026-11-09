"""The REST surface of slice 5B.

Thin, like every other router here: it resolves an actor, builds the service, and
returns what the service returns. Three things it does that the others do not, each
because Gmail is different in a way the adapter has to answer for.

**`GET /account` answers 200 even when Gmail is not configured.** An installation with
no Google client is not a broken one -- it is one that deliberately has no Google -- and
the settings page has to render that without the SPA treating the response as a failed
query. Every *other* endpoint answers 409 with the one sentence
`require_gmail_configured` raises, because they would have to talk to Google to do
anything at all.

**The OAuth callback is a browser navigation, not an XHR.** It therefore always ends on
a page of the SPA, with an outcome code the SPA renders in Italian: the Home while the
space is still empty, since that is where its «Collega Gmail» door is (spec 2026-09-16
§4.2, REB-222); otherwise the settings page for an admin, and «Primi passi», the other
page with that door, for anybody the admin-only settings page would turn away. A browser
that comes back with no live session at all lands on «Primi passi» with `session_expired`, and
the SPA's own login sends it on there once the person is back in (REB-446). Google's
own `error` is never forwarded: it is English and occasionally embeds the client id. Nor is a
`Conflict` from the exchange rendered as a problem document -- an RFC 9457 body in the
address bar strands the user outside the SPA at the end of a consent flow, with the one
message they could act on ("scollega prima l'account attuale") in a shape no browser
displays usefully. The settings page reloads `GET /account` instead, which shows the
mailbox that *is* connected and the button that disconnects it.

**No endpoint accepts a search string.** Deliberately, and asserted against the
generated OpenAPI document in `test_gmail_router.py`: everything that reaches Gmail is
built from the roster by `gmail/query.py`, and a `q` parameter here would be a way past
the address filter that guards the whole slice (spec 4.1, 12).
"""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.config import Settings, gmail_configured
from pigrocrm.core.first_steps import space_is_empty
from pigrocrm.core.gmail.account import GoogleAccountService
from pigrocrm.core.gmail.messages import GmailMessageService
from pigrocrm.core.gmail.oauth import GmailOAuthService
from pigrocrm.core.gmail.schemas import (
    DiscoveryReport,
    GmailBackfillRequest,
    GmailHealth,
    GmailMessageRead,
    GmailSettingsUpdate,
    GoogleAccountRead,
    SuggestedCustomer,
    SyncReport,
)
from pigrocrm.core.gmail.sync import SUGGEST_MAX_MONTHS, GmailSyncService
from pigrocrm.core.gmail.transport import GmailTransport
from pigrocrm_api.deps import ActorDep, SessionDep, SettingsDep, get_actor, off_entity_type_guard
from pigrocrm_api.errors import PROBLEM_RESPONSES
from pigrocrm_api.google_tokens import token_client
from pigrocrm_api.oauth_callback import finish_consent

router = APIRouter(prefix="/api/gmail", tags=["gmail"], responses=PROBLEM_RESPONSES)
# The one public route is the OAuth callback: a browser navigation that decides for itself
# what a missing session means (`oauth_callback.py`). Everything else sits behind
# `get_actor`, so a handler added to `authenticated` cannot forget to ask who is calling.
authenticated = APIRouter(dependencies=[Depends(get_actor)])

# Where the SPA renders the outcome of a consent flow (`oauth_callback.py`): the Home while
# the space is still empty, since that is where its «Collega Gmail» door is (spec
# 2026-09-16 §4.2, REB-222); otherwise the settings page for an admin, and «Primi passi»,
# the other page with that door, for anybody the admin-only settings page would turn away.
_SETTINGS_PAGE = "/app/settings/gmail"
_HOME_PAGE = "/app/"
_START_PAGE = "/app/get-started"

# `test_gmail_query.py`'s second guard refuses the literal "/messages" anywhere outside
# `gmail/query.py`, so that no module can hand-build a Gmail listing URL and slip past
# the address filter. This is a route on *our own* API and reaches nothing but the
# database, so it is named in that guard's short allowlist of our own route paths
# rather than spelled here in a way that evades the scan.
_MESSAGES_PATH = "/messages"
_ACCOUNT_PATH = "/account"
_SUGGESTIONS_PATH = "/customer-suggestions"
_CORRESPONDENTS_PATH = "/customers/{customer_id}/correspondents"
_CALLBACK_ROUTE = "/oauth/callback"
_CALLBACK_PATH = f"{router.prefix}{_CALLBACK_ROUTE}"


def _oauth(session: Session, settings: Settings) -> GmailOAuthService:
    return GmailOAuthService(session, settings=settings, tokens=token_client(settings))


def _sync(session: Session, settings: Settings) -> GmailSyncService:
    return GmailSyncService(
        session, settings=settings, transport=GmailTransport(), tokens=token_client(settings)
    )


@authenticated.get(_ACCOUNT_PATH, response_model=GmailHealth)
def read_account(session: SessionDep, actor: ActorDep, settings: SettingsDep) -> GmailHealth:
    """200 even when Gmail is not configured -- see the module docstring."""
    if not gmail_configured(settings):
        # `configured=False` is the whole difference between "this installation has no
        # Google" and "you have not connected the mailbox yet". Both answer with no
        # account, and they ask opposite things of the person.
        return GmailHealth(
            account=None, banner=None, banner_text=None, missing_scopes=[], configured=False
        )
    return GoogleAccountService(session, settings=settings).health(actor)


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
    # Every refusal this flow can produce is a `Conflict` -- a replayed or expired state, a
    # mailbox that is not the connected one, a grant that came back with no refresh token,
    # Gmail not answering -- so `finish_consent` turns exactly those into the `error`
    # page. A `PermissionDenied` is not an outcome of the consent flow but a caller who may
    # not perform it, and it belongs in the problem document like every other 403.
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
        page=lambda actor: _landing_page(session, actor),
    )


def _landing_page(session: Session, actor: Actor | None) -> str:
    # Every outcome goes to the same page, the refusals included: whoever pressed
    # «Collega Gmail» on an empty space's Home reads «Autorizzazione negata» next to the
    # door they pressed. Read after `complete`, which never creates a customer, so the
    # answer is the one the person saw before leaving for Google. A space with work in it
    # sends an admin to the settings page, as it always has, and anybody else to «Primi
    # passi»: a collaborator may connect their own mailbox (`require_write`) but may not
    # open Impostazioni, which would answer the consent with «Accesso riservato».
    #
    # With no actor (REB-446) the page is «Primi passi», whoever started the flow and
    # whatever the space holds: it carries the Gmail door for every role, it is the Home's
    # own content while the space is empty, and choosing it without reading the database
    # keeps a request with no session from learning whether the space is empty.
    if actor is None:
        return _START_PAGE
    if space_is_empty(session):
        return _HOME_PAGE
    return _SETTINGS_PAGE if actor.role == "admin" else _START_PAGE


@authenticated.delete(_ACCOUNT_PATH, status_code=204)
def disconnect(
    session: SessionDep,
    actor: ActorDep,
    settings: SettingsDep,
    delete_messages: Annotated[bool, Query()] = False,
) -> None:
    """`delete_messages` defaults to false, and the default is the decision: deleting a
    customer's correspondence because a token expired would be a disaster, so the
    destructive half has to be asked for."""
    _oauth(session, settings).disconnect(delete_messages=delete_messages, actor=actor)


@authenticated.patch(_ACCOUNT_PATH, response_model=GoogleAccountRead)
def update_settings(
    payload: GmailSettingsUpdate, session: SessionDep, actor: ActorDep, settings: SettingsDep
) -> GoogleAccountRead:
    """No `gmail_configured` gate, on purpose. Switching the body store off is the one
    thing a user must be able to do while the credential is broken -- that is exactly
    when they most want the CRM to stop keeping their mail -- and the service reaches
    the row through `_present`, not `usable`, for the same reason."""
    return GoogleAccountService(session, settings=settings).set_store_bodies(
        enabled=payload.gmail_store_bodies, actor=actor
    )


@authenticated.post("/sync", response_model=SyncReport)
def run_sync(session: SessionDep, actor: ActorDep, settings: SettingsDep) -> SyncReport:
    return _sync(session, settings).sync(actor)


@authenticated.post("/backfill", response_model=SyncReport)
def run_backfill(
    payload: GmailBackfillRequest, session: SessionDep, actor: ActorDep, settings: SettingsDep
) -> SyncReport:
    return _sync(session, settings).backfill(
        payload.entity_type, payload.entity_id, full=payload.full, actor=actor
    )


# The customers the connected mailbox proposes (spec 2026-09-16 §5, REB-223). It asks
# Gmail, so it answers 409 without a Google client or a mailbox, like `/sync`. No search
# string: the only parameter is how many months back, bounded by the service.
@authenticated.get(_SUGGESTIONS_PATH, response_model=list[SuggestedCustomer])
def suggest_customers(
    session: SessionDep,
    actor: ActorDep,
    settings: SettingsDep,
    months: Annotated[int, Query(ge=1, le=SUGGEST_MAX_MONTHS)] = 12,
) -> list[SuggestedCustomer]:
    return _sync(session, settings).suggest_customers(actor=actor, months=months)


# Who at one customer's domain the connected mailbox has written to or heard from
# (REB-766, `GmailSyncService.discover`). The domain is read from the customer record and
# nothing typed here reaches Gmail's `q`; it answers 409 without a Google client, a
# mailbox, or a customer with no domain to start from, like the proposals above.
@authenticated.get(_CORRESPONDENTS_PATH, response_model=DiscoveryReport)
def discover_correspondents(
    customer_id: UUID, session: SessionDep, actor: ActorDep, settings: SettingsDep
) -> DiscoveryReport:
    return _sync(session, settings).discover(customer_id, actor=actor)


@authenticated.get(
    _MESSAGES_PATH, response_model=list[GmailMessageRead], dependencies=[off_entity_type_guard]
)
def read_messages(
    session: SessionDep,
    actor: ActorDep,
    entity_type: Annotated[Literal["customer", "person", "deal"], Query()],
    entity_id: Annotated[UUID, Query()],
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    order: Annotated[
        Literal["conversations", "recent"],
        Query(
            description=(
                "`conversations`: grouped by conversation, like a client's page; `recent`: most "
                "recent first, so that the limit cuts the oldest"
            )
        ),
    ] = "conversations",
) -> list[GmailMessageRead]:
    """Reads only what is already in the CRM. There is no parameter here that reaches
    Gmail, by design (spec 8.2): the correspondence on a customer page comes from the
    stored mirror, so it is readable with the mailbox disconnected and costs nothing
    against anybody's Gmail quota.

    `entity_type` is a closed set rather than a free string: those are the three values
    `gmail/links.py` writes, and an endpoint that accepted a fourth would answer 200
    with an empty list for a typo -- which reads as "no correspondence" instead of
    "wrong question".
    """
    return GmailMessageService(session).list_for_entity(
        entity_type, entity_id, actor, limit=limit, newest_first=order == "recent"
    )


router.include_router(authenticated)
