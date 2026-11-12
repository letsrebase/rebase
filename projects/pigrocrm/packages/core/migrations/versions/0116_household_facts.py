"""the household the taxpayer states, on the fiscal profile

Revision ID: 0116
Revises: 0115

REB-1087. One nullable JSONB column, `fiscal_profile.household`: the people fiscally dependent on
the taxpayer for one tax year (`fiscal/household.py::Household`), which the IRPEF's family
detractions (art. 12 TUIR) and the regions' household rules read. `NULL` is «not stated»: the
estimate then applies none of them and says so, so no row needs a backfill.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0116"
down_revision: str | Sequence[str] | None = "0115"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "fiscal_profile",
        sa.Column("household", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("fiscal_profile", "household")
