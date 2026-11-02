"""companies: the link to rebase's CRM

Revision ID: 0046
Revises: 0045

REB-868: a completed company request is filed in rebase's own CRM (spec
`docs/superpowers/specs/2026-10-30-company-requests-into-the-rebase-crm-design.md` § 4).
The columns are a match's Pigro link (0021) carried to the request:

* `crm_stato`: `da_collegare`, `collegato`, `errore` or `rifiutato` under
  `ck_companies_crm_stato`; `NULL` for every row written before this ships.
* `crm_customer_id`, `crm_deal_id`, `crm_deal_url`: what the CRM answered.
* `crm_errore`, `crm_attempted_at`: the cause and the time of the last attempt.
* `crm_bozza` (JSONB): the AI's draft, kept from the first successful call.

**No backfill, on purpose** (Lorenzo, 2026-10-30): the requests already on the hub keep a
`NULL` state, the sweep never picks them up, and one reaches the CRM only when an admin
presses «Riprova» or «Collega a un altro deal» on it.

Conditional like every migration of this package: `IF NOT EXISTS`, and the `CHECK` behind a
`pg_constraint` guard.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0046"
down_revision: str | Sequence[str] | None = "0045"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COLUMNS = (
    "crm_stato VARCHAR(20)",
    "crm_customer_id UUID",
    "crm_deal_id UUID",
    "crm_deal_url TEXT",
    "crm_errore TEXT",
    "crm_attempted_at TIMESTAMPTZ",
    "crm_bozza JSONB",
)


def upgrade() -> None:
    for column in _COLUMNS:
        op.execute(f"ALTER TABLE companies ADD COLUMN IF NOT EXISTS {column}")
    op.execute(
        "DO $$ BEGIN "
        "IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_companies_crm_stato') "
        "THEN ALTER TABLE companies ADD CONSTRAINT ck_companies_crm_stato "
        "CHECK (crm_stato IS NULL OR crm_stato IN "
        "('da_collegare', 'collegato', 'errore', 'rifiutato')); "
        "END IF; END $$"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE companies DROP CONSTRAINT IF EXISTS ck_companies_crm_stato")
    for column in reversed(_COLUMNS):
        op.execute(f"ALTER TABLE companies DROP COLUMN IF EXISTS {column.split()[0]}")
