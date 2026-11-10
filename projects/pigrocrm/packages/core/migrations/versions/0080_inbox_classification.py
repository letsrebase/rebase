"""inbox classification: proposals from the inbox, what the classifier read, the model settings

Revision ID: 0080
Revises: 0079

REB-972 (design 2026-11-09, incoming documents inbox, § 3 and § 3.5).

`proposals`: `document_id` becomes nullable, `inbox_item_id` and `owner_user_id` are added, the
closed set of target types gains `supplier_invoice`, `receipt` and `bank_statement`, and two
checks say what each reads from: `ck_proposals_source` (a contract or a day from a document,
the three new types from an inbox item, never both) and
`ck_proposals_contract_id_required_for_work_unit`, which replaces
`ck_proposals_contract_id_required_unless_contract` (the old one named a rule that was true
of two target types and is not of five). A partial unique index allows one pending proposal
per inbox item. No existing row changes: every one has a document and a contract or a day.

`inbox_items`: `extracted_text` (what a PDF carries, for the user's agent), `parsed_fields`
(what a FatturaPA states, shown while the module that files it does not exist), and the two
columns a paid reading needs: `read_started_at` (a claim, so two runs do not pay for one file)
and `read_attempts` (so a file that never reads does not starve the others).

`proposals` row-level security: the policy and the `proposal` branch of `entity_visible` read a
proposal's document and contract, and a proposal read from the inbox has neither. They now
read an inbox proposal's visibility from its item, whose own policy then applies (a scoped
uploader sees and creates the proposals of the items they uploaded). `statements()` is what the
test template applies after `create_all`.

`ai_model_config` and `ai_model_usage`: PigroCRM's own model, off by default (design § 3.2).
One configuration row at most, the key sealed; one usage row per call, with the cost reserved
before the network.

`downgrade` deletes the proposals that read from an inbox item (they have no document to keep
the `NOT NULL` with, and the timeline entries of those proposals), sends the items they were
read for back to `new`, restores the policy and drops everything above.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0080"
down_revision: str | Sequence[str] | None = "0079"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_TARGET_TYPES = ("contract", "work_unit")
NEW_TARGET_TYPES = (*OLD_TARGET_TYPES, "supplier_invoice", "receipt", "bank_statement")
INBOX_TARGET_TYPES = ("supplier_invoice", "receipt", "bank_statement")
PROPOSAL_ENTITY_OLD = (
    "SELECT 1 FROM proposals p WHERE p.id = $2 AND document_visible(p.document_id)\n"
    "          AND (p.contract_id IS NULL OR contract_visible(p.contract_id)))"
)
PROPOSAL_ENTITY_NEW = (
    "SELECT 1 FROM proposals p WHERE p.id = $2 AND (\n"
    "          p.inbox_item_id IS NOT NULL AND EXISTS (\n"
    "            SELECT 1 FROM inbox_items i WHERE i.id = p.inbox_item_id)\n"
    "          OR p.inbox_item_id IS NULL AND document_visible(p.document_id)\n"
    "          AND (p.contract_id IS NULL OR contract_visible(p.contract_id))))"
)
POLICY_OLD = (
    "scope_all() OR (document_visible(document_id) AND (contract_id IS NULL OR "
    "contract_visible(contract_id)))"
)
POLICY_NEW = (
    "scope_all() OR (inbox_item_id IS NOT NULL AND EXISTS (SELECT 1 FROM inbox_items i "
    "WHERE i.id = proposals.inbox_item_id)) OR (inbox_item_id IS NULL AND "
    "document_visible(document_id) AND (contract_id IS NULL OR contract_visible(contract_id)))"
)
PROVIDERS = ("anthropic", "openai")
USAGE_TASKS = ("read_document", "test")
USAGE_STATUSES = ("reserved", "done", "failed")


def _in(column: str, values: Sequence[str]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def _replace_entity_visible(old: str, new: str) -> str:
    """A `DO` block that rewrites the `proposal` branch of `entity_visible` in the function's
    own definition (read back from the catalog, so whatever the earlier revisions left is what
    changes) and refuses to go on if the branch it expects is not there."""
    return (
        "DO $do$ DECLARE def text; BEGIN "
        "SELECT pg_get_functiondef('entity_visible(text, uuid, boolean)'::regprocedure) INTO def; "
        f"IF position($old${old}$old$ in def) = 0 THEN "
        "RAISE EXCEPTION 'entity_visible has no proposal branch to rewrite'; END IF; "
        f"EXECUTE replace(def, $old${old}$old$, $new${new}$new$); END $do$"
    )


def _policy(expression: str) -> list[str]:
    return [
        "DROP POLICY legal_entity_scope ON proposals",
        f"CREATE POLICY legal_entity_scope ON proposals FOR ALL "
        f"USING ({expression}) WITH CHECK ({expression})",
    ]


def statements() -> list[str]:
    """The row-level security this revision changes, applied after `create_all` by the test
    template (`projects/pigrocrm/conftest.py`) exactly as `upgrade` applies it."""
    return [_replace_entity_visible(PROPOSAL_ENTITY_OLD, PROPOSAL_ENTITY_NEW), *_policy(POLICY_NEW)]


def upgrade() -> None:
    op.add_column("inbox_items", sa.Column("extracted_text", sa.Text(), nullable=True))
    op.add_column(
        "inbox_items", sa.Column("parsed_fields", postgresql.JSONB(astext_type=sa.Text()))
    )
    op.add_column(
        "inbox_items", sa.Column("read_started_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "inbox_items",
        sa.Column("read_attempts", sa.SmallInteger(), server_default=sa.text("0"), nullable=False),
    )

    op.alter_column("proposals", "document_id", existing_type=sa.Uuid(), nullable=True)
    op.add_column("proposals", sa.Column("inbox_item_id", sa.Uuid(), nullable=True))
    op.add_column("proposals", sa.Column("owner_user_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_proposals_inbox_item_id", "proposals", "inbox_items", ["inbox_item_id"], ["id"]
    )
    op.create_foreign_key(
        "fk_proposals_owner_user_id", "proposals", "users", ["owner_user_id"], ["id"]
    )
    op.create_index("ix_proposals_inbox_item_id", "proposals", ["inbox_item_id"])
    op.create_index("ix_proposals_owner_user_id", "proposals", ["owner_user_id"])
    op.create_index(
        "uq_proposals_pending_inbox_item",
        "proposals",
        ["inbox_item_id"],
        unique=True,
        postgresql_where=sa.text("status = 'pending' AND inbox_item_id IS NOT NULL"),
    )
    op.drop_constraint("ck_proposals_target_type", "proposals", type_="check")
    op.drop_constraint("ck_proposals_contract_id_required_unless_contract", "proposals", "check")
    op.create_check_constraint(
        "ck_proposals_target_type", "proposals", _in("target_type", NEW_TARGET_TYPES)
    )
    op.create_check_constraint(
        "ck_proposals_contract_id_required_for_work_unit",
        "proposals",
        "target_type <> 'work_unit' OR contract_id IS NOT NULL",
    )
    op.create_check_constraint(
        "ck_proposals_source",
        "proposals",
        f"({_in('target_type', OLD_TARGET_TYPES)} AND document_id IS NOT NULL "
        "AND inbox_item_id IS NULL) "
        f"OR ({_in('target_type', INBOX_TARGET_TYPES)} AND inbox_item_id IS NOT NULL "
        "AND document_id IS NULL)",
    )

    for statement in statements():
        op.execute(statement)

    op.create_table(
        "ai_model_config",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("singleton", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("provider", sa.String(length=20), nullable=True),
        sa.Column("model", sa.String(length=100), nullable=True),
        sa.Column("api_key_sealed", sa.Text(), nullable=True),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("daily_cap_eur", sa.Numeric(10, 2), nullable=True),
        sa.Column("input_eur_per_mtok", sa.Numeric(10, 4), nullable=True),
        sa.Column("output_eur_per_mtok", sa.Numeric(10, 4), nullable=True),
        sa.Column("privacy_acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("privacy_acknowledged_by_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("singleton"),
        sa.ForeignKeyConstraint(["privacy_acknowledged_by_id"], ["users.id"]),
        sa.CheckConstraint("singleton", name="ck_ai_model_config_singleton"),
        sa.CheckConstraint(
            f"provider IS NULL OR {_in('provider', PROVIDERS)}", name="ck_ai_model_config_provider"
        ),
        sa.CheckConstraint(
            "daily_cap_eur IS NULL OR daily_cap_eur >= 0", name="ck_ai_model_config_cap"
        ),
        sa.CheckConstraint(
            "NOT enabled OR (provider IS NOT NULL AND model IS NOT NULL "
            "AND api_key_sealed IS NOT NULL AND daily_cap_eur IS NOT NULL "
            "AND input_eur_per_mtok IS NOT NULL AND output_eur_per_mtok IS NOT NULL "
            "AND privacy_acknowledged_at IS NOT NULL)",
            name="ck_ai_model_config_enabled_complete",
        ),
    )
    op.create_table(
        "ai_model_usage",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("task", sa.String(length=20), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("status", sa.String(length=10), nullable=False),
        sa.Column("inbox_item_id", sa.Uuid(), nullable=True),
        sa.Column("estimated_cost_eur", sa.Numeric(12, 6), nullable=False),
        sa.Column("cost_eur", sa.Numeric(12, 6), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(_in("task", USAGE_TASKS), name="ck_ai_model_usage_task"),
        sa.CheckConstraint(_in("status", USAGE_STATUSES), name="ck_ai_model_usage_status"),
    )
    op.create_index("ix_ai_model_usage_day", "ai_model_usage", ["day"])


def downgrade() -> None:
    op.drop_table("ai_model_usage")
    op.drop_table("ai_model_config")
    for statement in (
        _replace_entity_visible(PROPOSAL_ENTITY_NEW, PROPOSAL_ENTITY_OLD),
        *_policy(POLICY_OLD),
    ):
        op.execute(statement)
    # The items a proposal was read for wait to be read again, and the proposals go.
    op.execute(
        "UPDATE inbox_items SET state = 'new', kind = NULL, reason = NULL WHERE id IN "
        "(SELECT inbox_item_id FROM proposals WHERE inbox_item_id IS NOT NULL) "
        "AND state = 'proposed'"
    )
    op.execute(
        "DELETE FROM activities WHERE entity_type = 'proposal' AND entity_id IN "
        "(SELECT id FROM proposals WHERE document_id IS NULL)"
    )
    op.execute("DELETE FROM proposals WHERE document_id IS NULL")
    op.drop_constraint("ck_proposals_source", "proposals", type_="check")
    op.drop_constraint("ck_proposals_contract_id_required_for_work_unit", "proposals", "check")
    op.drop_constraint("ck_proposals_target_type", "proposals", type_="check")
    op.create_check_constraint(
        "ck_proposals_target_type", "proposals", _in("target_type", OLD_TARGET_TYPES)
    )
    op.create_check_constraint(
        "ck_proposals_contract_id_required_unless_contract",
        "proposals",
        "target_type = 'contract' OR contract_id IS NOT NULL",
    )
    op.drop_index("uq_proposals_pending_inbox_item", table_name="proposals")
    op.drop_index("ix_proposals_owner_user_id", table_name="proposals")
    op.drop_index("ix_proposals_inbox_item_id", table_name="proposals")
    op.drop_constraint("fk_proposals_owner_user_id", "proposals", type_="foreignkey")
    op.drop_constraint("fk_proposals_inbox_item_id", "proposals", type_="foreignkey")
    op.drop_column("proposals", "owner_user_id")
    op.drop_column("proposals", "inbox_item_id")
    op.alter_column("proposals", "document_id", existing_type=sa.Uuid(), nullable=False)
    op.drop_column("inbox_items", "read_attempts")
    op.drop_column("inbox_items", "read_started_at")
    op.drop_column("inbox_items", "parsed_fields")
    op.drop_column("inbox_items", "extracted_text")
