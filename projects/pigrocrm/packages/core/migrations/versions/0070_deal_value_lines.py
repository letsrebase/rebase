"""deal_value_lines: a deal's value as a list of priced lines

Revision ID: 0070
Revises: 0069

REB-928. A deal carried one number, `deals.expected_value`. It now owns an ordered list of
lines (a lump sum, a fee, a day or hour rate, paid once or every month or year) and its
`expected_value` is what they add up to, written by `DealService` in the same transaction.

The table carries the `legal_entity_scope` policy `deal_visible(deal_id)`, behind the
`scope_all()` fast path, with `FORCE ROW LEVEL SECURITY`, as every child table does (0048,
0068); `statements()` is what the test template applies after `create_all`. Grants need
nothing: the application role has default
privileges on what the owner creates, and the boot's `ensure-space-defaults` re-grants on
every table of every space.

Existing data: each deal with a non-null `expected_value` gets one `flat`, `one_off` line of
quantity 1 at that value, so its total stays what it was. A deal with no value gets no line.
`downgrade` drops the table; the values stay in `expected_value`.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0070"
down_revision: str | Sequence[str] | None = "0069"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "deal_value_lines"
# The «all» fast path first, as every through-the-parent policy since 0049 has it.
POLICY_EXPRESSION = "scope_all() OR (deal_visible(deal_id))"


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
        sa.Column("deal_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("description", sa.String(length=255), nullable=True),
        sa.Column("unit", sa.String(length=10), nullable=False),
        sa.Column("delivery_period", sa.String(length=10), nullable=False),
        sa.Column("unit_price", sa.Numeric(12, 2), nullable=False),
        sa.Column("quantity", sa.Numeric(8, 2), nullable=False),
        sa.Column("start", sa.Date(), nullable=True),
        sa.Column("duration_months", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["deal_id"], ["deals.id"]),
        sa.UniqueConstraint("deal_id", "position", name="uq_deal_value_lines_deal_id_position"),
        sa.CheckConstraint("unit IN ('flat', 'day', 'hour')", name="ck_deal_value_lines_unit"),
        sa.CheckConstraint(
            "delivery_period IN ('one_off', 'monthly', 'annual')",
            name="ck_deal_value_lines_delivery_period",
        ),
        sa.CheckConstraint(
            "unit <> 'flat' OR quantity = 1", name="ck_deal_value_lines_flat_quantity_one"
        ),
        sa.CheckConstraint(
            "NOT (unit = 'flat' AND delivery_period = 'one_off') OR duration_months IS NULL",
            name="ck_deal_value_lines_flat_one_off_no_duration",
        ),
        sa.CheckConstraint(
            "duration_months IS NULL OR duration_months >= 1",
            name="ck_deal_value_lines_duration_positive",
        ),
        sa.CheckConstraint("unit_price >= 0", name="ck_deal_value_lines_price_not_negative"),
        sa.CheckConstraint("quantity > 0", name="ck_deal_value_lines_quantity_positive"),
    )
    op.create_index("ix_deal_value_lines_deal_id", TABLE, ["deal_id"])
    # Before the policy goes on: the owner is bound by `FORCE` like anybody else.
    op.execute(
        f"INSERT INTO {TABLE} (id, deal_id, position, unit, delivery_period, unit_price, quantity) "
        "SELECT gen_random_uuid(), id, 0, 'flat', 'one_off', expected_value, 1 "
        "FROM deals WHERE expected_value IS NOT NULL"
    )
    for statement in statements():
        op.execute(statement)


def downgrade() -> None:
    op.drop_table(TABLE)
