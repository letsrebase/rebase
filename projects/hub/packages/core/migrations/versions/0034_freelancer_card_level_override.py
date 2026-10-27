"""freelancers.seniority_admin and freelancers.anni_admin: an admin's word on a talent's level

Revision ID: 0034
Revises: 0033

REB-799: the anonymous card's `seniority` and `anni` are Claude's reading of the CV, and
the audit found cards a CV could not support («junior, 2 years» for a founder whose CV
dates nothing). An admin's correction has to survive a «Rigenera scheda», and a regenerate
retires or rewrites the card's row, so the correction is two nullable columns on
`freelancers` and not part of the card: `seniority_admin` (one of the card's four words)
and `anni_admin` (0 to 60). `NULL` is «nobody corrected it». They are read in preference
to the card's own wherever the card is read, and written through the same override as
every other field of the talent, with its audit entry and its revert.

`ADD COLUMN IF NOT EXISTS`, the checks dropped `IF EXISTS` before they are added, so a
retried deploy lands on the same schema. Neither table is synced to PostHog's warehouse.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0034"
down_revision: str | Sequence[str] | None = "0033"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SENIORITY = "ck_freelancers_seniority_admin"
_ANNI = "ck_freelancers_anni_admin_range"


def upgrade() -> None:
    op.execute("ALTER TABLE freelancers ADD COLUMN IF NOT EXISTS seniority_admin VARCHAR(10)")
    op.execute("ALTER TABLE freelancers ADD COLUMN IF NOT EXISTS anni_admin INTEGER")
    op.execute(f"ALTER TABLE freelancers DROP CONSTRAINT IF EXISTS {_SENIORITY}")
    op.execute(
        f"ALTER TABLE freelancers ADD CONSTRAINT {_SENIORITY} "
        "CHECK (seniority_admin IS NULL OR seniority_admin IN ('junior', 'mid', 'senior', 'lead'))"
    )
    op.execute(f"ALTER TABLE freelancers DROP CONSTRAINT IF EXISTS {_ANNI}")
    op.execute(
        f"ALTER TABLE freelancers ADD CONSTRAINT {_ANNI} "
        "CHECK (anni_admin IS NULL OR (anni_admin >= 0 AND anni_admin <= 60))"
    )


def downgrade() -> None:
    op.execute(f"ALTER TABLE freelancers DROP CONSTRAINT IF EXISTS {_ANNI}")
    op.execute(f"ALTER TABLE freelancers DROP CONSTRAINT IF EXISTS {_SENIORITY}")
    op.execute("ALTER TABLE freelancers DROP COLUMN IF EXISTS anni_admin")
    op.execute("ALTER TABLE freelancers DROP COLUMN IF EXISTS seniority_admin")
