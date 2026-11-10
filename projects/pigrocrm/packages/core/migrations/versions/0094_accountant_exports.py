"""accountant exports: the row a built bundle leaves behind

Revision ID: 0094
Revises: 0093

REB-1012, § 3 of `2026-11-09-accountant-export-design.md`. A new table, `accountant_exports`:
one row per build (legal entity or all of them, period, the storage prefix, the ZIP parts, who
built it and when, a snapshot of the index) and the moment it expires, 13 months after the
build, and when its files were deleted. Nothing is backfilled: no export was stored before.

The table carries the row-level policy of every table that reaches a legal entity
(`legal_entity_visible(legal_entity_id)`, forced): the all-entities export has a `NULL` entity,
which the policy shows to an unscoped administrator alone. `statements()` is what the test
template applies after `create_all`.

The activity feed reads each row through `entity_visible(entity_type, entity_id, ...)`, whose
`ELSE` is the caller's own default (open): an export writes its entries under
`accountant_export`, and this revision gives the function a branch for it, so an entry is seen
only by whoever sees the export's legal entity (an unscoped administrator, for the export of
every entity). The branch is added to the definition the database holds
(`pg_get_functiondef`), not to a copy of it, as 0079 did for the staff; `downgrade` takes it
out again.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0094"
down_revision: str | Sequence[str] | None = "0093"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "accountant_exports"
POLICY_EXPRESSION = "legal_entity_visible(legal_entity_id)"


# `ELSE $3` is the one place in the function's text the branch can go before.
_ANCHOR = "ELSE $3"
_BRANCH = (
    "WHEN 'accountant_export' THEN current_setting('pigrocrm.legal_entities', true) = '*' "
    "OR EXISTS (\n"
    "          SELECT 1 FROM accountant_exports a WHERE a.id = $2 AND "
    "legal_entity_visible(a.legal_entity_id))\n"
    "        "
)
_FUNCTION = "'public.entity_visible(text, uuid, boolean)'::regprocedure"


def _rewrite_function(add: bool) -> str:
    """A `DO` block that re-creates `entity_visible` with the branch in (or out), and changes
    nothing when it already is (or already is not): applied by `upgrade`, `downgrade` and, for
    the test template, `statements`."""
    old, new = (_ANCHOR, _BRANCH + _ANCHOR) if add else (_BRANCH + _ANCHOR, _ANCHOR)
    check = "'accountant_export'"
    guard = (
        f"position({check} in definition) = 0" if add else f"position({check} in definition) > 0"
    )
    return (
        "DO $do$\n"
        "DECLARE definition text := pg_get_functiondef(" + _FUNCTION + ");\n"
        "BEGIN\n"
        f"  IF {guard} THEN\n"
        f"    EXECUTE replace(definition, $old${old}$old$, $new${new}$new$);\n"
        "  END IF;\n"
        "END\n"
        "$do$"
    )


def statements() -> list[str]:
    """The row-level security of the table and the branch of `entity_visible`, applied after
    `create_all` by the test template (`projects/pigrocrm/conftest.py`) exactly as `upgrade`
    applies them."""
    return [
        f"CREATE POLICY legal_entity_scope ON {TABLE} FOR ALL "
        f"USING ({POLICY_EXPRESSION}) WITH CHECK ({POLICY_EXPRESSION})",
        f"ALTER TABLE {TABLE} ENABLE ROW LEVEL SECURITY",
        f"ALTER TABLE {TABLE} FORCE ROW LEVEL SECURITY",
        _rewrite_function(add=True),
    ]


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("legal_entity_id", sa.Uuid(), nullable=True),
        sa.Column("scope", sa.String(length=20), nullable=False),
        sa.Column("period_from", sa.Date(), nullable=False),
        sa.Column("period_to", sa.Date(), nullable=False),
        sa.Column("prefix", sa.String(length=255), nullable=False),
        sa.Column("parts", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("checklist", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("built_by", sa.Uuid(), nullable=True),
        sa.Column("built_by_type", sa.String(length=20), nullable=False),
        sa.Column("built_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["legal_entity_id"], ["legal_entities.id"]),
    )
    op.create_index("ix_accountant_exports_expires_at", TABLE, ["expires_at"])
    for statement in statements():
        op.execute(statement)


def downgrade() -> None:
    op.execute(_rewrite_function(add=False))
    op.drop_table(TABLE)
