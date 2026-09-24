"""contract_documents: the envelope item Documenso seals, and why a document was cancelled

Revision ID: 0019
Revises: 0018

REB-387, phase 3. Documenso answers a create with the envelope's id only, and the sealed
copy is downloaded by the envelope *item*'s id (probe § 4 and § 11.7), so the hub reads
it once after the create and keeps it beside `documenso_id`: the two are set together or
not at all. `cancel_reason` says why a document became `annullato`: the freelancer
refused it on the signing site, with their reason, Documenso cancelled it, or an admin
did. `documenso_id` becomes unique, because the webhook finds its document by it.
`signed_copy_to_freelancer_at` and `signed_copy_to_rebase_at` are each set only once
that recipient's own signed-copy mail is accepted, so a restart or a refused mail
leaves that one `NULL` for the next `finish` to retry, without repeating a mail the
other recipient already got (REB-391).

It also carries `matches.request_fingerprint` (REB-406): the SHA-256 of the request a
draft was written from, so a retry of «Crea match» with the same client-generated id but
changed data is refused instead of silently handed back the stale match.

Conditional like every migration of this package: a retried deploy passes over what the
previous attempt already added, never run outside a throwaway test database. The check
constraint is added through a `pg_constraint` guard, since `ADD CONSTRAINT` has no
`IF NOT EXISTS`.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0019"
down_revision: str | Sequence[str] | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_STATEMENTS = (
    "ALTER TABLE contract_documents ADD COLUMN IF NOT EXISTS documenso_item_id VARCHAR(100)",
    "ALTER TABLE contract_documents ADD COLUMN IF NOT EXISTS cancel_reason VARCHAR(500)",
    "ALTER TABLE contract_documents ADD COLUMN IF NOT EXISTS signed_copy_to_freelancer_at "
    "TIMESTAMPTZ",
    "ALTER TABLE contract_documents ADD COLUMN IF NOT EXISTS signed_copy_to_rebase_at TIMESTAMPTZ",
    "ALTER TABLE matches ADD COLUMN IF NOT EXISTS request_fingerprint VARCHAR(64)",
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_contract_documents_documenso_id "
    "ON contract_documents (documenso_id)",
    "DO $$ BEGIN "
    "IF NOT EXISTS (SELECT 1 FROM pg_constraint "
    "WHERE conname = 'ck_contract_documents_envelope_item') THEN "
    "ALTER TABLE contract_documents ADD CONSTRAINT ck_contract_documents_envelope_item "
    "CHECK ((documenso_id IS NULL) = (documenso_item_id IS NULL)); "
    "END IF; END $$",
)


def upgrade() -> None:
    for statement in _STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    for statement in (
        "ALTER TABLE contract_documents DROP CONSTRAINT IF EXISTS "
        "ck_contract_documents_envelope_item",
        "DROP INDEX IF EXISTS uq_contract_documents_documenso_id",
        "ALTER TABLE matches DROP COLUMN IF EXISTS request_fingerprint",
        "ALTER TABLE contract_documents DROP COLUMN IF EXISTS signed_copy_to_rebase_at",
        "ALTER TABLE contract_documents DROP COLUMN IF EXISTS signed_copy_to_freelancer_at",
        "ALTER TABLE contract_documents DROP COLUMN IF EXISTS cancel_reason",
        "ALTER TABLE contract_documents DROP COLUMN IF EXISTS documenso_item_id",
    ):
        op.execute(statement)
