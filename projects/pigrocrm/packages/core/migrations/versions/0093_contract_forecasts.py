"""The rate card and period an invoice line bills

Revision ID: 0093
Revises: 0092

REB-997 (spec 2026-11-09, due items, § 1.2). An invoice line created from a contract carries the
rate card it bills and the day its period starts (`invoice_lines.rate_card_id`, `period_start`,
both or neither), so issuing drops the forecast of that period. The forecast's own identity,
`due_items.forecast_key` and its partial unique index, is revision 0090's.

Nothing is backfilled: the columns are nullable and an existing line bills no period. The row
policies are unchanged. `downgrade` drops the constraint, the index and the columns.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0093"
down_revision: str | Sequence[str] | None = "0092"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RATE_CARD_INDEX = "ix_invoice_lines_rate_card_id"
LINE_CHECK = "ck_invoice_lines_rate_card_with_period"


def upgrade() -> None:
    op.add_column("invoice_lines", sa.Column("rate_card_id", sa.Uuid(), nullable=True))
    op.add_column("invoice_lines", sa.Column("period_start", sa.Date(), nullable=True))
    op.create_foreign_key(
        "fk_invoice_lines_rate_card_id_rate_cards",
        "invoice_lines",
        "rate_cards",
        ["rate_card_id"],
        ["id"],
    )
    op.create_index(RATE_CARD_INDEX, "invoice_lines", ["rate_card_id"])
    op.create_check_constraint(
        LINE_CHECK, "invoice_lines", "(rate_card_id IS NULL) = (period_start IS NULL)"
    )


def downgrade() -> None:
    op.drop_constraint(LINE_CHECK, "invoice_lines", type_="check")
    op.drop_index(RATE_CARD_INDEX, table_name="invoice_lines")
    op.drop_constraint(
        "fk_invoice_lines_rate_card_id_rate_cards", "invoice_lines", type_="foreignkey"
    )
    op.drop_column("invoice_lines", "period_start")
    op.drop_column("invoice_lines", "rate_card_id")
