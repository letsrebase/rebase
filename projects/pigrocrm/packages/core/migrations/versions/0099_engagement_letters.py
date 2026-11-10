"""engagement letters: the template regime, the letter number, the letter's links, the signer

Revision ID: 0099
Revises: 0088

REB-1018, § 1 «The documents» of `2026-11-09-collaborators-in-the-crm-design.md`. Everything is
nullable or new and nothing is backfilled.

- `templates.letter_type` (`ordinary` or `occasional`), set exactly on the two new template types
  `framework_agreement` and `engagement_letter` (a check says so), and at most one active template
  per kind and regime (a partial unique index): a letter reads the space's template, so the choice
  must not be arbitrary.
- `engagements.letter_number`, `YYYY-NNN`, unique, drawn from the new `letter_counters` (one row
  per year, advanced by an upsert).
- `documents.engagement_id` (the engagement an engagement letter belongs to), `letter_type` and
  `text_version` (the regime and version of the text it was written from), and two partial unique
  indexes: one live letter per engagement, one live framework agreement per freelancer and regime.
- `legal_entities.legal_representative`: the person who signs for the entity, the `rebase-*`
  signer value the hub took from a server setting.

`downgrade` drops all of it; a letter number already drawn is lost with its column.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0099"
down_revision: str | Sequence[str] | None = "0088"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TEMPLATE_CHECK = "ck_templates_letter_type"
TEMPLATE_INDEX = "uq_templates_letter_kind"
NUMBER_INDEX = "uq_engagements_letter_number"
DOCUMENT_INDEX = "ix_documents_engagement_id"
LETTER_INDEX = "uq_documents_engagement_letter"
FRAMEWORK_INDEX = "uq_documents_framework_agreement"
DOCUMENT_FK = "fk_documents_engagement_id"


def upgrade() -> None:
    op.add_column("templates", sa.Column("letter_type", sa.String(length=20), nullable=True))
    op.create_check_constraint(
        TEMPLATE_CHECK,
        "templates",
        "(type IN ('framework_agreement', 'engagement_letter')) = (letter_type IS NOT NULL) "
        "AND (letter_type IS NULL OR letter_type IN ('ordinary', 'occasional'))",
    )
    op.create_index(
        TEMPLATE_INDEX,
        "templates",
        ["type", "letter_type"],
        unique=True,
        postgresql_where=sa.text("letter_type IS NOT NULL AND active"),
    )

    op.create_table(
        "letter_counters",
        sa.Column("year", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("last", sa.Integer(), nullable=False),
        sa.CheckConstraint("last >= 1", name="ck_letter_counters_positive"),
        sa.PrimaryKeyConstraint("year"),
    )
    op.add_column("engagements", sa.Column("letter_number", sa.String(length=8), nullable=True))
    op.create_index(NUMBER_INDEX, "engagements", ["letter_number"], unique=True)

    op.add_column("documents", sa.Column("engagement_id", sa.Uuid(), nullable=True))
    op.add_column("documents", sa.Column("letter_type", sa.String(length=20), nullable=True))
    op.add_column("documents", sa.Column("text_version", sa.String(length=20), nullable=True))
    op.create_foreign_key(DOCUMENT_FK, "documents", "engagements", ["engagement_id"], ["id"])
    op.create_index(DOCUMENT_INDEX, "documents", ["engagement_id"])
    op.create_index(
        LETTER_INDEX,
        "documents",
        ["engagement_id", "type"],
        unique=True,
        postgresql_where=sa.text("engagement_id IS NOT NULL AND deleted_at IS NULL"),
    )
    op.create_index(
        FRAMEWORK_INDEX,
        "documents",
        ["customer_id", "letter_type"],
        unique=True,
        postgresql_where=sa.text("type = 'framework_agreement' AND deleted_at IS NULL"),
    )

    op.add_column(
        "legal_entities", sa.Column("legal_representative", sa.String(length=160), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("legal_entities", "legal_representative")

    op.drop_index(FRAMEWORK_INDEX, table_name="documents")
    op.drop_index(LETTER_INDEX, table_name="documents")
    op.drop_index(DOCUMENT_INDEX, table_name="documents")
    op.drop_constraint(DOCUMENT_FK, "documents", type_="foreignkey")
    op.drop_column("documents", "text_version")
    op.drop_column("documents", "letter_type")
    op.drop_column("documents", "engagement_id")

    op.drop_index(NUMBER_INDEX, table_name="engagements")
    op.drop_column("engagements", "letter_number")
    op.drop_table("letter_counters")

    op.drop_index(TEMPLATE_INDEX, table_name="templates")
    op.drop_constraint(TEMPLATE_CHECK, "templates", type_="check")
    op.drop_column("templates", "letter_type")
