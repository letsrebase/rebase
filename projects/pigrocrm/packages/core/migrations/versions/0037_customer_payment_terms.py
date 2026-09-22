"""a customer carries its payment terms: giorni_pagamento and pagamento_fine_mese

Revision ID: 0037
Revises: 0036

REB-326. Until now every issued invoice was due «emission plus the fiscal profile's
days», one number for every customer, and «30 giorni data fattura fine mese» agreed
with a customer had nowhere to live but its notes. Two columns on `customers`: the
days (null: the profile's apply) and the end-of-month slide, NOT NULL with a server
default of false so every existing row reads "no" rather than "unknown".
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0037"
down_revision: str | Sequence[str] | None = "0036"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("customers", sa.Column("giorni_pagamento", sa.Integer(), nullable=True))
    op.add_column(
        "customers",
        sa.Column(
            "pagamento_fine_mese",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("customers", "pagamento_fine_mese")
    op.drop_column("customers", "giorni_pagamento")
