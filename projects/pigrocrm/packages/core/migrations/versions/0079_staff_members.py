"""staff_members and payroll_settings: employees and directors with their pay inputs

Revision ID: 0079
Revises: 0078

REB-977 (spec 2026-11-09, staff, payroll and expenses, § 1 and § 4). Two new tables, no
change to an existing one and no backfill.

`staff_members` holds an employee or a director of one legal entity with the inputs of the
payroll estimate (the gross annual pay, the number of monthly payments, the part-time share
and the employer's INPS and INAIL rates; a director's annual fee, how it is paid, the TFM and
the INPS split), their working hours a month, an optional link to a user (at most one staff
member per user, a partial unique index) and an optional pay day override. The kind decides
which columns are used, and the check constraints say so. `payroll_settings` holds the pay day
of a legal entity, one row each.

Both tables carry the `legal_entity_scope` policy, `scope_all()` first, with `FORCE ROW
LEVEL SECURITY`, as every table that reaches a legal entity does (0048, 0068, 0070, 0073,
0076); `statements()` is what the test template applies after `create_all`. That policy
filters by legal entity only: who may read pay (administrators, and agents with
`full_access`) is a check in `StaffService`. Grants need nothing: the application role has
default privileges on what the owner creates, and the boot's `ensure-space-defaults`
re-grants on every table of every space.

`downgrade` drops both tables, the people with them.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0079"
down_revision: str | Sequence[str] | None = "0078"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

STAFF = "staff_members"
SETTINGS = "payroll_settings"
# The «all» fast path first, as every policy since 0049 has it.
POLICY_EXPRESSION = "scope_all() OR legal_entity_visible(legal_entity_id)"


def statements() -> list[str]:
    """The row-level security of both tables, applied after `create_all` by the test template
    (`projects/pigrocrm/conftest.py`) exactly as `upgrade` applies it."""
    out: list[str] = []
    for table in (STAFF, SETTINGS):
        out += [
            f"CREATE POLICY legal_entity_scope ON {table} FOR ALL "
            f"USING ({POLICY_EXPRESSION}) WITH CHECK ({POLICY_EXPRESSION})",
            f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY",
            f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY",
        ]
    return out


def _timestamps() -> list[sa.Column[sa.DateTime]]:
    return [
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    ]


def upgrade() -> None:
    op.create_table(
        STAFF,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("legal_entity_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=10), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("hours_per_month", sa.Numeric(6, 2), nullable=True),
        sa.Column("pay_day", sa.Integer(), nullable=True),
        sa.Column("pay_month_offset", sa.Integer(), nullable=True),
        sa.Column("collective_agreement", sa.String(length=120), nullable=True),
        sa.Column("contract_level", sa.String(length=40), nullable=True),
        sa.Column("gross_annual_pay", sa.Numeric(12, 2), nullable=True),
        sa.Column("monthly_payments", sa.Integer(), nullable=True),
        sa.Column("part_time_percent", sa.Numeric(5, 2), nullable=True),
        sa.Column("employer_inps_rate", sa.Numeric(5, 2), nullable=True),
        sa.Column("employer_inail_rate", sa.Numeric(5, 2), nullable=True),
        sa.Column("annual_fee", sa.Numeric(12, 2), nullable=True),
        sa.Column("fee_payment", sa.String(length=10), nullable=True),
        sa.Column("accrues_tfm", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("tfm_annual_amount", sa.Numeric(12, 2), nullable=True),
        sa.Column("inps_company_share", sa.Numeric(5, 2), nullable=True),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["legal_entity_id"], ["legal_entities.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.CheckConstraint("kind IN ('employee', 'director')", name="ck_staff_members_kind"),
        sa.CheckConstraint(
            "end_date IS NULL OR end_date >= start_date", name="ck_staff_members_period"
        ),
        sa.CheckConstraint(
            "hours_per_month IS NULL OR (hours_per_month > 0 AND hours_per_month <= 744)",
            name="ck_staff_members_hours",
        ),
        sa.CheckConstraint(
            "(pay_day IS NULL) = (pay_month_offset IS NULL)", name="ck_staff_members_pay_day_pair"
        ),
        sa.CheckConstraint(
            "pay_day IS NULL OR pay_day BETWEEN 1 AND 28", name="ck_staff_members_pay_day_range"
        ),
        sa.CheckConstraint(
            "pay_month_offset IS NULL OR pay_month_offset IN (0, 1)",
            name="ck_staff_members_pay_month_offset",
        ),
        sa.CheckConstraint(
            "kind <> 'employee' OR (gross_annual_pay IS NOT NULL "
            "AND monthly_payments IS NOT NULL AND part_time_percent IS NOT NULL "
            "AND annual_fee IS NULL AND fee_payment IS NULL AND accrues_tfm = false "
            "AND tfm_annual_amount IS NULL AND inps_company_share IS NULL)",
            name="ck_staff_members_employee_columns",
        ),
        sa.CheckConstraint(
            "kind <> 'director' OR (annual_fee IS NOT NULL AND fee_payment IS NOT NULL "
            "AND collective_agreement IS NULL AND contract_level IS NULL "
            "AND gross_annual_pay IS NULL AND monthly_payments IS NULL "
            "AND part_time_percent IS NULL AND employer_inps_rate IS NULL "
            "AND employer_inail_rate IS NULL)",
            name="ck_staff_members_director_columns",
        ),
        sa.CheckConstraint(
            "gross_annual_pay IS NULL OR gross_annual_pay > 0", name="ck_staff_members_gross_pay"
        ),
        sa.CheckConstraint("annual_fee IS NULL OR annual_fee > 0", name="ck_staff_members_fee"),
        sa.CheckConstraint(
            "monthly_payments IS NULL OR monthly_payments IN (13, 14)",
            name="ck_staff_members_monthly_payments",
        ),
        sa.CheckConstraint(
            "part_time_percent IS NULL OR (part_time_percent > 0 AND part_time_percent <= 100)",
            name="ck_staff_members_part_time",
        ),
        sa.CheckConstraint(
            "employer_inps_rate IS NULL OR (employer_inps_rate >= 0 AND employer_inps_rate <= 100)",
            name="ck_staff_members_inps_rate",
        ),
        sa.CheckConstraint(
            "employer_inail_rate IS NULL "
            "OR (employer_inail_rate >= 0 AND employer_inail_rate <= 100)",
            name="ck_staff_members_inail_rate",
        ),
        sa.CheckConstraint(
            "fee_payment IS NULL OR fee_payment IN ('monthly', 'other')",
            name="ck_staff_members_fee_payment",
        ),
        sa.CheckConstraint(
            "(accrues_tfm AND tfm_annual_amount IS NOT NULL AND tfm_annual_amount > 0) "
            "OR (NOT accrues_tfm AND tfm_annual_amount IS NULL)",
            name="ck_staff_members_tfm",
        ),
        sa.CheckConstraint(
            "inps_company_share IS NULL OR (inps_company_share >= 0 AND inps_company_share <= 100)",
            name="ck_staff_members_inps_share",
        ),
    )
    op.create_index("ix_staff_members_legal_entity_id", STAFF, ["legal_entity_id"])
    op.create_index(
        "uq_staff_members_user_id",
        STAFF,
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("user_id IS NOT NULL"),
    )
    op.create_table(
        SETTINGS,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("legal_entity_id", sa.Uuid(), nullable=False),
        sa.Column("pay_day", sa.Integer(), nullable=False),
        sa.Column("pay_month_offset", sa.Integer(), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["legal_entity_id"], ["legal_entities.id"]),
        sa.UniqueConstraint("legal_entity_id", name="uq_payroll_settings_legal_entity_id"),
        sa.CheckConstraint("pay_day BETWEEN 1 AND 28", name="ck_payroll_settings_pay_day"),
        sa.CheckConstraint(
            "pay_month_offset IN (0, 1)", name="ck_payroll_settings_pay_month_offset"
        ),
    )
    for statement in statements():
        op.execute(statement)


def downgrade() -> None:
    op.drop_table(SETTINGS)
    op.drop_table(STAFF)
