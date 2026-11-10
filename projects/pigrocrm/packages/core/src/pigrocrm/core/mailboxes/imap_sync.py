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
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final, Protocol
from uuid import UUID

from sqlalchemy import Connection, Engine, select, text
from sqlalchemy.orm import Session

from pigrocrm.core.config import Settings
from pigrocrm.core.errors import Conflict, NotFound
from pigrocrm.core.mailboxes.credential import MAILBOX_ENTITY, open_password
from pigrocrm.core.mailboxes.health import MailboxHealth
from pigrocrm.core.mailboxes.imap_client import (
    BodyPart,
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
# How many messages a sink is handed, and committed, at a time.
SINK_CHUNK: Final = 5


PartFetcher = Callable[[MessageHead, BodyPart], bytes]
"""Hands over the decoded bytes of one part of one message: the only way a sink gets a body, and
only the part it names. `PartUnreadable` for a part that does not decode."""


class MessageSink(Protocol):
    """What reads a purpose's heads. `purpose` is the one it serves; `accept` is idempotent and
    joins the caller's transaction (it never commits: the cycle holds a lock to its end).
    `discard` undoes what `accept` put outside the database when the cycle is rolled back."""

    purpose: str

    def accept(
        self, mailbox: Mailbox, heads: Sequence[MessageHead], fetch: PartFetcher
    ) -> None: ...

    def committed(self) -> None:
        """The chunk handed to `accept` is committed: what `discard` would undo is now kept."""
        ...

    def discard(self) -> None: ...


@dataclass(frozen=True)
class ImapReport:
    """Counters and a state, never an address or a subject: it is printed in a log."""

    health: MailboxHealth
    heads_read: int = 0
    already_running: bool = False
    # Set when the mailbox was not tried: a stopped mailbox is reported, not retried.
    skipped: str | None = None


class _MailboxLock:
    """A session-level advisory lock for one mailbox, taken on a connection of its own so that
    committing the cycle's session does not release it (and a pooled connection never holds it
    after: `release` unlocks before the connection goes back). Where the session is bound to a
    single connection (a test's) that connection is used."""

    def __init__(self, session: Session, mailbox_id: UUID) -> None:
        bind = session.get_bind()
        self._own: Connection | None = bind.connect() if isinstance(bind, Engine) else None
        self._conn: Connection = self._own if self._own is not None else bind  # type: ignore[assignment]
        self._key = f"imap-mailbox:{mailbox_id}"
        self._held = False

    def acquire(self) -> bool:
        got = self._conn.execute(
            text("SELECT pg_try_advisory_lock(hashtextextended(:key, 0))"), {"key": self._key}
        ).scalar()
        self._held = bool(got)
        if self._own is not None:
            self._own.commit()
            if not self._held:
                self._own.close()
        return self._held

    def release(self) -> None:
        if not self._held:
            return
        self._conn.execute(
            text("SELECT pg_advisory_unlock(hashtextextended(:key, 0))"), {"key": self._key}
        )
        if self._own is not None:
            self._own.commit()
            self._own.close()


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
        # spend the server's login. A lock of the connection it is taken on, not of a transaction:
        # a billing cycle commits after each few messages, and the lock must outlast them.
        lock = _MailboxLock(self.session, mailbox.id)
        if not lock.acquire():
            return ImapReport(self._health(mailbox), already_running=True)
        try:
            return self._cycle(mailbox, moment)
        finally:
            lock.release()

    def _cycle(self, mailbox: Mailbox, moment: datetime) -> ImapReport:
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
            mailbox.status = "active"
            mailbox.last_error = None
            mailbox.last_error_at = None
            mailbox.failing_since = None
            self.session.commit()
        except ImapFailure as failure:
            # What the chunk in hand wrote goes (the sinks fetch before they write, so it is
            # usually nothing); what earlier chunks committed stays, with its watermark.
            self._undo()
            self._fail(mailbox, failure, moment)
            self.session.commit()
            return ImapReport(self._health(mailbox))
        except BaseException:
            # Not a verdict on the mailbox: a sink or the database broke.
            self._undo()
            raise
        return ImapReport("active", heads_read=heads_read)

    def _undo(self) -> None:
        self.session.rollback()
        for sink in self.sinks.values():
            sink.discard()

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
        # Oldest first, so the watermark only ever moves forward and a failure in a later chunk
        # leaves nothing older than it behind. A few messages at a time, each chunk its own
        # transaction: the sink's locks are held for a chunk and not for the cycle.
        kept.sort(key=lambda head: (head.internal_date, head.uid))
        sink = self.sinks[row.purpose]

        def fetch(head: MessageHead, part: BodyPart) -> bytes:
            return reader.fetch_part(head.uid, part)

        for start in range(0, len(kept), SINK_CHUNK):
            chunk = kept[start : start + SINK_CHUNK]
            sink.accept(mailbox, chunk, fetch)
            newest = max(head.internal_date for head in chunk)
            row.sync_watermark = max(row.sync_watermark or newest, newest)
            self.session.commit()
            sink.committed()
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
