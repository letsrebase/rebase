"""freelancer_fiscal, matches, contract_documents, contract_letter_counters

Revision ID: 0017
Revises: 0016

REB-387, phase 2: an admin pairs a freelancer card with a company request (`matches`),
and the hub writes the letter of engagement and, when the freelancer has no active one,
the framework agreement (`contract_documents`, PDFs in the row like the CV). The
freelancer's tax data are a table of their own (`freelancer_fiscal`) rather than
columns on `freelancers`, which PostHog's warehouse syncs whole; none of these four
tables is synced, so `test_warehouse_contract.py` does not change.
`contract_letter_counters` holds the last letter number taken in each year.

Every statement is conditional (`CREATE TABLE IF NOT EXISTS`, `CREATE INDEX IF NOT
EXISTS`), the discipline migration 0001's docstring states for this package: a retried
deploy must not error on a table the previous attempt already created. The check
constraints are declared inside each `CREATE TABLE`, so they arrive with their table
and need no `pg_constraint` guard of their own.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0017"
down_revision: str | Sequence[str] | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TIMESTAMPS = (
    "created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(), "
    "updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now()"
)

_TABLES = (
    "CREATE TABLE IF NOT EXISTS freelancer_fiscal ("
    "id UUID PRIMARY KEY, "
    "freelancer_id UUID NOT NULL REFERENCES freelancers (id) ON DELETE CASCADE, "
    "codice_fiscale VARCHAR(16) NOT NULL, "
    "partita_iva VARCHAR(11) NOT NULL, "
    "domicilio VARCHAR(300) NOT NULL, "
    "pec VARCHAR(320), "
    "updated_by UUID NOT NULL REFERENCES users (id), "
    f"{_TIMESTAMPS})",
    "CREATE TABLE IF NOT EXISTS matches ("
    "id UUID PRIMARY KEY, "
    "freelancer_id UUID NOT NULL REFERENCES freelancers (id), "
    "company_id UUID NOT NULL REFERENCES companies (id), "
    "cliente_ragione_sociale VARCHAR(200) NOT NULL, "
    "cliente_piva VARCHAR(32) NOT NULL, "
    "cliente_sede VARCHAR(300) NOT NULL, "
    "stato VARCHAR(20) NOT NULL, "
    "created_by UUID NOT NULL REFERENCES users (id), "
    "cancelled_at TIMESTAMP WITH TIME ZONE, "
    f"{_TIMESTAMPS}, "
    "CONSTRAINT ck_matches_stato CHECK "
    "(stato IN ('bozza', 'in_firma', 'attivo', 'concluso', 'annullato')))",
    "CREATE TABLE IF NOT EXISTS contract_documents ("
    "id UUID PRIMARY KEY, "
    "kind VARCHAR(10) NOT NULL, "
    "freelancer_id UUID NOT NULL REFERENCES freelancers (id), "
    "match_id UUID REFERENCES matches (id), "
    "numero VARCHAR(12), "
    "text_version VARCHAR(20) NOT NULL, "
    "testo_bozza BOOLEAN NOT NULL, "
    "data JSONB NOT NULL, "
    "pdf BYTEA NOT NULL, "
    "stato VARCHAR(20) NOT NULL, "
    "documenso_id VARCHAR(100), "
    "signing_url TEXT, "
    "sent_at TIMESTAMP WITH TIME ZONE, "
    "signed_at TIMESTAMP WITH TIME ZONE, "
    "signed_pdf BYTEA, "
    "notice_at TIMESTAMP WITH TIME ZONE, "
    "created_by UUID NOT NULL REFERENCES users (id), "
    "sent_by UUID REFERENCES users (id), "
    f"{_TIMESTAMPS}, "
    "CONSTRAINT ck_contract_documents_kind CHECK (kind IN ('quadro', 'lettera')), "
    "CONSTRAINT ck_contract_documents_stato CHECK (stato IN "
    "('generato', 'in_attesa', 'inviato', 'firmato', 'annullato', 'disdetto')), "
    "CONSTRAINT ck_contract_documents_match_for_letters CHECK "
    "((kind = 'quadro') = (match_id IS NULL)), "
    "CONSTRAINT ck_contract_documents_numero_for_letters CHECK "
    "((kind = 'lettera') = (numero IS NOT NULL)), "
    "CONSTRAINT ck_contract_documents_notice_for_quadro CHECK "
    "(stato <> 'disdetto' OR kind = 'quadro'))",
    "CREATE TABLE IF NOT EXISTS contract_letter_counters ("
    "anno INTEGER PRIMARY KEY, "
    "ultimo INTEGER NOT NULL, "
    "CONSTRAINT ck_contract_letter_counters_positive CHECK (ultimo >= 1))",
)

_INDEXES = (
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_freelancer_fiscal_freelancer_id "
    "ON freelancer_fiscal (freelancer_id)",
    "CREATE INDEX IF NOT EXISTS ix_matches_freelancer_created "
    "ON matches (freelancer_id, created_at)",
    "CREATE INDEX IF NOT EXISTS ix_matches_company_id ON matches (company_id)",
    "CREATE INDEX IF NOT EXISTS ix_contract_documents_freelancer "
    "ON contract_documents (freelancer_id, kind, created_at)",
    "CREATE INDEX IF NOT EXISTS ix_contract_documents_match_id ON contract_documents (match_id)",
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_contract_documents_numero ON contract_documents (numero)",
)


def upgrade() -> None:
    for statement in (*_TABLES, *_INDEXES):
        op.execute(statement)


def downgrade() -> None:
    for table in ("contract_letter_counters", "contract_documents", "matches", "freelancer_fiscal"):
        op.execute(f"DROP TABLE IF EXISTS {table}")
