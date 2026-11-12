"""tax losses: the losses of earlier years an estimate carries forward

Revision ID: 0119
Revises: 0118

REB-1088, § 1 of `2026-11-09-taxes-and-contributions-design.md`. A new table, `tax_losses`: one
row per legal entity, tax (`ires` or `irpef`) and year of origin, the loss that period closed
with (`amount`) and what was left of it at the end of `remaining_as_of_year` (`remaining`), typed
by an administrator, plus the two facts art. 84 c. 2 TUIR needs for the first three periods of a
new activity (`activity_start_year`, `new_activity`). Nothing is backfilled and nothing is
computed: a loss is whatever a person typed, and an estimate never writes the row.

The table carries the row-level policy of every table that reaches a legal entity
(`legal_entity_visible(legal_entity_id)`, forced), exactly `declared_tax_figures`'. `statements()`
is what the test template applies after `create_all`.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0119"
down_revision: str | Sequence[str] | None = "0118"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "tax_losses"
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
        sa.Column("tax", sa.String(length=10), nullable=False),
        sa.Column("origin_year", sa.Integer(), nullable=False),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("remaining", sa.Numeric(14, 2), nullable=False),
        sa.Column("remaining_as_of_year", sa.Integer(), nullable=False),
        sa.Column("new_activity", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("activity_start_year", sa.Integer(), nullable=True),
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
        sa.UniqueConstraint("legal_entity_id", "tax", "origin_year", name="uq_tax_losses"),
        sa.CheckConstraint("tax IN ('ires', 'irpef')", name="ck_tax_losses_tax"),
        sa.CheckConstraint(
            "amount > 0 AND remaining >= 0 AND remaining <= amount", name="ck_tax_losses_amounts"
        ),
        sa.CheckConstraint(
            "remaining_as_of_year >= origin_year", name="ck_tax_losses_remaining_year"
        ),
        sa.CheckConstraint(
            "(activity_start_year IS NULL OR activity_start_year <= origin_year) "
            "AND (NOT new_activity OR activity_start_year IS NOT NULL)",
            name="ck_tax_losses_new_activity",
        ),
    )
    for statement in statements():
        op.execute(statement)


def downgrade() -> None:
    op.drop_table(TABLE)
