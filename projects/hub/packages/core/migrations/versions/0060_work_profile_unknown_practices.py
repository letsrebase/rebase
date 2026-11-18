"""work_profiles.unknown_practices: how many practices items intake dropped

Revision ID: 0060
Revises: 0059

REB-1144. A work profile whose `practices` block names an id outside the hub's list is no
longer refused: the unknown items are dropped before storage and only their count is kept, which
the admin sees on the review panel. Profiles stored before dropped nothing, which is what the
default backfills.

Conditional like every migration of this package: `IF NOT EXISTS`.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0060"
down_revision: str | Sequence[str] | None = "0059"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE work_profiles ADD COLUMN IF NOT EXISTS unknown_practices "
        "INTEGER NOT NULL DEFAULT 0"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE work_profiles DROP COLUMN IF EXISTS unknown_practices")
