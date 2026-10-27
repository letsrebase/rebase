"""one person, one LinkedIn: normalised addresses, `merged_into_id`, `cv_uploaded_at`

Revision ID: 0035
Revises: 0034

REB-800. Identity was the address alone, so one person who signed up twice with two
addresses and the same LinkedIn profile was two `users` and two cards. This revision
prepares the schema for the rule that LinkedIn is the second identity:

* `users.linkedin_url` and `signups.linkedin_url` are rewritten to the stored shape
  `https://www.linkedin.com/in/<name>` (lower case, no query, no trailing slash, no
  locale). The pass lives here as a frozen copy of `rebase_core.linkedin`, as every
  migration of this package keeps its own SQL: the rule may change later, what this
  revision did must not. A value that is not a personal profile is left exactly as it is.
* `users.merged_into_id`: the soft pointer a merge leaves on the person it absorbed.
* `freelancers.cv_uploaded_at`, backfilled from `updated_at` where a CV is stored: the
  best date the table holds, and what «the newest CV» means for a merge.
* the two unique indexes on `lower(linkedin_url)`, **only when no two rows share a
  profile**. Production holds such a pair today, so on production this revision succeeds
  without them and `rebase talent-duplicates --apply` creates them once the merge has run
  (the runbook, `projects/hub/docs/merge-duplicate-talents.md`, orders the two steps).
  Every other database, the tests' included, has none and gets both here.

Everything is `IF NOT EXISTS`/guarded so a retried deploy lands on the same schema.
"""

import re
from collections.abc import Sequence
from urllib.parse import unquote

import sqlalchemy as sa
from alembic import op

revision: str = "0035"
down_revision: str | Sequence[str] | None = "0034"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PROFILE = re.compile(
    r"^(?:https?://)?(?:[a-z0-9-]+\.)*linkedin\.com/in/([^/?#\\\s]+)(?:[/?#].*)?$",
    re.IGNORECASE,
)
_NAME = re.compile(r"[\w-]+")
_PREFIX = "https://www.linkedin.com/in/"
_IS_PROFILE = f"linkedin_url LIKE '{_PREFIX}%'"
_MAX_LENGTH = 300


def canonical(value: str) -> str:
    """The stored shape of a personal profile, or `value` itself for anything else."""
    match = _PROFILE.match(value.strip())
    if match is None:
        return value
    name = unquote(match.group(1)).lower()
    if not _NAME.fullmatch(name) or len(_PREFIX + name) > _MAX_LENGTH:
        return value
    return _PREFIX + name


def _normalise(table: str) -> None:
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(f"SELECT id, linkedin_url FROM {table} WHERE linkedin_url IS NOT NULL")
    ).all()
    for row_id, value in rows:
        fixed = canonical(value)
        if fixed != value:
            bind.execute(
                sa.text(f"UPDATE {table} SET linkedin_url = :v WHERE id = :i"),
                {"v": fixed, "i": row_id},
            )


def _blank_to_null(table: str) -> None:
    """An empty or blank address is no address: two of them must not look like a profile."""
    op.execute(f"UPDATE {table} SET linkedin_url = NULL WHERE btrim(linkedin_url) = ''")


def _unique_index(table: str, name: str) -> None:
    """Created when nothing shares a profile; left for the merge command when something
    does (`rebase_core.duplicates.ensure_unique_indexes` runs the same statement). Only a
    personal profile counts, `rebase_core.linkedin.linkedin_key`'s own rule: a company
    page or a bare `linkedin.com` can be shared by two people."""
    bind = op.get_bind()
    shared = bind.execute(
        sa.text(
            f"SELECT 1 FROM {table} WHERE {_IS_PROFILE} "
            "GROUP BY lower(linkedin_url) HAVING count(*) > 1 LIMIT 1"
        )
    ).first()
    if shared is None:
        op.execute(
            f"CREATE UNIQUE INDEX IF NOT EXISTS {name} ON {table} (lower(linkedin_url)) "
            f"WHERE {_IS_PROFILE}"
        )


def upgrade() -> None:
    op.execute(
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS merged_into_id UUID REFERENCES users (id)"
    )
    op.execute("ALTER TABLE freelancers ADD COLUMN IF NOT EXISTS cv_uploaded_at TIMESTAMPTZ")
    op.execute(
        "UPDATE freelancers SET cv_uploaded_at = updated_at "
        "WHERE cv_bytes IS NOT NULL AND cv_uploaded_at IS NULL"
    )
    _blank_to_null("users")
    _blank_to_null("signups")
    _normalise("users")
    _normalise("signups")
    _unique_index("users", "uq_users_linkedin_lower")
    _unique_index("signups", "uq_signups_linkedin_lower")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_signups_linkedin_lower")
    op.execute("DROP INDEX IF EXISTS uq_users_linkedin_lower")
    op.execute("ALTER TABLE freelancers DROP COLUMN IF EXISTS cv_uploaded_at")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS merged_into_id")
