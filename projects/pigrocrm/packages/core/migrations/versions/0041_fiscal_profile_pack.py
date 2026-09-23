"""fiscal profile: the jurisdiction pack pointer, pack_id and pack_version

Revision ID: 0041
Revises: 0040

REB-361, REB-344 §8. `NOT NULL` with a server-side default, plus an explicit
backfill: every space this product has ever provisioned is on `it-flat-rate`
version `1` today, so there is no real "which pack" question for the one existing
row to leave unanswered. The server default alone already makes an existing row
satisfy the constraint at `ADD COLUMN` time; the `UPDATE` is deliberate belt and
suspenders, the same caution `0011_fiscal_income_columns.py` uses for its own
backfill: "not only defaulted for new ones."
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0041"
down_revision: str | Sequence[str] | None = "0040"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "fiscal_profile",
        sa.Column(
            "pack_id",
            sa.String(length=40),
            nullable=False,
            server_default=sa.text("'it-flat-rate'"),
        ),
    )
    op.add_column(
        "fiscal_profile",
        sa.Column(
            "pack_version",
            sa.String(length=10),
            nullable=False,
            server_default=sa.text("'1'"),
        ),
    )
    op.execute(
        "UPDATE fiscal_profile SET pack_id = 'it-flat-rate', pack_version = '1'"  # noqa: S608
    )


def downgrade() -> None:
    op.drop_column("fiscal_profile", "pack_version")
    op.drop_column("fiscal_profile", "pack_id")
