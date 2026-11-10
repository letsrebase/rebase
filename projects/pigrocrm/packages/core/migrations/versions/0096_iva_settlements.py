"""IVA settlements: the period's figures, and how often a legal entity settles

Revision ID: 0096
Revises: 0095

REB-1021, § 6 of `2026-11-09-suppliers-purchases-and-iva-design.md`. `fiscal_profile` gains
`iva_frequency` (`monthly` or `quarterly`; every existing row is `monthly`), and a new table,
`iva_settlements`: one row per legal entity, frequency, year and period, holding the sales
VAT, the reverse-charge VAT, the deductible purchase VAT, the credit and the small debit
carried in, the quarterly surcharge, the signed result and what it came to. Nothing is
backfilled: a period is settled by an administrator's call and written by
`IvaSettlementService`.

The table carries the row-level policy of every table that reaches a legal entity
(`legal_entity_visible(legal_entity_id)`, forced), exactly `declared_tax_figures`'.
`statements()` is what the test template applies after `create_all`.

`downgrade` drops the table and the column; the due items a settlement wrote stay, owed to the
tax office, with the settlement they were computed from gone.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0096"
down_revision: str | Sequence[str] | None = "0095"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "iva_settlements"
PROFILE = "fiscal_profile"
FREQUENCY_CHECK = "ck_fiscal_profile_iva_frequency"
POLICY_EXPRESSION = "legal_entity_visible(legal_entity_id)"
MONEY = sa.Numeric(12, 2)


def statements() -> list[str]:
    """The row-level security of the table, applied after `create_all` by the test
    template (`projects/pigrocrm/conftest.py`) exactly as `upgrade` applies it."""
    return [
        f"CREATE POLICY legal_entity_scope ON {TABLE} FOR ALL "
        f"USING ({POLICY_EXPRESSION}) WITH CHECK ({POLICY_EXPRESSION})",
        f"ALTER TABLE {TABLE} ENABLE ROW LEVEL SECURITY",
        f"ALTER TABLE {TABLE} FORCE ROW LEVEL SECURITY",
    ]


def upgrade() -> None:
    op.add_column(
        PROFILE,
        sa.Column(
            "iva_frequency",
            sa.String(length=10),
            server_default=sa.text("'monthly'"),
            nullable=False,
        ),
    )
    op.create_check_constraint(
        FREQUENCY_CHECK, PROFILE, "iva_frequency IN ('monthly', 'quarterly')"
    )
    op.create_table(
        TABLE,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("legal_entity_id", sa.Uuid(), nullable=False),
        sa.Column("frequency", sa.String(length=10), nullable=False),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("period_number", sa.Integer(), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("pack_id", sa.String(length=40), nullable=False),
        sa.Column("pack_version", sa.String(length=10), nullable=False),
        sa.Column("sales_vat", MONEY, nullable=False),
        sa.Column("reverse_charge_vat", MONEY, nullable=False),
        sa.Column("purchase_vat", MONEY, nullable=False),
        sa.Column("credit_in", MONEY, nullable=False),
        sa.Column("debit_in", MONEY, nullable=False),
        sa.Column("surcharge", MONEY, nullable=False),
        sa.Column("result", MONEY, nullable=False),
        sa.Column("outcome", sa.String(length=10), nullable=False),
        sa.Column("payment_due_on", sa.Date(), nullable=False),
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
        sa.UniqueConstraint(
            "legal_entity_id",
            "frequency",
            "year",
            "period_number",
            name="uq_iva_settlements_period",
        ),
        sa.CheckConstraint(
            "frequency IN ('monthly', 'quarterly')", name="ck_iva_settlements_frequency"
        ),
        sa.CheckConstraint(
            "outcome IN ('due', 'carried', 'credit', 'nil')", name="ck_iva_settlements_outcome"
        ),
        sa.CheckConstraint(
            "(frequency = 'monthly' AND period_number BETWEEN 1 AND 12) OR "
            "(frequency = 'quarterly' AND period_number BETWEEN 1 AND 4)",
            name="ck_iva_settlements_period_number",
        ),
        sa.CheckConstraint("period_start <= period_end", name="ck_iva_settlements_period_order"),
        sa.CheckConstraint(
            "credit_in >= 0 AND debit_in >= 0 AND surcharge >= 0",
            name="ck_iva_settlements_carry_not_negative",
        ),
    )
    for statement in statements():
        op.execute(statement)


def downgrade() -> None:
    op.drop_table(TABLE)
    op.drop_constraint(FREQUENCY_CHECK, PROFILE, type_="check")
    op.drop_column(PROFILE, "iva_frequency")
