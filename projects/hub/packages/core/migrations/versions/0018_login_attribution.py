"""magic_link_tokens and logins carry the campaign a login page was opened from

Revision ID: 0018
Revises: 0017

REB-426: an outreach mail sends a person to `/hub/login?utm_...`, and until now the hub
could say that they came in and when, never from which mail. The login page sends the
attribution it arrived with alongside the address, the link request keeps it on the
token (the one row both the request and the mail's click share), and the entry copies
it onto the `logins` row it writes. Both tables get the seven nullable `UtmMixin`
columns the freelancer and company rows already have, the same types and widths:
`origine` VARCHAR(40), the six `utm_*` VARCHAR(200).

Nothing is backfilled: a login recorded before this migration started from a page that
sent no attribution, and `NULL` is the truth about it. Both tables already hold real
rows in production; `ADD COLUMN IF NOT EXISTS` on a nullable column with no default is
a catalogue change in Postgres, no rewrite, and a retried deploy does not error on a
column the first attempt added (the discipline 0001's docstring states for this
package). `logins` is read by PostHog's warehouse: the new columns are appended at the
end, nothing is renamed or moved, so the sync keeps its schema and picks them up.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0018"
down_revision: str | Sequence[str] | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = ("magic_link_tokens", "logins")
_COLUMNS: tuple[tuple[str, int], ...] = (
    ("origine", 40),
    ("utm_source", 200),
    ("utm_medium", 200),
    ("utm_campaign", 200),
    ("utm_content", 200),
    ("utm_term", 200),
    ("utm_id", 200),
)


def upgrade() -> None:
    for table in _TABLES:
        for column, width in _COLUMNS:
            op.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} VARCHAR({width})")


def downgrade() -> None:
    for table in _TABLES:
        for column, _ in reversed(_COLUMNS):
            op.execute(f"ALTER TABLE {table} DROP COLUMN IF EXISTS {column}")
