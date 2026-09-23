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
page with that door, for anybody the admin-only settings page would turn away. Google's own `error`
is never forwarded: it is English and occasionally embeds the client id. Nor is a
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

import threading
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Query, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.config import Settings, gmail_configured
from pigrocrm.core.errors import Conflict
from pigrocrm.core.first_steps import space_is_empty
from pigrocrm.core.gmail.account import GoogleAccountService
from pigrocrm.core.gmail.oauth import GmailOAuthService
from pigrocrm.core.gmail.repository import GmailRepository
from pigrocrm.core.gmail.schemas import (
    GmailBackfillRequest,
    GmailHealth,
    GmailMessageRead,
    GmailSettingsUpdate,
    GoogleAccountRead,
    SyncReport,
)
from pigrocrm.core.gmail.sync import GmailSyncService
from pigrocrm.core.gmail.tokens import GoogleTokenClient
from pigrocrm.core.gmail.transport import GmailTransport
from pigrocrm_api.deps import ActorDep, SessionDep, SettingsDep, get_actor
from pigrocrm_api.errors import PROBLEM_RESPONSES
from pigrocrm_api.oauth_relay import relay_to_space
from pigrocrm_api.tenancy import cookie_path

router = APIRouter(prefix="/api/gmail", tags=["gmail"], responses=PROBLEM_RESPONSES)

# Where the SPA renders the outcome of a consent flow. Three codes and no free text:
# `esito` is looked up in a fixed table on the page, so nothing an attacker appends to
# this URL can put words of their own on the screen. The start page's Gmail door reads
# the same table, on the Home of an empty space and on «Primi passi» (`_back`).
_SETTINGS_PAGE = "/app/settings/gmail"
_HOME_PAGE = "/app/"
_START_PAGE = "/app/get-started"
_ESITO_COLLEGATO = "collegato"
_ESITO_NEGATO = "negato"
_ESITO_ERRORE = "errore"

# One `GoogleTokenClient` per process, keyed by the OAuth client it authenticates as.
#
# Not one per request, which is what an adapter naturally writes and what would quietly
# undo the module this cache lives in: `GoogleTokenClient` says "one per process,
# shared across requests: the cache is the whole point", and a fresh instance per
# request throws every cached access token away -- an extra OAuth round trip on every
# sync, which is defect 1 of the three `tokens.py` was written to remove. It also makes
# `forget()` mean something: `disconnect` calls it, and on a throwaway client that call
# would clear a cache nobody was ever going to read.
#
# Keyed on the client id rather than on the `Settings` object: `get_settings` is
# `lru_cache`d in production so the object is a singleton there, but the test suite
# overrides the dependency per test and an identity key would leak one test's client
# into the next. The secret is not part of the key -- one client id has one secret, and
# a dict key is one more place a credential would sit.
#
# The space's state prefix is part of it (REB-394). Spaces borrowing the root's client
# share its client id, and the access tokens in a client's cache are keyed by the
# account's id alone: a row copied into another space's database would otherwise pick
# up the first space's live token without unsealing anything, and `forget()` in one
# space would evict another's. The prefix is empty for the root and for a space with a
# client of its own, and it is not a secret.
_token_clients: dict[tuple[str, str], GoogleTokenClient] = {}
# FastAPI runs sync endpoints in a thread pool, so two requests can reach a cold cache
# at once. Same reasoning, and the same shape, as `deps.py`'s `_registry_lock`.
_token_clients_lock = threading.Lock()

# `test_gmail_query.py`'s second guard refuses the literal "/messages" anywhere outside
# `gmail/query.py`, so that no module can hand-build a Gmail listing URL and slip past
# the address filter. This is a route on *our own* API and reaches nothing but the
# database, so it is named in that guard's short allowlist of our own route paths
# rather than spelled here in a way that evades the scan.
_MESSAGES_PATH = "/messages"
_ACCOUNT_PATH = "/account"
_CALLBACK_ROUTE = "/oauth/callback"
_CALLBACK_PATH = f"{router.prefix}{_CALLBACK_ROUTE}"


def token_client_key(settings: Settings) -> tuple[str, str]:
    """Which cached `GoogleTokenClient` a request's settings get: one per client id and
    per space borrowing it (see `_token_clients`)."""
    return (settings.google_oauth_state_prefix, settings.google_client_id)


def token_client(settings: Settings) -> GoogleTokenClient:
    key = token_client_key(settings)
    client = _token_clients.get(key)
    if client is None:
        with _token_clients_lock:
            client = _token_clients.get(key)
            if client is None:
                client = GoogleTokenClient(
                    client_id=settings.google_client_id,
                    client_secret=settings.google_client_secret,
                    transport=GmailTransport(),
                )
                _token_clients[key] = client
    return client


def _oauth(session: Session, settings: Settings) -> GmailOAuthService:
    return GmailOAuthService(session, settings=settings, tokens=token_client(settings))


def _sync(session: Session, settings: Settings) -> GmailSyncService:
    return GmailSyncService(
        session, settings=settings, transport=GmailTransport(), tokens=token_client(settings)
    )


@router.get(_ACCOUNT_PATH, response_model=GmailHealth)
def read_account(session: SessionDep, actor: ActorDep, settings: SettingsDep) -> GmailHealth:
    """200 even when Gmail is not configured -- see the module docstring."""
    if not gmail_configured(settings):
        # `configured=False` is the whole difference between "questa installazione non
        # ha Google" and "non hai ancora collegato la casella". Both answer with no
        # account, and they ask opposite things of the person.
        return GmailHealth(
            account=None, banner=None, banner_text=None, missing_scopes=[], configured=False
        )
    return GoogleAccountService(session, settings=settings).health(actor)


@router.get("/oauth/start")
def start_oauth(session: SessionDep, actor: ActorDep, settings: SettingsDep) -> RedirectResponse:
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
    # Before the actor, on purpose: a consent a space started through the root's client
    # comes back here carrying that space's cookie only (`oauth_relay.py`, REB-394).
    relayed = relay_to_space(request, settings, _CALLBACK_PATH, code=code, state=state, error=error)
    if relayed is not None:
        return relayed
    actor = get_actor(request, session, settings)
    if error is not None or code is None or state is None:
        # One outcome code for every refusal on Google's side. Google's own `error` is
        # not forwarded: it is English, and it sometimes embeds the client id.
        return _back(request, session, actor, _ESITO_NEGATO)
    try:
        _oauth(session, settings).complete(code=code, state=state, actor=actor)
    except Conflict:
        # Every refusal this flow can produce is a `Conflict` -- a replayed or expired
        # state, a mailbox that is not the connected one, a grant that came back with
        # no refresh token, Gmail not answering. All of them arrive here through a
        # browser redirect, so all of them end on a page that re-reads `GET /account`
        # and shows the true state (the settings panel, or the Home's Gmail door). A
        # `PermissionDenied` deliberately is *not* caught: that is not an outcome of the
        # consent flow but a caller who may not perform it, and it belongs in the
        # problem document like every other 403.
        return _back(request, session, actor, _ESITO_ERRORE)
    return _back(request, session, actor, _ESITO_COLLEGATO)


def _back(request: Request, session: Session, actor: Actor, esito: str) -> RedirectResponse:
    # Under the prefix the request wore: a space's consent must end on that space's
    # page, not on the root's. `cookie_path` is the one place that already knows the
    # prefix, and its `/` is the bare root.
    #
    # Every outcome goes to the same page, the refusals included: whoever pressed
    # «Collega Gmail» on an empty space's Home reads «Autorizzazione negata» next to the
    # door they pressed. Read after `complete`, which never creates a customer, so the
    # answer is the one the person saw before leaving for Google. A space with work in it
    # sends an admin to the settings page, as it always has, and anybody else to «Primi
    # passi»: a collaboratore may connect their own mailbox (`require_write`) but may not
    # open Impostazioni, which would answer the consent with «Accesso riservato».
    prefix = cookie_path(request).rstrip("/")
    if space_is_empty(session):
        page = _HOME_PAGE
    elif actor.role == "admin":
        page = _SETTINGS_PAGE
    else:
        page = _START_PAGE
    return RedirectResponse(f"{prefix}{page}?esito={esito}", status_code=307)


@router.delete(_ACCOUNT_PATH, status_code=204)
def disconnect(
    session: SessionDep,
    actor: ActorDep,
    settings: SettingsDep,
    elimina_messaggi: Annotated[bool, Query()] = False,
) -> None:
    """`elimina_messaggi` defaults to false, and the default is the decision: deleting a
    customer's correspondence because a token expired would be a disaster, so the
    destructive half has to be asked for."""
    _oauth(session, settings).disconnect(delete_messages=elimina_messaggi, actor=actor)


@router.patch(_ACCOUNT_PATH, response_model=GoogleAccountRead)
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


@router.post("/sync", response_model=SyncReport)
def run_sync(session: SessionDep, actor: ActorDep, settings: SettingsDep) -> SyncReport:
    return _sync(session, settings).sync(actor)


@router.post("/backfill", response_model=SyncReport)
def run_backfill(
    payload: GmailBackfillRequest, session: SessionDep, actor: ActorDep, settings: SettingsDep
) -> SyncReport:
    return _sync(session, settings).backfill(
        payload.entity_type, payload.entity_id, full=payload.full, actor=actor
    )


@router.get(_MESSAGES_PATH, response_model=list[GmailMessageRead])
def read_messages(
    session: SessionDep,
    actor: ActorDep,
    entity_type: Annotated[Literal["customer", "person", "deal"], Query()],
    entity_id: Annotated[UUID, Query()],
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
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
    del actor  # authentication is the dependency's job; reading here is not role-gated
    rows = GmailRepository(session).messages_for_entity(entity_type, entity_id, limit=limit)
    return [GmailMessageRead.model_validate(row) for row in rows]
