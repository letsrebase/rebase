"""IVA period locks: a settled period closed against writes to its registers

Revision ID: 0101
Revises: 0100

REB-1036, § 6 of `2026-11-09-suppliers-purchases-and-iva-design.md`. A new table,
`iva_period_locks`: one row per legal entity, frequency, year and period that a person closed,
with the days it covers, when and by whom. While the row exists no purchase and no sales
document that changes the period's registers is written (`IvaPeriodLockService`); reopening
deletes it and the timeline keeps the history. Nothing is backfilled: no period is closed
until a person closes it.

The table carries the row-level policy of every table that reaches a legal entity
(`legal_entity_visible(legal_entity_id)`, forced), exactly `iva_settlements`'.
`statements()` is what the test template applies after `create_all`.

`downgrade` drops the table: every period it closed is open again, and the timeline entries of
its closes stay.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0101"
down_revision: str | Sequence[str] | None = "0100"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "iva_period_locks"
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
        sa.Column("frequency", sa.String(length=10), nullable=False),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("period_number", sa.Integer(), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_by", sa.Uuid(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["legal_entity_id"], ["legal_entities.id"]),
        sa.ForeignKeyConstraint(["closed_by"], ["users.id"]),
        sa.UniqueConstraint(
            "legal_entity_id",
            "frequency",
            "year",
            "period_number",
            name="uq_iva_period_locks_period",
        ),
        sa.CheckConstraint(
            "frequency IN ('monthly', 'quarterly')", name="ck_iva_period_locks_frequency"
        ),
        sa.CheckConstraint(
            "(frequency = 'monthly' AND period_number BETWEEN 1 AND 12) OR "
            "(frequency = 'quarterly' AND period_number BETWEEN 1 AND 4)",
            name="ck_iva_period_locks_period_number",
        ),
        sa.CheckConstraint("period_start <= period_end", name="ck_iva_period_locks_period_order"),
    )
    op.create_index(
        "ix_iva_period_locks_days", TABLE, ["legal_entity_id", "period_start", "period_end"]
    )
    for statement in statements():
        op.execute(statement)


def downgrade() -> None:
    op.drop_table(TABLE)
