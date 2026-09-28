"""talent_cloud_grants: one live grant per person and company, not per person

Revision ID: 0025
Revises: 0024

REB-518 (spec § 2): a person behind two company requests holds a grant for each, and
the cloud is open while any of them is live, so the partial unique index that keeps a
second «Apri il talent cloud» from writing a second row is on `(user_id, company_id)`,
not on `(user_id)` alone. The talent cloud's branch first moved it inside 0023; but
`hub-v0.43.0` shipped `main`'s 0023 to production on 2026-09-28 with the index on
`(user_id)`, and an edit to a revision a database already ran never runs there. So 0023
keeps the index it shipped with, and this revision swaps it.

Both statements are conditional (`DROP INDEX IF EXISTS`, `CREATE UNIQUE INDEX IF NOT
EXISTS`), the discipline migration 0001's docstring states for this package: the
preview was given the new index by hand and may or may not still hold the old one, and
a retried deploy must not error on an index the previous attempt already dropped or
created. The downgrade is the reverse, and fails while one person holds two live
grants, which the index on `(user_id)` cannot hold.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0025"
down_revision: str | Sequence[str] | None = "0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_talent_cloud_grants_user_id_live")
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_talent_cloud_grants_user_company_live "
        "ON talent_cloud_grants (user_id, company_id) WHERE revoked_at IS NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_talent_cloud_grants_user_company_live")
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_talent_cloud_grants_user_id_live "
        "ON talent_cloud_grants (user_id) WHERE revoked_at IS NULL"
    )
