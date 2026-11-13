"""engagement_notices: the outbox of what the CRM tells the hub about an engagement

Revision ID: 0126
Revises: 0125

REB-1106, § 5 of `2026-11-09-collaborators-in-the-crm-design.md` («The hub's read model»).

A row is written in the same transaction as each change of an engagement (created, updated while a
draft, sent, signed, cancelled, closed, a framework notice served), with the engagement's own
sequence number and the read-model fields as they were. A retry sweep in the leads space delivers
it to the hub and marks it `delivered` on a 2xx. Nothing is backfilled: an engagement that exists
tells the hub at its next change, and the hub's own rows for the matches it already holds are the
one-off migration's business.

The table has no foreign key and no row-level policy: it is the CRM's own bookkeeping, read by no
person and no API, and an engagement is never deleted. `downgrade` drops it with the notices not
yet delivered.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0126"
down_revision: str | Sequence[str] | None = "0125"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "engagement_notices"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("engagement_id", sa.Uuid(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "state", sa.String(length=10), server_default=sa.text("'pending'"), nullable=False
        ),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "next_attempt_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.String(length=200), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "kind IN ('created', 'updated', 'sent', 'signed', 'cancelled', 'closed', "
            "'notice_served')",
            name="ck_engagement_notices_kind",
        ),
        sa.CheckConstraint(
            "state IN ('pending', 'delivered', 'rejected')", name="ck_engagement_notices_state"
        ),
        sa.CheckConstraint("sequence >= 1", name="ck_engagement_notices_sequence"),
        sa.CheckConstraint("attempts >= 0", name="ck_engagement_notices_attempts"),
    )
    op.create_index(
        "uq_engagement_notices_sequence", TABLE, ["engagement_id", "sequence"], unique=True
    )
    op.create_index(
        "ix_engagement_notices_due",
        TABLE,
        ["next_attempt_at"],
        postgresql_where=sa.text("state = 'pending'"),
    )


def downgrade() -> None:
    op.drop_index("ix_engagement_notices_due", table_name=TABLE)
    op.drop_index("uq_engagement_notices_sequence", table_name=TABLE)
    op.drop_table(TABLE)
