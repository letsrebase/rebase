"""renewal_assumptions

Revision ID: 0040
Revises: 0039

REB-375, the last issue of "Bring mastro's ledger, invoice import and forecasting into
PigroCRM" (REB-352's own signed-off mapping, §1.3/§1.7/§5 item 6). One new table: a
contract's own human-recorded belief about revenue beyond its known term -- mastro's
`RenewalAssumption` (`certainty.ts:133-147`) -- read by `contracts/projection.py` to
produce a genuine "projected" figure, kept entirely apart from PigroCRM's existing
draft-based `CashOverview.proiettato`.

One row per contract (`unique=True` on `contract_id`), the same "single row, a human
revises it in place" shape `fiscal_profile` already uses for the whole installation,
scoped here to one contract. `probabilita` is `Integer`, 0-100, matching
`deals.probabilita`'s own shape rather than inventing a second percentage
representation on this schema.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0040"
down_revision: str | Sequence[str] | None = "0039"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "renewal_assumptions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("contract_id", sa.Uuid(), nullable=False, unique=True),
        sa.Column("probabilita", sa.Integer(), nullable=False),
        sa.Column("volume_atteso", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("orizzonte_al", sa.Date(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["contract_id"], ["contracts.id"]),
        sa.CheckConstraint(
            "probabilita >= 0 AND probabilita <= 100",
            name="ck_renewal_assumptions_probabilita_range",
        ),
        sa.CheckConstraint(
            "volume_atteso >= 0", name="ck_renewal_assumptions_volume_atteso_non_negative"
        ),
    )


def downgrade() -> None:
    op.drop_table("renewal_assumptions")
