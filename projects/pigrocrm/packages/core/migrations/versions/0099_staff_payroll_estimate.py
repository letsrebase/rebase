"""staff payroll estimate: the inputs it needs, accepted payslips, and a staff counterparty

Revision ID: 0099
Revises: 0088

REB-1008 (spec 2026-11-09, staff, payroll and expenses, § 2). Three changes, no backfill.

`staff_members` gains `employee_inps_rate` (the employee's own INPS share, typed because no
primary source states one rate for every employer), `thirteenth_month` and `fourteenth_month`
(the months the extra payments are paid in, which the collective agreement decides, so they are
typed), each nullable: «not stated» is never a default. The director's column check names them
so a director cannot hold one, and two checks keep a month in 1..12 and a 14th payment to a
14-payment contract.

`staff_payslips` holds the figures of an accepted payslip (gross, net, income tax withheld,
for one month of one staff member), the source of the two ratios the estimate reads and of the
month's real cost. It carries the `legal_entity_scope` policy, `scope_all()` first, with `FORCE
ROW LEVEL SECURITY`, as the staff tables do (0079); `statements()` is what the test template
applies after `create_all`.

`due_items` gains `staff_member_id` (nullable, indexed) and the counterparty type
`staff_member`: the net pay and the 13th and 14th payments are owed to a person. The two checks
that name the counterparty are replaced by ones that know the third kind.

`downgrade` puts the checks back, drops the column and the table, and removes the staff
columns. A `staff_member` due item cannot survive it: they are deleted first, with their
settlements.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0099"
down_revision: str | Sequence[str] | None = "0088"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PAYSLIPS = "staff_payslips"
POLICY_EXPRESSION = "scope_all() OR legal_entity_visible(legal_entity_id)"

DIRECTOR_CHECK_OLD = (
    "kind <> 'director' OR (annual_fee IS NOT NULL AND fee_payment IS NOT NULL "
    "AND collective_agreement IS NULL AND contract_level IS NULL "
    "AND gross_annual_pay IS NULL AND monthly_payments IS NULL "
    "AND part_time_percent IS NULL AND employer_inps_rate IS NULL "
    "AND employer_inail_rate IS NULL)"
)
DIRECTOR_CHECK_NEW = (
    "kind <> 'director' OR (annual_fee IS NOT NULL AND fee_payment IS NOT NULL "
    "AND collective_agreement IS NULL AND contract_level IS NULL "
    "AND gross_annual_pay IS NULL AND monthly_payments IS NULL "
    "AND part_time_percent IS NULL AND employer_inps_rate IS NULL "
    "AND employer_inail_rate IS NULL AND employee_inps_rate IS NULL "
    "AND thirteenth_month IS NULL AND fourteenth_month IS NULL)"
)
COUNTERPARTY_TYPE_OLD = "counterparty_type IN ('organization', 'tax_office')"
COUNTERPARTY_TYPE_NEW = "counterparty_type IN ('organization', 'tax_office', 'staff_member')"
COUNTERPARTY_COLUMNS_OLD = (
    "(counterparty_type = 'organization' AND organization_id IS NOT NULL "
    "AND tax IS NULL) OR "
    "(counterparty_type = 'tax_office' AND tax IS NOT NULL AND organization_id IS NULL)"
)
COUNTERPARTY_COLUMNS_NEW = (
    "(counterparty_type = 'organization' AND organization_id IS NOT NULL "
    "AND tax IS NULL AND staff_member_id IS NULL) OR "
    "(counterparty_type = 'tax_office' AND tax IS NOT NULL AND organization_id IS NULL "
    "AND staff_member_id IS NULL) OR "
    "(counterparty_type = 'staff_member' AND staff_member_id IS NOT NULL "
    "AND organization_id IS NULL AND tax IS NULL)"
)


def statements() -> list[str]:
    """The row-level security of `staff_payslips`, applied after `create_all` by the test
    template (`projects/pigrocrm/conftest.py`) exactly as `upgrade` applies it."""
    return [
        f"CREATE POLICY legal_entity_scope ON {PAYSLIPS} FOR ALL "
        f"USING ({POLICY_EXPRESSION}) WITH CHECK ({POLICY_EXPRESSION})",
        f"ALTER TABLE {PAYSLIPS} ENABLE ROW LEVEL SECURITY",
        f"ALTER TABLE {PAYSLIPS} FORCE ROW LEVEL SECURITY",
    ]


def upgrade() -> None:
    op.add_column("staff_members", sa.Column("employee_inps_rate", sa.Numeric(5, 2), nullable=True))
    op.add_column("staff_members", sa.Column("thirteenth_month", sa.Integer(), nullable=True))
    op.add_column("staff_members", sa.Column("fourteenth_month", sa.Integer(), nullable=True))
    op.create_check_constraint(
        "ck_staff_members_employee_inps_rate",
        "staff_members",
        "employee_inps_rate IS NULL OR (employee_inps_rate >= 0 AND employee_inps_rate <= 100)",
    )
    op.create_check_constraint(
        "ck_staff_members_thirteenth_month",
        "staff_members",
        "thirteenth_month IS NULL OR thirteenth_month BETWEEN 1 AND 12",
    )
    op.create_check_constraint(
        "ck_staff_members_fourteenth_month",
        "staff_members",
        "fourteenth_month IS NULL OR fourteenth_month BETWEEN 1 AND 12",
    )
    op.create_check_constraint(
        "ck_staff_members_fourteenth",
        "staff_members",
        "fourteenth_month IS NULL OR monthly_payments = 14",
    )
    op.drop_constraint("ck_staff_members_director_columns", "staff_members", type_="check")
    op.create_check_constraint(
        "ck_staff_members_director_columns", "staff_members", DIRECTOR_CHECK_NEW
    )

    op.create_table(
        PAYSLIPS,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("staff_member_id", sa.Uuid(), nullable=False),
        sa.Column("legal_entity_id", sa.Uuid(), nullable=False),
        sa.Column("period", sa.Date(), nullable=False),
        sa.Column("gross", sa.Numeric(12, 2), nullable=False),
        sa.Column("net", sa.Numeric(12, 2), nullable=False),
        sa.Column("withholding", sa.Numeric(12, 2), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["staff_member_id"], ["staff_members.id"]),
        sa.ForeignKeyConstraint(["legal_entity_id"], ["legal_entities.id"]),
        sa.UniqueConstraint("staff_member_id", "period", name="uq_staff_payslips_member_period"),
        sa.CheckConstraint("gross > 0", name="ck_staff_payslips_gross"),
        sa.CheckConstraint("net >= 0 AND net <= gross", name="ck_staff_payslips_net"),
        sa.CheckConstraint(
            "withholding >= 0 AND withholding <= gross", name="ck_staff_payslips_withholding"
        ),
        sa.CheckConstraint("net + withholding <= gross", name="ck_staff_payslips_parts"),
        sa.CheckConstraint("extract(day FROM period) = 1", name="ck_staff_payslips_period"),
    )
    op.create_index("ix_staff_payslips_legal_entity_id", PAYSLIPS, ["legal_entity_id"])
    for statement in statements():
        op.execute(statement)

    op.add_column("due_items", sa.Column("staff_member_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_due_items_staff_member_id", "due_items", "staff_members", ["staff_member_id"], ["id"]
    )
    op.create_index("ix_due_items_staff_member_id", "due_items", ["staff_member_id"])
    op.drop_constraint("ck_due_items_counterparty_type", "due_items", type_="check")
    op.drop_constraint("ck_due_items_counterparty_columns", "due_items", type_="check")
    op.create_check_constraint("ck_due_items_counterparty_type", "due_items", COUNTERPARTY_TYPE_NEW)
    op.create_check_constraint(
        "ck_due_items_counterparty_columns", "due_items", COUNTERPARTY_COLUMNS_NEW
    )


def downgrade() -> None:
    # A due item owed to a person has nowhere to go in the old shape: it goes, with its
    # settlements, and so does any item whose dependents it would leave dangling (none: the
    # payroll source writes fixed rules only).
    op.execute(
        "DELETE FROM due_item_settlements WHERE due_item_id IN "
        "(SELECT id FROM due_items WHERE counterparty_type = 'staff_member')"
    )
    op.execute("DELETE FROM due_items WHERE counterparty_type = 'staff_member'")
    op.drop_constraint("ck_due_items_counterparty_columns", "due_items", type_="check")
    op.drop_constraint("ck_due_items_counterparty_type", "due_items", type_="check")
    op.create_check_constraint("ck_due_items_counterparty_type", "due_items", COUNTERPARTY_TYPE_OLD)
    op.create_check_constraint(
        "ck_due_items_counterparty_columns", "due_items", COUNTERPARTY_COLUMNS_OLD
    )
    op.drop_index("ix_due_items_staff_member_id", table_name="due_items")
    op.drop_constraint("fk_due_items_staff_member_id", "due_items", type_="foreignkey")
    op.drop_column("due_items", "staff_member_id")

    op.drop_table(PAYSLIPS)

    op.drop_constraint("ck_staff_members_director_columns", "staff_members", type_="check")
    op.create_check_constraint(
        "ck_staff_members_director_columns", "staff_members", DIRECTOR_CHECK_OLD
    )
    for name in (
        "ck_staff_members_fourteenth",
        "ck_staff_members_fourteenth_month",
        "ck_staff_members_thirteenth_month",
        "ck_staff_members_employee_inps_rate",
    ):
        op.drop_constraint(name, "staff_members", type_="check")
    op.drop_column("staff_members", "fourteenth_month")
    op.drop_column("staff_members", "thirteenth_month")
    op.drop_column("staff_members", "employee_inps_rate")
