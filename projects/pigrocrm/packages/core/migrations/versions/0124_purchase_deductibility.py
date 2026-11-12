"""how the law treats a purchase: its deductibility kind and its asset group

Revision ID: 0124
Revises: 0117

REB-1089. `cost_categories.deductibility_kind` and `purchases.deductibility_kind` hold the sort of
cost whose deduction the law limits (a vehicle, a phone, a hotel or restaurant, a representation
expense) or `full` for a cost stated as deductible without limit; a purchase's own value wins over
its category's. `purchases.asset_group` flags a purchase as a depreciable asset and names the row
of the ministerial depreciation table it belongs to (`other` for a row the pack does not carry).
`NULL` is «not stated» on all three, so nothing is backfilled: a purchase with no stated kind is
deducted in full, as it was. A CHECK keeps each to its values, and an asset is a positive purchase
of an invoice, a receipt or an expense (a credit note or a refund is not one).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0124"
down_revision: str | Sequence[str] | None = "0117"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

KINDS = (
    "full",
    "vehicle",
    "vehicle_agent",
    "vehicle_employee",
    "phone",
    "hospitality",
    "representation",
)
ASSET_GROUPS = ("office_furniture", "office_electronics", "cars", "goods_vehicles", "other")


def upgrade() -> None:
    op.add_column(
        "cost_categories", sa.Column("deductibility_kind", sa.String(length=20), nullable=True)
    )
    op.create_check_constraint(
        "ck_cost_categories_deductibility_kind",
        "cost_categories",
        f"deductibility_kind IS NULL OR deductibility_kind IN {KINDS!r}",
    )
    op.add_column("purchases", sa.Column("deductibility_kind", sa.String(length=20), nullable=True))
    op.add_column("purchases", sa.Column("asset_group", sa.String(length=20), nullable=True))
    op.create_check_constraint(
        "ck_purchases_deductibility_kind",
        "purchases",
        f"deductibility_kind IS NULL OR deductibility_kind IN {KINDS!r}",
    )
    op.create_check_constraint(
        "ck_purchases_asset_group",
        "purchases",
        f"asset_group IS NULL OR (asset_group IN {ASSET_GROUPS!r} AND amount > 0 "
        "AND kind IN ('invoice', 'receipt', 'expense'))",
    )


def downgrade() -> None:
    op.drop_constraint("ck_purchases_asset_group", "purchases", type_="check")
    op.drop_constraint("ck_purchases_deductibility_kind", "purchases", type_="check")
    op.drop_column("purchases", "asset_group")
    op.drop_column("purchases", "deductibility_kind")
    op.drop_constraint("ck_cost_categories_deductibility_kind", "cost_categories", type_="check")
    op.drop_column("cost_categories", "deductibility_kind")
