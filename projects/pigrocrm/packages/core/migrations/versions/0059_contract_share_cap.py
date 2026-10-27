"""contracts: a cap on one customer's share of the azienda's revenue

Revision ID: 0059
Revises: 0058

REB-761. One nullable column, `tetto_quota_cliente`, a percentage (`50.00`) strictly
between 0 and 100: the largest share of the azienda's invoiced revenue the contract's
customer may be over the contract's own anniversary year. `NULL` is no cap, which is what
every existing contract keeps, so nothing is backfilled. The CHECK refuses 0 (which
forbids the customer) and 100 (which never binds).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0059"
down_revision: str | Sequence[str] | None = "0058"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "contracts",
        sa.Column("tetto_quota_cliente", sa.Numeric(precision=5, scale=2), nullable=True),
    )
    op.create_check_constraint(
        "ck_contracts_tetto_quota_cliente_range",
        "contracts",
        "tetto_quota_cliente IS NULL OR (tetto_quota_cliente > 0 AND tetto_quota_cliente < 100)",
    )


def downgrade() -> None:
    op.drop_constraint("ck_contracts_tetto_quota_cliente_range", "contracts", type_="check")
    op.drop_column("contracts", "tetto_quota_cliente")
