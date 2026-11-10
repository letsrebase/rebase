"""invoices: the purchase order an invoice answers

Revision ID: 0050
Revises: 0049

Four nullable columns and one CHECK, for REB-693. A customer whose own client is a public
body sends a purchase order carrying a CIG, and needs both on the invoice in the place
FPR12 has for them, `DatiOrdineAcquisto`. Until now the only place was the free text of
`causale`.

`ordine_numero` is `IdDocumento` (String20Type), `ordine_data` is `Data`, `ordine_cig`
and `ordine_cup` are `CodiceCIG` and `CodiceCUP` (String15Type). One order per invoice,
for the whole document. `ck_invoices_ordine_needs_numero` refuses a date, a CIG or a CUP
without the number, which is the one mandatory child of the block.

No backfill: nothing here can tell which order an old invoice answered, and writing one
onto a frozen fiscal document would state a fact nobody stated. The downgrade drops the
CHECK and the four columns.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0050"
down_revision: str | Sequence[str] | None = "0049"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("invoices", sa.Column("ordine_numero", sa.String(length=20), nullable=True))
    op.add_column("invoices", sa.Column("ordine_data", sa.Date(), nullable=True))
    op.add_column("invoices", sa.Column("ordine_cig", sa.String(length=15), nullable=True))
    op.add_column("invoices", sa.Column("ordine_cup", sa.String(length=15), nullable=True))
    op.create_check_constraint(
        "ck_invoices_ordine_needs_numero",
        "invoices",
        "ordine_numero IS NOT NULL "
        "OR (ordine_data IS NULL AND ordine_cig IS NULL AND ordine_cup IS NULL)",
    )


def downgrade() -> None:
    op.drop_constraint("ck_invoices_ordine_needs_numero", "invoices", type_="check")
    op.drop_column("invoices", "ordine_cup")
    op.drop_column("invoices", "ordine_cig")
    op.drop_column("invoices", "ordine_data")
    op.drop_column("invoices", "ordine_numero")
