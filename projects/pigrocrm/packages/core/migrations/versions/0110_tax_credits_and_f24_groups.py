"""tax credits and F24 groups: the rows behind a negative tax item and an F24 paid by one movement

Revision ID: 0110
Revises: 0109

REB-1076, § 2 and § 5 of `2026-11-09-taxes-and-contributions-design.md`. Four new tables and one
column.

* `tax_credits`: what a due item of source kind `tax_credit` points at, with the origin of the
  credit (a withholding suffered on sales in a tax year, or the IVA credit one settled period
  leaves to the next).
* `f24_groups`: the due items of one date paid with one F24. `open` until the bank movement that
  pays its net is confirmed, then `settled` with the movement and the day; `dissolved` once a
  person ungroups it.
* `f24_group_items`: a group's lines, each with the F24 code it was grouped under (null when the
  pack carries none, which the surfaces read as «codice da verificare»). A due item is in at most
  one live line.
* `f24_group_receipts`: the inbox items (kind `tax_document`) that are a group's receipts.
* `movement_match_items.credit`: the item takes its amount off the movement instead of out of it,
  which is what a credit in a grouped F24 does when one movement pays the net.

Nothing is backfilled: no credit or group exists before this card. The four tables carry the
row-level policy of every table that reaches a legal entity (`legal_entity_visible`, forced).
`statements()` is what the test template applies after `create_all`.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0110"
down_revision: str | Sequence[str] | None = "0109"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ("tax_credits", "f24_groups", "f24_group_items", "f24_group_receipts")
POLICY_EXPRESSION = "legal_entity_visible(legal_entity_id)"
CREDIT_ORIGINS = ("iva_carry_forward", "withholding_suffered")
GROUP_STATES = ("open", "settled", "dissolved")
CODE_KINDS = ("tax_code", "inps_causale")


def statements() -> list[str]:
    """The row-level security of the four tables, applied after `create_all` by the test
    template (`projects/pigrocrm/conftest.py`) exactly as `upgrade` applies it."""
    out: list[str] = []
    for table in TABLES:
        out += [
            f"CREATE POLICY legal_entity_scope ON {table} FOR ALL "
            f"USING ({POLICY_EXPRESSION}) WITH CHECK ({POLICY_EXPRESSION})",
            f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY",
            f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY",
        ]
    return out


def _timestamps() -> list[sa.Column[object]]:
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
        "tax_credits",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("legal_entity_id", sa.Uuid(), nullable=False),
        sa.Column("origin", sa.String(length=30), nullable=False),
        sa.Column("tax", sa.String(length=30), nullable=False),
        sa.Column("tax_year", sa.Integer(), nullable=False),
        sa.Column("iva_settlement_id", sa.Uuid(), nullable=True),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["legal_entity_id"], ["legal_entities.id"]),
        sa.ForeignKeyConstraint(["iva_settlement_id"], ["iva_settlements.id"]),
        sa.CheckConstraint(f"origin IN {CREDIT_ORIGINS!r}", name="ck_tax_credits_origin"),
        sa.CheckConstraint("amount > 0", name="ck_tax_credits_amount_positive"),
        sa.CheckConstraint(
            "(origin = 'iva_carry_forward') = (iva_settlement_id IS NOT NULL)",
            name="ck_tax_credits_iva_settlement",
        ),
    )
    op.create_index("ix_tax_credits_legal_entity_id", "tax_credits", ["legal_entity_id"])
    op.create_index(
        "uq_tax_credits_iva_settlement",
        "tax_credits",
        ["iva_settlement_id"],
        unique=True,
        postgresql_where=sa.text("iva_settlement_id IS NOT NULL"),
    )
    op.create_index(
        "uq_tax_credits_withholding_year",
        "tax_credits",
        ["legal_entity_id", "tax_year"],
        unique=True,
        postgresql_where=sa.text("origin = 'withholding_suffered'"),
    )

    op.create_table(
        "f24_groups",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("legal_entity_id", sa.Uuid(), nullable=False),
        sa.Column("payment_date", sa.Date(), nullable=False),
        sa.Column("state", sa.String(length=10), server_default="open", nullable=False),
        sa.Column("note", sa.String(length=255), nullable=True),
        sa.Column("bank_movement_id", sa.Uuid(), nullable=True),
        sa.Column("settled_on", sa.Date(), nullable=True),
        sa.Column("created_by_id", sa.Uuid(), nullable=True),
        sa.Column("created_by_type", sa.String(length=10), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["legal_entity_id"], ["legal_entities.id"]),
        sa.ForeignKeyConstraint(
            ["bank_movement_id", "legal_entity_id"],
            ["bank_movements.id", "bank_movements.legal_entity_id"],
            name="fk_f24_groups_movement_same_legal_entity",
        ),
        sa.UniqueConstraint("id", "legal_entity_id", name="uq_f24_groups_id_legal_entity_id"),
        sa.CheckConstraint(f"state IN {GROUP_STATES!r}", name="ck_f24_groups_state"),
        sa.CheckConstraint(
            "(state = 'settled') = (bank_movement_id IS NOT NULL AND settled_on IS NOT NULL)",
            name="ck_f24_groups_settled",
        ),
    )
    op.create_index("ix_f24_groups_legal_entity_id", "f24_groups", ["legal_entity_id"])
    op.create_index(
        "ix_f24_groups_schedule", "f24_groups", ["legal_entity_id", "state", "payment_date"]
    )

    op.create_table(
        "f24_group_items",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("group_id", sa.Uuid(), nullable=False),
        sa.Column("due_item_id", sa.Uuid(), nullable=False),
        sa.Column("legal_entity_id", sa.Uuid(), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("line", sa.String(length=40), nullable=True),
        sa.Column("code", sa.String(length=10), nullable=True),
        sa.Column("code_kind", sa.String(length=15), nullable=True),
        sa.Column("code_label", sa.String(length=255), nullable=True),
        sa.Column("reference_year", sa.Integer(), nullable=True),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["legal_entity_id"], ["legal_entities.id"]),
        sa.ForeignKeyConstraint(
            ["group_id", "legal_entity_id"],
            ["f24_groups.id", "f24_groups.legal_entity_id"],
            name="fk_f24_group_items_same_legal_entity",
        ),
        sa.ForeignKeyConstraint(
            ["due_item_id", "legal_entity_id"],
            ["due_items.id", "due_items.legal_entity_id"],
            name="fk_f24_group_items_due_item_same_legal_entity",
        ),
        sa.CheckConstraint("amount <> 0", name="ck_f24_group_items_amount_not_zero"),
        sa.CheckConstraint(
            f"code_kind IS NULL OR code_kind IN {CODE_KINDS!r}", name="ck_f24_group_items_code_kind"
        ),
        sa.CheckConstraint(
            "(code IS NULL) = (code_kind IS NULL)", name="ck_f24_group_items_code_pair"
        ),
    )
    op.create_index("ix_f24_group_items_legal_entity_id", "f24_group_items", ["legal_entity_id"])
    op.create_index("ix_f24_group_items_group_id", "f24_group_items", ["group_id"])
    op.create_index(
        "uq_f24_group_items_live",
        "f24_group_items",
        ["due_item_id"],
        unique=True,
        postgresql_where=sa.text("released_at IS NULL"),
    )

    op.create_table(
        "f24_group_receipts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("group_id", sa.Uuid(), nullable=False),
        sa.Column("legal_entity_id", sa.Uuid(), nullable=False),
        sa.Column("inbox_item_id", sa.Uuid(), nullable=False),
        sa.Column("attached_by_id", sa.Uuid(), nullable=True),
        sa.Column("attached_by_type", sa.String(length=10), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["legal_entity_id"], ["legal_entities.id"]),
        sa.ForeignKeyConstraint(["inbox_item_id"], ["inbox_items.id"]),
        sa.ForeignKeyConstraint(
            ["group_id", "legal_entity_id"],
            ["f24_groups.id", "f24_groups.legal_entity_id"],
            name="fk_f24_group_receipts_same_legal_entity",
        ),
        sa.UniqueConstraint("group_id", "inbox_item_id", name="uq_f24_group_receipts_item"),
    )
    op.create_index(
        "ix_f24_group_receipts_legal_entity_id", "f24_group_receipts", ["legal_entity_id"]
    )
    op.create_index("ix_f24_group_receipts_inbox_item_id", "f24_group_receipts", ["inbox_item_id"])

    op.add_column(
        "movement_match_items",
        sa.Column("credit", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    for statement in statements():
        op.execute(statement)


def downgrade() -> None:
    op.drop_column("movement_match_items", "credit")
    for table in reversed(TABLES):
        op.drop_table(table)
