"""expense notes: a staff member's receipt from submission to reimbursement

Revision ID: 0103
Revises: 0102

REB-1046 (spec 2026-11-09, staff, payroll and expenses, § 3 and § 4). One new table and two
changes to `purchases`, nothing backfilled.

`expense_notes` is what a staff member spent for the company: the person who paid, the date,
the amount (kilometres times a typed rate on a mileage note), a cost category and, when there
is one, the inbox item that holds the receipt. It is `submitted`, then `approved` or
`rejected`, then `reimbursed`; an approval names the purchase and the due item it wrote, the
way of paying is chosen after it (`reimbursement_method`: a bank transfer on a date, or the
payslip of a month). A partial unique index lets a receipt back one live note, and a rejected
note frees it. The table carries the `legal_entity_scope` policy, forced, as every table that
reaches a legal entity does; `statements()` is what the test template applies after
`create_all`. The activity feed reads each row through `entity_visible(entity_type, entity_id,
...)`, whose `ELSE` is open: the note's timeline entries (`expense_note`) get a branch of their own,
so a scoped administrator sees only the entries of the legal entities they see (as 0079 does for
the staff member and 0094 for the accountant export). The branch is added to the definition the
database holds, so the revisions that rewrote the function before this one stay the source of the
rest.

`purchases` gains the kind `expense_note` (the kind check is replaced by one that knows it) and
`staff_member_id`, the staff member an expense note pays back. A check ties the two: a purchase
names a staff member exactly when it is an expense note.

`downgrade` takes the branch out and drops the table, then the purchases of kind `expense_note` with
the due items they wrote, their payments and the bank matches that paid them (the movements go back
to `unmatched`), since the previous release has no such kind, and puts the checks back.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0103"
down_revision: str | Sequence[str] | None = "0102"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NOTES = "expense_notes"
PURCHASES = "purchases"
POLICY_EXPRESSION = "scope_all() OR legal_entity_visible(legal_entity_id)"
KINDS_OLD = ("invoice", "credit_note", "receipt", "expense")
KINDS_NEW = (*KINDS_OLD, "expense_note")
KIND_CHECK = "ck_purchases_kind"
STAFF_CHECK = "ck_purchases_expense_note_staff"
STAFF_FK = "fk_purchases_staff_member_id_staff_members"
STAFF_INDEX = "ix_purchases_staff_member_id"


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(value) for value in values)})"


# `ELSE $3` is the one place in the function's text the branch can go before.
_ANCHOR = "ELSE $3"
_BRANCH = (
    "WHEN 'expense_note' THEN current_setting('pigrocrm.legal_entities', true) = '*' "
    "OR EXISTS (\n"
    "          SELECT 1 FROM expense_notes n WHERE n.id = $2 AND "
    "legal_entity_visible(n.legal_entity_id))\n"
    "        "
)
_FUNCTION = "'public.entity_visible(text, uuid, boolean)'::regprocedure"
# The due items an approval wrote: the purchases of kind `expense_note` are their source.
_NOTE_ITEMS = (
    "(SELECT id FROM due_items WHERE source_kind = 'purchase' AND source_id IN "
    "(SELECT id FROM purchases WHERE kind = 'expense_note'))"
)


def _rewrite_function(add: bool) -> str:
    """A `DO` block that re-creates `entity_visible` with the branch in (or out), and changes
    nothing when it already is (or already is not): applied by `upgrade`, `downgrade` and, for
    the test template, `statements`."""
    old, new = (_ANCHOR, _BRANCH + _ANCHOR) if add else (_BRANCH + _ANCHOR, _ANCHOR)
    check = "'expense_note'"
    guard = (
        f"position({check} in definition) = 0" if add else f"position({check} in definition) > 0"
    )
    return (
        "DO $do$\n"
        "DECLARE definition text := pg_get_functiondef(" + _FUNCTION + ");\n"
        "BEGIN\n"
        f"  IF {guard} THEN\n"
        f"    EXECUTE replace(definition, $old${old}$old$, $new${new}$new$);\n"
        "  END IF;\n"
        "END\n"
        "$do$"
    )


def statements() -> list[str]:
    """The row-level security of `expense_notes` and the branch of `entity_visible`, applied
    after `create_all` by the test template (`projects/pigrocrm/conftest.py`) exactly as
    `upgrade` applies them."""
    return [
        f"CREATE POLICY legal_entity_scope ON {NOTES} FOR ALL "
        f"USING ({POLICY_EXPRESSION}) WITH CHECK ({POLICY_EXPRESSION})",
        f"ALTER TABLE {NOTES} ENABLE ROW LEVEL SECURITY",
        f"ALTER TABLE {NOTES} FORCE ROW LEVEL SECURITY",
        _rewrite_function(add=True),
    ]


def upgrade() -> None:
    op.drop_constraint(KIND_CHECK, PURCHASES, type_="check")
    op.create_check_constraint(KIND_CHECK, PURCHASES, _in("kind", KINDS_NEW))
    op.add_column(PURCHASES, sa.Column("staff_member_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(STAFF_FK, PURCHASES, "staff_members", ["staff_member_id"], ["id"])
    op.create_index(STAFF_INDEX, PURCHASES, ["staff_member_id"])
    op.create_check_constraint(
        STAFF_CHECK, PURCHASES, "(kind = 'expense_note') = (staff_member_id IS NOT NULL)"
    )

    op.create_table(
        NOTES,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("staff_member_id", sa.Uuid(), nullable=False),
        sa.Column("legal_entity_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=10), nullable=False),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("category_id", sa.Uuid(), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("kilometres", sa.Numeric(8, 2), nullable=True),
        sa.Column("rate_per_km", sa.Numeric(8, 4), nullable=True),
        sa.Column("route_from", sa.String(length=120), nullable=True),
        sa.Column("route_to", sa.String(length=120), nullable=True),
        sa.Column("inbox_item_id", sa.Uuid(), nullable=True),
        sa.Column("submitted_by_id", sa.Uuid(), nullable=True),
        sa.Column("submitted_by_type", sa.String(length=10), nullable=False),
        sa.Column("decided_by_id", sa.Uuid(), nullable=True),
        sa.Column("decided_by_type", sa.String(length=10), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejection_reason", sa.String(length=255), nullable=True),
        sa.Column("self_approved", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("purchase_id", sa.Uuid(), nullable=True),
        sa.Column("due_item_id", sa.Uuid(), nullable=True),
        sa.Column("reimbursement_method", sa.String(length=10), nullable=True),
        sa.Column("reimbursement_date", sa.Date(), nullable=True),
        sa.Column("payroll_month", sa.Date(), nullable=True),
        sa.Column("reimbursed_on", sa.Date(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["staff_member_id"], ["staff_members.id"]),
        sa.ForeignKeyConstraint(["legal_entity_id"], ["legal_entities.id"]),
        sa.ForeignKeyConstraint(["category_id"], ["cost_categories.id"]),
        sa.ForeignKeyConstraint(["inbox_item_id"], ["inbox_items.id"]),
        sa.ForeignKeyConstraint(["purchase_id"], ["purchases.id"]),
        sa.ForeignKeyConstraint(["due_item_id"], ["due_items.id"]),
        sa.CheckConstraint("kind IN ('expense', 'mileage')", name="ck_expense_notes_kind"),
        sa.CheckConstraint(
            "status IN ('submitted', 'approved', 'rejected', 'reimbursed')",
            name="ck_expense_notes_status",
        ),
        sa.CheckConstraint("amount > 0", name="ck_expense_notes_amount"),
        sa.CheckConstraint(
            "(kind = 'mileage' AND kilometres IS NOT NULL AND kilometres > 0 "
            "AND rate_per_km IS NOT NULL AND rate_per_km > 0 AND route_from IS NOT NULL "
            "AND route_to IS NOT NULL AND inbox_item_id IS NULL) OR "
            "(kind = 'expense' AND kilometres IS NULL AND rate_per_km IS NULL "
            "AND route_from IS NULL AND route_to IS NULL)",
            name="ck_expense_notes_mileage",
        ),
        sa.CheckConstraint(
            "status = 'submitted' OR (decided_by_type IS NOT NULL AND decided_at IS NOT NULL)",
            name="ck_expense_notes_decided",
        ),
        sa.CheckConstraint(
            "(status = 'rejected') = (rejection_reason IS NOT NULL)",
            name="ck_expense_notes_rejection_reason",
        ),
        sa.CheckConstraint(
            "(status IN ('approved', 'reimbursed')) = (purchase_id IS NOT NULL AND due_item_id "
            "IS NOT NULL)",
            name="ck_expense_notes_approved_records",
        ),
        sa.CheckConstraint(
            "reimbursement_method IS NULL OR reimbursement_method IN ('transfer', 'payslip')",
            name="ck_expense_notes_method",
        ),
        sa.CheckConstraint(
            "reimbursement_method IS NULL OR status IN ('approved', 'reimbursed')",
            name="ck_expense_notes_method_status",
        ),
        sa.CheckConstraint(
            "(reimbursement_method IS NULL AND reimbursement_date IS NULL "
            "AND payroll_month IS NULL) OR "
            "(reimbursement_method = 'transfer' AND reimbursement_date IS NOT NULL "
            "AND payroll_month IS NULL) OR "
            "(reimbursement_method = 'payslip' AND reimbursement_date IS NOT NULL "
            "AND payroll_month IS NOT NULL AND extract(day FROM payroll_month) = 1)",
            name="ck_expense_notes_way",
        ),
        sa.CheckConstraint(
            "(status = 'reimbursed') = (reimbursed_on IS NOT NULL) "
            "AND (status <> 'reimbursed' OR reimbursement_method IS NOT NULL)",
            name="ck_expense_notes_reimbursed",
        ),
        sa.CheckConstraint(
            "NOT self_approved OR status <> 'submitted'", name="ck_expense_notes_self"
        ),
    )
    op.create_index("ix_expense_notes_legal_entity_status", NOTES, ["legal_entity_id", "status"])
    op.create_index("ix_expense_notes_staff_member_id", NOTES, ["staff_member_id"])
    op.create_index("ix_expense_notes_purchase_id", NOTES, ["purchase_id"])
    op.create_index("ix_expense_notes_due_item_id", NOTES, ["due_item_id"])
    op.create_index(
        "uq_expense_notes_inbox_item_id",
        NOTES,
        ["inbox_item_id"],
        unique=True,
        postgresql_where=sa.text("inbox_item_id IS NOT NULL AND status <> 'rejected'"),
    )
    for statement in statements():
        op.execute(statement)


def downgrade() -> None:
    op.execute(_rewrite_function(add=False))
    op.drop_table(NOTES)
    # The previous release has no such kind: what an approval wrote goes with it. A bank match
    # that paid one of the due items goes first (its movements wait for a match again), then the
    # payments and the items, then the purchases.
    op.execute(f"DELETE FROM movement_match_items WHERE due_item_id IN {_NOTE_ITEMS}")
    op.execute(
        "DELETE FROM movement_matches m WHERE NOT EXISTS "
        "(SELECT 1 FROM movement_match_items i WHERE i.match_id = m.id)"
    )
    op.execute(
        "UPDATE bank_movements SET state = 'unmatched' WHERE state = 'matched' AND NOT EXISTS "
        "(SELECT 1 FROM movement_matches m WHERE m.bank_movement_id = bank_movements.id "
        "AND m.state = 'confirmed')"
    )
    op.execute(f"DELETE FROM due_item_settlements WHERE due_item_id IN {_NOTE_ITEMS}")
    op.execute(
        "DELETE FROM due_items WHERE source_kind = 'purchase' AND source_id IN "
        "(SELECT id FROM purchases WHERE kind = 'expense_note')"
    )
    op.execute("DELETE FROM purchases WHERE kind = 'expense_note'")
    op.drop_constraint(STAFF_CHECK, PURCHASES, type_="check")
    op.drop_index(STAFF_INDEX, table_name=PURCHASES)
    op.drop_constraint(STAFF_FK, PURCHASES, type_="foreignkey")
    op.drop_column(PURCHASES, "staff_member_id")
    op.drop_constraint(KIND_CHECK, PURCHASES, type_="check")
    op.create_check_constraint(KIND_CHECK, PURCHASES, _in("kind", KINDS_OLD))
