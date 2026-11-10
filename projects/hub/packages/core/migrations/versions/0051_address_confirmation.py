"""users: the address's confirmation, asked for once before the first campaign

Revision ID: 0051
Revises: 0050

REB-911 (finding HCORE-13): the hub's form takes any address, so somebody else's entered
the campaign audiences without anyone confirming it. A person whose address never opened a
session and never followed a confirmation link is now left out of campaigns until it does,
and gets one confirmation mail.

Seven columns on `users`, the address's own facts: `email_confirmed_at` (the link was
followed), `confirmation_requested_at` (a campaign was prepared and left the person out),
`confirmation_sent_at` (the one mail went, or may have: a timeout is not a refusal; never
reset once set), `confirmation_attempts` and `confirmation_attempted_at` (every try counts
against the day's budget, a definitely refused one is tried again the next day, up to a
small number of times), and the link's `confirmation_token_hash` (sha256, unique) with its
`confirmation_expires_at`. A login
confirms the address without any of them: `logins` is the evidence, read by the audience.

Existing rows start unconfirmed on purpose: whoever never logged in is exactly the
address nobody has proved.

Conditional like every migration of this package: `IF NOT EXISTS`, `IF EXISTS`.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0051"
down_revision: str | Sequence[str] | None = "0050"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COLUMNS = (
    ("email_confirmed_at", "TIMESTAMPTZ"),
    ("confirmation_requested_at", "TIMESTAMPTZ"),
    ("confirmation_sent_at", "TIMESTAMPTZ"),
    ("confirmation_attempts", "INTEGER NOT NULL DEFAULT 0"),
    ("confirmation_attempted_at", "TIMESTAMPTZ"),
    ("confirmation_token_hash", "VARCHAR(64)"),
    ("confirmation_expires_at", "TIMESTAMPTZ"),
)


def upgrade() -> None:
    for name, kind in _COLUMNS:
        op.execute(f"ALTER TABLE users ADD COLUMN IF NOT EXISTS {name} {kind}")
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_users_confirmation_token_hash "
        "ON users (confirmation_token_hash)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_users_confirmation_token_hash")
    for name, _ in reversed(_COLUMNS):
        op.execute(f"ALTER TABLE users DROP COLUMN IF EXISTS {name}")
