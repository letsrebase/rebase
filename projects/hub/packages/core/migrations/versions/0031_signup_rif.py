"""signups.rif: the referral code a cold signup arrived with

Revision ID: 0031
Revises: 0030

REB-554: `?rif=` reached the wizards' applications but not the lighter `signups` row, so a
card an admin drafted from a signup weeks later lost the referrer. The column holds the
code as the visitor's link carried it (at most `REFERRAL_CODE_LENGTH` characters,
`NULL` for every row written before this revision and for every visitor without a link).
Nothing is backfilled: a code that was never recorded cannot be guessed.

`ADD COLUMN IF NOT EXISTS` and `DROP COLUMN IF EXISTS`, so a retried deploy lands on the
same table. The downgrade drops the column and the codes with it.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0031"
down_revision: str | Sequence[str] | None = "0030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE signups ADD COLUMN IF NOT EXISTS rif VARCHAR(10)")


def downgrade() -> None:
    op.execute("ALTER TABLE signups DROP COLUMN IF EXISTS rif")
