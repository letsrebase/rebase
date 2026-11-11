"""accountant export: the kind of an export, whether it carries pay, and the one-time codes

Revision ID: 0106
Revises: 0105

REB-1048, § 2 to § 4 of `2026-11-09-accountant-export-design.md`.

`accountant_exports.kind` tells the period bundle (`accountant`) from the payroll consultant's
«variazioni paghe» (`payroll_changes`), and `payroll` says the files carry pay: both are written
at the build, and every export made before this revision is an `accountant` one with no pay, so
the two columns backfill by their defaults.

`accountant_export_links.recipient_email` is the address a one-time code goes to before the files
of a link open. It is `NULL` on a plain link, which exists for an export with no pay and is what
every link made before this revision is.

`accountant_export_link_codes` is each code sent: only a salted hash of it, when it was sent and
when it ends, how many wrong answers it took, when the right one spent it and the session that
answer opened (again a hash). It carries the entity of its export and the `legal_entity_visible`
policy, forced, as the other three tables of the export do; the public door that checks a code
binds the system scope for the one link a token names. `statements()` is what the test template
applies after `create_all`.

`downgrade` drops the table and the three columns.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0106"
down_revision: str | Sequence[str] | None = "0105"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EXPORTS = "accountant_exports"
LINKS = "accountant_export_links"
CODES = "accountant_export_link_codes"
POLICY_EXPRESSION = "legal_entity_visible(legal_entity_id)"


def statements() -> list[str]:
    """The row-level security of the new table, applied after `create_all` by the test template
    (`projects/pigrocrm/conftest.py`) exactly as `upgrade` applies it."""
    return [
        f"CREATE POLICY legal_entity_scope ON {CODES} FOR ALL "
        f"USING ({POLICY_EXPRESSION}) WITH CHECK ({POLICY_EXPRESSION})",
        f"ALTER TABLE {CODES} ENABLE ROW LEVEL SECURITY",
        f"ALTER TABLE {CODES} FORCE ROW LEVEL SECURITY",
    ]


def upgrade() -> None:
    op.add_column(
        EXPORTS,
        sa.Column(
            "kind", sa.String(length=20), server_default=sa.text("'accountant'"), nullable=False
        ),
    )
    op.add_column(
        EXPORTS,
        sa.Column("payroll", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.add_column(LINKS, sa.Column("recipient_email", sa.String(length=320), nullable=True))
    op.create_table(
        CODES,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("link_id", sa.Uuid(), nullable=False),
        sa.Column("legal_entity_id", sa.Uuid(), nullable=True),
        sa.Column("code_hash", sa.String(length=64), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("failures", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("access_hash", sa.String(length=64), nullable=True),
        sa.Column("access_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["link_id"], [f"{LINKS}.id"]),
        sa.ForeignKeyConstraint(["legal_entity_id"], ["legal_entities.id"]),
    )
    op.create_index("ix_accountant_export_link_codes_link_id", CODES, ["link_id"])
    for statement in statements():
        op.execute(statement)


def downgrade() -> None:
    op.drop_table(CODES)
    op.drop_column(LINKS, "recipient_email")
    op.drop_column(EXPORTS, "payroll")
    op.drop_column(EXPORTS, "kind")
