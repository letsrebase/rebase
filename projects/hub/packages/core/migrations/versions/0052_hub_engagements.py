"""hub_engagements: the hub's read model of the CRM's engagements, and the reward pointing at it

Revision ID: 0052
Revises: 0051

REB-1106, § 5 of the collaborators design («The hub's read model»). The CRM tells the hub of each
engagement's changes in notices; this table is what they upsert, and what the referral reward,
the verification of a referral by a first signed letter and the card's engagement facts read
instead of `matches` and `contract_documents`. `sequence` is the notice's, so an older notice is
ignored; `state` includes `handoff`, a row the hub opened at «Crea match» that nothing reads until
the CRM's first notice; `company_daily_budget` is the company's own snapshot, never sent by the CRM.

`referral_rewards.document_id`, a foreign key to `contract_documents`, becomes `engagement_id`, a
foreign key to `hub_engagements`. Production has no reward row. The preview may have test rows: each
one keeps its link, because the migration writes a `hub_engagements` row for the match its letter
belonged to, keyed by the match's id, with the figures the reward was computed from (`sequence` 0,
so the CRM's first real notice for it replaces them), and only then drops the old column. Every
statement is conditional, like every migration of this package, so it can run again.

`downgrade` puts `document_id` back from the match's signed letter and **deletes a reward whose
engagement is not a match with one**: a reward earned from an engagement the CRM holds has no hub
letter to point at.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0052"
down_revision: str | Sequence[str] | None = "0051"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TS = "TIMESTAMP WITH TIME ZONE"

_CREATE = (
    "CREATE TABLE IF NOT EXISTS hub_engagements ("
    "id UUID PRIMARY KEY, "
    "crm_engagement_id UUID, "
    "freelancer_id UUID NOT NULL REFERENCES freelancers (id), "
    "company_id UUID REFERENCES companies (id), "
    "client_name VARCHAR(200), "
    "letter_id UUID, "
    "letter_number VARCHAR(12), "
    f"signed_at {_TS}, "
    "mode VARCHAR(5), "
    "fee NUMERIC(12, 2), "
    "expected_days NUMERIC(8, 2), "
    "role VARCHAR(200), "
    "starts_on DATE, "
    "ends_on DATE, "
    "planned_ends_on DATE, "
    "company_daily_budget NUMERIC(10, 2), "
    "state VARCHAR(20) NOT NULL, "
    "sequence INTEGER NOT NULL, "
    f"created_at {_TS} NOT NULL DEFAULT now(), "
    f"updated_at {_TS} NOT NULL DEFAULT now(), "
    "CONSTRAINT ck_hub_engagements_state CHECK (state IN ('handoff', 'draft', "
    "'awaiting_signature', 'active', 'concluded', 'cancelled')), "
    "CONSTRAINT ck_hub_engagements_mode CHECK (mode IS NULL OR mode IN ('day', 'hour', 'flat')), "
    "CONSTRAINT ck_hub_engagements_sequence CHECK (sequence >= 0))",
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_hub_engagements_crm_engagement_id "
    "ON hub_engagements (crm_engagement_id)",
    "CREATE INDEX IF NOT EXISTS ix_hub_engagements_freelancer "
    "ON hub_engagements (freelancer_id, starts_on)",
    "CREATE INDEX IF NOT EXISTS ix_hub_engagements_company ON hub_engagements (company_id)",
)

# What a reward's match was when the reward was written, as the notice would say it. The pay mode
# a letter printed is its `modalita` field («a giornata», «a corpo»).
_BACKFILL_ENGAGEMENTS = (
    "INSERT INTO hub_engagements (id, freelancer_id, company_id, client_name, letter_id, "
    "letter_number, signed_at, mode, fee, expected_days, role, starts_on, ends_on, "
    "planned_ends_on, company_daily_budget, state, sequence) "
    "SELECT DISTINCT ON (m.id) m.id, m.freelancer_id, m.company_id, "
    "LEFT(m.customer_legal_name, 200), d.id, d.number, d.signed_at, "
    "CASE d.data ->> 'modalita' WHEN 'a giornata' THEN 'day' WHEN 'a corpo' THEN 'flat' END, "
    "m.letter_fee, m.expected_days, LEFT(c.requested_role, 200), m.letter_start_date, "
    "m.letter_end_date, m.letter_end_date, m.company_daily_budget, m.status, 0 "
    "FROM referral_rewards r "
    "JOIN contract_documents d ON d.id = r.document_id "
    "JOIN matches m ON m.id = d.match_id "
    "JOIN companies c ON c.id = m.company_id "
    "ORDER BY m.id, d.created_at "
    "ON CONFLICT (id) DO NOTHING"
)
_LINK_REWARDS = (
    "UPDATE referral_rewards r SET engagement_id = d.match_id "
    "FROM contract_documents d WHERE d.id = r.document_id AND r.engagement_id IS NULL"
)


def _has_column(table: str, column: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return column in {c["name"] for c in inspector.get_columns(table)}


def upgrade() -> None:
    for statement in _CREATE:
        op.execute(statement)
    op.execute(
        "ALTER TABLE referral_rewards ADD COLUMN IF NOT EXISTS engagement_id UUID "
        "REFERENCES hub_engagements (id)"
    )
    if _has_column("referral_rewards", "document_id"):
        op.execute(_BACKFILL_ENGAGEMENTS)
        op.execute(_LINK_REWARDS)
        op.execute("ALTER TABLE referral_rewards DROP COLUMN document_id")
    op.execute("ALTER TABLE referral_rewards ALTER COLUMN engagement_id SET NOT NULL")


def downgrade() -> None:
    op.execute(
        "ALTER TABLE referral_rewards ADD COLUMN IF NOT EXISTS document_id UUID "
        "REFERENCES contract_documents (id)"
    )
    op.execute(
        "UPDATE referral_rewards r SET document_id = ("
        "SELECT d.id FROM contract_documents d WHERE d.match_id = r.engagement_id "
        "AND d.kind = 'letter' AND d.status = 'signed' ORDER BY d.signed_at, d.id LIMIT 1) "
        "WHERE r.document_id IS NULL"
    )
    op.execute("DELETE FROM referral_rewards WHERE document_id IS NULL")
    op.execute("ALTER TABLE referral_rewards ALTER COLUMN document_id SET NOT NULL")
    op.execute("ALTER TABLE referral_rewards DROP COLUMN IF EXISTS engagement_id")
    op.execute("DROP TABLE IF EXISTS hub_engagements")
