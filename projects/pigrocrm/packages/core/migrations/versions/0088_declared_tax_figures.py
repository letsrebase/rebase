"""declared tax figures: what the accountant's return says, typed beside the estimate

Revision ID: 0088
Revises: 0087

REB-1006, § 2 and § 5 of `2026-11-09-taxes-and-contributions-design.md`. A new table,
`declared_tax_figures`: one row per legal entity, tax and tax year, the figures of the tax
return as the accountant computed them (the tax due, the balance, the advances), typed by an
administrator. Nothing is backfilled and nothing is computed: a figure is whatever a person
typed, and a newer estimate never touches the row.

`tax_due` and `advances` are never negative, `balance` is signed (a credit is negative), and
at least one of the three is present. The tax is one of the names the design lists, validated
in code like the due items' source kinds; `source` says how the row came to be (`typed` today,
a document read and accepted by a person later) and is validated in code for the same reason.

The table carries the row-level policy of every table that reaches a legal entity
(`legal_entity_visible(legal_entity_id)`, forced), exactly `fiscal_estimates`'. `statements()`
is what the test template applies after `create_all`.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0088"
down_revision: str | Sequence[str] | None = "0087"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "declared_tax_figures"
POLICY_EXPRESSION = "legal_entity_visible(legal_entity_id)"


def statements() -> list[str]:
    """The row-level security of the table, applied after `create_all` by the test
    template (`projects/pigrocrm/conftest.py`) exactly as `upgrade` applies it."""
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
        sa.Column("legal_entity_id", sa.Uuid(), nullable=False),
        sa.Column("tax", sa.String(length=30), nullable=False),
        sa.Column("tax_year", sa.Integer(), nullable=False),
        sa.Column("tax_due", sa.Numeric(14, 2), nullable=True),
        sa.Column("balance", sa.Numeric(14, 2), nullable=True),
        sa.Column("advances", sa.Numeric(14, 2), nullable=True),
        sa.Column(
            "source", sa.String(length=20), server_default=sa.text("'typed'"), nullable=False
        ),
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
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["legal_entity_id"], ["legal_entities.id"]),
        sa.UniqueConstraint("legal_entity_id", "tax", "tax_year", name="uq_declared_tax_figures"),
        sa.CheckConstraint(
            "tax_due IS NOT NULL OR balance IS NOT NULL OR advances IS NOT NULL",
            name="ck_declared_tax_figures_one_figure",
        ),
        sa.CheckConstraint(
            "(tax_due IS NULL OR tax_due >= 0) AND (advances IS NULL OR advances >= 0)",
            name="ck_declared_tax_figures_not_negative",
        ),
    )
    for statement in statements():
        op.execute(statement)


def downgrade() -> None:
    op.drop_table(TABLE)
