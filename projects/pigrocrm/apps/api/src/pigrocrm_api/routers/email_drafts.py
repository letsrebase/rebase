"""The composer's surface, and the one endpoint in the product that sends an email.

Thin, like every other router here. Two things about it are not thin, and both are
decisions rather than plumbing:

**`POST /{draft_id}/send` has no MCP counterpart, and never will.** The refusal is
structural, not a permission check: a personal access token inherits its owner's full
role and never expires (residuo R10), so a check *inside* a registered tool is a check an
administrator's token passes. The only mechanism that holds is that the tool does not
exist -- see `apps/mcp/tests/test_mcp_invoice_ban.py`, which owns the ban, and
`tools/gmail.py`, which says why. An email leaving the owner's mailbox cannot be
recalled, the client reads it as the owner's own words, and an agent holding *read* of
the mail and *send* on one channel has the injection source and the exfiltration channel
together.

**`POST /{draft_id}/reconcile` is an endpoint of its own, not a branch of send.** The
send is the one call in the system with no idempotency key, so an unknown outcome
(`incerto`) is resolved by *asking Gmail* and never by sending again. A UI that offered
"riprova" on an unknown outcome would be offering the client a second copy of the same
email; what it offers instead is «verifica», and this is what it calls.

Nothing here accepts an uploaded file. Attachments come from `document_versions` (spec
6.4) and are named by id, which `test_email_drafts_router.py` asserts against the
generated OpenAPI document rather than against this file's own route list.
"""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Query
from sqlalchemy.orm import Session

from pigrocrm.core.config import Settings
from pigrocrm.core.gmail.drafts import EmailDraftService
from pigrocrm.core.gmail.schemas import (
    EmailDraftCreate,
    EmailDraftListQuery,
    EmailDraftPage,
    EmailDraftRead,
    EmailDraftUpdate,
    SendState,
)
from pigrocrm.core.gmail.send import EmailSendService, reconcile_only
from pigrocrm.core.gmail.transport import GmailTransport
from pigrocrm.core.storage.base import DocumentStorage
from pigrocrm_api.deps import ActorDep, SessionDep, SettingsDep, StorageDep
from pigrocrm_api.errors import PROBLEM_RESPONSES
from pigrocrm_api.routers.gmail import token_client

router = APIRouter(prefix="/api/email-drafts", tags=["email-drafts"], responses=PROBLEM_RESPONSES)


def _drafts(session: Session, settings: Settings) -> EmailDraftService:
    return EmailDraftService(session, settings=settings)


def _sender(session: Session, settings: Settings, storage: DocumentStorage) -> EmailSendService:
    """`token_client` is `routers/gmail.py`'s per-process `GoogleTokenClient` cache, reused
    rather than re-created here. A fresh client per request throws away every cached
    access token -- an extra OAuth round trip on every send -- and would make
    `disconnect`'s `forget()` clear a cache nobody was going to read."""
    return EmailSendService(
        session,
        settings=settings,
        transport=GmailTransport(),
        tokens=token_client(settings),
        storage=storage,
    )


@router.post("", response_model=EmailDraftRead, status_code=201)
def create_draft(
    payload: EmailDraftCreate, session: SessionDep, actor: ActorDep, settings: SettingsDep
) -> EmailDraftRead:
    """The row exists before Gmail is called and the `Message-ID` is minted here (spec
    6.2), which is what makes text somebody typed survive a closed tab -- and an unknown
    outcome resolvable by an exact lookup instead of by comparing timestamps."""
    return _drafts(session, settings).create(payload, actor)


@router.get("", response_model=EmailDraftPage)
def list_drafts(
    session: SessionDep,
    actor: ActorDep,
    settings: SettingsDep,
    # Spelled out as query parameters and hand-assembled below, the same shape as
    # `routers/customers.py`. No `SafeStr` is needed here and its absence is the point:
    # every filter on this list is a closed set or an id, so there is no free text on the
    # surface at all -- which is also why nothing here can become a Gmail search string.
    entity_type: Annotated[Literal["customer", "person", "deal"] | None, Query()] = None,
    entity_id: Annotated[UUID | None, Query()] = None,
    send_state: Annotated[SendState | None, Query()] = None,
    unsent: Annotated[bool, Query()] = False,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> EmailDraftPage:
    query = EmailDraftListQuery(
        entity_type=entity_type,
        entity_id=entity_id,
        send_state=send_state,
        unsent=unsent,
        limit=limit,
    )
    return _drafts(session, settings).list(query, actor)


@router.get("/{draft_id}", response_model=EmailDraftRead)
def read_draft(
    draft_id: UUID, session: SessionDep, actor: ActorDep, settings: SettingsDep
) -> EmailDraftRead:
    return _drafts(session, settings).get(draft_id, actor)


@router.patch("/{draft_id}", response_model=EmailDraftRead)
def update_draft(
    draft_id: UUID,
    payload: EmailDraftUpdate,
    session: SessionDep,
    actor: ActorDep,
    settings: SettingsDep,
) -> EmailDraftRead:
    """A `fallito` draft is editable, and editing returns it to `bozza` with the error
    cleared (spec 6.3(a)). The service owns that transition; the composer only has to
    honour the state it gets back."""
    return _drafts(session, settings).update(draft_id, payload, actor)


@router.delete("/{draft_id}", status_code=204)
def delete_draft(
    draft_id: UUID, session: SessionDep, actor: ActorDep, settings: SettingsDep
) -> None:
    return _drafts(session, settings).delete(draft_id, actor)


@router.post("/{draft_id}/send", response_model=EmailDraftRead)
def send_draft(
    draft_id: UUID,
    session: SessionDep,
    actor: ActorDep,
    settings: SettingsDep,
    storage: StorageDep,
) -> EmailDraftRead:
    """The only endpoint in the product that sends an email, and deliberately **not** an
    MCP tool -- see the module docstring for why that gap is the design.

    It takes no body. Everything that will be sent is already on the row, which is what
    lets the person read exactly what will leave before they press Invia.
    """
    return _sender(session, settings, storage).send(draft_id, actor)


@router.post("/{draft_id}/reconcile", response_model=EmailDraftRead)
def reconcile_draft(
    draft_id: UUID, session: SessionDep, actor: ActorDep, settings: SettingsDep
) -> EmailDraftRead:
    """Resolves an `incerto` draft by asking Gmail. Also runs at the start of every sync,
    so an unresolved outcome does not wait for somebody to remember it.

    Built through `reconcile_only`, which supplies a storage backend that refuses every
    call: this path looks a message up and adopts an id, so it composes nothing and reads
    no document. Handing it the real storage would work today and would stop being an
    assertion tomorrow.
    """
    return reconcile_only(
        session, settings=settings, transport=GmailTransport(), tokens=token_client(settings)
    ).reconcile(draft_id, actor)
