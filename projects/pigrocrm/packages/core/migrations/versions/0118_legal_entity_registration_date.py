"""the day a legal entity applied for registration, and the period of an F24 line

Revision ID: 0118
Revises: 0117

REB-1100. `legal_entities.registration_date` is the date of the application for registration in
the companies register. The annual chamber fee of that year is a fixed amount due within 30 days
of it (D.I. 21 April 2011 art. 4 c. 2), not the turnover-based fee of 30 June. Null for every
row that exists: nobody told the product, so every company is read as an older one.

`f24_group_items.reference_period` is the month of a withholding line (`MM`, the `mese rif.`
column) or of a payroll contribution (`MM/AAAA`, the INPS section's «periodo da»). Null for every
line that exists: none of them was a line with a period.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0118"
down_revision: str | Sequence[str] | None = "0117"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("legal_entities", sa.Column("registration_date", sa.Date(), nullable=True))
    op.add_column(
        "f24_group_items", sa.Column("reference_period", sa.String(length=7), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("f24_group_items", "reference_period")
    op.drop_column("legal_entities", "registration_date")
