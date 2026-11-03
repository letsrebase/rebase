"""contracts.deal_id: the deal a contract was converted from

Revision ID: 0072
Revises: 0071

REB-933. A won deal converts into a contract (spec 2026-11-03 §8). `contracts.deal_id` is the
link: nullable, because a contract made by hand has no deal, and unique, because a deal
becomes at most one contract and a contract comes from at most one deal. A renewal does not
copy it (`renewal_service._NOT_RENEWED`): the chain is `previous_contract_id`.

No row-level policy changes: `contracts` already carries `legal_entity_scope`, and a new
column is covered by it. No data is written: no contract has a deal yet.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0072"
down_revision: str | Sequence[str] | None = "0071"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("contracts", sa.Column("deal_id", sa.Uuid(), nullable=True))
    op.create_foreign_key("contracts_deal_id_fkey", "contracts", "deals", ["deal_id"], ["id"])
    op.create_unique_constraint("contracts_deal_id_key", "contracts", ["deal_id"])


def downgrade() -> None:
    op.drop_constraint("contracts_deal_id_key", "contracts", type_="unique")
    op.drop_constraint("contracts_deal_id_fkey", "contracts", type_="foreignkey")
    op.drop_column("contracts", "deal_id")
