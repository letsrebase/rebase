"""contract_documents.regime and freelancer_fiscal.partita_iva nullable: contracts for
members without a partita IVA

Revision ID: 0033
Revises: 0032

REB-687 (design record 2026-10-12): a member who works without a partita IVA signs a
framework agreement for occasional work and occasional letters, a second family of the
same two documents. Every contract document now says which family it belongs to:
`regime`, `partita_iva` or `occasionale`, `NOT NULL DEFAULT 'partita_iva'`, so every row
written before this revision, all of them ordinary, is backfilled by the column's own
default, and `ck_contract_documents_regime` pins the two values. The kind constraints do
not change. `freelancer_fiscal.partita_iva` becomes nullable: tax data saved without one
belong to a member paid as a prestazione occasionale. Neither table is synced to
PostHog's warehouse.

`ADD COLUMN IF NOT EXISTS`, the check dropped `IF EXISTS` before it is added, and `DROP
NOT NULL` on a column already nullable is a no-op, so a retried deploy lands on the same
schema. The downgrade refuses while an `occasionale` document exists, since without the
column it would be typeset again with the ordinary text, or while tax data have no
partita IVA, since `NOT NULL` cannot come back over them.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0033"
down_revision: str | Sequence[str] | None = "0032"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CHECK = "ck_contract_documents_regime"


def upgrade() -> None:
    op.execute(
        "ALTER TABLE contract_documents ADD COLUMN IF NOT EXISTS regime VARCHAR(12) "
        "NOT NULL DEFAULT 'partita_iva'"
    )
    op.execute(f"ALTER TABLE contract_documents DROP CONSTRAINT IF EXISTS {_CHECK}")
    op.execute(
        f"ALTER TABLE contract_documents ADD CONSTRAINT {_CHECK} "
        "CHECK (regime IN ('partita_iva', 'occasionale'))"
    )
    op.execute("ALTER TABLE freelancer_fiscal ALTER COLUMN partita_iva DROP NOT NULL")


def downgrade() -> None:
    bind = op.get_bind()
    has_regime = bind.scalar(
        sa.text(
            "SELECT count(*) FROM information_schema.columns "
            "WHERE table_name = 'contract_documents' AND column_name = 'regime'"
        )
    )
    if has_regime and bind.scalar(
        sa.text("SELECT count(*) FROM contract_documents WHERE regime = 'occasionale'")
    ):
        raise RuntimeError(
            "0033 does not downgrade while a contract document is occasionale: without "
            "`regime` it would be typeset again with the ordinary text. Cancel or remove "
            "those documents first, by hand and on purpose."
        )
    if bind.scalar(sa.text("SELECT count(*) FROM freelancer_fiscal WHERE partita_iva IS NULL")):
        raise RuntimeError(
            "0033 does not downgrade while tax data have no partita IVA: NOT NULL cannot "
            "come back over them. Fill or remove those rows first, by hand and on purpose."
        )
    op.execute(f"ALTER TABLE contract_documents DROP CONSTRAINT IF EXISTS {_CHECK}")
    op.execute("ALTER TABLE contract_documents DROP COLUMN IF EXISTS regime")
    op.execute("ALTER TABLE freelancer_fiscal ALTER COLUMN partita_iva SET NOT NULL")
