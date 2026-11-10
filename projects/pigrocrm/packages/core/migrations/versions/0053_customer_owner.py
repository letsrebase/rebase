"""customers: an owner of their own

Revision ID: 0053
Revises: 0052

`customers.owner_id`, for REB-773. Until now the only owner in the schema was
`deals.owner_id`, and the reminders spec (2026-10-26 §4) had to derive a customer's owner
from its open deals. Lorenzo decided the same day that a customer gets a real one: the
column is nullable (a user is archived, never deleted, and a customer created by a job
has nobody to name), indexed because the reminders look a customer's owner up for every
subject that hangs on it, and foreign-keyed to `users`.

Backfill, once, by the rule the spec gave for the derivation: a customer takes the owner
of its open deals, the one that owns most of them and, on a tie, the one whose first open
deal is the oldest. A deal is open when its pipeline stage is of type `open` and it is not
archived; a deal without an owner says nothing. A customer with no such deal stays
without an owner, and the reminders give it to the space's owner at the time they are
raised. Safe on a table that already holds rows: the column is added empty, and the
backfill only writes where it finds an answer.

The downgrade drops the column, the index and the foreign key with it.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0053"
down_revision: str | Sequence[str] | None = "0052"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("customers", sa.Column("owner_id", sa.Uuid(), nullable=True))
    op.create_foreign_key("fk_customers_owner_id_users", "customers", "users", ["owner_id"], ["id"])
    op.create_index("ix_customers_owner_id", "customers", ["owner_id"])
    op.execute(
        """
        UPDATE customers c
        SET owner_id = chosen.owner_id
        FROM (
            SELECT DISTINCT ON (customer_id) customer_id, owner_id
            FROM (
                SELECT d.customer_id, d.owner_id, count(*) AS open_deals,
                       min(d.created_at) AS first_deal_at
                FROM deals d
                JOIN pipeline_stages s ON s.id = d.pipeline_stage_id
                WHERE d.deleted_at IS NULL AND s.tipo = 'open' AND d.owner_id IS NOT NULL
                GROUP BY d.customer_id, d.owner_id
            ) per_owner
            ORDER BY customer_id, open_deals DESC, first_deal_at ASC, owner_id
        ) chosen
        WHERE c.id = chosen.customer_id
        """
    )


def downgrade() -> None:
    op.drop_index("ix_customers_owner_id", table_name="customers")
    op.drop_constraint("fk_customers_owner_id_users", "customers", type_="foreignkey")
    op.drop_column("customers", "owner_id")
