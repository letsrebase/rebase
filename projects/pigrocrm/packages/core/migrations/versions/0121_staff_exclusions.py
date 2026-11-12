"""staff exclusions: apprentices, training contracts and disabled workers

Revision ID: 0121
Revises: 0120

REB-1091. `staff_members.contract_type` also takes `apprentice` and `training` (a contratto di
formazione lavoro), and the table gains `disabled_worker` (art. 1 L. 68/1999 or art. 5
L. 482/1968), nullable: `NULL` is «not stated». Art. 11 c. 4-bis.1 D.Lgs. 446/1997 leaves these
people out of the five-employee count and c. 1 lett. a n. 5 deducts their cost in full. No
backfill: nothing in the existing rows says who they are. The director's column check now names
the new column, so a director cannot hold it.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0121"
down_revision: str | Sequence[str] | None = "0120"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TYPES_OLD = ("permanent", "fixed_term", "seasonal")
TYPES_NEW = ("permanent", "fixed_term", "seasonal", "apprentice", "training")
DIRECTOR_CHECK_OLD = (
    "kind <> 'director' OR (annual_fee IS NOT NULL AND fee_payment IS NOT NULL "
    "AND collective_agreement IS NULL AND contract_level IS NULL "
    "AND gross_annual_pay IS NULL AND monthly_payments IS NULL "
    "AND part_time_percent IS NULL AND employer_inps_rate IS NULL "
    "AND employer_inail_rate IS NULL AND employee_inps_rate IS NULL "
    "AND contract_type IS NULL "
    "AND thirteenth_month IS NULL AND fourteenth_month IS NULL)"
)
DIRECTOR_CHECK_NEW = DIRECTOR_CHECK_OLD.replace(
    "AND contract_type IS NULL ", "AND contract_type IS NULL AND disabled_worker IS NULL "
)


def _types(types: tuple[str, ...]) -> str:
    return f"contract_type IS NULL OR contract_type IN {types!r}"


def upgrade() -> None:
    op.add_column("staff_members", sa.Column("disabled_worker", sa.Boolean(), nullable=True))
    op.drop_constraint("ck_staff_members_contract_type", "staff_members", type_="check")
    op.create_check_constraint("ck_staff_members_contract_type", "staff_members", _types(TYPES_NEW))
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
    op.execute(
        "UPDATE staff_members SET contract_type = NULL "
        "WHERE contract_type IN ('apprentice', 'training')"
    )
    op.create_check_constraint("ck_staff_members_contract_type", "staff_members", _types(TYPES_OLD))
    op.drop_column("staff_members", "disabled_worker")
