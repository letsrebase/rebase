"""magic_link_tokens: which public door asked for the link

Revision ID: 0048
Revises: 0047

REB-889 review: the public mail budget counted every magic link of the day, so enough
logins closed sign-in for everybody. `asked_via` tells the doors apart: `login` (the login
page) and `wizard` (the «you already have a card» mail of an application) count against a
person's own daily cap, and only `wizard`, the mail nobody asked for, against the global
budget. `NULL` is an admin's promotion, the command line and every row written before this
migration: not counted.

Conditional like every migration of this package: `IF NOT EXISTS`, and the `CHECK` behind a
`pg_constraint` guard.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0048"
down_revision: str | Sequence[str] | None = "0047"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE magic_link_tokens ADD COLUMN IF NOT EXISTS asked_via VARCHAR(10)")
    op.execute(
        "DO $$ BEGIN "
        "IF NOT EXISTS "
        "(SELECT 1 FROM pg_constraint WHERE conname = 'ck_magic_link_tokens_asked_via') "
        "THEN ALTER TABLE magic_link_tokens ADD CONSTRAINT ck_magic_link_tokens_asked_via "
        "CHECK (asked_via IS NULL OR asked_via IN ('login', 'wizard')); "
        "END IF; END $$"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE magic_link_tokens DROP CONSTRAINT IF EXISTS ck_magic_link_tokens_asked_via"
    )
    op.execute("ALTER TABLE magic_link_tokens DROP COLUMN IF EXISTS asked_via")
