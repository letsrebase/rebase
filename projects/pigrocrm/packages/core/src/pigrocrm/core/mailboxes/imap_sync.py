"""One read of one IMAP mailbox, and what it does to the mailbox's health (REB-1015, § 2.2).

A cycle logs in, opens INBOX read-only and, for each purpose that has a sink, reads the headers
and BODYSTRUCTURE of what arrived since that purpose's own watermark (90 days on a first
connection) and hands them to the sink. It never fetches a body, never sets a flag and never
deletes. A purpose with no sink is not read at all, and its watermark stays where it is, so the
sink that arrives later (`billing`, REB-1016) starts from the mailbox's first day and not from
the day it was written. What a sink does with a head is its own: it must be idempotent, because
the boundary message is handed over again until a newer one arrives (the read is `>=` the
watermark, so that two messages in one second cannot be split across two cycles).

**Health.** A cycle that reads makes the mailbox `active` and clears its error. A transient
failure (`ImapFailure.kind == "transient"`) makes it `failing` and is retried by the next cycle;
`failing_since` is when that run began. A refused password makes it `auth_failed`, and an
`auth_failed` mailbox is **not tried again** until a person types a new password
(`ImapMailboxService.change_password`): trying a password the server refused is how an account is
locked. The sentence stored is Italian, from `ImapFailure.reason`, and holds nothing the server
said.
"""

import ssl
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final, Protocol
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from pigrocrm.core.config import Settings
from pigrocrm.core.errors import Conflict, NotFound
from pigrocrm.core.mailboxes.credential import MAILBOX_ENTITY, open_password
from pigrocrm.core.mailboxes.health import MailboxHealth
from pigrocrm.core.mailboxes.imap_client import (
    ImapFailure,
    ImapReader,
    ImapTarget,
    MessageHead,
    Resolver,
)
from pigrocrm.core.mailboxes.models import Mailbox, MailboxPurpose
from pigrocrm.core.reminders.push import resolve_public

BACKFILL_DAYS: Final = 90
# What one cycle hands over per purpose. The rest waits for the next one, from the new watermark.
MAX_HEADS_PER_CYCLE: Final = 200


class MessageSink(Protocol):
    """What reads a purpose's heads. `purpose` is the one it serves; `accept` is idempotent."""

    purpose: str

    def accept(self, mailbox: Mailbox, heads: Sequence[MessageHead]) -> None: ...


@dataclass(frozen=True)
class ImapReport:
    """Counters and a state, never an address or a subject: it is printed in a log."""

    health: MailboxHealth
    heads_read: int = 0
    already_running: bool = False
    # Set when the mailbox was not tried: a stopped mailbox is reported, not retried.
    skipped: str | None = None


class ImapSyncService:
    def __init__(
        self,
        session: Session,
        *,
        settings: Settings,
        sinks: Sequence[MessageSink] = (),
        resolve: Resolver = resolve_public,
        context: ssl.SSLContext | None = None,
    ) -> None:
        self.session = session
        self.settings = settings
        self.sinks = {sink.purpose: sink for sink in sinks}
        self._resolve = resolve
        self._context = context

    def sync(self, mailbox_id: UUID, *, now: datetime | None = None) -> ImapReport:
        moment = now or datetime.now(UTC)
        mailbox = self.session.get(Mailbox, mailbox_id)
        if mailbox is None or mailbox.provider != "imap" or mailbox.status == "disconnected":
            raise NotFound(MAILBOX_ENTITY, mailbox_id)
        if mailbox.status == "auth_failed":
            return ImapReport("auth_failed", skipped="the password was refused: a person must act")
        # One cycle per mailbox at a time: a cron and a person pressing a button must not both
        # spend the server's login. Held to the end of the transaction.
        if not self.session.scalar(
            text("SELECT pg_try_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": f"imap-mailbox:{mailbox.id}"},
        ):
            return ImapReport(self._health(mailbox), already_running=True)

        try:
            password = open_password(mailbox, self.settings)
        except Conflict:
            # A rotated or missing key: the mailbox is not healthy, and only a person typing the
            # password again, under the key as it is now, brings it back.
            self._fail(
                mailbox,
                ImapFailure(
                    "auth",
                    "la password memorizzata non si legge più: inserisci di nuovo la password",
                ),
                moment,
            )
            self.session.commit()
            return ImapReport("auth_failed")
        assert mailbox.imap_host and mailbox.imap_port and mailbox.imap_security
        assert mailbox.imap_username
        target = ImapTarget(
            mailbox.imap_host,
            mailbox.imap_port,
            mailbox.imap_security,
            mailbox.imap_username,
            password,
        )
        purposes = [
            row
            for row in self.session.scalars(
                select(MailboxPurpose).where(MailboxPurpose.mailbox_id == mailbox.id)
            )
            if row.purpose in self.sinks
        ]
        heads_read = 0
        try:
            with ImapReader(target, resolve=self._resolve, context=self._context) as reader:
                reader.select_inbox()
                for row in purposes:
                    heads_read += self._read_purpose(reader, mailbox, row, moment)
        except ImapFailure as failure:
            self._fail(mailbox, failure, moment)
            self.session.commit()
            return ImapReport(self._health(mailbox))
        mailbox.status = "active"
        mailbox.last_error = None
        mailbox.last_error_at = None
        mailbox.failing_since = None
        self.session.commit()
        return ImapReport("active", heads_read=heads_read)

    def _read_purpose(
        self, reader: ImapReader, mailbox: Mailbox, row: MailboxPurpose, moment: datetime
    ) -> int:
        since = row.sync_watermark or moment - timedelta(days=BACKFILL_DAYS)
        kept: list[MessageHead] = []
        for head in reader.heads(reader.uids_since(since)):
            if head.internal_date >= since:
                kept.append(head)
            if len(kept) >= MAX_HEADS_PER_CYCLE:
                break
        if kept:
            self.sinks[row.purpose].accept(mailbox, kept)
            row.sync_watermark = max(head.internal_date for head in kept)
        row.last_sync_at = moment
        return len(kept)

    def _fail(self, mailbox: Mailbox, failure: ImapFailure, moment: datetime) -> None:
        mailbox.last_error = failure.reason
        mailbox.last_error_at = moment
        if failure.kind == "auth":
            mailbox.status = "auth_failed"
            mailbox.failing_since = None
        else:
            mailbox.status = "failing"
            mailbox.failing_since = mailbox.failing_since or moment

    @staticmethod
    def _health(mailbox: Mailbox) -> MailboxHealth:
        assert mailbox.status is not None
        return mailbox.status  # type: ignore[return-value]
