"""when a tax item's carried difference was confirmed

Revision ID: 0112
Revises: 0111

REB-1079. `due_items.carry_confirmed_at` is set when an administrator confirms the difference an
advance already paid left on a tax item (REB-1074 asks for it in the item's description). Null for
every item that exists, so nothing is backfilled.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0112"
down_revision: str | Sequence[str] | None = "0111"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "due_items", sa.Column("carry_confirmed_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("due_items", "carry_confirmed_at")
