"""Fee and day or hour rate cards coexist on a contract; day and hour cards carry a forecast

Revision ID: 0071
Revises: 0069

REB-932. A contract converted from a deal holds an upfront fee, a monthly fee and a day
rate together, which `ck_rate_cards_no_overlap` refused. It now applies only to the
`daily` and `hourly` cards (the one rate a worked day is priced by); fee cards overlap
freely.

`rate_cards.expected_quantity` (Numeric(8, 2), nullable) is the days or hours a day or hour
card forecasts per delivery period. `ck_rate_cards_period_only_fixed_recurring` lets a
`daily` or `hourly` card carry a `delivery_period` (one_off, monthly, annual), only together
with `expected_quantity`; two new CHECKs state that the quantity belongs to day and hour
cards only and that the two come as a pair.

`down_revision` is "0069" because this branch was cut before revision 0070
(`deal_value_lines`) landed; the integration owner re-points it to "0070" when the branches
meet. `downgrade` fails if two fee cards overlap, as the old constraint would.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0071"
down_revision: str | Sequence[str] | None = "0069"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_RANGE = "daterange(valid_from, valid_to, '[]'::text)"


def upgrade() -> None:
    op.add_column("rate_cards", sa.Column("expected_quantity", sa.Numeric(8, 2), nullable=True))
    op.drop_constraint("ck_rate_cards_period_only_fixed_recurring", "rate_cards", type_="check")
    op.create_check_constraint(
        "ck_rate_cards_period_only_fixed_recurring",
        "rate_cards",
        "type = 'fixed_recurring' OR delivery_period IS NULL "
        "OR (type IN ('daily', 'hourly') AND expected_quantity IS NOT NULL "
        "AND delivery_period IN ('one_off', 'monthly', 'annual'))",
    )
    op.create_check_constraint(
        "ck_rate_cards_quantity_only_day_hour",
        "rate_cards",
        "type IN ('daily', 'hourly') OR expected_quantity IS NULL",
    )
    op.create_check_constraint(
        "ck_rate_cards_quantity_with_period",
        "rate_cards",
        "type NOT IN ('daily', 'hourly') OR expected_quantity IS NULL "
        "OR delivery_period IS NOT NULL",
    )
    op.execute("ALTER TABLE rate_cards DROP CONSTRAINT ck_rate_cards_no_overlap")
    op.execute(
        f"ALTER TABLE rate_cards ADD CONSTRAINT ck_rate_cards_no_overlap EXCLUDE USING gist "
        f"(contract_id WITH =, {_RANGE} WITH &&) WHERE (type IN ('daily', 'hourly'))"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE rate_cards DROP CONSTRAINT ck_rate_cards_no_overlap")
    op.execute(
        f"ALTER TABLE rate_cards ADD CONSTRAINT ck_rate_cards_no_overlap EXCLUDE USING gist "
        f"(contract_id WITH =, {_RANGE} WITH &&)"
    )
    op.drop_constraint("ck_rate_cards_quantity_with_period", "rate_cards", type_="check")
    op.drop_constraint("ck_rate_cards_quantity_only_day_hour", "rate_cards", type_="check")
    op.drop_constraint("ck_rate_cards_period_only_fixed_recurring", "rate_cards", type_="check")
    op.execute("UPDATE rate_cards SET delivery_period = NULL WHERE type <> 'fixed_recurring'")
    op.create_check_constraint(
        "ck_rate_cards_period_only_fixed_recurring",
        "rate_cards",
        "type = 'fixed_recurring' OR delivery_period IS NULL",
    )
    op.drop_column("rate_cards", "expected_quantity")
