"""proposals: review-before-write for a contract's first draft or a day's evidence

Revision ID: 0044
Revises: 0043

REB-362, the intake issue of "Bring mastro's ledger, invoice import and forecasting
into PigroCRM" (REB-344's signed-off mapping,
`docs/superpowers/specs/2026-09-23-mastro-ledger-onto-pigrocrm-design.md` §10).

No trigger, unlike 0042: `proposals` needs no cross-table subquery a plain `CHECK`
cannot express. `ck_proposals_contract_id_required_unless_contratto` mirrors mastro's
own `proposal.ts:167-172` CHECK exactly -- `contract_id` is required for every
`target_type` except `'contratto'`, whose first intake has no contract row yet to
point at.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0044"
down_revision: str | Sequence[str] | None = "0043"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TIMESTAMPS = (
    sa.Column(
        "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    ),
    sa.Column(
        "updated_at",
        sa.DateTime(timezone=True),
        server_default=sa.func.now(),
        nullable=False,
    ),
)


def upgrade() -> None:
    op.create_table(
        "proposals",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("documents.id"),
            nullable=False,
        ),
        sa.Column("contract_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("contracts.id")),
        sa.Column("target_type", sa.String(20), nullable=False),
        sa.Column("campi_proposti", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("estratto", sa.Text(), nullable=False),
        sa.Column(
            "tipo_estratto",
            sa.String(12),
            nullable=False,
            server_default=sa.text("'citato'"),
        ),
        sa.Column("confidenza", sa.Numeric(3, 2), nullable=False),
        sa.Column("motivo_confidenza", sa.Text()),
        sa.Column(
            "stato",
            sa.String(12),
            nullable=False,
            server_default=sa.text("'in_attesa'"),
        ),
        sa.Column("campi_accettati", postgresql.JSONB(astext_type=sa.Text())),
        sa.Column("id_risultato", postgresql.UUID(as_uuid=True)),
        sa.Column("deciso_da", sa.Text()),
        sa.Column("deciso_il", sa.DateTime(timezone=True)),
        *_TIMESTAMPS,
        sa.CheckConstraint(
            "target_type IN ('contratto', 'giornata')", name="ck_proposals_target_type"
        ),
        sa.CheckConstraint(
            "tipo_estratto IN ('citato', 'trascritto')", name="ck_proposals_tipo_estratto"
        ),
        sa.CheckConstraint(
            "stato IN ('in_attesa', 'accettata', 'rifiutata')", name="ck_proposals_stato"
        ),
        sa.CheckConstraint(
            "target_type = 'contratto' OR contract_id IS NOT NULL",
            name="ck_proposals_contract_id_required_unless_contratto",
        ),
    )
    op.create_index("ix_proposals_document_id", "proposals", ["document_id"])
    op.create_index("ix_proposals_contract_id", "proposals", ["contract_id"])
    op.create_index("ix_proposals_stato_created_at", "proposals", ["stato", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_proposals_stato_created_at", table_name="proposals")
    op.drop_index("ix_proposals_contract_id", table_name="proposals")
    op.drop_index("ix_proposals_document_id", table_name="proposals")
    op.drop_table("proposals")
