"""tax advance methods: which method a legal entity's advances of a tax year follow

Revision ID: 0108
Revises: 0107

REB-1074, § 2 and § 5 of `2026-11-09-taxes-and-contributions-design.md`. A new table,
`tax_advance_methods`: one row per legal entity and tax year, the method an administrator chose
for the advances of that year, `historical` or `forecast`. A year with no row is `historical`, the
default, so nothing is backfilled and nothing a legal entity has today changes.

The method is validated by a CHECK, since the table's own invariant is «one of the two». The tax
due items the method moves are written by `TaxDueItemService` and need no schema: their source
kinds `tax_balance` and `tax_advance` are validated in code like every other kind.

The table carries the row-level policy of every table that reaches a legal entity
(`legal_entity_visible(legal_entity_id)`, forced), exactly `declared_tax_figures`'.
`statements()` is what the test template applies after `create_all`.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0108"
down_revision: str | Sequence[str] | None = "0107"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "tax_advance_methods"
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
        sa.Column("tax_year", sa.Integer(), nullable=False),
        sa.Column("method", sa.String(length=10), nullable=False),
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
        sa.UniqueConstraint("legal_entity_id", "tax_year", name="uq_tax_advance_methods"),
        sa.CheckConstraint(
            "method IN ('historical', 'forecast')", name="ck_tax_advance_methods_method"
        ),
    )
    for statement in statements():
        op.execute(statement)


def downgrade() -> None:
    op.drop_table(TABLE)
