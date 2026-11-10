"""bank_accounts, bank_statements, bank_movements, statement_movements, organization_ibans

Revision ID: 0084
Revises: 0081

REB-998 (spec 2026-11-09, due items and the cash schedule, § 2). Five new tables, no change to
an existing one and nothing backfilled.

`bank_accounts` are ours: always a legal entity, the IBAN (unique), currency, the opening
balance with its date, how statements arrive and how often, and the senders they come from.
`bank_statements` is one file read for an account: the covered period, the balances it
states, its format, the inbox item it came from and a state (`proposed`, `imported`,
`reverted`). `bank_movements` is one line, signed, with the counterparty's name and IBAN, the
bank's reference and a state; its `dedup_key` is unique per account, which is what keeps the
same movement of two overlapping statements once. `statement_movements` says which statements
carry which movement. `organization_ibans` is the counterparty's list of accounts, learned from
supplier XML or proposed from a matched movement.

A statement and a movement name the same legal entity as their account, by composite foreign
key to `(id, legal_entity_id)`. The four bank tables carry the `legal_entity_scope` policy,
`scope_all()` first, with `FORCE ROW LEVEL SECURITY`; `organization_ibans` follows its
organisation through `organization_visible`, as `engagements` does. `statements()` is what the
test template applies after `create_all`. Plain `STABLE` functions only; no definer function.

`downgrade` drops the five tables and everything in them.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0084"
down_revision: str | Sequence[str] | None = "0081"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ACCOUNTS = "bank_accounts"
STATEMENTS = "bank_statements"
MOVEMENTS = "bank_movements"
LINKS = "statement_movements"
IBANS = "organization_ibans"

IBAN_PATTERN = "^[A-Z]{2}[0-9]{2}[A-Z0-9]{11,30}$"
ENTITY_POLICY = "scope_all() OR legal_entity_visible(legal_entity_id)"
ORGANIZATION_POLICY = "scope_all() OR organization_visible(organization_id)"


def statements() -> list[str]:
    """The row-level security of the five tables, applied after `create_all` by the test
    template (`projects/pigrocrm/conftest.py`) exactly as `upgrade` applies it."""
    out: list[str] = []
    for table, expression in (
        (ACCOUNTS, ENTITY_POLICY),
        (STATEMENTS, ENTITY_POLICY),
        (MOVEMENTS, ENTITY_POLICY),
        (LINKS, ENTITY_POLICY),
        (IBANS, ORGANIZATION_POLICY),
    ):
        out += [
            f"CREATE POLICY legal_entity_scope ON {table} FOR ALL "
            f"USING ({expression}) WITH CHECK ({expression})",
            f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY",
            f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY",
        ]
    return out


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    ]


def upgrade() -> None:
    op.create_table(
        ACCOUNTS,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("legal_entity_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("bank", sa.String(length=255), nullable=False),
        sa.Column("iban", sa.String(length=34), nullable=False),
        sa.Column("currency", sa.String(length=3), server_default="EUR", nullable=False),
        sa.Column("opening_balance", sa.Numeric(14, 2), nullable=True),
        sa.Column("opening_balance_date", sa.Date(), nullable=True),
        sa.Column("arrival", sa.String(length=10), server_default="upload", nullable=False),
        sa.Column("cadence", sa.String(length=10), server_default="monthly", nullable=False),
        sa.Column(
            "senders",
            sa.ARRAY(sa.String(length=320)),
            server_default=sa.text("'{}'::varchar[]"),
            nullable=False,
        ),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["legal_entity_id"], ["legal_entities.id"]),
        sa.UniqueConstraint("id", "legal_entity_id", name="uq_bank_accounts_id_legal_entity_id"),
        sa.CheckConstraint(f"iban ~ '{IBAN_PATTERN}'", name="ck_bank_accounts_iban"),
        sa.CheckConstraint("currency ~ '^[A-Z]{3}$'", name="ck_bank_accounts_currency"),
        sa.CheckConstraint(
            "arrival IN ('upload', 'mailbox', 'api')", name="ck_bank_accounts_arrival"
        ),
        sa.CheckConstraint("cadence IN ('monthly', 'quarterly')", name="ck_bank_accounts_cadence"),
        sa.CheckConstraint(
            "(opening_balance IS NULL) = (opening_balance_date IS NULL)",
            name="ck_bank_accounts_opening_pair",
        ),
    )
    op.create_index("uq_bank_accounts_iban", ACCOUNTS, ["iban"], unique=True)
    op.create_index("ix_bank_accounts_legal_entity_id", ACCOUNTS, ["legal_entity_id"])

    op.create_table(
        STATEMENTS,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("bank_account_id", sa.Uuid(), nullable=False),
        sa.Column("legal_entity_id", sa.Uuid(), nullable=False),
        sa.Column("period_from", sa.Date(), nullable=False),
        sa.Column("period_to", sa.Date(), nullable=False),
        sa.Column("opening_balance", sa.Numeric(14, 2), nullable=True),
        sa.Column("closing_balance", sa.Numeric(14, 2), nullable=True),
        sa.Column("format", sa.String(length=30), nullable=False),
        sa.Column("inbox_item_id", sa.Uuid(), nullable=True),
        sa.Column("state", sa.String(length=10), nullable=False),
        sa.Column("reverted_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["legal_entity_id"], ["legal_entities.id"]),
        sa.ForeignKeyConstraint(["inbox_item_id"], ["inbox_items.id"]),
        sa.UniqueConstraint("id", "legal_entity_id", name="uq_bank_statements_id_legal_entity_id"),
        sa.ForeignKeyConstraint(
            ["bank_account_id", "legal_entity_id"],
            [f"{ACCOUNTS}.id", f"{ACCOUNTS}.legal_entity_id"],
            name="fk_bank_statements_same_legal_entity",
        ),
        sa.CheckConstraint(
            "state IN ('proposed', 'imported', 'reverted')", name="ck_bank_statements_state"
        ),
        sa.CheckConstraint("period_to >= period_from", name="ck_bank_statements_period_order"),
        sa.CheckConstraint(
            "(state = 'reverted') = (reverted_at IS NOT NULL)",
            name="ck_bank_statements_reverted_pair",
        ),
    )
    op.create_index(
        "ix_bank_statements_account_period",
        STATEMENTS,
        ["bank_account_id", "period_from", "period_to"],
    )
    op.create_index("ix_bank_statements_legal_entity_id", STATEMENTS, ["legal_entity_id"])
    op.create_index("ix_bank_statements_inbox_item_id", STATEMENTS, ["inbox_item_id"])

    op.create_table(
        MOVEMENTS,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("bank_account_id", sa.Uuid(), nullable=False),
        sa.Column("legal_entity_id", sa.Uuid(), nullable=False),
        sa.Column("booking_date", sa.Date(), nullable=False),
        sa.Column("value_date", sa.Date(), nullable=True),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("counterparty_name", sa.String(length=255), nullable=True),
        sa.Column("counterparty_iban", sa.String(length=34), nullable=True),
        sa.Column("description", sa.String(length=1000), nullable=True),
        sa.Column("bank_reference", sa.String(length=255), nullable=True),
        sa.Column("balance_after", sa.Numeric(14, 2), nullable=True),
        sa.Column("dedup_key", sa.String(length=64), nullable=False),
        sa.Column("state", sa.String(length=10), server_default="unmatched", nullable=False),
        sa.Column("ignored_reason", sa.String(length=255), nullable=True),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["legal_entity_id"], ["legal_entities.id"]),
        sa.UniqueConstraint("id", "legal_entity_id", name="uq_bank_movements_id_legal_entity_id"),
        sa.ForeignKeyConstraint(
            ["bank_account_id", "legal_entity_id"],
            [f"{ACCOUNTS}.id", f"{ACCOUNTS}.legal_entity_id"],
            name="fk_bank_movements_same_legal_entity",
        ),
        sa.CheckConstraint("amount <> 0", name="ck_bank_movements_amount_not_zero"),
        sa.CheckConstraint("currency ~ '^[A-Z]{3}$'", name="ck_bank_movements_currency"),
        sa.CheckConstraint(
            "state IN ('unmatched', 'matched', 'transfer', 'ignored')",
            name="ck_bank_movements_state",
        ),
        sa.CheckConstraint(
            f"counterparty_iban IS NULL OR counterparty_iban ~ '{IBAN_PATTERN}'",
            name="ck_bank_movements_counterparty_iban",
        ),
        sa.CheckConstraint(
            "(state = 'ignored') = (ignored_reason IS NOT NULL)",
            name="ck_bank_movements_ignored_reason",
        ),
    )
    op.create_index(
        "uq_bank_movements_account_dedup_key",
        MOVEMENTS,
        ["bank_account_id", "dedup_key"],
        unique=True,
    )
    op.create_index(
        "ix_bank_movements_account_booking_date", MOVEMENTS, ["bank_account_id", "booking_date"]
    )
    op.create_index("ix_bank_movements_legal_entity_id", MOVEMENTS, ["legal_entity_id"])
    op.create_index(
        "ix_bank_movements_unmatched",
        MOVEMENTS,
        ["legal_entity_id"],
        postgresql_where=sa.text("state = 'unmatched'"),
    )
    op.create_index("ix_bank_movements_counterparty_iban", MOVEMENTS, ["counterparty_iban"])

    op.create_table(
        LINKS,
        sa.Column("statement_id", sa.Uuid(), nullable=False),
        sa.Column("movement_id", sa.Uuid(), nullable=False),
        sa.Column("legal_entity_id", sa.Uuid(), nullable=False),
        sa.PrimaryKeyConstraint("statement_id", "movement_id"),
        sa.ForeignKeyConstraint(["legal_entity_id"], ["legal_entities.id"]),
        sa.ForeignKeyConstraint(
            ["statement_id", "legal_entity_id"],
            [f"{STATEMENTS}.id", f"{STATEMENTS}.legal_entity_id"],
            ondelete="CASCADE",
            name="fk_statement_movements_statement",
        ),
        sa.ForeignKeyConstraint(
            ["movement_id", "legal_entity_id"],
            [f"{MOVEMENTS}.id", f"{MOVEMENTS}.legal_entity_id"],
            ondelete="CASCADE",
            name="fk_statement_movements_movement",
        ),
    )
    op.create_index("ix_statement_movements_movement_id", LINKS, ["movement_id"])
    op.create_index("ix_statement_movements_legal_entity_id", LINKS, ["legal_entity_id"])

    op.create_table(
        IBANS,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("iban", sa.String(length=34), nullable=False),
        sa.Column("holder_name", sa.String(length=255), nullable=True),
        sa.Column("source", sa.String(length=15), nullable=False),
        sa.Column("state", sa.String(length=10), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.CheckConstraint(f"iban ~ '{IBAN_PATTERN}'", name="ck_organization_ibans_iban"),
        sa.CheckConstraint("state IN ('proposed', 'accepted')", name="ck_organization_ibans_state"),
        sa.CheckConstraint(
            "source IN ('supplier_xml', 'movement', 'manual')", name="ck_organization_ibans_source"
        ),
    )
    op.create_index("ix_organization_ibans_organization_id", IBANS, ["organization_id"])
    op.create_index(
        "uq_organization_ibans_organization_iban", IBANS, ["organization_id", "iban"], unique=True
    )
    op.create_index("ix_organization_ibans_iban", IBANS, ["iban"])
    for statement in statements():
        op.execute(statement)


def downgrade() -> None:
    op.drop_table(IBANS)
    op.drop_table(LINKS)
    op.drop_table(MOVEMENTS)
    op.drop_table(STATEMENTS)
    op.drop_table(ACCOUNTS)
