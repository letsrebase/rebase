"""inbox_items: one file that arrived, before it becomes anything

Revision ID: 0074
Revises: 0073

REB-971 (design 2026-11-09, incoming documents inbox, § 1 and § 2.1). The table holds what an
upload leaves: the file's name, type, size, hashes and where its bytes are kept, who uploaded
it, the ZIP it came out of, and its state (`new`, `proposed`, `filed`, `duplicate` of which item,
`discarded` and why). No existing row is touched, and there is nothing to backfill.

The table carries the `legal_entity_scope` policy with `FORCE ROW LEVEL SECURITY`, as every
table does: an unscoped actor sees every row, a scoped one the rows of its legal entities and
the ones it uploaded itself (an item has no legal entity until it is classified, design § 1,
Visibility); `statements()` is what the test template applies after `create_all`. That an
unclassified item is nobody else's is a role and not a scope, so it is the service's clause
(`inbox/access.py`), not the policy's.

`downgrade` drops the table and with it every item; the bytes stay in storage under
`inbox/`.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0074"
down_revision: str | Sequence[str] | None = "0073"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "inbox_items"
POLICY_EXPRESSION = (
    "scope_all() OR legal_entity_visible(legal_entity_id) "
    "OR uploaded_by_id::text = coalesce(current_setting('pigrocrm.user_id', true), '')"
)
FILE_TYPES = ("xml", "camt053", "p7m", "zip", "pdf", "png", "jpeg", "csv", "xls", "xlsx")
KINDS = (
    "sales_invoice",
    "supplier_invoice",
    "credit_note",
    "receipt",
    "bank_statement",
    "contract",
    "tax_document",
    "payslip",
    "archive",
    "other",
)
STATES = ("new", "proposed", "filed", "duplicate", "discarded")


def _in(column: str, values: Sequence[str]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def statements() -> list[str]:
    """The row-level security of the table, applied after `create_all` by the test template
    (`projects/pigrocrm/conftest.py`) exactly as `upgrade` applies it."""
    return [
        f"CREATE POLICY legal_entity_scope ON {TABLE} FOR ALL "
        f"USING ({POLICY_EXPRESSION}) WITH CHECK ({POLICY_EXPRESSION})",
        f"ALTER TABLE {TABLE} ENABLE ROW LEVEL SECURITY",
        f"ALTER TABLE {TABLE} FORCE ROW LEVEL SECURITY",
    ]


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.Column("original_name", sa.String(length=255), nullable=False),
        sa.Column("file_type", sa.String(length=10), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("storage_key", sa.String(length=255), nullable=True),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("natural_key", sa.String(length=64), nullable=True),
        sa.Column("parent_id", sa.Uuid(), nullable=True),
        sa.Column("uploaded_by_id", sa.Uuid(), nullable=True),
        sa.Column("legal_entity_id", sa.Uuid(), nullable=True),
        sa.Column("kind", sa.String(length=20), nullable=True),
        sa.Column("state", sa.String(length=10), nullable=False),
        sa.Column("duplicate_of_id", sa.Uuid(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["parent_id"], ["inbox_items.id"]),
        sa.ForeignKeyConstraint(["uploaded_by_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["legal_entity_id"], ["legal_entities.id"]),
        sa.ForeignKeyConstraint(["duplicate_of_id"], ["inbox_items.id"]),
        sa.CheckConstraint(_in("source", ("upload",)), name="ck_inbox_items_source"),
        sa.CheckConstraint(
            f"file_type IS NULL OR {_in('file_type', FILE_TYPES)}", name="ck_inbox_items_file_type"
        ),
        sa.CheckConstraint(f"kind IS NULL OR {_in('kind', KINDS)}", name="ck_inbox_items_kind"),
        sa.CheckConstraint(_in("state", STATES), name="ck_inbox_items_state"),
        sa.CheckConstraint("size_bytes >= 0", name="ck_inbox_items_size"),
        sa.CheckConstraint(
            "(state = 'duplicate') = (duplicate_of_id IS NOT NULL)",
            name="ck_inbox_items_duplicate_of",
        ),
    )
    for column in (
        "sha256",
        "content_sha256",
        "natural_key",
        "parent_id",
        "uploaded_by_id",
        "legal_entity_id",
        "state",
        "duplicate_of_id",
    ):
        op.create_index(f"ix_{TABLE}_{column}", TABLE, [column])
    for statement in statements():
        op.execute(statement)


def downgrade() -> None:
    op.drop_table(TABLE)
