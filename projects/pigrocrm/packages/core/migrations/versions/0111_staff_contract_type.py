"""staff contract type: what an employee's contract is, for the IRAP deductions

Revision ID: 0111
Revises: 0110

REB-1081. `staff_members` gains `contract_type` (`permanent`, `fixed_term`, `seasonal`),
nullable: `NULL` is «not stated» and no deduction is made for the person. No backfill: nothing
in the existing rows says what a contract is. A CHECK keeps the values to the three, and the
director's column check now names it, so a director (not an employee) cannot hold one.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0111"
down_revision: str | Sequence[str] | None = "0110"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TYPES = ("permanent", "fixed_term", "seasonal")
DIRECTOR_CHECK_OLD = (
    "kind <> 'director' OR (annual_fee IS NOT NULL AND fee_payment IS NOT NULL "
    "AND collective_agreement IS NULL AND contract_level IS NULL "
    "AND gross_annual_pay IS NULL AND monthly_payments IS NULL "
    "AND part_time_percent IS NULL AND employer_inps_rate IS NULL "
    "AND employer_inail_rate IS NULL AND employee_inps_rate IS NULL "
    "AND thirteenth_month IS NULL AND fourteenth_month IS NULL)"
)
DIRECTOR_CHECK_NEW = DIRECTOR_CHECK_OLD.replace(
    "AND employee_inps_rate IS NULL ", "AND employee_inps_rate IS NULL AND contract_type IS NULL "
)


def upgrade() -> None:
    op.add_column("staff_members", sa.Column("contract_type", sa.String(length=10), nullable=True))
    op.create_check_constraint(
        "ck_staff_members_contract_type",
        "staff_members",
        f"contract_type IS NULL OR contract_type IN {TYPES!r}",
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
    op.drop_constraint("ck_staff_members_contract_type", "staff_members", type_="check")
    op.drop_column("staff_members", "contract_type")
