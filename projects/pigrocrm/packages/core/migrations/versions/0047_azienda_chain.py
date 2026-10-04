"""The azienda on customers, deals, contracts, documents and costs (REB-623).

Revision ID: 0047
Revises: 0046

Spec 2026-10-03 §1.6, §1.7, §2 steps 4 and 5, milestone 3 of §9. `customers`, `deals`,
`contracts` and `documents` gain `azienda_id`, backfilled from the space's default
azienda and then `NOT NULL`, with a foreign key and an index each: every row that
exists is bound to the one azienda the space already had (§1.12), nothing is
reassigned by nation. `costs` gains it nullable, backfilled from the deal's where the
cost has a deal and left `NULL` where it has none, which is a shared cost.

Every space has exactly one azienda when this runs (0045, `ensure_defaults`,
`createadmin`), so the backfills are the one row; a database with rows in these
tables and no default azienda stops on the `NOT NULL`, loudly. The downgrade drops the
columns: an assignment is not a fact the old world had, so there is nothing it could
fail to give back.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0047"
down_revision: str | None = "0046"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DEFAULT_AZIENDA = "(SELECT id FROM emitter_profile WHERE predefinita LIMIT 1)"
OWNED = ("customers", "deals", "contracts", "documents")


def upgrade() -> None:
    for table in OWNED:
        op.add_column(table, sa.Column("azienda_id", sa.Uuid(), nullable=True))
        op.execute(f"UPDATE {table} SET azienda_id = {DEFAULT_AZIENDA}")  # noqa: S608
        op.alter_column(table, "azienda_id", nullable=False)
        op.create_foreign_key(
            f"fk_{table}_azienda_id_emitter_profile",
            table,
            "emitter_profile",
            ["azienda_id"],
            ["id"],
        )
        op.create_index(f"ix_{table}_azienda_id", table, ["azienda_id"])

    op.add_column("costs", sa.Column("azienda_id", sa.Uuid(), nullable=True))
    op.execute(
        "UPDATE costs SET azienda_id = deals.azienda_id FROM deals WHERE costs.deal_id = deals.id"
    )
    op.create_foreign_key(
        "fk_costs_azienda_id_emitter_profile", "costs", "emitter_profile", ["azienda_id"], ["id"]
    )
    op.create_index("ix_costs_azienda_id", "costs", ["azienda_id"])


def downgrade() -> None:
    op.drop_index("ix_costs_azienda_id", table_name="costs")
    op.drop_constraint("fk_costs_azienda_id_emitter_profile", "costs", type_="foreignkey")
    op.drop_column("costs", "azienda_id")
    for table in reversed(OWNED):
        op.drop_index(f"ix_{table}_azienda_id", table_name=table)
        op.drop_constraint(f"fk_{table}_azienda_id_emitter_profile", table, type_="foreignkey")
        op.drop_column(table, "azienda_id")
