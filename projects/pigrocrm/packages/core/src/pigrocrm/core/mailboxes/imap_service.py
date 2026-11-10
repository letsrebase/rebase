"""Connecting, re-keying, disconnecting and listing mailboxes (REB-1015, § 2.2).

**Connecting a mailbox and typing its password is a person's act**, like every credential
(REB-880): `require_person` comes first, so a personal access token is refused whatever it
carries, and then the role, which is the owner's own for a personal mailbox and an unscoped
administrator's for the space's. Nothing is stored until the server has accepted the login: a
mailbox that cannot be read is refused with the reason, and no row, sealed password or purpose
is left behind.

A mailbox somebody else owns is `NotFound`, not forbidden, so its existence is not a thing a
colleague can probe.
"""

import ssl
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from pigrocrm.core.activities.service import ActivityService
from pigrocrm.core.actor import Actor
from pigrocrm.core.config import Settings
from pigrocrm.core.errors import Conflict, NotFound, ValidationFailed
from pigrocrm.core.gmail.models import GoogleAccount
from pigrocrm.core.mailboxes.credential import MAILBOX_ENTITY, seal_password
from pigrocrm.core.mailboxes.imap_client import (
    ImapFailure,
    ImapReader,
    ImapTarget,
    Resolver,
    validate_target,
)
from pigrocrm.core.mailboxes.models import Mailbox
from pigrocrm.core.mailboxes.repository import MailboxRepository
from pigrocrm.core.mailboxes.schemas import ImapMailboxCreate, MailboxRead
from pigrocrm.core.reminders.push import resolve_public

_ALREADY = "questa casella è già collegata: se non legge, aggiorna la password"


class ImapMailboxService:
    def __init__(
        self,
        session: Session,
        *,
        settings: Settings,
        resolve: Resolver = resolve_public,
        context: ssl.SSLContext | None = None,
    ) -> None:
        self.session = session
        self.settings = settings
        self.repo = MailboxRepository(session)
        self.activities = ActivityService(session)
        self._resolve = resolve
        self._context = context

    # --- reading ----------------------------------------------------------------------

    def list(self, actor: Actor) -> list[MailboxRead]:
        """The caller's own mailboxes and, for an administrator, the space's."""
        if actor.id is None:
            return []
        return [
            self._read(mailbox)
            for mailbox in self.repo.visible_to(actor.id, administers=actor.can_administer)
        ]

    # --- writing ----------------------------------------------------------------------

    def connect(self, actor: Actor, data: ImapMailboxCreate) -> MailboxRead:
        actor.require_person("connect_mailbox")
        self._require_role(actor, space_owned=data.space_owned, action="connect_mailbox")
        if actor.id is None and not data.space_owned:
            raise Conflict(MAILBOX_ENTITY, "solo un utente può collegare la propria casella")
        if not data.space_owned and "billing" in data.purposes:
            raise ValidationFailed(
                MAILBOX_ENTITY, "purposes", "billing is allowed on a space-owned mailbox only"
            )
        try:
            validate_target(data.host, data.port, data.security)
        except ValueError as exc:
            raise ValidationFailed(MAILBOX_ENTITY, "host", str(exc)) from exc
        owner = None if data.space_owned else actor.id
        if self.repo.imap_address_taken(owner, data.address):
            raise Conflict(MAILBOX_ENTITY, _ALREADY)
        # The key first: no point reaching a server when the password could not be kept.
        ciphertext, nonce = seal_password(data.password, self.settings)
        self._probe(data.host, data.port, data.security, data.username, data.password)

        mailbox = Mailbox(
            owner_user_id=owner,
            provider="imap",
            status="active",
            address=data.address.strip(),
            imap_host=data.host.lower(),
            imap_port=data.port,
            imap_security=data.security,
            imap_username=data.username,
            password_ciphertext=ciphertext,
            password_nonce=nonce,
        )
        self.session.add(mailbox)
        try:
            self.session.flush()
        except IntegrityError:
            # Two connects of one address at once: the check above passed for both. Said in
            # words, and the statement (which holds the sealed password) goes nowhere.
            self.session.rollback()
            raise Conflict(MAILBOX_ENTITY, _ALREADY) from None
        for purpose in dict.fromkeys(data.purposes):
            self.repo.add_purpose(mailbox, purpose)
        # Who connected it, with what it reads and how; never the password or its ciphertext.
        self.activities.record(
            MAILBOX_ENTITY,
            mailbox.id,
            "mailbox.connected",
            actor,
            {
                "address": mailbox.address,
                "host": mailbox.imap_host,
                "security": mailbox.imap_security,
                "purposes": list(dict.fromkeys(data.purposes)),
                "space_owned": mailbox.space_owned,
            },
        )
        self.session.commit()
        return self._read(mailbox)

    def change_password(self, actor: Actor, mailbox_id: UUID, password: str) -> MailboxRead:
        """A new password, tried before it is kept: it is how a mailbox leaves `auth_failed`, and
        how a disconnected one is connected again."""
        actor.require_person("change_mailbox_password")
        mailbox = self._manageable(actor, mailbox_id, "change_mailbox_password")
        ciphertext, nonce = seal_password(password, self.settings)
        assert mailbox.imap_host and mailbox.imap_port and mailbox.imap_security
        assert mailbox.imap_username
        self._probe(
            mailbox.imap_host,
            mailbox.imap_port,
            mailbox.imap_security,
            mailbox.imap_username,
            password,
        )
        mailbox.password_ciphertext = ciphertext
        mailbox.password_nonce = nonce
        mailbox.status = "active"
        mailbox.last_error = None
        mailbox.last_error_at = None
        mailbox.failing_since = None
        mailbox.disconnected_at = None
        self.activities.record(
            MAILBOX_ENTITY,
            mailbox.id,
            "mailbox.password_changed",
            actor,
            {"address": mailbox.address},
        )
        self.session.commit()
        return self._read(mailbox)

    def disconnect(self, actor: Actor, mailbox_id: UUID) -> MailboxRead:
        """Stops reading and forgets the password. What was received stays: the items of a space
        mailbox belong to the space."""
        actor.require_person("disconnect_mailbox")
        mailbox = self._manageable(actor, mailbox_id, "disconnect_mailbox")
        mailbox.status = "disconnected"
        mailbox.password_ciphertext = None
        mailbox.password_nonce = None
        mailbox.disconnected_at = datetime.now(UTC)
        mailbox.failing_since = None
        self.activities.record(
            MAILBOX_ENTITY, mailbox.id, "mailbox.disconnected", actor, {"address": mailbox.address}
        )
        self.session.commit()
        return self._read(mailbox)

    # --- internals --------------------------------------------------------------------

    def _require_role(self, actor: Actor, *, space_owned: bool, action: str) -> None:
        if space_owned:
            actor.require_unscoped_admin(action)
        else:
            actor.require_write(action)

    def _manageable(self, actor: Actor, mailbox_id: UUID, action: str) -> Mailbox:
        # The role first, before any lookup: a person who cannot write learns nothing, not even
        # whether the mailbox exists. Managing a mailbox is writing, whoever owns it.
        actor.require_write(action)
        mailbox = self.session.get(Mailbox, mailbox_id)
        if mailbox is None or mailbox.provider != "imap":
            raise NotFound(MAILBOX_ENTITY, mailbox_id)
        if mailbox.space_owned:
            actor.require_unscoped_admin(action)
        elif mailbox.owner_user_id != actor.id:
            raise NotFound(MAILBOX_ENTITY, mailbox_id)
        return mailbox

    def _probe(self, host: str, port: int, security: str, username: str, password: str) -> None:
        """Logs in once and reads nothing: what the server says decides, and a refusal is worded
        for the person (`ImapFailure.reason`), never with the server's own text."""
        target = ImapTarget(host.lower(), port, security, username, password)
        try:
            with ImapReader(target, resolve=self._resolve, context=self._context) as reader:
                reader.select_inbox()
        except ImapFailure as failure:
            field = "password" if failure.kind == "auth" else "host"
            raise ValidationFailed(MAILBOX_ENTITY, field, failure.reason) from failure

    def _read(self, mailbox: Mailbox) -> MailboxRead:
        address = mailbox.address
        if mailbox.google_account_id is not None:
            address = self.session.scalar(
                select(GoogleAccount.email_address).where(
                    GoogleAccount.id == mailbox.google_account_id
                )
            )
        return MailboxRead(
            id=mailbox.id,
            provider=mailbox.provider,  # type: ignore[arg-type]
            address=address,
            owner_user_id=mailbox.owner_user_id,
            space_owned=mailbox.space_owned,
            purposes=[p.purpose for p in self.repo.purposes(mailbox)],  # type: ignore[misc]
            health=self.repo.health(mailbox),
            last_error=mailbox.last_error,
            last_error_at=mailbox.last_error_at,
            failing_since=mailbox.failing_since,
            host=mailbox.imap_host,
            port=mailbox.imap_port,
            security=mailbox.imap_security,  # type: ignore[arg-type]
            username=mailbox.imap_username,
        )
