"""freelancer_sources.attempts: how many reads in a row failed

Revision ID: 0040
Revises: 0039

REB-844: the first production refetch after the sources were switched on got ten GitHub
profiles and twenty-two `error` rows, and an `error` row then waited the full week for
its next try. `attempts` counts the reads of one source that failed in a row, so the
pass can retry on a backoff (an hour, doubling, a day at most) and forget it on the first
read that works.

Rows already `error` start at 1, so the first pass after the deploy retries them: that is
the point of the card. Every other row starts at 0.

`IF NOT EXISTS` and a backfill that only touches rows still at 0, so a retried deploy
lands on the same schema.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0040"
down_revision: str | Sequence[str] | None = "0039"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE freelancer_sources
            ADD COLUMN IF NOT EXISTS attempts INTEGER NOT NULL DEFAULT 0
            CONSTRAINT ck_freelancer_sources_attempts CHECK (attempts >= 0)
        """
    )
    op.execute("UPDATE freelancer_sources SET attempts = 1 WHERE status = 'error' AND attempts = 0")


def downgrade() -> None:
    op.execute("ALTER TABLE freelancer_sources DROP COLUMN IF EXISTS attempts")
