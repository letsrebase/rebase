"""logins.method: how a person got in, the magic link or a provider

Revision ID: 0058
Revises: 0057

REB-1137. A sign-in with GitHub or Google writes the same `logins` row as the magic link, and
the admin's «Accessi» screen says which of the three it was. Every row written before this
migration was a magic link, which is what the column's default backfills. Nothing else about
the provider is stored.

Conditional like every migration of this package: `IF NOT EXISTS`, and the `CHECK` behind a
`pg_constraint` guard. `logins` is one of the tables PostHog's warehouse reads; the new column
is a short label, never an identifier of the person at the provider.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0058"
down_revision: str | Sequence[str] | None = "0057"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE logins ADD COLUMN IF NOT EXISTS method VARCHAR(10) NOT NULL DEFAULT 'link'"
    )
    op.execute(
        "DO $$ BEGIN "
        "IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_logins_method') "
        "THEN ALTER TABLE logins ADD CONSTRAINT ck_logins_method "
        "CHECK (method IN ('link', 'github', 'google')); "
        "END IF; END $$"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE logins DROP CONSTRAINT IF EXISTS ck_logins_method")
    op.execute("ALTER TABLE logins DROP COLUMN IF EXISTS method")
