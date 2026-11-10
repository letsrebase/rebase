"""engagement_monthly_reports: the monthly «Rendiconto» of an engagement as an approval request

Revision ID: 0099
Revises: 0094

REB-1002, § 4 of `2026-11-09-collaborators-in-the-crm-design.md` («What makes a day payable»).

One row per engagement and month: the day rebase received the freelancer's report, the tacit
date (the seventh working day after it, framework 6.2), who approved it and when, and the days
the client contested. It copies no day: the report stays live in the freelancer's space. The row
carries the freelancer's organization like `engagements` does, so the same row-level policy
(`legal_entity_scope`, forced) hides it from a scoped admin who does not see the freelancer.

`downgrade` drops the table; the approvals recorded in it are lost with it.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0099"
down_revision: str | Sequence[str] | None = "0094"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "engagement_monthly_reports"
POLICY_EXPRESSION = "scope_all() OR organization_visible(freelancer_id)"


def statements() -> list[str]:
    """The row-level security of `engagement_monthly_reports`, applied after `create_all` by the
    test template (`projects/pigrocrm/conftest.py`) exactly as `upgrade` applies it."""
    return [
        f"CREATE POLICY legal_entity_scope ON {TABLE} FOR ALL "
        f"USING ({POLICY_EXPRESSION}) WITH CHECK ({POLICY_EXPRESSION})",
        f"ALTER TABLE {TABLE} ENABLE ROW LEVEL SECURITY",
        f"ALTER TABLE {TABLE} FORCE ROW LEVEL SECURITY",
    ]


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("engagement_id", sa.Uuid(), nullable=False),
        sa.Column("freelancer_id", sa.Uuid(), nullable=False),
        sa.Column("month", sa.Date(), nullable=False),
        sa.Column(
            "state", sa.String(length=20), server_default=sa.text("'requested'"), nullable=False
        ),
        sa.Column("received_on", sa.Date(), nullable=False),
        sa.Column("tacit_on", sa.Date(), nullable=False),
        sa.Column("decided_on", sa.Date(), nullable=True),
        sa.Column("decided_by", sa.Uuid(), nullable=True),
        sa.Column("objection", sa.Text(), nullable=True),
        sa.Column(
            "held_dates",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["engagement_id"], ["engagements.id"]),
        sa.ForeignKeyConstraint(["freelancer_id"], ["organizations.id"]),
        sa.CheckConstraint(
            "state IN ('requested', 'approved')", name="ck_engagement_monthly_reports_state"
        ),
        sa.CheckConstraint(
            "EXTRACT(DAY FROM month) = 1", name="ck_engagement_monthly_reports_month_first_day"
        ),
        sa.CheckConstraint(
            "(state = 'approved') = (decided_on IS NOT NULL)",
            name="ck_engagement_monthly_reports_decided",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(held_dates) = 'array'", name="ck_engagement_monthly_reports_held_array"
        ),
        sa.CheckConstraint(
            "jsonb_array_length(held_dates) = 0 OR objection IS NOT NULL",
            name="ck_engagement_monthly_reports_hold_has_reason",
        ),
    )
    op.create_index(
        "uq_engagement_monthly_reports_month", TABLE, ["engagement_id", "month"], unique=True
    )
    op.create_index("ix_engagement_monthly_reports_engagement_id", TABLE, ["engagement_id"])
    op.create_index("ix_engagement_monthly_reports_freelancer_id", TABLE, ["freelancer_id"])
    for statement in statements():
        op.execute(statement)


def downgrade() -> None:
    op.drop_table(TABLE)
