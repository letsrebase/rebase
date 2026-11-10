"""accountant export: share links, their openings and the copies written to Drive

Revision ID: 0104
Revises: 0103

REB-1047, § 3 of `2026-11-09-accountant-export-design.md`. Three new tables, nothing backfilled:
no export was shared or written to Drive before.

`accountant_export_links` is a share link for one stored export: the SHA-256 of a 256-bit random
token (the raw value is never stored, so a dump of the table opens nothing), the moment it
expires (7 days by default, 30 at most, a rule of the service), who made it, when it was revoked
and by whom, and how many files were handed out through it. `accountant_export_link_openings` is
the audit: one row per file handed out, with when and from which address. A link is never
deleted.

`accountant_export_drive_writes` is a copy of an export written to the space's Drive by a person:
the folder (`commercialista/<entity-slug>/<period>/<timestamp>/`), the files and when.

The three carry the entity of their export (`NULL` for the export of every entity) and the
`legal_entity_visible` policy, forced, as `accountant_exports` does: a scoped administrator
reaches the links of their own entities and an unscoped one every link. The public door that
opens a link binds the system scope (`*`) for the one row its token hash names.
`statements()` is what the test template applies after `create_all`.

`downgrade` drops the three tables.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0104"
down_revision: str | Sequence[str] | None = "0103"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LINKS = "accountant_export_links"
OPENINGS = "accountant_export_link_openings"
WRITES = "accountant_export_drive_writes"
POLICY_EXPRESSION = "legal_entity_visible(legal_entity_id)"


def statements() -> list[str]:
    """The row-level security of the three tables, applied after `create_all` by the test
    template (`projects/pigrocrm/conftest.py`) exactly as `upgrade` applies it."""
    out: list[str] = []
    for table in (LINKS, OPENINGS, WRITES):
        out += [
            f"CREATE POLICY legal_entity_scope ON {table} FOR ALL "
            f"USING ({POLICY_EXPRESSION}) WITH CHECK ({POLICY_EXPRESSION})",
            f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY",
            f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY",
        ]
    return out


def _stamps() -> list[sa.Column[sa.DateTime]]:
    return [
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    ]


def upgrade() -> None:
    op.create_table(
        LINKS,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("export_id", sa.Uuid(), nullable=False),
        sa.Column("legal_entity_id", sa.Uuid(), nullable=True),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("created_by_type", sa.String(length=20), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_by", sa.Uuid(), nullable=True),
        sa.Column("download_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        *_stamps(),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["export_id"], ["accountant_exports.id"]),
        sa.ForeignKeyConstraint(["legal_entity_id"], ["legal_entities.id"]),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_index("ix_accountant_export_links_export_id", LINKS, ["export_id"])
    op.create_table(
        OPENINGS,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("link_id", sa.Uuid(), nullable=False),
        sa.Column("legal_entity_id", sa.Uuid(), nullable=True),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("address", sa.String(length=64), nullable=False),
        sa.Column("part", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["link_id"], [f"{LINKS}.id"]),
        sa.ForeignKeyConstraint(["legal_entity_id"], ["legal_entities.id"]),
    )
    op.create_index("ix_accountant_export_link_openings_link_id", OPENINGS, ["link_id"])
    op.create_table(
        WRITES,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("export_id", sa.Uuid(), nullable=False),
        sa.Column("legal_entity_id", sa.Uuid(), nullable=True),
        sa.Column("prefix", sa.String(length=255), nullable=False),
        sa.Column("files", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("written_by", sa.Uuid(), nullable=True),
        sa.Column("written_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["export_id"], ["accountant_exports.id"]),
        sa.ForeignKeyConstraint(["legal_entity_id"], ["legal_entities.id"]),
    )
    op.create_index("ix_accountant_export_drive_writes_export_id", WRITES, ["export_id"])
    for statement in statements():
        op.execute(statement)


def downgrade() -> None:
    op.drop_table(WRITES)
    op.drop_table(OPENINGS)
    op.drop_table(LINKS)
