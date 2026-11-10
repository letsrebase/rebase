"""reminders: the bookkeeping around what the detectors raise

Revision ID: 0054
Revises: 0053

Six tables, for REB-732 and the reminders spec of 2026-10-26 §8. A reminder itself is
derived on every pass and never stored; these hold only what a person or a channel did
about one, keyed by `type:subject-id`:

- `reminder_preference`: one layer of settings, the space's defaults (`user_id` null) or a
  person's overrides, for one type or, with `type` null, for the settings that belong to no
  type (quiet hours, the summary hour). A null column inherits. `(user_id, type)` is unique
  `NULLS NOT DISTINCT` (PostgreSQL 15), since both columns may be null.
- `reminder_ack` and `reminder_snooze`: what one user has seen, at which severity rank,
  and for a snooze until which day. Unique per `(key, user_id)`.
- `reminder_delivery`: what a channel accepted, written after it did. Unique on
  `(key, user_id, channel, mode, period)`, `NULLS NOT DISTINCT` for the same reason; the
  rank is not in the key, so an escalation is an upsert.
- `reminder_summary_run`: that a user's daily summary for a day went.
- `reminder_job_run`: when each scheduled job last ran.

All six are new, so nothing here touches a row that exists; `IF NOT EXISTS` is not needed.
The downgrade drops them.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0054"
down_revision: str | Sequence[str] | None = "0053"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _audit() -> list[sa.Column]:
    return [
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    ]


def upgrade() -> None:
    op.create_table(
        "reminder_preference",
        *_audit(),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("type", sa.String(length=40), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=True),
        sa.Column("channels", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("ladder", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("urgent_from", sa.String(length=10), nullable=True),
        sa.Column("email_mode", sa.String(length=10), nullable=True),
        sa.Column("quiet_hours", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("summary_hour", sa.SmallInteger(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.UniqueConstraint(
            "user_id",
            "type",
            name="uq_reminder_preference_layer",
            postgresql_nulls_not_distinct=True,
        ),
        sa.CheckConstraint(
            "urgent_from IS NULL OR urgent_from IN ('warning', 'serious', 'critical')",
            name="ck_reminder_preference_urgent_from",
        ),
        sa.CheckConstraint(
            "email_mode IS NULL OR email_mode IN ('immediate', 'summary')",
            name="ck_reminder_preference_email_mode",
        ),
        sa.CheckConstraint(
            "summary_hour IS NULL OR summary_hour BETWEEN 0 AND 23",
            name="ck_reminder_preference_summary_hour",
        ),
        sa.CheckConstraint(
            "(type IS NULL AND enabled IS NULL AND channels IS NULL AND ladder IS NULL "
            "AND urgent_from IS NULL AND email_mode IS NULL) "
            "OR (type IS NOT NULL AND quiet_hours IS NULL AND summary_hour IS NULL)",
            name="ck_reminder_preference_scope_of_settings",
        ),
    )
    op.create_index("ix_reminder_preference_user_id", "reminder_preference", ["user_id"])

    for table, extra in (
        ("reminder_ack", []),
        ("reminder_snooze", [sa.Column("until", sa.Date(), nullable=False)]),
    ):
        op.create_table(
            table,
            *_audit(),
            sa.Column("key", sa.String(length=100), nullable=False),
            sa.Column("type", sa.String(length=40), nullable=False),
            sa.Column("user_id", sa.Uuid(), nullable=False),
            sa.Column("severity_rank", sa.SmallInteger(), nullable=False),
            *extra,
            sa.PrimaryKeyConstraint("id"),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
            sa.UniqueConstraint("key", "user_id", name=f"uq_{table}_key_user"),
            sa.CheckConstraint("severity_rank BETWEEN 1 AND 3", name=f"ck_{table}_rank"),
        )
        op.create_index(f"ix_{table}_type", table, ["type"])
        op.create_index(f"ix_{table}_user_id", table, ["user_id"])

    op.create_table(
        "reminder_delivery",
        *_audit(),
        sa.Column("key", sa.String(length=100), nullable=False),
        sa.Column("type", sa.String(length=40), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("channel", sa.String(length=10), nullable=False),
        sa.Column(
            "mode", sa.String(length=10), server_default=sa.text("'immediate'"), nullable=False
        ),
        sa.Column("period", sa.Date(), nullable=True),
        sa.Column("severity_rank", sa.SmallInteger(), nullable=False),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.UniqueConstraint(
            "key",
            "user_id",
            "channel",
            "mode",
            "period",
            name="uq_reminder_delivery_occurrence",
            postgresql_nulls_not_distinct=True,
        ),
        sa.CheckConstraint("severity_rank BETWEEN 1 AND 3", name="ck_reminder_delivery_rank"),
        sa.CheckConstraint(
            "channel IN ('in_app', 'push', 'slack', 'email')", name="ck_reminder_delivery_channel"
        ),
        sa.CheckConstraint("mode IN ('immediate', 'summary')", name="ck_reminder_delivery_mode"),
    )
    op.create_index("ix_reminder_delivery_type", "reminder_delivery", ["type"])
    op.create_index("ix_reminder_delivery_user_id", "reminder_delivery", ["user_id"])

    op.create_table(
        "reminder_summary_run",
        *_audit(),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.UniqueConstraint("user_id", "day", name="uq_reminder_summary_run_user_day"),
    )

    op.create_table(
        "reminder_job_run",
        *_audit(),
        sa.Column("job", sa.String(length=40), nullable=False),
        sa.Column("ran_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job"),
    )


def downgrade() -> None:
    op.drop_table("reminder_job_run")
    op.drop_table("reminder_summary_run")
    op.drop_table("reminder_delivery")
    op.drop_table("reminder_snooze")
    op.drop_table("reminder_ack")
    op.drop_table("reminder_preference")
