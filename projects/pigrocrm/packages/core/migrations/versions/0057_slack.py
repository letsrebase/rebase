"""slack: the workspace a space is connected to, who is whom, and the channel posts

Revision ID: 0057
Revises: 0056

Three new tables, for REB-739 and REB-740 (reminders spec 2026-10-26 §6.4):

- `slack_workspaces`: the Slack workspace the space's app was installed in. A unique index
  over a constant keeps it to one row per space (a space is its own database). The bot token
  is `bot_token_ciphertext` and `bot_token_nonce`, sealed with AES-256-GCM like the Google
  refresh tokens, under a key derived for the space.
- `slack_user_links`: which Slack member a user is, `email`, `manual` or `none` (looked up,
  nobody there). One row per user; a Slack member belongs to one user, by a partial unique
  index over the ids that exist.
- `slack_channel_posts`: that a space-wide reminder was posted to the chosen channel, unique
  on `(key, channel_id, period)` `NULLS NOT DISTINCT` (PostgreSQL 15) for the reason
  `reminder_delivery`'s is; the rank is not in the key, so an escalation is an upsert.

All three are new, so nothing here touches a row that exists and `IF NOT EXISTS` is not
needed. No policy: they are space-level tables like `google_drive_accounts`, readable by
anyone and written only by an unscoped admin or by the pass. The downgrade drops them.
`google_oauth_states.purpose` already holds ten characters, and `slack` fits.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0057"
down_revision: str | Sequence[str] | None = "0056"
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
        "slack_workspaces",
        *_audit(),
        sa.Column("team_id", sa.String(length=32), nullable=False),
        sa.Column("team_name", sa.String(length=255), nullable=False),
        sa.Column("bot_user_id", sa.String(length=32), nullable=False),
        sa.Column("bot_token_ciphertext", sa.LargeBinary(), nullable=False),
        sa.Column("bot_token_nonce", sa.LargeBinary(), nullable=False),
        sa.Column(
            "scopes_granted", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("channel_id", sa.String(length=32), nullable=True),
        sa.Column("channel_name", sa.String(length=80), nullable=True),
        sa.Column("connected_by", sa.Uuid(), nullable=True),
        sa.Column("last_error", sa.String(length=500), nullable=True),
        sa.Column("last_error_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "connected_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("disconnected_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["connected_by"],
            ["users.id"],
            ondelete="SET NULL",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'revoked', 'disconnected')", name="ck_slack_workspaces_status"
        ),
    )
    op.create_index(
        "uq_slack_workspaces_one_per_space", "slack_workspaces", [sa.text("(true)")], unique=True
    )

    op.create_table(
        "slack_user_links",
        *_audit(),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("slack_user_id", sa.String(length=32), nullable=True),
        sa.Column("slack_email", sa.String(length=320), nullable=True),
        sa.Column("source", sa.String(length=10), nullable=False),
        sa.Column(
            "checked_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("user_id"),
        sa.CheckConstraint("source IN ('email', 'manual', 'none')", name="ck_slack_user_links_source"),
        sa.CheckConstraint(
            "(source = 'none') = (slack_user_id IS NULL)", name="ck_slack_user_links_id_iff_found"
        ),
    )
    op.create_index(
        "uq_slack_user_links_slack_user",
        "slack_user_links",
        ["slack_user_id"],
        unique=True,
        postgresql_where=sa.text("slack_user_id IS NOT NULL"),
    )

    op.create_table(
        "slack_channel_posts",
        *_audit(),
        sa.Column("key", sa.String(length=100), nullable=False),
        sa.Column("type", sa.String(length=40), nullable=False),
        sa.Column("channel_id", sa.String(length=32), nullable=False),
        sa.Column("period", sa.Date(), nullable=True),
        sa.Column("severity_rank", sa.SmallInteger(), nullable=False),
        sa.Column(
            "posted_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "key",
            "channel_id",
            "period",
            name="uq_slack_channel_post_occurrence",
            postgresql_nulls_not_distinct=True,
        ),
        sa.CheckConstraint("severity_rank BETWEEN 1 AND 3", name="ck_slack_channel_posts_rank"),
    )
    op.create_index("ix_slack_channel_posts_type", "slack_channel_posts", ["type"])


def downgrade() -> None:
    op.drop_index("ix_slack_channel_posts_type", table_name="slack_channel_posts")
    op.drop_table("slack_channel_posts")
    op.drop_index("uq_slack_user_links_slack_user", table_name="slack_user_links")
    op.drop_table("slack_user_links")
    op.drop_index("uq_slack_workspaces_one_per_space", table_name="slack_workspaces")
    op.drop_table("slack_workspaces")
