"""The bookkeeping around a reminder, never the reminder (spec 2026-10-26 §8).

A reminder is **derived**: the detectors recompute it on every pass from the data, so a
cleared cause makes it disappear with no job to clean up after it. What is stored is only
what a person or a channel did about one, keyed by `type:subject-id`. Everything here
lives in the space's own database and carries the usual audit columns.

**Acknowledge, snooze and delivery are per user.** The reminder is one object and one
person acknowledging it does not hide it from the other (the reverse of Mastro, which
kept one `acknowledgedBy` per key). `type` is repeated on every keyed row, redundant with
the key's prefix, because the end of a pass deletes the rows of every key a type no
longer raises and must be able to say which type it is talking about in one indexed
predicate.
"""

from datetime import UTC, date, datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    SmallInteger,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from pigrocrm.core.db import Base, PrimaryKeyMixin, TimestampMixin

KEY_MAX_LENGTH = 100
TYPE_MAX_LENGTH = 40


class ReminderPreference(Base, PrimaryKeyMixin, TimestampMixin):
    """One layer of settings: the space's defaults (`user_id` null) or one person's
    overrides, for one type, or for the settings that belong to no type (`type` null:
    quiet hours and the summary hour, spec §5). A null column is «inherit», which is what
    lets the way back to the default be a delete of the row, or of one column, and not a
    copy of the current default that a later change would never reach (spec §7)."""

    __tablename__ = "reminder_preference"
    __table_args__ = (
        # `NULLS NOT DISTINCT` (PostgreSQL 15), because the space's row has a null
        # `user_id` and the type-less row a null `type`, and a plain unique constraint
        # accepts two of either.
        UniqueConstraint(
            "user_id",
            "type",
            name="uq_reminder_preference_layer",
            postgresql_nulls_not_distinct=True,
        ),
        CheckConstraint(
            "urgent_from IS NULL OR urgent_from IN ('warning', 'serious', 'critical')",
            name="ck_reminder_preference_urgent_from",
        ),
        CheckConstraint(
            "email_mode IS NULL OR email_mode IN ('immediate', 'summary')",
            name="ck_reminder_preference_email_mode",
        ),
        CheckConstraint(
            "summary_hour IS NULL OR summary_hour BETWEEN 0 AND 23",
            name="ck_reminder_preference_summary_hour",
        ),
        # A row is about one type or about none, never both: the per-type settings and the
        # two that are not per type cannot sit in the same row.
        CheckConstraint(
            "(type IS NULL AND enabled IS NULL AND channels IS NULL AND ladder IS NULL "
            "AND urgent_from IS NULL AND email_mode IS NULL) "
            "OR (type IS NOT NULL AND quiet_hours IS NULL AND summary_hour IS NULL)",
            name="ck_reminder_preference_scope_of_settings",
        ),
    )

    user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"), default=None, index=True)
    type: Mapped[str | None] = mapped_column(String(TYPE_MAX_LENGTH), default=None)
    enabled: Mapped[bool | None] = mapped_column(default=None)
    # The switchable channels this type travels by, in-app excluded (always on).
    channels: Mapped[list[str] | None] = mapped_column(JSONB, default=None)
    # `{severity: number}`, the whole ladder when set (`catalogue.validate_ladder`).
    ladder: Mapped[dict[str, int] | None] = mapped_column(JSONB, default=None)
    urgent_from: Mapped[str | None] = mapped_column(String(10), default=None)
    email_mode: Mapped[str | None] = mapped_column(String(10), default=None)
    # `{"enabled": bool, "start": "20:00", "end": "08:00", "weekend": bool}`.
    quiet_hours: Mapped[dict[str, object] | None] = mapped_column(JSONB, default=None)
    summary_hour: Mapped[int | None] = mapped_column(SmallInteger, default=None)


class ReminderAck(Base, PrimaryKeyMixin, TimestampMixin):
    """A reminder one user has seen, at the severity it had. A later detection at a
    strictly higher rank is not covered and reappears (spec §8.2)."""

    __tablename__ = "reminder_ack"
    __table_args__ = (
        UniqueConstraint("key", "user_id", name="uq_reminder_ack_key_user"),
        CheckConstraint("severity_rank BETWEEN 1 AND 3", name="ck_reminder_ack_rank"),
        Index("ix_reminder_ack_type", "type"),
    )

    key: Mapped[str] = mapped_column(String(KEY_MAX_LENGTH), nullable=False)
    type: Mapped[str] = mapped_column(String(TYPE_MAX_LENGTH), nullable=False)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    severity_rank: Mapped[int] = mapped_column(SmallInteger, nullable=False)


class ReminderSnooze(Base, PrimaryKeyMixin, TimestampMixin):
    """The same with a date: covered until that calendar day in the installation's zone,
    compared as dates and never as instants (spec §8.2)."""

    __tablename__ = "reminder_snooze"
    __table_args__ = (
        UniqueConstraint("key", "user_id", name="uq_reminder_snooze_key_user"),
        CheckConstraint("severity_rank BETWEEN 1 AND 3", name="ck_reminder_snooze_rank"),
        Index("ix_reminder_snooze_type", "type"),
    )

    key: Mapped[str] = mapped_column(String(KEY_MAX_LENGTH), nullable=False)
    type: Mapped[str] = mapped_column(String(TYPE_MAX_LENGTH), nullable=False)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    severity_rank: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    until: Mapped[date] = mapped_column(Date, nullable=False)


class ReminderDelivery(Base, PrimaryKeyMixin, TimestampMixin):
    """What a channel accepted, written **after** it accepted it (spec §8.3): a failed send
    writes nothing, so the next pass finds the reminder undelivered and tries again.

    The rank is not part of the key, so an escalation is an upsert and not a second row
    (`ReminderLedger.record`), and a delivery covers its rank and below. `period` is null
    except for the types that repeat daily, where it is that day. The unique constraint is
    `NULLS NOT DISTINCT` for the reason `ReminderPreference`'s is."""

    __tablename__ = "reminder_delivery"
    __table_args__ = (
        UniqueConstraint(
            "key",
            "user_id",
            "channel",
            "mode",
            "period",
            name="uq_reminder_delivery_occurrence",
            postgresql_nulls_not_distinct=True,
        ),
        CheckConstraint("severity_rank BETWEEN 1 AND 3", name="ck_reminder_delivery_rank"),
        CheckConstraint(
            "channel IN ('in_app', 'push', 'slack', 'email')", name="ck_reminder_delivery_channel"
        ),
        CheckConstraint("mode IN ('immediate', 'summary')", name="ck_reminder_delivery_mode"),
        Index("ix_reminder_delivery_type", "type"),
    )

    key: Mapped[str] = mapped_column(String(KEY_MAX_LENGTH), nullable=False)
    type: Mapped[str] = mapped_column(String(TYPE_MAX_LENGTH), nullable=False)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    channel: Mapped[str] = mapped_column(String(10), nullable=False)
    mode: Mapped[str] = mapped_column(
        String(10), nullable=False, default="immediate", server_default=text("'immediate'")
    )
    period: Mapped[date | None] = mapped_column(Date, default=None)
    severity_rank: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    delivered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )


class ReminderSummaryRun(Base, PrimaryKeyMixin, TimestampMixin):
    """That a user's daily summary for a day was sent, so an urgent mail at 09:00 is not
    mistaken for it (spec §8.1)."""

    __tablename__ = "reminder_summary_run"
    __table_args__ = (UniqueConstraint("user_id", "day", name="uq_reminder_summary_run_user_day"),)

    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    day: Mapped[date] = mapped_column(Date, nullable=False)


class ReminderJobRun(Base, PrimaryKeyMixin, TimestampMixin):
    """When each scheduled job last ran, for `job_late`, the reminder pass included."""

    __tablename__ = "reminder_job_run"

    job: Mapped[str] = mapped_column(String(40), nullable=False, unique=True)
    ran_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
