"""tax payment plans: the instalment plan of a tax year and the plans of a tax office's notice

Revision ID: 0109
Revises: 0108

REB-1075, § 2 of `2026-11-09-taxes-and-contributions-design.md`. Two new tables:

- `tax_instalment_plans`: one row per legal entity, tax and tax year holding the number of monthly
  instalments an administrator chose for the June balance and first advance. A year with no row is
  paid in one payment, so nothing is backfilled. The dates and the interest of each instalment are
  the pack's and are not stored.
- `tax_notice_plans`: a payment plan from a notice of the tax office entered by hand: its label,
  the tax, the total before interest and the instalments the notice states (a date and the
  interest of each) as a JSON list.

The due items the plans write (`tax_instalment` and `notice_plan`) need no schema: their source
kinds are validated in code like every other kind.

Both tables carry the row-level policy of every table that reaches a legal entity
(`legal_entity_visible(legal_entity_id)`, forced), exactly `tax_advance_methods`'.
`statements()` is what the test template applies after `create_all`.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0109"
down_revision: str | Sequence[str] | None = "0108"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ("tax_instalment_plans", "tax_notice_plans")
POLICY_EXPRESSION = "legal_entity_visible(legal_entity_id)"


def statements() -> list[str]:
    """The row-level security of the tables, applied after `create_all` by the test
    template (`projects/pigrocrm/conftest.py`) exactly as `upgrade` applies it."""
    out: list[str] = []
    for table in TABLES:
        out += [
            f"CREATE POLICY legal_entity_scope ON {table} FOR ALL "
            f"USING ({POLICY_EXPRESSION}) WITH CHECK ({POLICY_EXPRESSION})",
            f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY",
            f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY",
        ]
    return out


def _stamps() -> list[sa.Column[object]]:
    return [
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    ]


def upgrade() -> None:
    op.create_table(
        "tax_instalment_plans",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("legal_entity_id", sa.Uuid(), nullable=False),
        sa.Column("tax", sa.String(length=30), nullable=False),
        sa.Column("tax_year", sa.Integer(), nullable=False),
        sa.Column("instalments", sa.SmallInteger(), nullable=False),
        *_stamps(),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["legal_entity_id"], ["legal_entities.id"]),
        sa.UniqueConstraint("legal_entity_id", "tax", "tax_year", name="uq_tax_instalment_plans"),
        sa.CheckConstraint(
            "instalments BETWEEN 2 AND 12", name="ck_tax_instalment_plans_instalments"
        ),
    )
    op.create_table(
        "tax_notice_plans",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("legal_entity_id", sa.Uuid(), nullable=False),
        sa.Column("label", sa.String(length=120), nullable=False),
        sa.Column("tax", sa.String(length=30), nullable=False),
        sa.Column("total", sa.Numeric(12, 2), nullable=False),
        sa.Column("instalments", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        *_stamps(),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["legal_entity_id"], ["legal_entities.id"]),
        sa.CheckConstraint("total > 0", name="ck_tax_notice_plans_total"),
    )
    for statement in statements():
        op.execute(statement)


def downgrade() -> None:
    for table in reversed(TABLES):
        op.drop_table(table)
