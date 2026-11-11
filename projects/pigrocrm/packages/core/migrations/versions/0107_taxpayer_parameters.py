"""the taxpayer facts the INPS and flat-rate law read, on the fiscal profile

Revision ID: 0107
Revises: 0106

REB-1070. Nine nullable columns on `fiscal_profile`, every one `NULL` = «not stated» (a figure
that needs it names it as missing, never a guess), so no row needs a backfill:

* INPS (`InpsYearLaw.contributions`): `inps_trade` (`artigiani` or `commercianti`, checked),
  `inps_covered_elsewhere` (gestione separata's lower rate), `inps_before_1996` (the older
  ceiling), `flat_rate_inps_reduction` (the 35% reduction election).
* Flat-rate regime (`law_flat_rate`): `ateco_group` (the annex 4 group key, validated by the
  API against the pack), `flat_rate_start_year` and the three conditions of c. 65 for the 5%
  rate, answered as met or not.

`downgrade` drops the check and the columns.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0107"
down_revision: str | Sequence[str] | None = "0106"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "fiscal_profile"
BOOLEANS = (
    "inps_covered_elsewhere",
    "inps_before_1996",
    "flat_rate_inps_reduction",
    "startup_no_prior_activity",
    "startup_not_a_continuation",
    "startup_predecessor_within_limit",
)


def upgrade() -> None:
    op.add_column(TABLE, sa.Column("inps_trade", sa.String(length=20), nullable=True))
    op.add_column(TABLE, sa.Column("ateco_group", sa.String(length=60), nullable=True))
    op.add_column(TABLE, sa.Column("flat_rate_start_year", sa.SmallInteger(), nullable=True))
    for name in BOOLEANS:
        op.add_column(TABLE, sa.Column(name, sa.Boolean(), nullable=True))
    op.create_check_constraint(
        "ck_fiscal_profile_inps_trade", TABLE, "inps_trade IN ('artigiani', 'commercianti')"
    )


def downgrade() -> None:
    op.drop_constraint("ck_fiscal_profile_inps_trade", TABLE, type_="check")
    for name in reversed(BOOLEANS):
        op.drop_column(TABLE, name)
    op.drop_column(TABLE, "flat_rate_start_year")
    op.drop_column(TABLE, "ateco_group")
    op.drop_column(TABLE, "inps_trade")
