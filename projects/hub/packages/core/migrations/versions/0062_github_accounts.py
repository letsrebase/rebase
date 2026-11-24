"""github_accounts, and the work profile's artifact checks

Revision ID: 0062
Revises: 0061

REB-1166 to REB-1168 (design "Cross-check work profiles against a verified GitHub account",
2026-11-24). `github_accounts` is the GitHub account a freelancer proved is theirs by signing in
to it from the member area: GitHub's numeric id and the login, the scope the person chose
(`public` or `private`), and the snapshot read during the link (`activity`, 24 months of counts,
and `languages`, technology labels). One per freelancer and one freelancer per account, both
unique; `ON DELETE CASCADE` with the card. The token is never stored.

`work_profiles.artifact_checks` and `artifacts_checked_at` hold what checking each project's URL
artifact against that account found, for the admin's review only.

Neither is read by PostHog's warehouse: `posthog_ro` is granted table by table and this table is
not in the grant. Conditional like every migration of this package: `IF NOT EXISTS`.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0062"
down_revision: str | Sequence[str] | None = "0061"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TS = "TIMESTAMP WITH TIME ZONE"

_UP = (
    "CREATE TABLE IF NOT EXISTS github_accounts ("
    "id UUID PRIMARY KEY, "
    "freelancer_id UUID NOT NULL REFERENCES freelancers (id) ON DELETE CASCADE, "
    "github_id BIGINT NOT NULL, "
    "login VARCHAR(39) NOT NULL, "
    "scope VARCHAR(10) NOT NULL, "
    f"linked_at {_TS} NOT NULL DEFAULT now(), "
    "activity JSONB NOT NULL DEFAULT '[]'::jsonb, "
    "languages JSONB NOT NULL DEFAULT '[]'::jsonb, "
    "CONSTRAINT ck_github_accounts_scope CHECK (scope IN ('public', 'private')))",
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_github_accounts_freelancer_id "
    "ON github_accounts (freelancer_id)",
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_github_accounts_github_id ON github_accounts (github_id)",
    "ALTER TABLE work_profiles ADD COLUMN IF NOT EXISTS artifact_checks "
    "JSONB NOT NULL DEFAULT '[]'::jsonb",
    f"ALTER TABLE work_profiles ADD COLUMN IF NOT EXISTS artifacts_checked_at {_TS}",
)

_DOWN = (
    "ALTER TABLE work_profiles DROP COLUMN IF EXISTS artifacts_checked_at",
    "ALTER TABLE work_profiles DROP COLUMN IF EXISTS artifact_checks",
    "DROP TABLE IF EXISTS github_accounts",
)


def upgrade() -> None:
    for statement in _UP:
        op.execute(statement)


def downgrade() -> None:
    for statement in _DOWN:
        op.execute(statement)
