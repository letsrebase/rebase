"""an F24 that nets to zero or to a credit is settled by filing it

Revision ID: 0113
Revises: 0112

REB-1093. A group whose net is zero or a credit has no bank movement to match: a person marks it
filed (the day, optionally a receipt). Three changes to the group tables.

* `f24_groups.ck_f24_groups_settled` no longer ties `settled` to a movement: a settled group has its
  day, and a movement is only ever on a settled group. A settled group with no movement was filed.
* `f24_group_items.used_amount`: what the F24 took of the line once it is settled, with the line's
  sign (all of it for a movement's match; for a filing, all of a debit and the part of a credit that
  covered the debits). Backfilled for the live lines of the groups a movement already settled.
* `f24_group_items.settlement_id`: the payment a filing wrote for the line, which reopening
  reverses. Null for every line that exists, since a movement's match keeps its payments on the
  match.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0113"
down_revision: str | Sequence[str] | None = "0112"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_SETTLED = "(state = 'settled') = (bank_movement_id IS NOT NULL AND settled_on IS NOT NULL)"
NEW_SETTLED = (
    "(state = 'settled') = (settled_on IS NOT NULL) "
    "AND (bank_movement_id IS NULL OR state = 'settled')"
)
USED_WITHIN = (
    "used_amount IS NULL OR (used_amount * amount >= 0 AND abs(used_amount) <= abs(amount))"
)


def upgrade() -> None:
    op.add_column("f24_group_items", sa.Column("used_amount", sa.Numeric(12, 2), nullable=True))
    op.add_column(
        "f24_group_items",
        sa.Column(
            "settlement_id",
            sa.Uuid(),
            sa.ForeignKey("due_item_settlements.id"),
            nullable=True,
        ),
    )
    op.create_check_constraint(
        "ck_f24_group_items_used_within_amount", "f24_group_items", USED_WITHIN
    )
    op.execute(
        "UPDATE f24_group_items SET used_amount = amount "
        "WHERE released_at IS NULL "
        "AND group_id IN (SELECT id FROM f24_groups WHERE state = 'settled')"
    )
    op.drop_constraint("ck_f24_groups_settled", "f24_groups", type_="check")
    op.create_check_constraint("ck_f24_groups_settled", "f24_groups", NEW_SETTLED)


def downgrade() -> None:
    # The old rule has no settled group without a movement: a filed one goes back to open, with
    # the payments it wrote left on their items.
    op.execute(
        "UPDATE f24_groups SET state = 'open', settled_on = NULL "
        "WHERE state = 'settled' AND bank_movement_id IS NULL"
    )
    op.drop_constraint("ck_f24_groups_settled", "f24_groups", type_="check")
    op.create_check_constraint("ck_f24_groups_settled", "f24_groups", OLD_SETTLED)
    op.drop_constraint("ck_f24_group_items_used_within_amount", "f24_group_items", type_="check")
    op.drop_column("f24_group_items", "settlement_id")
    op.drop_column("f24_group_items", "used_amount")
