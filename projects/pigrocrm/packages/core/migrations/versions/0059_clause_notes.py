"""clause_notes: a contract's written readings of its own clauses

Revision ID: 0059
Revises: 0058

REB-762. One table, keyed on a contract: the clause's reference, its verbatim text, the
interpretation adopted and an optional note. It carries the row-level policy every table
that reaches an azienda carries (spec 2026-10-03 §4): the contract's own visibility, with
the «tutte» fast path first (0049), `FORCE ROW LEVEL SECURITY` like the rest. Nothing
existing changes.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0059"
down_revision: str | Sequence[str] | None = "0058"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "clause_notes"
POLICY_EXPRESSION = "scope_tutte() OR (contract_visibile(contract_id))"


def statements() -> list[str]:
    """The row-level security of the table, applied after `create_all` by the test
    template (`projects/pigrocrm/conftest.py`) exactly as `upgrade` applies it."""
    return [
        f"ALTER TABLE {TABLE} ENABLE ROW LEVEL SECURITY",
        f"ALTER TABLE {TABLE} FORCE ROW LEVEL SECURITY",
        f"CREATE POLICY ambito_azienda ON {TABLE} FOR ALL "
        f"USING ({POLICY_EXPRESSION}) WITH CHECK ({POLICY_EXPRESSION})",
    ]


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("contract_id", sa.Uuid(), nullable=False),
        sa.Column("riferimento_clausola", sa.String(length=200), nullable=False),
        sa.Column("testo_letterale", sa.Text(), nullable=False),
        sa.Column("interpretazione_adottata", sa.Text(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
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
        sa.ForeignKeyConstraint(["contract_id"], ["contracts.id"]),
    )
    op.create_index("ix_clause_notes_contract_id", TABLE, ["contract_id"])
    for statement in statements():
        op.execute(statement)


def downgrade() -> None:
    op.drop_table(TABLE)
