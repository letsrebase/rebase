"""contract_expenses, database-trigger enforced

Revision ID: 0043
Revises: 0042

REB-360, the rebillable-expenses issue of "Bring mastro's ledger, invoice import and
forecasting into PigroCRM" (REB-344's signed-off mapping,
`docs/superpowers/specs/2026-09-23-mastro-ledger-onto-pigrocrm-design.md` §7, §12).

`Cost` (`timetracking/models.py`) is structurally the opposite concept -- it has no
`invoice_line_id` at all, by design -- so this is a genuinely new table, reusing
`CostCategory` for categorisation exactly as §7 asks.

**The one trigger function**
(`pigrocrm.core.contract_expenses.triggers.CONTRACT_EXPENSE_TRIGGER_SQL`,
imported rather than typed out a second time -- see that module's own docstring):
`contract_expense_set_rimborsabile` (`BEFORE INSERT OR UPDATE` on `contract_expenses`),
recomputing `rimborsabile` from `pre_autorizzata` against the owning contract's
`politica_spese` on every write, never rejecting the write itself.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from pigrocrm.core.contract_expenses.triggers import (
    CONTRACT_EXPENSE_TRIGGER_SQL,
    DROP_CONTRACT_EXPENSE_TRIGGER_SQL,
)

revision: str = "0043"
down_revision: str | Sequence[str] | None = "0042"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TIMESTAMPS = (
    sa.Column(
        "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
    ),
    sa.Column(
        "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
    ),
)


def upgrade() -> None:
    op.create_table(
        "contract_expenses",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("contract_id", sa.Uuid(), nullable=False),
        sa.Column("category_id", sa.Uuid(), nullable=False),
        sa.Column("data", sa.Date(), nullable=False),
        sa.Column("importo", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("descrizione", sa.Text(), nullable=False),
        sa.Column("pre_autorizzata", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("riferimento_autorizzazione", sa.Text(), nullable=True),
        sa.Column("rimborsabile", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("invoice_line_id", sa.Uuid(), nullable=True),
        sa.Column("document_id", sa.Uuid(), nullable=True),
        *_TIMESTAMPS,
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["contract_id"], ["contracts.id"]),
        sa.ForeignKeyConstraint(["category_id"], ["cost_categories.id"]),
        sa.ForeignKeyConstraint(["invoice_line_id"], ["invoice_lines.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"]),
        sa.CheckConstraint("importo > 0", name="ck_contract_expenses_importo_positive"),
        sa.CheckConstraint(
            "(pre_autorizzata AND riferimento_autorizzazione IS NOT NULL) "
            "OR (NOT pre_autorizzata AND riferimento_autorizzazione IS NULL)",
            name="ck_contract_expenses_riferimento_matches_pre_autorizzata",
        ),
    )
    op.create_index("ix_contract_expenses_contract_id", "contract_expenses", ["contract_id"])
    op.create_index("ix_contract_expenses_category_id", "contract_expenses", ["category_id"])
    op.create_index(
        "ix_contract_expenses_invoice_line_id", "contract_expenses", ["invoice_line_id"]
    )

    # The one trigger function and its CREATE TRIGGER call (§12's own pattern) -- see
    # `pigrocrm.core.contract_expenses.triggers` for the full text and why it lives
    # there.
    op.execute(CONTRACT_EXPENSE_TRIGGER_SQL)


def downgrade() -> None:
    op.execute(DROP_CONTRACT_EXPENSE_TRIGGER_SQL)

    op.drop_index("ix_contract_expenses_invoice_line_id", "contract_expenses")
    op.drop_index("ix_contract_expenses_category_id", "contract_expenses")
    op.drop_index("ix_contract_expenses_contract_id", "contract_expenses")
    op.drop_table("contract_expenses")
