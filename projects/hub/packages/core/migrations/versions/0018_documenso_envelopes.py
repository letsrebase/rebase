"""contract_documents: the envelope item Documenso seals, and why a document was cancelled

Revision ID: 0018
Revises: 0017

REB-387, phase 3. Documenso answers a create with the envelope's id only, and the sealed
copy is downloaded by the envelope *item*'s id (probe § 4 and § 11.7), so the hub reads
it once after the create and keeps it beside `documenso_id`: the two are set together or
not at all. `cancel_reason` says why a document became `annullato`: the freelancer
refused it on the signing site, with their reason, Documenso cancelled it, or an admin
did. `documenso_id` becomes unique, because the webhook finds its document by it.
`signed_copy_mailed_at` is set only once both signed-copy mails (the freelancer's and
rebase's) are accepted, so a restart or a refused mail leaves it `NULL` for the next
`finish` to retry rather than skip (REB-391).

Conditional like every migration of this package: a retried deploy passes over what the
previous attempt already added. The check constraint is added through a `pg_constraint`
guard, since `ADD CONSTRAINT` has no `IF NOT EXISTS`.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0018"
down_revision: str | Sequence[str] | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_STATEMENTS = (
    "ALTER TABLE contract_documents ADD COLUMN IF NOT EXISTS documenso_item_id VARCHAR(100)",
    "ALTER TABLE contract_documents ADD COLUMN IF NOT EXISTS cancel_reason VARCHAR(500)",
    "ALTER TABLE contract_documents ADD COLUMN IF NOT EXISTS signed_copy_mailed_at TIMESTAMPTZ",
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
        "ALTER TABLE contract_documents DROP COLUMN IF EXISTS signed_copy_mailed_at",
        "ALTER TABLE contract_documents DROP COLUMN IF EXISTS cancel_reason",
        "ALTER TABLE contract_documents DROP COLUMN IF EXISTS documenso_item_id",
    ):
        op.execute(statement)
