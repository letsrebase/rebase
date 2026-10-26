"""The channels a reminder travels by besides the app (spec 2026-10-26 §6, §8.3).

The in-app list is the source of truth and every other channel is a copy of it, so a
channel here never decides what is a reminder or whether a person has dealt with it: the
engine does (`ReminderEngine.open_reminders`), and a channel is handed what is left, one
`Candidate` per reminder and person, with everything the spec says it must honour already
resolved: the person's effective settings, and whether *now* is inside their quiet hours.

**Three steps, in the digest's order** (reads, then the network, then the ledger):

1. `ChannelDispatcher.prepare` reads, once, everything a pass needs (the people, the
   settings, the ledger) and asks each channel to `prepare` its sends from plain values.
2. The caller ends the read transaction, then calls each `Prepared.send`, which touches the
   network and never the session. A channel's `send` **never raises**: a failure is a
   message left out of its `ChannelResult.accepted`.
3. The caller writes what was accepted in the transaction of the pass
   (`ReminderEngine.record_pass`): a delivery row **after** the channel took the message,
   so a failed send writes nothing and the next pass finds the reminder undelivered
   (§8.3), and the summary marker, and the push subscriptions the service reported gone.

**A channel plugs in by implementing `Channel`** and being listed in `channels_for`
(`channels.py`). `identity` is the ledger's key: `(reminder key, user, channel, mode,
period)`; a channel covers an identity with `ReminderLedger.covered`, the one rule «a
delivery covers its rank and below».
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from pigrocrm.core.config import Settings
from pigrocrm.core.reminders.catalogue import SEVERITY_RANK, Severity
from pigrocrm.core.reminders.detect import Reminder
from pigrocrm.core.reminders.directory import Directory, Person
from pigrocrm.core.reminders.ledger import DeliveryIdentity, DeliveryRecord, ReminderLedger
from pigrocrm.core.reminders.preferences import (
    EffectiveGeneral,
    EffectiveSettings,
    ReminderSettings,
)

logger = logging.getLogger(__name__)

IN_APP = "in_app"


@dataclass(frozen=True)
class Candidate:
    """One reminder for one person, as a channel sees it: still open for them (neither
    acknowledged nor snoozed at this severity), with their effective settings and the
    verdict on quiet hours. The channel decides whether it is its to send."""

    reminder: Reminder
    person: Person
    effective: EffectiveSettings
    general: EffectiveGeneral
    quiet: bool
    # When the reminder reached the person's list: the pass itself for one that is new or
    # has climbed, the earlier delivery's time otherwise. A channel a person turned on
    # later (a push subscription) announces what reached the list after that, never the
    # backlog.
    listed_at: datetime

    def identity(self, channel: str, mode: str = "immediate") -> DeliveryIdentity:
        reminder = self.reminder
        return (reminder.key, reminder.user_id, channel, mode, reminder.period)


@dataclass(frozen=True)
class PassContext:
    """What every channel of one pass shares."""

    settings: Settings
    today: date
    now: datetime
    # The base of every link: the space's public URL, with no trailing slash, or empty.
    public_url: str
    # Every ledger row of the open reminders' keys, of every channel.
    delivered: Mapping[DeliveryIdentity, DeliveryRecord]
    people: Mapping[UUID, Person]
    preferences: ReminderSettings
    # The registry's name of the space this pass runs for, `None` for the root installation:
    # a channel whose credentials are per space (Slack's bot token, sealed under a key
    # derived from it) derives the space's own settings from `settings` and this.
    slug: str | None = None

    @property
    def local_now(self) -> datetime:
        return self.now.astimezone(ZoneInfo(self.settings.timezone))

    def covered(self, identity: DeliveryIdentity, severity: Severity) -> bool:
        record = self.delivered.get(identity)
        return record is not None and record.rank >= SEVERITY_RANK[severity]


@dataclass(frozen=True)
class Accepted:
    """A delivery a channel took, to be written to the ledger."""

    identity: DeliveryIdentity
    severity: Severity


@dataclass
class ChannelResult:
    """What the sends of one or more channels did. `sent` counts messages the provider
    accepted (a mail carrying five reminders is one, a push per reminder is one each),
    by channel."""

    accepted: list[Accepted] = field(default_factory=list)
    # `(user, day)` of the daily summaries that went.
    summaries: list[tuple[UUID, date]] = field(default_factory=list)
    # Push subscriptions the push service reported gone: deleted with the ledger writes.
    gone_endpoints: list[str] = field(default_factory=list)
    sent: dict[str, int] = field(default_factory=dict)
    # Writes a channel needs in the transaction of the pass, for what the ledger's rows do
    # not say: Slack's post to the space channel (a delivery with no user), a match found
    # while sending, a token Slack revoked. Each takes the session and does not commit.
    writes: list[Callable[[Session], None]] = field(default_factory=list)

    def merge(self, other: ChannelResult) -> None:
        self.accepted.extend(other.accepted)
        self.summaries.extend(other.summaries)
        self.gone_endpoints.extend(other.gone_endpoints)
        self.writes.extend(other.writes)
        for channel, count in other.sent.items():
            self.sent[channel] = self.sent.get(channel, 0) + count


@dataclass(frozen=True)
class Prepared:
    """One channel's sends, planned from plain values. `planned` is what a rehearsal
    reports; `send` is called only on a real pass, with no transaction open."""

    channel: str
    planned: int
    send: Callable[[], ChannelResult]


class Channel:
    """A way a reminder reaches a person besides the app."""

    name: str

    def prepare(
        self, session: Session, context: PassContext, candidates: Sequence[Candidate]
    ) -> Prepared:
        """Reads what the channel needs (a push subscription, a Slack match) and plans its
        sends. Writes nothing."""
        raise NotImplementedError


class ChannelDispatcher:
    def __init__(self, channels: Sequence[Channel]) -> None:
        self.channels = tuple(channels)

    def prepare(
        self,
        session: Session,
        settings: Settings,
        open_reminders: Sequence[Reminder],
        *,
        newly_listed: set[DeliveryIdentity],
        today: date,
        now: datetime,
        public_url: str = "",
        slug: str | None = None,
    ) -> list[Prepared]:
        """Plans every channel's sends for the reminders the engine left open.

        `newly_listed` is the in-app identities this pass is about to write, which is how
        a candidate's `listed_at` is `now` for a reminder that is new or has climbed.
        """
        if not self.channels:
            return []
        preferences = ReminderSettings.load(session)
        directory = Directory.load(session)
        delivered = ReminderLedger(session).deliveries({r.key for r in open_reminders})
        context = PassContext(
            settings=settings,
            today=today,
            now=now,
            public_url=public_url.rstrip("/"),
            delivered=delivered,
            people=directory.people,
            preferences=preferences,
            slug=slug,
        )
        local = context.local_now
        candidates: list[Candidate] = []
        for reminder in open_reminders:
            person = directory.people.get(reminder.user_id)
            if person is None:
                continue
            resolved = preferences.for_user(reminder.user_id)
            general = resolved.general()
            in_app = (reminder.key, reminder.user_id, IN_APP, "immediate", reminder.period)
            record = delivered.get(in_app)
            listed_at = now if in_app in newly_listed or record is None else record.at
            candidates.append(
                Candidate(
                    reminder=reminder,
                    person=person,
                    effective=resolved.type(reminder.type),
                    general=general,
                    quiet=general.quiet_at(local.weekday(), local.time()),
                    listed_at=listed_at,
                )
            )
        return [channel.prepare(session, context, candidates) for channel in self.channels]

    @staticmethod
    def send(prepared: Sequence[Prepared]) -> ChannelResult:
        """Runs every channel's sends and gathers what they accepted. A channel's own `send`
        never raises, and the guard here is for the one that does anyway (a bug, a row
        nobody foresaw): its messages are left unrecorded, and so retried, and the others
        and the pass's own writes go on. The type is logged and never the message, which
        may quote an address."""
        result = ChannelResult()
        for plan in prepared:
            try:
                result.merge(plan.send())
            except Exception as exc:  # noqa: BLE001 - one channel must not cost the pass
                logger.error("canale %s: invio fallito (%s)", plan.channel, type(exc).__name__)
        return result
