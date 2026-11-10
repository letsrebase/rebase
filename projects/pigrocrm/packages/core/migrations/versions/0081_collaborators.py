"""collaborators: the hub freelancer id on organizations, and the engagements table

Revision ID: 0081
Revises: 0080

REB-976, § 1 and § 3 of `2026-11-09-collaborators-in-the-crm-design.md`.

On `organizations`: `hub_freelancer_id`, the freelancer's identity (the hub's card id), unique
so that a freelancer who changes email is still one organization, and a check that only an
organization with the role `freelancer` carries one. Nothing is backfilled: every existing row
has none.

The new table `engagements` is the CRM's name for the hub's match: the freelancer, the client
(and the deal or contract it serves, both optional), the role and dates, `days_per_week` (null
until filled, which availability reads as «sconosciuta»), the fee and its unit, the letter
type, the payment rule with its fields (the checks keep a fixed term and a collection wait
apart, and put the 60-day limit in the schema) and the state. `hub_match_id` is unique: the
import's idempotency key. The table carries no legal entity of its own: its row-level policy
(`legal_entity_scope`, forced) reads the freelancer's organization through `organization_visible`,
so a scoped admin sees the engagements of the freelancers they see.

`downgrade` drops the table and the column; an engagement is lost with it.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0081"
down_revision: str | Sequence[str] | None = "0080"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ORGANIZATIONS = "organizations"
ENGAGEMENTS = "engagements"
HUB_INDEX = "uq_organizations_hub_freelancer_id"
HUB_CHECK = "ck_organizations_hub_freelancer_role"


# An engagement is visible where its freelancer's organization is: the «all» fast path first,
# then the parent's own row policy through `organization_visible`.
POLICY_EXPRESSION = "scope_all() OR organization_visible(freelancer_id)"


def statements() -> list[str]:
    """The row-level security of `engagements`, applied after `create_all` by the test template
    (`projects/pigrocrm/conftest.py`) exactly as `upgrade` applies it."""
    return [
        f"CREATE POLICY legal_entity_scope ON {ENGAGEMENTS} FOR ALL "
        f"USING ({POLICY_EXPRESSION}) WITH CHECK ({POLICY_EXPRESSION})",
        f"ALTER TABLE {ENGAGEMENTS} ENABLE ROW LEVEL SECURITY",
        f"ALTER TABLE {ENGAGEMENTS} FORCE ROW LEVEL SECURITY",
    ]


def upgrade() -> None:
    op.add_column(ORGANIZATIONS, sa.Column("hub_freelancer_id", sa.Uuid(), nullable=True))
    op.create_index(HUB_INDEX, ORGANIZATIONS, ["hub_freelancer_id"], unique=True)
    op.create_check_constraint(
        HUB_CHECK, ORGANIZATIONS, "hub_freelancer_id IS NULL OR 'freelancer' = ANY(roles)"
    )

    op.create_table(
        ENGAGEMENTS,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("hub_match_id", sa.Uuid(), nullable=True),
        sa.Column("freelancer_id", sa.Uuid(), nullable=False),
        sa.Column("client_organization_id", sa.Uuid(), nullable=True),
        sa.Column("deal_id", sa.Uuid(), nullable=True),
        sa.Column("contract_id", sa.Uuid(), nullable=True),
        sa.Column("role", sa.String(length=200), nullable=False),
        sa.Column("starts_on", sa.Date(), nullable=False),
        sa.Column("ends_on", sa.Date(), nullable=True),
        sa.Column("days_per_week", sa.Numeric(2, 1), nullable=True),
        sa.Column("fee", sa.Numeric(12, 2), nullable=False),
        sa.Column("fee_unit", sa.String(length=10), nullable=False),
        sa.Column("expected_days", sa.Numeric(8, 2), nullable=True),
        sa.Column(
            "letter_type",
            sa.String(length=20),
            server_default=sa.text("'ordinary'"),
            nullable=False,
        ),
        sa.Column("payment_rule", sa.String(length=20), nullable=False),
        sa.Column("payment_days", sa.Integer(), nullable=True),
        sa.Column(
            "payment_end_of_month", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column("collection_days", sa.Integer(), nullable=True),
        sa.Column("state", sa.String(length=20), server_default=sa.text("'draft'"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["freelancer_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["client_organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["deal_id"], ["deals.id"]),
        sa.ForeignKeyConstraint(["contract_id"], ["contracts.id"]),
        sa.CheckConstraint(
            "state IN ('draft', 'awaiting_signature', 'active', 'concluded', 'cancelled')",
            name="ck_engagements_state",
        ),
        sa.CheckConstraint("fee_unit IN ('day', 'hour', 'flat')", name="ck_engagements_fee_unit"),
        sa.CheckConstraint(
            "letter_type IN ('ordinary', 'occasional')", name="ck_engagements_letter_type"
        ),
        sa.CheckConstraint(
            "payment_rule IN ('fixed_term', 'on_collection')", name="ck_engagements_payment_rule"
        ),
        sa.CheckConstraint("fee > 0", name="ck_engagements_fee_positive"),
        sa.CheckConstraint(
            "expected_days IS NULL OR expected_days > 0", name="ck_engagements_expected_days"
        ),
        sa.CheckConstraint(
            "ends_on IS NULL OR ends_on >= starts_on", name="ck_engagements_dates_in_order"
        ),
        sa.CheckConstraint(
            "days_per_week IS NULL OR (days_per_week BETWEEN 0.5 AND 5 "
            "AND mod(days_per_week * 2, 1) = 0)",
            name="ck_engagements_days_per_week",
        ),
        sa.CheckConstraint(
            "(payment_rule = 'fixed_term' AND payment_days BETWEEN 1 AND 60 "
            "AND collection_days IS NULL "
            "AND (NOT payment_end_of_month OR payment_days <= 30)) "
            "OR (payment_rule = 'on_collection' AND payment_days IS NULL "
            "AND collection_days BETWEEN 1 AND 60 "
            "AND NOT payment_end_of_month)",
            name="ck_engagements_payment_rule_columns",
        ),
    )
    op.create_index("uq_engagements_hub_match_id", ENGAGEMENTS, ["hub_match_id"], unique=True)
    op.create_index(
        "ix_engagements_freelancer_dates", ENGAGEMENTS, ["freelancer_id", "starts_on", "ends_on"]
    )
    op.create_index("ix_engagements_freelancer_id", ENGAGEMENTS, ["freelancer_id"])
    op.create_index(
        "ix_engagements_client_organization_id", ENGAGEMENTS, ["client_organization_id"]
    )
    op.create_index("ix_engagements_deal_id", ENGAGEMENTS, ["deal_id"])
    op.create_index("ix_engagements_contract_id", ENGAGEMENTS, ["contract_id"])
    for statement in statements():
        op.execute(statement)


def downgrade() -> None:
    op.drop_table(ENGAGEMENTS)
    op.drop_constraint(HUB_CHECK, ORGANIZATIONS, type_="check")
    op.drop_index(HUB_INDEX, table_name=ORGANIZATIONS)
    op.drop_column(ORGANIZATIONS, "hub_freelancer_id")
