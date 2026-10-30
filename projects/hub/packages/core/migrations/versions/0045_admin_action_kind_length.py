"""admin_actions.kind is 40 characters wide

Revision ID: 0045
Revises: 0044

REB-862: `anonymous_cv_downloaded` is 23 characters and the column held 20, so the trail
cut the kind to `anonymous_cv_downloa` and no reader found it by name. 40 leaves room for the
next kind. Widening a VARCHAR rewrites nothing; the downgrade narrows it again only when no
entry is longer than 20 (the entry's kind is cut, and an entry for the download is the one
that would be).
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0045"
down_revision: str | Sequence[str] | None = "0044"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE admin_actions ALTER COLUMN kind TYPE VARCHAR(40)")


def downgrade() -> None:
    op.execute("UPDATE admin_actions SET kind = LEFT(kind, 20) WHERE LENGTH(kind) > 20")
    op.execute("ALTER TABLE admin_actions ALTER COLUMN kind TYPE VARCHAR(20)")
