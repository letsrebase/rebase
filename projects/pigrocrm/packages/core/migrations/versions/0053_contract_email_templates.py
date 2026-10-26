"""contract_email_templates: the saved text a contract's invoice and register go out with

Revision ID: 0053
Revises: 0052

REB-753. One table, keyed on a contract, with the row-level policy every table that reaches
an azienda carries (spec 2026-10-03 §4): the contract's own visibility, with the «tutte»
fast path first (0049), `FORCE ROW LEVEL SECURITY` like the rest. Nothing existing changes.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0053"
down_revision: str | Sequence[str] | None = "0052"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "contract_email_templates"
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
        sa.Column("nome", sa.String(length=120), nullable=False),
        sa.Column("oggetto", sa.String(length=300), nullable=False),
        sa.Column("corpo", sa.Text(), nullable=False),
        sa.Column(
            "allegati",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("attivo", sa.Boolean(), server_default=sa.text("true"), nullable=False),
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
    op.create_index("ix_contract_email_templates_contract_id", TABLE, ["contract_id"])
    op.create_index(
        "uq_contract_email_templates_nome",
        TABLE,
        ["contract_id", sa.text("lower(nome)")],
        unique=True,
    )
    for statement in statements():
        op.execute(statement)


def downgrade() -> None:
    op.drop_table(TABLE)
