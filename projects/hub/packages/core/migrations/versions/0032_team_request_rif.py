"""team_requests.rif: the referral code a public team request arrived with

Revision ID: 0032
Revises: 0031

REB-600: a public «Assumi team» request creates no user and no company, so the `?rif=` the
visitor came with had nowhere to live and a company that reached us through a member's
link and then used `/hub/team` was never attributed. The column holds the code as the
visitor's link carried it (at most `REFERRAL_CODE_LENGTH` characters, `NULL` for every row
written before this revision, for every request from the cloud and for every visitor
without a link). Nothing is backfilled: a code that was never recorded cannot be guessed.

`ADD COLUMN IF NOT EXISTS` and `DROP COLUMN IF EXISTS`, so a retried deploy lands on the
same table. The downgrade drops the column and the codes with it.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0032"
down_revision: str | Sequence[str] | None = "0031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE team_requests ADD COLUMN IF NOT EXISTS rif VARCHAR(10)")


def downgrade() -> None:
    op.execute("ALTER TABLE team_requests DROP COLUMN IF EXISTS rif")
