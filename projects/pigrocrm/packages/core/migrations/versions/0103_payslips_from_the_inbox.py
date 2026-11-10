"""payslips from the inbox: a payslip proposal, closed payroll months, the model's payslip switch

Revision ID: 0103
Revises: 0102

REB-1045 (spec 2026-11-09, staff, payroll and expenses, § 2 and § 4). Four changes, no backfill.

`proposals`: the closed set of target types gains `payslip`, and `ck_proposals_source` says a
payslip, like the three kinds before it, reads from an inbox item and never from a document.

`staff_payslips` gains `inbox_item_id` (nullable, indexed): the inbox item a payslip was accepted
from, whose file is the payslip's PDF. Figures a person typed leave it empty.

`payroll_month_closures` is one row per legal entity and month a person closed: when, by whom and
`snapshot`, the month as `get_payroll_month` answered it when it was closed, so closing keeps the
month's estimates as they were. It carries the `legal_entity_scope` policy, `scope_all()` first,
with `FORCE ROW LEVEL SECURITY`, as the staff tables do (0079, 0091); `statements()` is what the
test template applies after `create_all`.

`ai_model_config.read_payslips` is the second switch the staff design asks for before the space's
model reads a payslip (§ 2, «Where a payslip file lives»): off by default, set by an unscoped
administrator beside the privacy notice, and never a reason to switch the model itself on.

`downgrade` deletes the payslip proposals (and the timeline entries of those proposals), sends
the inbox items they were read from back to `new` with no kind, puts the checks back and drops
the columns and the table; the closures are lost, so those months are open again, and a payslip
that came from an item keeps its figures and loses the link to the file.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0103"
down_revision: str | Sequence[str] | None = "0102"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CLOSURES = "payroll_month_closures"
PAYSLIPS = "staff_payslips"
POLICY_EXPRESSION = "scope_all() OR legal_entity_visible(legal_entity_id)"
OLD_TARGET_TYPES = ("contract", "work_unit", "supplier_invoice", "receipt", "bank_statement")
NEW_TARGET_TYPES = (*OLD_TARGET_TYPES, "payslip")
OLD_INBOX_TYPES = ("supplier_invoice", "receipt", "bank_statement")
NEW_INBOX_TYPES = (*OLD_INBOX_TYPES, "payslip")
DOCUMENT_TYPES = ("contract", "work_unit")


def _in(column: str, values: Sequence[str]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def _source_check(inbox_types: Sequence[str]) -> str:
    return (
        f"({_in('target_type', DOCUMENT_TYPES)} AND document_id IS NOT NULL "
        "AND inbox_item_id IS NULL) "
        f"OR ({_in('target_type', inbox_types)} AND inbox_item_id IS NOT NULL "
        "AND document_id IS NULL)"
    )


def statements() -> list[str]:
    """The row-level security of `payroll_month_closures`, applied after `create_all` by the test
    template (`projects/pigrocrm/conftest.py`) exactly as `upgrade` applies it."""
    return [
        f"CREATE POLICY legal_entity_scope ON {CLOSURES} FOR ALL "
        f"USING ({POLICY_EXPRESSION}) WITH CHECK ({POLICY_EXPRESSION})",
        f"ALTER TABLE {CLOSURES} ENABLE ROW LEVEL SECURITY",
        f"ALTER TABLE {CLOSURES} FORCE ROW LEVEL SECURITY",
    ]


def upgrade() -> None:
    op.drop_constraint("ck_proposals_target_type", "proposals", type_="check")
    op.drop_constraint("ck_proposals_source", "proposals", type_="check")
    op.create_check_constraint(
        "ck_proposals_target_type", "proposals", _in("target_type", NEW_TARGET_TYPES)
    )
    op.create_check_constraint("ck_proposals_source", "proposals", _source_check(NEW_INBOX_TYPES))

    op.add_column(PAYSLIPS, sa.Column("inbox_item_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_staff_payslips_inbox_item_id", PAYSLIPS, "inbox_items", ["inbox_item_id"], ["id"]
    )
    op.create_index("ix_staff_payslips_inbox_item_id", PAYSLIPS, ["inbox_item_id"])

    op.create_table(
        CLOSURES,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("legal_entity_id", sa.Uuid(), nullable=False),
        sa.Column("month", sa.Date(), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_by_id", sa.Uuid(), nullable=True),
        sa.Column("snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["legal_entity_id"], ["legal_entities.id"]),
        sa.ForeignKeyConstraint(["closed_by_id"], ["users.id"]),
        sa.UniqueConstraint("legal_entity_id", "month", name="uq_payroll_month_closures_month"),
        sa.CheckConstraint("extract(day FROM month) = 1", name="ck_payroll_month_closures_month"),
    )
    op.create_index("ix_payroll_month_closures_legal_entity_id", CLOSURES, ["legal_entity_id"])
    for statement in statements():
        op.execute(statement)

    op.add_column(
        "ai_model_config",
        sa.Column("read_payslips", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("ai_model_config", "read_payslips")
    op.drop_table(CLOSURES)
    op.drop_index("ix_staff_payslips_inbox_item_id", table_name=PAYSLIPS)
    op.drop_constraint("fk_staff_payslips_inbox_item_id", PAYSLIPS, type_="foreignkey")
    op.drop_column(PAYSLIPS, "inbox_item_id")

    # A payslip proposal has no home before this revision: the items it was read from wait again.
    op.execute(
        "UPDATE inbox_items SET state = 'new', kind = NULL WHERE id IN "
        "(SELECT inbox_item_id FROM proposals WHERE target_type = 'payslip' "
        "AND status = 'pending')"
    )
    op.execute(
        "DELETE FROM activities WHERE entity_type = 'proposal' AND entity_id IN "
        "(SELECT id FROM proposals WHERE target_type = 'payslip')"
    )
    op.execute("DELETE FROM proposals WHERE target_type = 'payslip'")
    op.drop_constraint("ck_proposals_target_type", "proposals", type_="check")
    op.drop_constraint("ck_proposals_source", "proposals", type_="check")
    op.create_check_constraint(
        "ck_proposals_target_type", "proposals", _in("target_type", OLD_TARGET_TYPES)
    )
    op.create_check_constraint("ck_proposals_source", "proposals", _source_check(OLD_INBOX_TYPES))
