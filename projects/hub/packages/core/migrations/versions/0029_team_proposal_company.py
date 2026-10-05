"""team_proposals.company_id: a cloud proposal remembers the company it was made for

Revision ID: 0029
Revises: 0028

REB-578 (the review of #434): «Assumi team» filed a cloud proposal under the caller's
newest live grant, so a referente behind Acme and Beta who proposed under Beta and then
got a newer Acme grant could file the Beta proposal for Acme. The proposal now carries
the company of the grant it was made under, and «Assumi team» files under that company
or refuses.

One nullable column with a foreign key to `companies`, `ADD COLUMN IF NOT EXISTS` and the
constraint dropped `IF EXISTS` before it is added, so a retried deploy adds the same
again. `NULL` stays for the public and admin proposals, which belong to no company.

Existing cloud rows are backfilled only where the company is not a guess: the proposal's
user holds grants for exactly one company in all, and the proposal is no older than the
earliest of them, so it can only have been made under that company. Every other cloud row
(a person behind two companies) stays `NULL` and keeps the old rule, the date check
against the newest live grant. Rows that were attempts (`errore` set) are never requested
and are left alone.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0029"
down_revision: str | Sequence[str] | None = "0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_FK = "fk_team_proposals_company_id_companies"


def upgrade() -> None:
    op.execute("ALTER TABLE team_proposals ADD COLUMN IF NOT EXISTS company_id UUID")
    op.execute(f"ALTER TABLE team_proposals DROP CONSTRAINT IF EXISTS {_FK}")
    op.execute(
        f"ALTER TABLE team_proposals ADD CONSTRAINT {_FK} "
        "FOREIGN KEY (company_id) REFERENCES companies (id)"
    )
    op.execute(
        """
        UPDATE team_proposals AS p
        SET company_id = only_one.company_id
        FROM (
            SELECT user_id, MIN(company_id::text)::uuid AS company_id, MIN(granted_at) AS since
            FROM talent_cloud_grants
            GROUP BY user_id
            HAVING COUNT(DISTINCT company_id) = 1
        ) AS only_one
        WHERE p.origine = 'cloud'
          AND p.errore IS NULL
          AND p.company_id IS NULL
          AND p.user_id = only_one.user_id
          AND p.created_at >= only_one.since
        """
    )


def downgrade() -> None:
    op.execute(f"ALTER TABLE team_proposals DROP CONSTRAINT IF EXISTS {_FK}")
    op.execute("ALTER TABLE team_proposals DROP COLUMN IF EXISTS company_id")
