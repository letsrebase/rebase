"""sales: net receivable, withholding and split payment on invoices

Revision ID: 0089
Revises: 0088

REB-994, § 3 and «Money net of withholding and split payment» of
`2026-11-09-suppliers-purchases-and-iva-design.md`.

`invoices` gains `net_receivable` (what the customer pays: the total minus the withholding it
keeps and, on a split payment, the VAT it pays to the tax office itself), `withholding_amount`
and `withholding_rate`, and `split_payment`. Every existing row is backfilled with what it
was: no withholding, no split payment, and a net equal to its total. One check: the
withholding is never negative.

`downgrade` drops the columns; an invoice with a withholding or a split payment keeps its total
and loses the amounts that made its net differ from it.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0089"
down_revision: str | Sequence[str] | None = "0088"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INVOICES = "invoices"
WITHHOLDING_CHECK = "ck_invoices_withholding_not_negative"


def upgrade() -> None:
    op.add_column(INVOICES, sa.Column("net_receivable", sa.Numeric(12, 2), nullable=True))
    op.execute(f"UPDATE {INVOICES} SET net_receivable = total")
    op.alter_column(INVOICES, "net_receivable", nullable=False)
    op.add_column(
        INVOICES,
        sa.Column(
            "withholding_amount",
            sa.Numeric(12, 2),
            nullable=False,
            server_default=sa.text("0.00"),
        ),
    )
    op.add_column(INVOICES, sa.Column("withholding_rate", sa.Numeric(5, 2), nullable=True))
    op.add_column(
        INVOICES,
        sa.Column("split_payment", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    # The defaults above only backfilled the existing rows: the application writes both on
    # each row it creates.
    for column in ("withholding_amount", "split_payment"):
        op.alter_column(INVOICES, column, server_default=None)
    op.create_check_constraint(WITHHOLDING_CHECK, INVOICES, "withholding_amount >= 0")


def downgrade() -> None:
    op.drop_constraint(WITHHOLDING_CHECK, INVOICES, type_="check")
    for column in ("split_payment", "withholding_rate", "withholding_amount", "net_receivable"):
        op.drop_column(INVOICES, column)
