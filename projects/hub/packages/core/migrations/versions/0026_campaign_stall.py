"""campaigns.fermo_at, campaigns.fermo_motivo: when and why a send stopped

Revision ID: 0026
Revises: 0025

REB-524. A pass that meets a stop which will not fix itself (Resend refusing the key
with a 401 or 403, or a list the tick can no longer read) leaves the campaign
`in_invio` and tries again every minute, and the page went on saying «Parte il…».
The pass now writes the moment and the reason here, the page reads «Invio fermo:
<motivo>», and the first mail that leaves afterwards clears both. Two nullable columns
and nothing else; `campaigns` is not read by PostHog's warehouse
(`test_warehouse_contract.py`). It follows 0025, the talent cloud's (#434), which
merges first.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0026"
down_revision: str | Sequence[str] | None = "0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS fermo_at TIMESTAMP WITH TIME ZONE")
    op.execute("ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS fermo_motivo VARCHAR(200)")


def downgrade() -> None:
    op.execute("ALTER TABLE campaigns DROP COLUMN IF EXISTS fermo_motivo")
    op.execute("ALTER TABLE campaigns DROP COLUMN IF EXISTS fermo_at")
