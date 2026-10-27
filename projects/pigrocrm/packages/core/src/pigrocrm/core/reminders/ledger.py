"""The delivery ledger and the summary marker (spec 2026-10-26 §8.3); the job clock is `jobs.py`.

**A delivery row is written after the channel accepted the message.** A failed send
writes nothing, so the next pass finds the reminder undelivered and tries again, as
Mastro did. The unique key makes a duplicate *row* impossible, not a duplicate message:
the order is send, then record, as the digest's (`digest/run.py`, «the concurrency window
this leaves open»), and what closes that window is the per-space lock of the pass. The
failure that can remain is a duplicate message after a crash between the send and the
write, never a missing one.

**The rank is not part of the key**, so an escalation is an upsert and not a second row:
`record` keeps the highest rank delivered (`GREATEST`) and the new delivery time. A
delivery covers its rank and below, so a reminder delivered at `serious` is not sent
again at `serious`, is sent again when it climbs to `critical`, and a threshold edit that
lowers a severity does not resend. `attivita_overdue` is delivered once a day, through
`period`; every other type once per rank.

**A cleared cause ends the occurrence** (`forget_unraised`): at the end of a pass, for
every type that applied and ran, the acknowledge, snooze and delivery rows of every key
the pass did not raise are deleted, so a connection that recovers and fails again, or an
invoice reopened after a payment is undone, is a new occurrence that nothing earlier
suppresses. A type that did not run leaves its rows alone, so a switch turned back on
resumes where it was.
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from datetime import UTC, date, datetime
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from pigrocrm.core.reminders.catalogue import SEVERITY_RANK, Severity, type_of_key
from pigrocrm.core.reminders.models import (
    ReminderAck,
    ReminderDelivery,
    ReminderSnooze,
    ReminderSummaryRun,
)
from pigrocrm.core.slack.models import SlackChannelPost

# What identifies one delivery: `(key, user, channel, mode, period)`.
DeliveryIdentity = tuple[str, UUID, str, str, date | None]


@dataclass(frozen=True)
class DeliveryRecord:
    """What the ledger holds for one identity: the highest rank delivered and when."""

    rank: int
    at: datetime


class ReminderLedger:
    def __init__(self, session: Session) -> None:
        self.session = session

    def deliveries(self, keys: Collection[str]) -> dict[DeliveryIdentity, DeliveryRecord]:
        """The rank delivered and the time, for each identity of these keys, read once
        per pass."""
        if not keys:
            return {}
        rows = self.session.scalars(select(ReminderDelivery).where(ReminderDelivery.key.in_(keys)))
        return {
            (row.key, row.user_id, row.channel, row.mode, row.period): DeliveryRecord(
                row.severity_rank, row.delivered_at
            )
            for row in rows
        }

    def delivered(self, keys: Collection[str]) -> dict[DeliveryIdentity, int]:
        """The rank delivered for each identity of these keys, read once per pass."""
        return {identity: record.rank for identity, record in self.deliveries(keys).items()}

    @staticmethod
    def covered(
        delivered: dict[DeliveryIdentity, int], identity: DeliveryIdentity, severity: Severity
    ) -> bool:
        """Whether a delivery at this rank or above already exists for the identity."""
        rank = delivered.get(identity)
        return rank is not None and rank >= SEVERITY_RANK[severity]

    def record(
        self,
        identity: DeliveryIdentity,
        severity: Severity,
        *,
        at: datetime | None = None,
    ) -> None:
        """Writes that a channel accepted this reminder. An upsert that keeps the highest
        rank delivered, so an escalation never needs a second row and a delivery written
        at a lower rank than the one on record (a threshold edit) lowers nothing. Does not
        commit: the caller commits the ledger writes of one pass together."""
        key, user_id, channel, mode, period = identity
        moment = at or datetime.now(UTC)
        statement = insert(ReminderDelivery).values(
            key=key,
            type=type_of_key(key),
            user_id=user_id,
            channel=channel,
            mode=mode,
            period=period,
            severity_rank=SEVERITY_RANK[severity],
            delivered_at=moment,
        )
        self.session.execute(
            statement.on_conflict_do_update(
                constraint="uq_reminder_delivery_occurrence",
                set_={
                    "severity_rank": func.greatest(
                        ReminderDelivery.severity_rank, statement.excluded.severity_rank
                    ),
                    "delivered_at": statement.excluded.delivered_at,
                    "updated_at": func.now(),
                },
            )
        )

    def forget_unraised(
        self, ran: Collection[str], raised: Collection[str], *, before: datetime | None = None
    ) -> int:
        """Deletes the acknowledge, snooze and delivery rows (and Slack's channel posts) of
        every key of the types that ran this pass which the pass did not raise; the number
        of rows deleted.

        «Raised» is by key and for anybody: a key raised for one person keeps everybody's
        rows. The rows of a type that did not run are not touched. A row written after
        `before` (when the evaluation was made) is not this evaluation's to end: the pass
        sends between its reads and its writes, and a person who acknowledges a reminder
        meanwhile has done it about a cause the evaluation never saw.
        """
        if not ran:
            return 0
        deleted = 0
        for model in (ReminderAck, ReminderSnooze, ReminderDelivery):
            statement = delete(model).where(model.type.in_(list(ran)))
            if raised:
                statement = statement.where(model.key.not_in(list(raised)))
            if before is not None:
                statement = statement.where(model.updated_at <= before)
            # `RETURNING` and not `rowcount`, the way `gmail/repository.py` counts: the
            # DBAPI's row count is not typed on a generic result.
            deleted += len(self.session.execute(statement.returning(model.id)).all())
        # The posts Slack's channel took end with the occurrence too (`SlackChannelPost`),
        # so a cause that clears and returns is posted again. Not counted: they are
        # not a person's rows.
        posts = delete(SlackChannelPost).where(SlackChannelPost.type.in_(list(ran)))
        if raised:
            posts = posts.where(SlackChannelPost.key.not_in(list(raised)))
        self.session.execute(posts)
        return deleted

    # -- the daily summary -------------------------------------------------------

    def summary_sent(self, user_id: UUID, day: date) -> bool:
        return (
            self.session.scalar(
                select(ReminderSummaryRun.id).where(
                    ReminderSummaryRun.user_id == user_id, ReminderSummaryRun.day == day
                )
            )
            is not None
        )

    def summaries_sent(self, day: date) -> set[UUID]:
        """Every user whose summary for `day` already went, read once per pass."""
        return set(
            self.session.scalars(
                select(ReminderSummaryRun.user_id).where(ReminderSummaryRun.day == day)
            )
        )

    def mark_summary_sent(self, user_id: UUID, day: date) -> None:
        """That the user's summary for `day` went, so the pass does not send it twice. Does
        not commit."""
        self.session.execute(
            insert(ReminderSummaryRun)
            .values(user_id=user_id, day=day)
            .on_conflict_do_nothing(constraint="uq_reminder_summary_run_user_day")
        )
