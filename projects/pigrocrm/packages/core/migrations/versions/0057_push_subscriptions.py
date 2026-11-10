"""push_subscription: the browsers a person receives reminders on

Revision ID: 0057
Revises: 0056

For REB-738 (reminders as web push, spec 2026-10-26 §6.1). One row per browser that
allowed notifications: the endpoint at the vendor's push service, the two keys that
encrypt a message for that browser, and what the browser called itself. `endpoint` is
unique, because a browser has one subscription and it belongs to the person who last
subscribed from it (`PushSubscriptionService.subscribe`); `user_id` is indexed for the pass,
which reads every subscription of the people it is about to tell.

The table is new, so nothing here touches a row that exists and `IF NOT EXISTS` is not
needed. The downgrade drops it.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0057"
down_revision: str | Sequence[str] | None = "0056"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "push_subscription",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("endpoint", sa.Text(), nullable=False),
        sa.Column("p256dh", sa.String(length=128), nullable=False),
        sa.Column("auth", sa.String(length=64), nullable=False),
        sa.Column("user_agent", sa.String(length=300), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.UniqueConstraint("endpoint"),
        sa.CheckConstraint("char_length(endpoint) <= 2048", name="ck_push_subscription_endpoint"),
    )
    op.create_index("ix_push_subscription_user_id", "push_subscription", ["user_id"])


def downgrade() -> None:
    op.drop_table("push_subscription")
