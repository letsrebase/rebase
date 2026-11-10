"""contract_expenses, database-trigger enforced

Revision ID: 0043
Revises: 0042

REB-360, the rebillable-expenses issue of "Bring mastro's ledger, invoice import and
forecasting into PigroCRM" (REB-344's signed-off mapping,
`docs/superpowers/specs/2026-09-23-mastro-ledger-onto-pigrocrm-design.md` §7, §12).

`Cost` (`timetracking/models.py`) is structurally the opposite concept -- it has no
`invoice_line_id` at all, by design -- so this is a genuinely new table, reusing
`CostCategory` for categorisation exactly as §7 asks.

**The one trigger function** (frozen below as `_CONTRACT_EXPENSE_TRIGGER_SQL`, the text
`pigrocrm.core.contract_expenses.triggers.CONTRACT_EXPENSE_TRIGGER_SQL` rendered when this
revision was written, so that a later change to the trigger is a revision of its own):
`contract_expense_set_rimborsabile` (`BEFORE INSERT OR UPDATE` on `contract_expenses`),
recomputing `rimborsabile` from `pre_autorizzata` against the owning contract's
`politica_spese` on every write, never rejecting the write itself.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0043"
down_revision: str | Sequence[str] | None = "0042"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Frozen (PCORE-A-19): the SQL this revision created, not an import of what the
# application says today.
_CONTRACT_EXPENSE_TRIGGER_SQL = """
CREATE OR REPLACE FUNCTION contract_expense_set_rimborsabile() RETURNS TRIGGER AS $$
DECLARE
    v_politica jsonb;
    v_kind text;
    v_richiede_preautorizzazione boolean;
BEGIN
    SELECT c.politica_spese INTO v_politica
    FROM contracts c WHERE c.id = NEW.contract_id;

    IF v_politica IS NULL THEN
        RAISE EXCEPTION 'contract_expenses.contract_id % does not reference a contract',
            NEW.contract_id
            USING ERRCODE = 'foreign_key_violation';
    END IF;

    v_kind := v_politica ->> 'kind';
    IF v_kind IS NULL THEN
        RAISE EXCEPTION
            'contracts.politica_spese for contract % has no "kind" tag: %',
            NEW.contract_id, v_politica
            USING ERRCODE = 'check_violation';
    END IF;
    v_richiede_preautorizzazione := COALESCE(
        (v_politica ->> 'richiede_preautorizzazione')::boolean, false
    );

    -- mastro's own `expense_set_reimbursable`, translated one for one: never
    -- reimbursable at all under 'non_rimborsabile', regardless of
    -- pre-authorisation; otherwise reimbursable unless the policy requires
    -- pre-authorisation and this expense does not have it -- in which case it is
    -- still recorded, just flagged, never rejected outright.
    NEW.rimborsabile := (v_kind <> 'non_rimborsabile')
        AND (NOT v_richiede_preautorizzazione OR NEW.pre_autorizzata);

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_contract_expenses_set_rimborsabile ON contract_expenses;
CREATE TRIGGER trg_contract_expenses_set_rimborsabile
    BEFORE INSERT OR UPDATE ON contract_expenses
    FOR EACH ROW EXECUTE FUNCTION contract_expense_set_rimborsabile();
"""

_DROP_CONTRACT_EXPENSE_TRIGGER_SQL = """
DROP TRIGGER IF EXISTS trg_contract_expenses_set_rimborsabile ON contract_expenses;
DROP FUNCTION IF EXISTS contract_expense_set_rimborsabile();
"""

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

    # The one trigger function and its CREATE TRIGGER call (§12's own pattern).
    op.execute(_CONTRACT_EXPENSE_TRIGGER_SQL)


def downgrade() -> None:
    op.execute(_DROP_CONTRACT_EXPENSE_TRIGGER_SQL)

    op.drop_index("ix_contract_expenses_invoice_line_id", "contract_expenses")
    op.drop_index("ix_contract_expenses_category_id", "contract_expenses")
    op.drop_index("ix_contract_expenses_contract_id", "contract_expenses")
    op.drop_table("contract_expenses")
