"""work_profiles.unknown_technologies: how many techUsage rows intake dropped

Revision ID: 0059
Revises: 0058

REB-1141. A work profile whose `techUsage` names a technology outside the hub's labels is no
longer refused: the unknown rows are dropped before storage and only their count is kept, which
the admin sees on the review panel. Profiles stored before dropped nothing, which is what the
default backfills.

Conditional like every migration of this package: `IF NOT EXISTS`.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0059"
down_revision: str | Sequence[str] | None = "0058"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE work_profiles ADD COLUMN IF NOT EXISTS unknown_technologies "
        "INTEGER NOT NULL DEFAULT 0"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE work_profiles DROP COLUMN IF EXISTS unknown_technologies")
