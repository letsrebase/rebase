"""bank_movements.end_to_end_id: the payer's id for a payment

Revision ID: 0085
Revises: 0084

REB-1017. A nullable column, nothing backfilled: the SEPA `EndToEndId` a reader finds in a file
or in a description, kept apart from `bank_reference` because payers reuse it, so it must never
decide whether two movements are the same one (`dedup_key` does). An index serves the lookup
matching makes. The table's row policy is unchanged.

`downgrade` drops the index and the column.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0085"
down_revision: str | Sequence[str] | None = "0084"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INDEX = "ix_bank_movements_end_to_end_id"


def upgrade() -> None:
    op.add_column("bank_movements", sa.Column("end_to_end_id", sa.String(length=35), nullable=True))
    op.create_index(INDEX, "bank_movements", ["end_to_end_id"])


def downgrade() -> None:
    op.drop_index(INDEX, table_name="bank_movements")
    op.drop_column("bank_movements", "end_to_end_id")
