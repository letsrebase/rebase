"""Drop the hub's match side: matches, contracts, tax data and the letter counter

Revision ID: 0053
Revises: 0052

REB-1108, § 7 step 3 of the collaborators design. The engagement with a freelancer (match,
letters, signature, tax data) lives in the CRM; the hub keeps `hub_engagements`, its read model.
Nothing in the hub reads these four tables any more. They hold no enum or type of their own (every
state is a CHECK constraint that goes with its table), so dropping the tables is the whole job.

`downgrade` recreates the four tables **empty**, with the schema 0052 left them: what they held is
not restored (production's one match was a draft moved by hand to the CRM; the rest is in the
backups). Every statement is conditional, like every migration of this package.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0053"
down_revision: str | Sequence[str] | None = "0052"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Children first: `contract_documents` points at `matches`.
_DROP = (
    "DROP TABLE IF EXISTS contract_letter_counters",
    "DROP TABLE IF EXISTS contract_documents",
    "DROP TABLE IF EXISTS matches",
    "DROP TABLE IF EXISTS freelancer_fiscal",
)

_RECREATE = (
    (
        "CREATE TABLE IF NOT EXISTS freelancer_fiscal (freelancer_id UUID NOT NULL, "
        "tax_code VARCHAR(16) NOT NULL, vat_number VARCHAR(11), domicile VARCHAR(300) NOT "
        "NULL, certified_email VARCHAR(320), updated_by UUID NOT NULL, id UUID NOT NULL, "
        "created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, updated_at TIMESTAMP "
        "WITH TIME ZONE DEFAULT now() NOT NULL, PRIMARY KEY (id), FOREIGN "
        "KEY(freelancer_id) REFERENCES freelancers (id) ON DELETE CASCADE, FOREIGN "
        "KEY(updated_by) REFERENCES users (id))"
    ),
    (
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_freelancer_fiscal_freelancer_id ON "
        "freelancer_fiscal (freelancer_id)"
    ),
    (
        "CREATE TABLE IF NOT EXISTS matches (freelancer_id UUID NOT NULL, company_id UUID "
        "NOT NULL, customer_legal_name VARCHAR(200) NOT NULL, customer_vat_number "
        "VARCHAR(32) NOT NULL, customer_registered_office VARCHAR(300) NOT NULL, status "
        "VARCHAR(20) NOT NULL, created_by UUID NOT NULL, cancelled_at TIMESTAMP WITH TIME "
        "ZONE, request_fingerprint VARCHAR(64), expected_days INTEGER, letter_start_date "
        "DATE, letter_end_date DATE, letter_fee NUMERIC(7, 2), company_daily_budget "
        "NUMERIC(10, 2), pigro_status VARCHAR(20), pigro_slug VARCHAR(32), pigro_deal_id "
        "UUID, pigro_url TEXT, pigro_linked_at TIMESTAMP WITH TIME ZONE, "
        "pigro_attempted_at TIMESTAMP WITH TIME ZONE, pigro_error TEXT, pigro_mail_sent_at "
        "TIMESTAMP WITH TIME ZONE, id UUID NOT NULL, created_at TIMESTAMP WITH TIME ZONE "
        "DEFAULT now() NOT NULL, updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT "
        "NULL, PRIMARY KEY (id), CONSTRAINT ck_matches_status CHECK (status IN ('draft', "
        "'awaiting_signature', 'active', 'concluded', 'cancelled')), CONSTRAINT "
        "ck_matches_expected_days CHECK (expected_days IS NULL OR expected_days BETWEEN 1 "
        "AND 366), CONSTRAINT ck_matches_pigro_status CHECK (pigro_status IS NULL OR "
        "pigro_status IN ('to_link', 'linked', 'error', 'rejected')), FOREIGN "
        "KEY(freelancer_id) REFERENCES freelancers (id), FOREIGN KEY(company_id) "
        "REFERENCES companies (id), FOREIGN KEY(created_by) REFERENCES users (id))"
    ),
    (
        "CREATE INDEX IF NOT EXISTS ix_matches_freelancer_created ON matches "
        "(freelancer_id, created_at)"
    ),
    "CREATE INDEX IF NOT EXISTS ix_matches_company_id ON matches (company_id)",
    (
        "CREATE TABLE IF NOT EXISTS contract_documents (kind VARCHAR(10) NOT NULL, regime "
        "VARCHAR(16) DEFAULT 'vat_registered' NOT NULL, freelancer_id UUID NOT NULL, "
        "match_id UUID, number VARCHAR(12), text_version VARCHAR(20) NOT NULL, draft_text "
        "BOOLEAN NOT NULL, data JSONB NOT NULL, pdf BYTEA NOT NULL, status VARCHAR(20) NOT "
        "NULL, documenso_id VARCHAR(100), documenso_item_id VARCHAR(100), signing_url "
        "TEXT, sent_at TIMESTAMP WITH TIME ZONE, signed_at TIMESTAMP WITH TIME ZONE, "
        "signed_pdf BYTEA, signed_copy_to_freelancer_at TIMESTAMP WITH TIME ZONE, "
        "signed_copy_to_rebase_at TIMESTAMP WITH TIME ZONE, notice_at TIMESTAMP WITH TIME "
        "ZONE, cancel_reason VARCHAR(500), created_by UUID NOT NULL, sent_by UUID, id UUID "
        "NOT NULL, created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, updated_at "
        "TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, PRIMARY KEY (id), CONSTRAINT "
        "ck_contract_documents_kind CHECK (kind IN ('framework', 'letter')), CONSTRAINT "
        "ck_contract_documents_regime CHECK (regime IN ('vat_registered', 'occasional')), "
        "CONSTRAINT ck_contract_documents_status CHECK (status IN ('generated', 'pending', "
        "'sent', 'signed', 'cancelled', 'notice_served')), CONSTRAINT "
        "ck_contract_documents_match_for_letters CHECK ((kind = 'framework') = (match_id "
        "IS NULL)), CONSTRAINT ck_contract_documents_number_for_letters CHECK ((kind = "
        "'letter') = (number IS NOT NULL)), CONSTRAINT "
        "ck_contract_documents_notice_for_framework CHECK (status <> 'notice_served' OR "
        "kind = 'framework'), CONSTRAINT ck_contract_documents_envelope_item CHECK "
        "((documenso_id IS NULL) = (documenso_item_id IS NULL)), FOREIGN "
        "KEY(freelancer_id) REFERENCES freelancers (id), FOREIGN KEY(match_id) REFERENCES "
        "matches (id), FOREIGN KEY(created_by) REFERENCES users (id), FOREIGN KEY(sent_by) "
        "REFERENCES users (id))"
    ),
    (
        "CREATE INDEX IF NOT EXISTS ix_contract_documents_freelancer ON contract_documents "
        "(freelancer_id, kind, created_at)"
    ),
    (
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_contract_documents_documenso_id ON "
        "contract_documents (documenso_id)"
    ),
    ("CREATE INDEX IF NOT EXISTS ix_contract_documents_match_id ON contract_documents (match_id)"),
    (
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_contract_documents_number ON "
        "contract_documents (number)"
    ),
    (
        "CREATE TABLE IF NOT EXISTS contract_letter_counters (year INTEGER NOT NULL, last "
        "INTEGER NOT NULL, PRIMARY KEY (year), CONSTRAINT "
        "ck_contract_letter_counters_positive CHECK (last >= 1))"
    ),
)


def upgrade() -> None:
    for statement in _DROP:
        op.execute(statement)


def downgrade() -> None:
    for statement in _RECREATE:
        op.execute(statement)
