"""work_profiles.guard_hits: what the anonymity guards found in the fields a company reads

Revision ID: 0056
Revises: 0055

REB-1127. The cloud shows an approved profile's summary, domain labels and project domains to
companies, so the hub's deterministic guards read those fields when the profile is stored and the
hits (the field and the rule, never the matched text) are kept on the row. Approval is refused while
one stands unless an admin overrides with a note. Conditional: `IF NOT EXISTS`, `IF EXISTS`.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0056"
down_revision: str | Sequence[str] | None = "0055"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE work_profiles ADD COLUMN IF NOT EXISTS guard_hits JSONB "
        "NOT NULL DEFAULT '[]'::jsonb"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE work_profiles DROP COLUMN IF EXISTS guard_hits")
