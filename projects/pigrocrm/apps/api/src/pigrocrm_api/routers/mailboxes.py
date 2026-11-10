"""The REST surface of the mailboxes (REB-1015, § 2.2 of the incoming-documents design).

Connecting an IMAP or PEC mailbox, typing a new password for it and disconnecting it are a
**person's** acts: a personal access token is refused on all three whatever it carries
(`require_person` in the service). The
password goes in a request and nowhere else: no route returns it, sealed or not.

`imap_options` is the dependency that decides how a connection is opened (the resolver and the
TLS context), so a test replaces it with a fake IMAP server's and nothing here reaches a real
account from a test.
"""

import ssl
from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends

from pigrocrm.core.mailboxes.imap_client import Resolver
from pigrocrm.core.mailboxes.imap_service import ImapMailboxService
from pigrocrm.core.mailboxes.schemas import ImapMailboxCreate, ImapPasswordChange, MailboxRead
from pigrocrm.core.reminders.push import resolve_public
from pigrocrm_api.deps import ActorDep, SessionDep, SettingsDep, get_actor
from pigrocrm_api.errors import PROBLEM_RESPONSES

router = APIRouter(
    prefix="/api/mailboxes",
    tags=["mailboxes"],
    dependencies=[Depends(get_actor)],
    responses=PROBLEM_RESPONSES,
)


@dataclass(frozen=True)
class ImapOptions:
    resolve: Resolver = resolve_public
    context: ssl.SSLContext | None = None


def imap_options() -> ImapOptions:
    return ImapOptions()


ImapOptionsDep = Annotated[ImapOptions, Depends(imap_options)]


def _service(
    session: SessionDep, settings: SettingsDep, options: ImapOptions
) -> ImapMailboxService:
    return ImapMailboxService(
        session, settings=settings, resolve=options.resolve, context=options.context
    )


@router.get("", response_model=list[MailboxRead])
def list_mailboxes(
    session: SessionDep, actor: ActorDep, settings: SettingsDep, options: ImapOptionsDep
) -> list[MailboxRead]:
    """The caller's own mailboxes and, for an administrator, the space's, each with its health."""
    return _service(session, settings, options).list(actor)


@router.post(
    "/imap",
    response_model=MailboxRead,
    status_code=201,
)
def connect_imap(
    body: ImapMailboxCreate,
    session: SessionDep,
    actor: ActorDep,
    settings: SettingsDep,
    options: ImapOptionsDep,
) -> MailboxRead:
    """Logs in once with the password, and keeps the mailbox only if the server accepted it.
    The connection is TLS or STARTTLS and the host must resolve to a public address."""
    return _service(session, settings, options).connect(actor, body)


@router.put(
    "/{mailbox_id}/password",
    response_model=MailboxRead,
)
def change_password(
    mailbox_id: UUID,
    body: ImapPasswordChange,
    session: SessionDep,
    actor: ActorDep,
    settings: SettingsDep,
    options: ImapOptionsDep,
) -> MailboxRead:
    """A new password, tried first: how a mailbox leaves `auth_failed`."""
    return _service(session, settings, options).change_password(actor, mailbox_id, body.password)


@router.delete(
    "/{mailbox_id}",
    response_model=MailboxRead,
)
def disconnect(
    mailbox_id: UUID,
    session: SessionDep,
    actor: ActorDep,
    settings: SettingsDep,
    options: ImapOptionsDep,
) -> MailboxRead:
    """Stops reading and forgets the password; what was received stays."""
    return _service(session, settings, options).disconnect(actor, mailbox_id)
