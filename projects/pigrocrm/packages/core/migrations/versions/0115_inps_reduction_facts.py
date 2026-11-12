"""the taxpayer facts of the INPS reductions, and the INAIL rate's third decimal

Revision ID: 0115
Revises: 0114

REB-1097. Three nullable columns on `fiscal_profile` hold the facts the INPS reductions read: that
INPS granted the 50% reduction of a pensioner over 65 (`inps_over_65_reduction`), that INPS
granted the 50% reduction of the IVS rate to a first enrolment (`inps_new_enrolment_reduction`)
and the month of that enrolment (`inps_first_enrolment_month`, the first day of the month, which
counts the 36 months). `NULL` is «not stated» for all three, so nothing is backfilled.

`staff_members.employer_inail_rate` holds a percent and INAIL states the rate per mille with two
decimals (9.75 per mille, «Autoliquidazione 2025/2026»), which is 0.975 percent: the column goes
from `NUMERIC(5, 2)` to `NUMERIC(6, 3)` so that the third decimal is kept. Every stored value
keeps its figure. Going down rounds a third decimal to the cent.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0115"
down_revision: str | Sequence[str] | None = "0114"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "fiscal_profile", sa.Column("inps_over_65_reduction", sa.Boolean(), nullable=True)
    )
    op.add_column(
        "fiscal_profile", sa.Column("inps_new_enrolment_reduction", sa.Boolean(), nullable=True)
    )
    op.add_column(
        "fiscal_profile", sa.Column("inps_first_enrolment_month", sa.Date(), nullable=True)
    )
    op.alter_column(
        "staff_members",
        "employer_inail_rate",
        existing_type=sa.Numeric(5, 2),
        type_=sa.Numeric(6, 3),
        existing_nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "staff_members",
        "employer_inail_rate",
        existing_type=sa.Numeric(6, 3),
        type_=sa.Numeric(5, 2),
        existing_nullable=True,
    )
    op.drop_column("fiscal_profile", "inps_first_enrolment_month")
    op.drop_column("fiscal_profile", "inps_new_enrolment_reduction")
    op.drop_column("fiscal_profile", "inps_over_65_reduction")
