"""Recurring expenses: the rule that forecasts money going out

Revision ID: 0099
Revises: 0097

REB-1022, § 1.2 of `2026-11-09-due-items-bank-and-cash-schedule-design.md`. A new table,
`recurring_expenses`: one row per supplier that bills the same amount on a cadence, holding the
legal entity, the supplier, the cost category, the amount, the cadence, the first due date, the
optional last one and the day the rule was stopped. The daily pass writes a forecast out for
each occurrence from it (`due_items/recurring.py`); nothing is backfilled.

The table carries the row-level policy of every table that reaches a legal entity
(`legal_entity_visible(legal_entity_id)`, forced), exactly `iva_settlements`'.
`statements()` is what the test template applies after `create_all`.

`downgrade` drops the table; the forecasts it wrote stay as due items with a source that no
longer exists, and the next pass of the old release leaves them alone.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0099"
down_revision: str | Sequence[str] | None = "0097"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "recurring_expenses"
POLICY_EXPRESSION = "legal_entity_visible(legal_entity_id)"
CADENCES = ("monthly", "bimonthly", "quarterly", "semiannual", "annual")


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
        sa.Column("supplier_id", sa.Uuid(), nullable=False),
        sa.Column("category_id", sa.Uuid(), nullable=False),
        sa.Column("description", sa.String(length=255), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("cadence", sa.String(length=12), nullable=False),
        sa.Column("first_due_date", sa.Date(), nullable=False),
        sa.Column("ends_on", sa.Date(), nullable=True),
        sa.Column("stopped_on", sa.Date(), nullable=True),
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
        sa.ForeignKeyConstraint(["supplier_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["category_id"], ["cost_categories.id"]),
        sa.CheckConstraint("amount > 0", name="ck_recurring_expenses_amount_positive"),
        sa.CheckConstraint(f"cadence IN {CADENCES!r}", name="ck_recurring_expenses_cadence"),
        sa.CheckConstraint(
            "ends_on IS NULL OR ends_on >= first_due_date", name="ck_recurring_expenses_ends_on"
        ),
    )
    op.create_index("ix_recurring_expenses_legal_entity_id", TABLE, ["legal_entity_id"])
    op.create_index("ix_recurring_expenses_supplier", TABLE, ["supplier_id"])
    op.create_index("ix_recurring_expenses_active", TABLE, ["legal_entity_id", "stopped_on"])
    for statement in statements():
        op.execute(statement)


def downgrade() -> None:
    op.drop_table(TABLE)
