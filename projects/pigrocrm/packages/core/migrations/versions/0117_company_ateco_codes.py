"""company ATECO codes: the activity an srl states, for the regional IRAP rows

Revision ID: 0117
Revises: 0116

REB-1086. `fiscal_profile` gains `ateco_2007_code` and `ateco_2025_code` (`NN.NN.NN`, nullable):
the prevailing ATECO code of an srl in each classification. The regional IRAP lists are written in
ATECO 2007 and table 1 of D.L. 21 February 2026 n. 21 in ATECO 2025, so a rate that depends on
the activity reads both. `NULL` is «not stated»; no backfill, nothing in the existing rows says
what a company does. A CHECK keeps the shape of a code, so a typo is refused by the database too.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0117"
down_revision: str | Sequence[str] | None = "0116"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "fiscal_profile"
COLUMNS = ("ateco_2007_code", "ateco_2025_code")
SHAPE = "[0-9]{2}\\.[0-9]{2}\\.[0-9]{2}"


def upgrade() -> None:
    for name in COLUMNS:
        op.add_column(TABLE, sa.Column(name, sa.String(length=8), nullable=True))
        op.create_check_constraint(f"ck_fiscal_profile_{name}", TABLE, f"{name} ~ '^{SHAPE}$'")


def downgrade() -> None:
    for name in reversed(COLUMNS):
        op.drop_constraint(f"ck_fiscal_profile_{name}", TABLE, type_="check")
        op.drop_column(TABLE, name)
