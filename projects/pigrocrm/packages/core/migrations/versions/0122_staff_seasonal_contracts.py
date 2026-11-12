"""staff seasonal contracts: the contract history for the 70% IRAP deduction

Revision ID: 0122
Revises: 0121

REB-1090. `staff_members` gains `seasonal_contracts` (JSONB, nullable): a list of
`{start_date, end_date, worked_days}`, one entry per contract of a seasonal worker with the
company, `worked_days` being the days of effective work by tax year. Art. 11 c. 4-octies
D.Lgs. 446/1997 deducts 70% of the cost of a seasonal worker from the second contract on, within
two years of the end of the previous one, when the worker was employed at least 120 days in two tax
periods: the rule reads this history. `NULL` is «not stated» and no deduction is made. No
backfill: nothing in the existing rows says what a contract was. The director's column check now
names the column, so a director cannot hold one.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0122"
down_revision: str | Sequence[str] | None = "0121"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DIRECTOR_CHECK_OLD = (
    "kind <> 'director' OR (annual_fee IS NOT NULL AND fee_payment IS NOT NULL "
    "AND collective_agreement IS NULL AND contract_level IS NULL "
    "AND gross_annual_pay IS NULL AND monthly_payments IS NULL "
    "AND part_time_percent IS NULL AND employer_inps_rate IS NULL "
    "AND employer_inail_rate IS NULL AND employee_inps_rate IS NULL "
    "AND contract_type IS NULL AND disabled_worker IS NULL "
    "AND thirteenth_month IS NULL AND fourteenth_month IS NULL)"
)
DIRECTOR_CHECK_NEW = DIRECTOR_CHECK_OLD.replace(
    "AND disabled_worker IS NULL ", "AND disabled_worker IS NULL AND seasonal_contracts IS NULL "
)


def upgrade() -> None:
    op.add_column(
        "staff_members",
        sa.Column("seasonal_contracts", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.drop_constraint("ck_staff_members_director_columns", "staff_members", type_="check")
    op.create_check_constraint(
        "ck_staff_members_director_columns", "staff_members", DIRECTOR_CHECK_NEW
    )


def downgrade() -> None:
    op.drop_constraint("ck_staff_members_director_columns", "staff_members", type_="check")
    op.create_check_constraint(
        "ck_staff_members_director_columns", "staff_members", DIRECTOR_CHECK_OLD
    )
    op.drop_column("staff_members", "seasonal_contracts")
