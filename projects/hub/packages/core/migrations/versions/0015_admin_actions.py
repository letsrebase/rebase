"""admin_actions: soft delete for freelancers and companies, and the override trail

Revision ID: 0015
Revises: 0014

REB-347: an admin can override, clear or delete any `Freelancer`/`Company` field or
record, reversibly. `deleted_at` on both tables, `NULL` while the row is live, is the
soft delete; `admin_actions` is the append-only trail, the same shape `comments` (0004)
already gives entity_type/entity_id, plus who did it (`admin_id`, a `users.id`) and the
diff (`payload`, empty for a `deleted`/`restored` entry, `changed`/`before`/`after` for
an `overridden`/`cleared` one -- see `rebase_core.audit.field_changes`).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0015"
down_revision: str | Sequence[str] | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("freelancers", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("companies", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))

    op.create_table(
        "admin_actions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("entity_type", sa.String(length=20), nullable=False),
        sa.Column("entity_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("admin_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index(
        "ix_admin_actions_entity", "admin_actions", ["entity_type", "entity_id", "created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_admin_actions_entity", table_name="admin_actions")
    op.drop_table("admin_actions")
    op.drop_column("companies", "deleted_at")
    op.drop_column("freelancers", "deleted_at")
