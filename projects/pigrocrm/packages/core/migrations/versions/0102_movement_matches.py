"""movement matches: a bank movement proposed to, confirmed against and undone from due items

Revision ID: 0102
Revises: 0101

REB-1042, § 3 of `2026-11-09-due-items-bank-and-cash-schedule-design.md`. Two new tables and one
new column, nothing backfilled.

`movement_matches` is one proposal or decision about one movement: `proposed` (by the importer or
by an agent, waiting for a person), `confirmed` (a person's act, which settles the items),
`dismissed` (a proposal that another one or a person's choice replaced) and `undone` (a confirmed
match a person took back, which reopens the items). The reasons it was ranked first, who proposed
it, who confirmed it and who closed it with what reason are columns of the row, so the history of
a movement is its rows. A partial unique index allows one live row (`proposed` or `confirmed`) per
movement. A match names its movement's legal entity by composite foreign key; the movement's
deletion (reverting its statement) takes the proposals with it.

`movement_match_items` is the due items a match pays and how much of the movement goes to each
(`amount`, a magnitude). On confirmation `settlement_id` names the payment it settled with, or,
when the item had already been marked paid by hand for the same amount, the payment it adopted
(`adopted`): undoing the match reverses the first and only detaches the evidence from the second.

`due_item_settlements.bank_movement_id` is the evidence: the movement a payment is proved by. It
is null for a payment marked by hand. The movement and the settlement name the same legal entity
by composite foreign key.

The two tables carry the `legal_entity_scope` policy, forced, as the bank tables do.
`statements()` is what the test template applies after `create_all`.

`downgrade` drops the two tables and the column and sets the movements it had matched back to
`unmatched`: the payments stay, without their evidence.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0102"
down_revision: str | Sequence[str] | None = "0101"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

MATCHES = "movement_matches"
ITEMS = "movement_match_items"
SETTLEMENTS = "due_item_settlements"
MOVEMENTS = "bank_movements"
POLICY_EXPRESSION = "scope_all() OR legal_entity_visible(legal_entity_id)"
STATES = ("proposed", "confirmed", "dismissed", "undone")
EVIDENCE_FK = "fk_due_item_settlements_movement_same_legal_entity"
EVIDENCE_INDEX = "ix_due_item_settlements_bank_movement_id"


def statements() -> list[str]:
    """The row-level security of the two tables, applied after `create_all` by the test
    template (`projects/pigrocrm/conftest.py`) exactly as `upgrade` applies it."""
    out: list[str] = []
    for table in (MATCHES, ITEMS):
        out += [
            f"CREATE POLICY legal_entity_scope ON {table} FOR ALL "
            f"USING ({POLICY_EXPRESSION}) WITH CHECK ({POLICY_EXPRESSION})",
            f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY",
            f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY",
        ]
    return out


def _timestamps() -> list[sa.Column]:  # type: ignore[type-arg]
    return [
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    ]


def upgrade() -> None:
    op.add_column(SETTLEMENTS, sa.Column("bank_movement_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        EVIDENCE_FK,
        SETTLEMENTS,
        MOVEMENTS,
        ["bank_movement_id", "legal_entity_id"],
        ["id", "legal_entity_id"],
    )
    op.create_index(EVIDENCE_INDEX, SETTLEMENTS, ["bank_movement_id"])

    op.create_table(
        MATCHES,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("bank_movement_id", sa.Uuid(), nullable=False),
        sa.Column("legal_entity_id", sa.Uuid(), nullable=False),
        sa.Column("state", sa.String(length=10), nullable=False),
        sa.Column(
            "reasons",
            sa.ARRAY(sa.String(length=40)),
            server_default=sa.text("'{}'::varchar[]"),
            nullable=False,
        ),
        sa.Column("note", sa.String(length=255), nullable=True),
        sa.Column("proposed_by_id", sa.Uuid(), nullable=True),
        sa.Column("proposed_by_type", sa.String(length=10), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("confirmed_by_id", sa.Uuid(), nullable=True),
        sa.Column("confirmed_by_type", sa.String(length=10), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_by_id", sa.Uuid(), nullable=True),
        sa.Column("closed_by_type", sa.String(length=10), nullable=True),
        sa.Column("close_reason", sa.String(length=255), nullable=True),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["legal_entity_id"], ["legal_entities.id"]),
        sa.UniqueConstraint("id", "legal_entity_id", name="uq_movement_matches_id_legal_entity_id"),
        sa.ForeignKeyConstraint(
            ["bank_movement_id", "legal_entity_id"],
            [f"{MOVEMENTS}.id", f"{MOVEMENTS}.legal_entity_id"],
            ondelete="CASCADE",
            name="fk_movement_matches_same_legal_entity",
        ),
        sa.CheckConstraint(f"state IN {STATES!r}", name="ck_movement_matches_state"),
        sa.CheckConstraint(
            "(confirmed_at IS NOT NULL) = (state IN ('confirmed', 'undone')) "
            "AND (confirmed_at IS NOT NULL) = (confirmed_by_type IS NOT NULL)",
            name="ck_movement_matches_confirmed",
        ),
        sa.CheckConstraint(
            "(closed_at IS NOT NULL) = (state IN ('dismissed', 'undone')) "
            "AND (closed_at IS NOT NULL) = (closed_by_type IS NOT NULL)",
            name="ck_movement_matches_closed",
        ),
    )
    op.create_index("ix_movement_matches_legal_entity_id", MATCHES, ["legal_entity_id"])
    op.create_index("ix_movement_matches_bank_movement_id", MATCHES, ["bank_movement_id"])
    op.create_index(
        "uq_movement_matches_live",
        MATCHES,
        ["bank_movement_id"],
        unique=True,
        postgresql_where=sa.text("state IN ('proposed', 'confirmed')"),
    )

    op.create_table(
        ITEMS,
        sa.Column("match_id", sa.Uuid(), nullable=False),
        sa.Column("due_item_id", sa.Uuid(), nullable=False),
        sa.Column("legal_entity_id", sa.Uuid(), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("settlement_id", sa.Uuid(), nullable=True),
        sa.Column("adopted", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.PrimaryKeyConstraint("match_id", "due_item_id"),
        sa.ForeignKeyConstraint(["legal_entity_id"], ["legal_entities.id"]),
        sa.ForeignKeyConstraint(["settlement_id"], [f"{SETTLEMENTS}.id"]),
        sa.ForeignKeyConstraint(
            ["match_id", "legal_entity_id"],
            [f"{MATCHES}.id", f"{MATCHES}.legal_entity_id"],
            ondelete="CASCADE",
            name="fk_movement_match_items_match",
        ),
        sa.ForeignKeyConstraint(
            ["due_item_id", "legal_entity_id"],
            ["due_items.id", "due_items.legal_entity_id"],
            name="fk_movement_match_items_due_item_same_legal_entity",
        ),
        sa.CheckConstraint("amount > 0", name="ck_movement_match_items_amount_positive"),
        sa.CheckConstraint(
            "NOT adopted OR settlement_id IS NOT NULL", name="ck_movement_match_items_adopted"
        ),
    )
    op.create_index("ix_movement_match_items_due_item_id", ITEMS, ["due_item_id"])
    op.create_index("ix_movement_match_items_legal_entity_id", ITEMS, ["legal_entity_id"])
    op.create_index("ix_movement_match_items_settlement_id", ITEMS, ["settlement_id"])
    for statement in statements():
        op.execute(statement)


def downgrade() -> None:
    # Nothing before this revision set `matched`: every such movement was matched by what is
    # being dropped, and the previous release has no way to take it back.
    op.execute("UPDATE bank_movements SET state = 'unmatched' WHERE state = 'matched'")
    op.drop_table(ITEMS)
    op.drop_table(MATCHES)
    op.drop_index(EVIDENCE_INDEX, table_name=SETTLEMENTS)
    op.drop_constraint(EVIDENCE_FK, SETTLEMENTS, type_="foreignkey")
    op.drop_column(SETTLEMENTS, "bank_movement_id")
