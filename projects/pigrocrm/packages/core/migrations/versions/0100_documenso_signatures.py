"""signing a letter: its state, Documenso envelope and signed copy, on `documents`

Revision ID: 0100
Revises: 0099

REB-1019, § 2 of `2026-11-09-collaborators-in-the-crm-design.md`. A framework agreement and an
engagement letter are `documents` rows (REB-1018); what happens to one once it leaves is columns
of the same row, null on every other document:

- `signature_state`: `generated` (written, never sent), `pending` (a letter waiting for its
  framework agreement), `sent` (out for signature), `signed`, `cancelled` and, on a framework
  agreement only, `notice_served` (the hub's own six names). The documents the previous revision
  already wrote are `generated`, which is what they are.
- `envelope_id` and `envelope_item_id` (Documenso's: the webhook finds its document by the
  envelope, the sealed copy is downloaded by the item), both or neither, the envelope unique.
- `signing_url`, `sent_at`, `sent_by`, `signed_at` (the signer's own date), `signed_version` (the
  number of the `document_versions` row that holds the signed PDF), the two moments the signed copy
  was mailed (to the freelancer and to the issuer, each set only once its own mail is accepted),
  `notice_at` and `cancel_reason`.

Two checks keep the columns honest: a notice only on a framework agreement, and an envelope only on
a document that left (`sent`, `signed`, `cancelled` or `notice_served`). No policy changes: the
columns are the row's own, under the legal entity policy of `documents`.

`uq_documents_framework_agreement` (one live framework agreement per freelancer and regime) now
leaves out one that was cancelled and one a notice ended: the freelancer's next engagement writes
a new one, as it did in the hub. `downgrade` restores the index as it was.

`downgrade` drops the columns: a signature already recorded is lost with them, the signed copies
stay as versions of their documents.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0100"
down_revision: str | Sequence[str] | None = "0099"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

STATE_CHECK = "ck_documents_signature_state"
NOTICE_CHECK = "ck_documents_notice_for_framework"
ENVELOPE_PAIR_CHECK = "ck_documents_envelope_item"
ENVELOPE_STATE_CHECK = "ck_documents_envelope_left"
ENVELOPE_INDEX = "uq_documents_envelope_id"
FRAMEWORK_INDEX = "uq_documents_framework_agreement"
SENT_BY_FK = "fk_documents_sent_by"
STATES = ("generated", "pending", "sent", "signed", "cancelled", "notice_served")
COLUMNS: tuple[tuple[str, sa.types.TypeEngine], ...] = (  # type: ignore[type-arg]
    ("signature_state", sa.String(length=20)),
    ("envelope_id", sa.String(length=100)),
    ("envelope_item_id", sa.String(length=100)),
    ("signing_url", sa.Text()),
    ("sent_at", sa.DateTime(timezone=True)),
    ("sent_by", sa.Uuid()),
    ("signed_at", sa.DateTime(timezone=True)),
    ("signed_version", sa.Integer()),
    ("signed_copy_to_freelancer_at", sa.DateTime(timezone=True)),
    ("signed_copy_to_issuer_at", sa.DateTime(timezone=True)),
    ("notice_at", sa.DateTime(timezone=True)),
    ("cancel_reason", sa.String(length=200)),
)


def _in(column: str, values: Sequence[str]) -> str:
    return f"{column} IN ({', '.join(repr(value) for value in values)})"


def upgrade() -> None:
    for name, kind in COLUMNS:
        op.add_column("documents", sa.Column(name, kind, nullable=True))
    op.execute(
        "UPDATE documents SET signature_state = 'generated' "
        "WHERE type IN ('framework_agreement', 'engagement_letter')"
    )
    op.create_foreign_key(SENT_BY_FK, "documents", "users", ["sent_by"], ["id"])
    op.create_check_constraint(
        STATE_CHECK, "documents", f"signature_state IS NULL OR {_in('signature_state', STATES)}"
    )
    op.create_check_constraint(
        NOTICE_CHECK,
        "documents",
        "signature_state IS DISTINCT FROM 'notice_served' OR type = 'framework_agreement'",
    )
    op.create_check_constraint(
        ENVELOPE_PAIR_CHECK, "documents", "(envelope_id IS NULL) = (envelope_item_id IS NULL)"
    )
    op.create_check_constraint(
        ENVELOPE_STATE_CHECK,
        "documents",
        "envelope_id IS NULL OR "
        f"{_in('signature_state', ('sent', 'signed', 'cancelled', 'notice_served'))}",
    )
    # The webhook finds its document by the envelope: one envelope, one document.
    op.create_index(ENVELOPE_INDEX, "documents", ["envelope_id"], unique=True)
    op.drop_index(FRAMEWORK_INDEX, table_name="documents")
    op.create_index(
        FRAMEWORK_INDEX,
        "documents",
        ["customer_id", "letter_type"],
        unique=True,
        postgresql_where=sa.text(
            "type = 'framework_agreement' AND deleted_at IS NULL "
            "AND signature_state IS DISTINCT FROM 'cancelled' "
            "AND signature_state IS DISTINCT FROM 'notice_served'"
        ),
    )


def downgrade() -> None:
    op.drop_index(FRAMEWORK_INDEX, table_name="documents")
    # The index as it was allows one live framework agreement per freelancer and regime: of those
    # that share one, the best is kept (one that is not cancelled or ended by a notice, then the
    # newest) and the others are put aside by a soft delete.
    ended = "(signature_state IN ('cancelled', 'notice_served'))::int"
    op.execute(
        "UPDATE documents SET deleted_at = now() WHERE type = 'framework_agreement' "
        "AND deleted_at IS NULL AND EXISTS (SELECT 1 FROM documents better WHERE "
        "better.type = 'framework_agreement' AND better.deleted_at IS NULL "
        "AND better.customer_id = documents.customer_id "
        "AND better.letter_type = documents.letter_type AND better.id <> documents.id "
        f"AND ((better.signature_state IN ('cancelled', 'notice_served'))::int < {ended} "
        f"OR ((better.signature_state IN ('cancelled', 'notice_served'))::int = {ended} "
        "AND (better.created_at, better.id) > (documents.created_at, documents.id))))"
    )
    op.create_index(
        FRAMEWORK_INDEX,
        "documents",
        ["customer_id", "letter_type"],
        unique=True,
        postgresql_where=sa.text("type = 'framework_agreement' AND deleted_at IS NULL"),
    )
    op.drop_index(ENVELOPE_INDEX, table_name="documents")
    for name in (ENVELOPE_STATE_CHECK, ENVELOPE_PAIR_CHECK, NOTICE_CHECK, STATE_CHECK):
        op.drop_constraint(name, "documents", type_="check")
    op.drop_constraint(SENT_BY_FK, "documents", type_="foreignkey")
    for name, _kind in reversed(COLUMNS):
        op.drop_column("documents", name)
