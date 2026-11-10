"""purchases: the `costs` table becomes `purchases`, and gains the fields of a supplier's document

Revision ID: 0086
Revises: 0085

REB-993, § 2 «Purchases» and § 8 of `2026-11-09-suppliers-purchases-and-iva-design.md`. Every
cost becomes a purchase of kind `expense` in this one revision, keeping what it had: its id (so
the history and every reference stay), `description`, `created_at`, `custom_fields`, `amount`,
its soft delete and the document it holds. The free-text supplier becomes `merchant_name`.
`cost_amount`, what the purchase costs, starts equal to `amount` on every migrated row: the
readers that summed `costs.amount` sum `purchases.cost_amount` and read the same number.

What is added to the table: `kind` and `status`, the supplier (an organization), the number, the
dates (`received_on`, `iva_period`), the currency with its euro rate, the frozen cost and the
buyer's pack and regime, the stamp duty, the withholding and the professional fund, the two
VAT-mechanism flags, the invoice a credit note corrects and the inbox item the purchase came
from. The recap lines are a new table, `purchase_vat_lines`, with the row-level policy of the
purchase they belong to.

What is rewritten, because a function body and a stored string are text that
`ALTER TABLE RENAME` never touches (`0068_english_names.py`, `0077_organizations.py`):

- the `cost` branch of `entity_visible`, which reads the table by name, becomes `purchase`;
- the discriminator string `'cost'` in the activity feed, which holds the history of every row
  and which the activity feed reads through that function, and in the custom-field definitions
  (`field_definitions`), whose entity becomes `purchase`;
- the space's module switch: the `module_costs` row of `space_settings` is `module_purchases`
  from here, so a space where costs were off gets purchases off.

`downgrade` turns every purchase back into a cost row of its total (the supplier's name in the
free-text column, a cancelled purchase archived), and drops the recap lines.

`statements()` is not the policy of `purchases`, which the table keeps through the rename; it is
the policy of the new table, applied by the test template after `create_all` as `upgrade` does.
The earlier revisions' statements speak of `costs`; `rewrite()` says them in the new names.
"""

import re
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0086"
down_revision: str | Sequence[str] | None = "0085"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_TABLE = "costs"
NEW_TABLE = "purchases"
LINES = "purchase_vat_lines"
KINDS = ("invoice", "credit_note", "receipt", "expense")
STATUSES = ("registered", "cancelled")

# Tables with a column that holds an entity type. `notes` is not one of them: its check
# constraint never admitted a cost.
DISCRIMINATOR_TABLES = ("activities", "gmail_message_links", "email_drafts", "field_definitions")

LINES_POLICY = (
    "scope_all() OR EXISTS (SELECT 1 FROM purchases p WHERE p.id = purchase_vat_lines.purchase_id)"
)

# The `cost` branch of `entity_visible`, as `0068_english_names.py` wrote it and every later
# revision left it, and the same branch under its new name.
_BRANCH_OLD = (
    "WHEN 'cost' THEN current_setting('pigrocrm.legal_entities', true) = '*' OR EXISTS (\n"
    "          SELECT 1 FROM costs c WHERE c.id = $2"
)
_BRANCH_NEW = (
    "WHEN 'purchase' THEN current_setting('pigrocrm.legal_entities', true) = '*' OR EXISTS (\n"
    "          SELECT 1 FROM purchases c WHERE c.id = $2"
)


def rewrite(sql: str) -> str:
    """A statement of an earlier revision, in the names this one gives: the table and the
    discriminator. The test template runs the policy statements of 0068 to 0085 through it
    after `create_all`."""
    sql = re.sub(rf"\b{OLD_TABLE}\b", NEW_TABLE, sql)
    return sql.replace("'cost'", "'purchase'")


def statements() -> list[str]:
    """The row-level security of the new table, applied after `create_all` by the test template
    (`projects/pigrocrm/conftest.py`) exactly as `upgrade` applies it."""
    return [
        f"CREATE POLICY legal_entity_scope ON {LINES} FOR ALL "
        f"USING ({LINES_POLICY}) WITH CHECK ({LINES_POLICY})",
        f"ALTER TABLE {LINES} ENABLE ROW LEVEL SECURITY",
        f"ALTER TABLE {LINES} FORCE ROW LEVEL SECURITY",
    ]


def _in(column: str, values: Sequence[str]) -> str:
    return f"{column} IN ({', '.join(repr(value) for value in values)})"


def _rename_table_objects(table: str, old: str, new: str) -> None:
    """The constraints and indexes of `table` whose name carries `old`: a constraint first
    (which renames the index that backs a primary key), then the plain indexes."""
    bind = op.get_bind()
    constraints = bind.execute(
        sa.text(
            "SELECT conname FROM pg_constraint WHERE conrelid = CAST(:table AS regclass) "
            "AND (conname LIKE :plain OR conname LIKE :prefixed)"
        ),
        {"table": table, "plain": f"{old}\\_%", "prefixed": f"%\\_{old}\\_%"},
    ).scalars()
    for name in list(constraints):
        op.execute(
            f'ALTER TABLE {table} RENAME CONSTRAINT "{name}" TO "{name.replace(old, new, 1)}"'
        )
    indexes = bind.execute(
        sa.text(
            "SELECT indexname FROM pg_indexes WHERE schemaname = current_schema() "
            "AND tablename = :table AND indexname LIKE :prefixed"
        ),
        {"table": table, "prefixed": f"ix\\_{old}\\_%"},
    ).scalars()
    for name in list(indexes):
        op.execute(f'ALTER INDEX "{name}" RENAME TO "{name.replace(old, new, 1)}"')


def _replace_in_entity_visible(old: str, new: str) -> None:
    """Rewrite one branch of `entity_visible` in the function's own definition, read back from
    the catalog, so whatever the earlier revisions left is what changes; refuse to go on if the
    branch it expects is not there."""
    op.execute(
        "DO $do$ DECLARE def text; BEGIN "
        "SELECT pg_get_functiondef('entity_visible(text, uuid, boolean)'::regprocedure) INTO def; "
        f"IF position($old${old}$old$ in def) = 0 THEN "
        "RAISE EXCEPTION 'entity_visible has no branch to rewrite'; END IF; "
        f"EXECUTE replace(def, $old${old}$old$, $new${new}$new$); END $do$"
    )


def _rename_discriminator(old: str, new: str) -> None:
    for table in DISCRIMINATOR_TABLES:
        op.execute(f"UPDATE {table} SET entity_type = '{new}' WHERE entity_type = '{old}'")


def _rename_module_switch(old: str, new: str) -> None:
    op.execute(f"UPDATE space_settings SET key = '{new}' WHERE key = '{old}'")


def upgrade() -> None:
    op.rename_table(OLD_TABLE, NEW_TABLE)
    _rename_table_objects(NEW_TABLE, OLD_TABLE, NEW_TABLE)
    op.alter_column(NEW_TABLE, "supplier", new_column_name="merchant_name")

    op.add_column(
        NEW_TABLE,
        sa.Column("kind", sa.String(20), server_default=sa.text("'expense'"), nullable=False),
    )
    op.add_column(
        NEW_TABLE,
        sa.Column("status", sa.String(20), server_default=sa.text("'registered'"), nullable=False),
    )
    op.add_column(NEW_TABLE, sa.Column("supplier_id", sa.Uuid(), nullable=True))
    op.add_column(NEW_TABLE, sa.Column("number", sa.String(50), nullable=True))
    op.add_column(NEW_TABLE, sa.Column("received_on", sa.Date(), nullable=True))
    op.add_column(NEW_TABLE, sa.Column("iva_period", sa.Date(), nullable=True))
    op.add_column(
        NEW_TABLE,
        sa.Column("currency", sa.String(3), server_default=sa.text("'EUR'"), nullable=False),
    )
    op.add_column(NEW_TABLE, sa.Column("currency_amount", sa.Numeric(12, 2), nullable=True))
    op.add_column(NEW_TABLE, sa.Column("exchange_rate", sa.Numeric(14, 6), nullable=True))
    op.add_column(NEW_TABLE, sa.Column("exchange_rate_date", sa.Date(), nullable=True))
    # What a cost counted, which is its amount: written before the column is required.
    op.add_column(NEW_TABLE, sa.Column("cost_amount", sa.Numeric(12, 2), nullable=True))
    op.execute(f"UPDATE {NEW_TABLE} SET cost_amount = amount")
    op.alter_column(NEW_TABLE, "cost_amount", nullable=False)
    op.add_column(NEW_TABLE, sa.Column("stamp_duty", sa.Numeric(12, 2), nullable=True))
    op.add_column(NEW_TABLE, sa.Column("withholding_rate", sa.Numeric(5, 2), nullable=True))
    op.add_column(NEW_TABLE, sa.Column("withholding_base", sa.Numeric(12, 2), nullable=True))
    op.add_column(NEW_TABLE, sa.Column("withholding_amount", sa.Numeric(12, 2), nullable=True))
    op.add_column(NEW_TABLE, sa.Column("fund_rate", sa.Numeric(5, 2), nullable=True))
    op.add_column(NEW_TABLE, sa.Column("fund_amount", sa.Numeric(12, 2), nullable=True))
    op.add_column(
        NEW_TABLE,
        sa.Column("split_payment", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.add_column(
        NEW_TABLE,
        sa.Column("reverse_charge", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.add_column(NEW_TABLE, sa.Column("corrects_purchase_id", sa.Uuid(), nullable=True))
    op.add_column(NEW_TABLE, sa.Column("inbox_item_id", sa.Uuid(), nullable=True))
    op.add_column(NEW_TABLE, sa.Column("buyer_pack_id", sa.String(40), nullable=True))
    op.add_column(NEW_TABLE, sa.Column("buyer_pack_version", sa.String(10), nullable=True))
    op.add_column(NEW_TABLE, sa.Column("buyer_regime_code", sa.String(4), nullable=True))

    op.create_foreign_key(
        "purchases_supplier_id_fkey", NEW_TABLE, "organizations", ["supplier_id"], ["id"]
    )
    op.create_foreign_key(
        "purchases_corrects_purchase_id_fkey",
        NEW_TABLE,
        NEW_TABLE,
        ["corrects_purchase_id"],
        ["id"],
    )
    op.create_foreign_key(
        "purchases_inbox_item_id_fkey", NEW_TABLE, "inbox_items", ["inbox_item_id"], ["id"]
    )
    op.create_index("ix_purchases_supplier_id", NEW_TABLE, ["supplier_id"])
    op.create_index("ix_purchases_corrects_purchase_id", NEW_TABLE, ["corrects_purchase_id"])
    op.create_index("ix_purchases_inbox_item_id", NEW_TABLE, ["inbox_item_id"])

    checks = {
        "ck_purchases_kind": _in("kind", KINDS),
        "ck_purchases_status": _in("status", STATUSES),
        "ck_purchases_amount_sign": (
            "(kind <> 'invoice' OR amount > 0) AND (kind <> 'credit_note' OR amount < 0)"
        ),
        "ck_purchases_supplier_required": (
            "kind NOT IN ('invoice', 'credit_note') OR supplier_id IS NOT NULL"
        ),
        "ck_purchases_corrects_credit_note": "corrects_purchase_id IS NULL OR kind = 'credit_note'",
        "ck_purchases_currency": (
            "(currency = 'EUR' AND currency_amount IS NULL AND exchange_rate IS NULL "
            "AND exchange_rate_date IS NULL) OR (currency <> 'EUR' AND currency_amount IS NOT "
            "NULL AND exchange_rate IS NOT NULL AND exchange_rate > 0 AND exchange_rate_date IS "
            "NOT NULL)"
        ),
        "ck_purchases_withholding_together": (
            "(withholding_rate IS NULL AND withholding_base IS NULL AND withholding_amount IS "
            "NULL) OR (withholding_rate IS NOT NULL AND withholding_base IS NOT NULL AND "
            "withholding_amount IS NOT NULL)"
        ),
        "ck_purchases_fund_together": (
            "(fund_rate IS NULL AND fund_amount IS NULL) OR (fund_rate IS NOT NULL AND "
            "fund_amount IS NOT NULL)"
        ),
        "ck_purchases_vat_kinds_only": (
            "kind IN ('invoice', 'credit_note') OR (NOT split_payment AND NOT reverse_charge "
            "AND iva_period IS NULL AND fund_rate IS NULL)"
        ),
    }
    for name, expression in checks.items():
        op.create_check_constraint(name, NEW_TABLE, expression)

    op.create_table(
        LINES,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("purchase_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("rate", sa.Numeric(5, 2), nullable=False),
        sa.Column("nature", sa.String(4), nullable=True),
        sa.Column("taxable_amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("vat_amount", sa.Numeric(12, 2), nullable=False),
        sa.Column(
            "deductible_share", sa.Numeric(5, 2), server_default=sa.text("100"), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["purchase_id"], ["purchases.id"], ondelete="CASCADE"),
        sa.CheckConstraint("rate >= 0 AND rate <= 100", name="ck_purchase_vat_lines_rate"),
        sa.CheckConstraint(
            "deductible_share >= 0 AND deductible_share <= 100", name="ck_purchase_vat_lines_share"
        ),
        sa.CheckConstraint(
            "(rate = 0 AND nature IS NOT NULL AND vat_amount = 0) OR (rate > 0 AND nature IS NULL)",
            name="ck_purchase_vat_lines_nature",
        ),
    )
    op.create_index("ix_purchase_vat_lines_purchase", LINES, ["purchase_id", "position"])
    for statement in statements():
        op.execute(statement)

    _replace_in_entity_visible(_BRANCH_OLD, _BRANCH_NEW)
    _rename_discriminator("cost", "purchase")
    _rename_module_switch("module_costs", "module_purchases")


def downgrade() -> None:
    _rename_module_switch("module_purchases", "module_costs")
    _rename_discriminator("purchase", "cost")

    # What a cost cannot say is dropped; what it can is kept: the supplier's name stands in the
    # free-text column and a cancelled purchase is archived, so the readers that knew only
    # costs see no more and no less than they counted.
    op.execute(
        f"UPDATE {NEW_TABLE} p SET merchant_name = COALESCE(p.merchant_name, "
        "(SELECT left(o.legal_name, 200) FROM organizations o WHERE o.id = p.supplier_id)), "
        "deleted_at = CASE WHEN p.status = 'cancelled' "
        "THEN COALESCE(p.deleted_at, now()) ELSE p.deleted_at END"
    )
    op.drop_table(LINES)
    # Drop the constraint that names a column before the column, then the columns.
    for name in (
        "ck_purchases_kind",
        "ck_purchases_status",
        "ck_purchases_amount_sign",
        "ck_purchases_supplier_required",
        "ck_purchases_corrects_credit_note",
        "ck_purchases_currency",
        "ck_purchases_withholding_together",
        "ck_purchases_fund_together",
        "ck_purchases_vat_kinds_only",
    ):
        op.drop_constraint(name, NEW_TABLE, type_="check")
    for name in ("supplier_id", "corrects_purchase_id", "inbox_item_id"):
        op.drop_index(f"ix_purchases_{name}", table_name=NEW_TABLE)
        op.drop_constraint(f"purchases_{name}_fkey", NEW_TABLE, type_="foreignkey")
    for column in (
        "kind",
        "status",
        "supplier_id",
        "number",
        "received_on",
        "iva_period",
        "currency",
        "currency_amount",
        "exchange_rate",
        "exchange_rate_date",
        "cost_amount",
        "stamp_duty",
        "withholding_rate",
        "withholding_base",
        "withholding_amount",
        "fund_rate",
        "fund_amount",
        "split_payment",
        "reverse_charge",
        "corrects_purchase_id",
        "inbox_item_id",
        "buyer_pack_id",
        "buyer_pack_version",
        "buyer_regime_code",
    ):
        op.drop_column(NEW_TABLE, column)
    op.alter_column(NEW_TABLE, "merchant_name", new_column_name="supplier")
    _rename_table_objects(NEW_TABLE, NEW_TABLE, OLD_TABLE)
    op.rename_table(NEW_TABLE, OLD_TABLE)
    # After the table is back: the function body is checked against the catalog when it is
    # created, and `costs` has to exist for it.
    _replace_in_entity_visible(_BRANCH_NEW, _BRANCH_OLD)
