"""due_items.forecast_key, and a waiting item that carries its offset

Revision ID: 0090
Revises: 0089

REB-1003. Two changes to `due_items`, nothing backfilled.

* `forecast_key`: the identity of a forecast (a uuid5 of its source kind, source and period),
  unique among live forecasts by a partial index, so a source that re-forecasts finds the
  row it wrote and a race cannot double it (spec 2026-11-09, due items, § 1.2).
* `ck_due_items_rule_columns`: an `after_settlement` item that waits for a dependency not
  recorded yet (it has its backstop and no `depends_on_id`) may carry the offset it will date
  from. A freelancer paid N days after the client has settled is created before the client's
  invoice exists, and the offset is what `attach_dependency` finds there once it does.

`downgrade` puts the offset of a waiting item back to zero, restores the stricter check and
drops the index and the column.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0090"
down_revision: str | Sequence[str] | None = "0089"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CHECK = "ck_due_items_rule_columns"
INDEX = "uq_due_items_forecast_key"
_COMMON = (
    "(rule_kind = 'fixed' AND depends_on_id IS NULL AND rule_day_of_month IS NULL "
    "AND rule_not_after IS NULL AND rule_offset_days = 0) OR "
    "(rule_kind <> 'fixed' AND depends_on_id IS NOT NULL) OR "
)
_WAITING_STRICT = (
    "(rule_kind = 'after_settlement' AND depends_on_id IS NULL "
    "AND rule_not_after IS NOT NULL AND rule_offset_days = 0 "
    "AND rule_day_of_month IS NULL)"
)
_WAITING = (
    "(rule_kind = 'after_settlement' AND depends_on_id IS NULL "
    "AND rule_not_after IS NOT NULL AND rule_day_of_month IS NULL)"
)


def upgrade() -> None:
    op.add_column("due_items", sa.Column("forecast_key", sa.Uuid(), nullable=True))
    op.create_index(
        INDEX,
        "due_items",
        ["forecast_key"],
        unique=True,
        postgresql_where=sa.text("state = 'forecast' AND forecast_key IS NOT NULL"),
    )
    op.drop_constraint(CHECK, "due_items", type_="check")
    op.create_check_constraint(CHECK, "due_items", _COMMON + _WAITING)


def downgrade() -> None:
    op.drop_constraint(CHECK, "due_items", type_="check")
    op.execute(
        "UPDATE due_items SET rule_offset_days = 0 "
        "WHERE rule_kind = 'after_settlement' AND depends_on_id IS NULL"
    )
    op.create_check_constraint(CHECK, "due_items", _COMMON + _WAITING_STRICT)
    op.drop_index(INDEX, table_name="due_items")
    op.drop_column("due_items", "forecast_key")
