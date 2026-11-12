"""the income kind of an ordinary-regime person, and split payment excluding reverse charge

Revision ID: 0114
Revises: 0113

REB-1099. Two changes, one revision:

* `fiscal_profile.income_kind` (`self_employment` or `business`, checked): whether the income of
  the activity is an art or profession (art. 53 TUIR), which the 20% withholding of art. 25
  DPR 600/1973 is owed on, or business income. Nullable: `NULL` is «not stated» and keeps the
  product's reading, a professional, so no row is backfilled.
* `ck_purchases_split_payment_not_reverse_charge`: split payment applies only where the buyer
  is not the VAT debtor, and under reverse charge it is (art. 17-ter c. 1 DPR 633/1972;
  Agenzia, Circolare 14/E of 2015 § 4). The check cannot be added over a row that breaks it,
  and a wrong flag cannot be guessed away, so the upgrade **fails** naming the purchases it
  found; a person fixes those rows (one of the two flags is wrong) and runs it again.

`downgrade` drops both.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0114"
down_revision: str | Sequence[str] | None = "0113"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EXCLUSIVE = "ck_purchases_split_payment_not_reverse_charge"


def upgrade() -> None:
    op.add_column("fiscal_profile", sa.Column("income_kind", sa.String(length=20), nullable=True))
    op.create_check_constraint(
        "ck_fiscal_profile_income_kind",
        "fiscal_profile",
        "income_kind IN ('self_employment', 'business')",
    )
    bind = op.get_bind()
    broken = (
        bind.execute(
            sa.text("SELECT id FROM purchases WHERE split_payment AND reverse_charge ORDER BY id")
        )
        .scalars()
        .all()
    )
    if broken:
        raise RuntimeError(
            f"{len(broken)} purchase(s) carry both split_payment and reverse_charge, which "
            "exclude each other (art. 17-ter c. 1 DPR 633/1972). Correct one of the two flags "
            f"on each, then migrate again: {', '.join(str(row) for row in broken)}"
        )
    op.create_check_constraint(EXCLUSIVE, "purchases", "NOT (split_payment AND reverse_charge)")


def downgrade() -> None:
    op.drop_constraint(EXCLUSIVE, "purchases", type_="check")
    op.drop_constraint("ck_fiscal_profile_income_kind", "fiscal_profile", type_="check")
    op.drop_column("fiscal_profile", "income_kind")
