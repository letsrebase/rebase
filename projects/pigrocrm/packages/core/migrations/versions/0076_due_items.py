"""due_items and due_item_settlements: every amount that falls due, and its payments

Revision ID: 0076
Revises: 0075

REB-967 (spec 2026-11-09, due items, § 1). Two new tables, no change to an existing one and
no backfill: the issued invoices are folded in by the next card (REB-968), whose reconcile
gives each its due item.

`due_items` carries the direction, the counterparty (an organisation, or the tax office with
the tax it is for), the legal entity, the amount and currency, the state, the source (a kind
validated in code, never a CHECK, an id and, for a forecast, the rule that produced it) and
the due rule with its dependency. `due_date` is the date the schedule reads, stored by
`DueItemService` so a list can filter and sort on it. `due_item_settlements` holds the
payments, partial ones included; a reversal stamps the row and never deletes it.

A settlement and a dependent name the same legal entity as the item they hang on, by
composite foreign key to `(id, legal_entity_id)`. Both tables carry the `legal_entity_scope`
policy, `scope_all()` first, with `FORCE ROW LEVEL SECURITY`, as every table that reaches a
legal entity does (0048, 0068, 0070, 0073); `statements()` is what the test template applies
after `create_all`. Grants need nothing: the application role has default privileges on what
the owner creates, and the boot's `ensure-space-defaults` re-grants on every table of every
space.

`downgrade` drops both tables, the payments with them.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0076"
down_revision: str | Sequence[str] | None = "0075"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ITEMS = "due_items"
SETTLEMENTS = "due_item_settlements"
# The «all» fast path first, as every policy since 0049 has it.
POLICY_EXPRESSION = "scope_all() OR legal_entity_visible(legal_entity_id)"


def statements() -> list[str]:
    """The row-level security of both tables, applied after `create_all` by the test template
    (`projects/pigrocrm/conftest.py`) exactly as `upgrade` applies it."""
    out: list[str] = []
    for table in (ITEMS, SETTLEMENTS):
        out += [
            f"CREATE POLICY legal_entity_scope ON {table} FOR ALL "
            f"USING ({POLICY_EXPRESSION}) WITH CHECK ({POLICY_EXPRESSION})",
            f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY",
            f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY",
        ]
    return out


def upgrade() -> None:
    op.create_table(
        ITEMS,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("legal_entity_id", sa.Uuid(), nullable=False),
        sa.Column("direction", sa.String(length=3), nullable=False),
        sa.Column("state", sa.String(length=10), nullable=False),
        sa.Column("counterparty_type", sa.String(length=20), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=True),
        sa.Column("tax", sa.String(length=30), nullable=True),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("currency", sa.String(length=3), server_default="EUR", nullable=False),
        sa.Column("description", sa.String(length=255), nullable=True),
        sa.Column("source_kind", sa.String(length=30), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=True),
        sa.Column("source_rule", sa.String(length=60), nullable=True),
        sa.Column("own_date", sa.Date(), nullable=True),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column(
            "due_date_estimated", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column("rule_kind", sa.String(length=30), server_default="fixed", nullable=False),
        sa.Column("depends_on_id", sa.Uuid(), nullable=True),
        sa.Column("rule_offset_days", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("rule_day_of_month", sa.Integer(), nullable=True),
        sa.Column("rule_not_after", sa.Date(), nullable=True),
        sa.Column("dropped_reason", sa.String(length=255), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["legal_entity_id"], ["legal_entities.id"]),
        sa.ForeignKeyConstraint(["organization_id"], ["customers.id"]),
        sa.UniqueConstraint("id", "legal_entity_id", name="uq_due_items_id_legal_entity_id"),
        sa.ForeignKeyConstraint(
            ["depends_on_id", "legal_entity_id"],
            ["due_items.id", "due_items.legal_entity_id"],
            name="fk_due_items_depends_on_same_legal_entity",
        ),
        sa.CheckConstraint("direction IN ('in', 'out')", name="ck_due_items_direction"),
        sa.CheckConstraint(
            "state IN ('forecast', 'expected', 'settled', 'dropped')", name="ck_due_items_state"
        ),
        sa.CheckConstraint(
            "rule_kind IN ('fixed', 'after_settlement', 'month_after_settlement_on_day', "
            "'on_settlement')",
            name="ck_due_items_rule_kind",
        ),
        sa.CheckConstraint(
            "counterparty_type IN ('organization', 'tax_office')",
            name="ck_due_items_counterparty_type",
        ),
        sa.CheckConstraint(
            "(counterparty_type = 'organization' AND organization_id IS NOT NULL "
            "AND tax IS NULL) OR "
            "(counterparty_type = 'tax_office' AND tax IS NOT NULL AND organization_id IS NULL)",
            name="ck_due_items_counterparty_columns",
        ),
        sa.CheckConstraint("amount <> 0", name="ck_due_items_amount_not_zero"),
        sa.CheckConstraint("currency ~ '^[A-Z]{3}$'", name="ck_due_items_currency"),
        sa.CheckConstraint(
            "state <> 'forecast' OR source_rule IS NOT NULL", name="ck_due_items_forecast_rule"
        ),
        sa.CheckConstraint(
            "depends_on_id IS NULL OR depends_on_id <> id", name="ck_due_items_no_self"
        ),
        sa.CheckConstraint(
            "(rule_kind = 'fixed' AND depends_on_id IS NULL AND rule_day_of_month IS NULL "
            "AND rule_not_after IS NULL AND rule_offset_days = 0) OR "
            "(rule_kind <> 'fixed' AND depends_on_id IS NOT NULL) OR "
            "(rule_kind = 'after_settlement' AND depends_on_id IS NULL "
            "AND rule_not_after IS NOT NULL AND rule_offset_days = 0 "
            "AND rule_day_of_month IS NULL)",
            name="ck_due_items_rule_columns",
        ),
        sa.CheckConstraint(
            "(rule_kind = 'month_after_settlement_on_day') = (rule_day_of_month IS NOT NULL)",
            name="ck_due_items_rule_day_of_month",
        ),
        sa.CheckConstraint(
            "rule_kind = 'after_settlement' OR rule_offset_days = 0",
            name="ck_due_items_rule_offset",
        ),
        sa.CheckConstraint("rule_offset_days >= 0", name="ck_due_items_rule_offset_not_negative"),
        sa.CheckConstraint(
            "rule_day_of_month IS NULL OR rule_day_of_month BETWEEN 1 AND 28",
            name="ck_due_items_rule_day_range",
        ),
    )
    op.create_index("ix_due_items_legal_entity_id", ITEMS, ["legal_entity_id"])
    op.create_index("ix_due_items_organization_id", ITEMS, ["organization_id"])
    op.create_index("ix_due_items_schedule", ITEMS, ["legal_entity_id", "state", "due_date"])
    op.create_index("ix_due_items_source", ITEMS, ["source_kind", "source_id"])
    op.create_index("ix_due_items_depends_on_id", ITEMS, ["depends_on_id"])

    op.create_table(
        SETTLEMENTS,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("due_item_id", sa.Uuid(), nullable=False),
        sa.Column("legal_entity_id", sa.Uuid(), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("account_amount", sa.Numeric(12, 2), nullable=True),
        sa.Column("account_currency", sa.String(length=3), nullable=True),
        sa.Column("settled_on", sa.Date(), nullable=False),
        sa.Column("value_date", sa.Date(), nullable=True),
        sa.Column("note", sa.String(length=255), nullable=True),
        sa.Column("recorded_by_id", sa.Uuid(), nullable=True),
        sa.Column("recorded_by_type", sa.String(length=10), nullable=False),
        sa.Column("reversed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reversed_by_id", sa.Uuid(), nullable=True),
        sa.Column("reversed_by_type", sa.String(length=10), nullable=True),
        sa.Column("reversal_reason", sa.String(length=255), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["legal_entity_id"], ["legal_entities.id"]),
        sa.ForeignKeyConstraint(
            ["due_item_id", "legal_entity_id"],
            ["due_items.id", "due_items.legal_entity_id"],
            name="fk_due_item_settlements_same_legal_entity",
        ),
        sa.CheckConstraint("amount <> 0", name="ck_due_item_settlements_amount_not_zero"),
        sa.CheckConstraint(
            "(account_amount IS NULL) = (account_currency IS NULL)",
            name="ck_due_item_settlements_account_pair",
        ),
        sa.CheckConstraint(
            "account_currency IS NULL OR account_currency ~ '^[A-Z]{3}$'",
            name="ck_due_item_settlements_account_currency",
        ),
        sa.CheckConstraint(
            "(reversed_at IS NULL) = (reversed_by_type IS NULL)",
            name="ck_due_item_settlements_reversal_pair",
        ),
    )
    op.create_index("ix_due_item_settlements_due_item_id", SETTLEMENTS, ["due_item_id"])
    op.create_index("ix_due_item_settlements_legal_entity_id", SETTLEMENTS, ["legal_entity_id"])
    op.create_index(
        "ix_due_item_settlements_settled_on", SETTLEMENTS, ["legal_entity_id", "settled_on"]
    )
    for statement in statements():
        op.execute(statement)


def downgrade() -> None:
    op.drop_table(SETTLEMENTS)
    op.drop_table(ITEMS)
