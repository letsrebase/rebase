"""organizations: the `customers` table, and every name carried by it, becomes `organizations`

Revision ID: 0077
Revises: 0076

REB-969, first milestone of REB-961 (`2026-11-09-suppliers-purchases-and-iva-design.md`,
«Rename blast radius»). A pure rename: nothing about what a row means or who may read it
changes. The same company can be a customer and, later, a supplier, so the registry is called
what it is. The foreign keys keep the name of the role they play (`deals.customer_id` still
says who is billed), and so do the columns about a customer as such (`customer_share_cap`).

What this revision renames:

- the table, its primary key, its foreign keys and its indexes (`ix_customers_*`);
- the five SQL functions the row-level security reads: `customer_visible`,
  `customer_archived` and `active_deals_of_customer` are renamed, and `person_visible` and
  `entity_visible` are rewritten, because a function body is text that `ALTER TABLE RENAME`
  never touches (`0068_english_names.py`). The policies hold the functions by object id, so
  they follow the rename and are not recreated;
- the discriminator string `'customer'`, in the five columns that hold an entity type
  (`activities`, `notes`, `gmail_message_links`, `email_drafts`, `field_definitions`) and in
  the check constraint of `notes`.

`downgrade` renames everything back, row for row.

`statements()` is not needed: the test template creates `organizations` with `create_all` and
applies the earlier revisions' policy statements through `rewrite()`, which says them in the
new names (`projects/pigrocrm/conftest.py`).
"""

import re
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0077"
down_revision: str | Sequence[str] | None = "0076"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_TABLE = "customers"
NEW_TABLE = "organizations"

# Functions that change name, with their argument lists.
RENAMED_FUNCTIONS = (
    ("customer_visible", "organization_visible", "uuid"),
    ("customer_archived", "organization_archived", "uuid"),
    ("active_deals_of_customer", "active_deals_of_organization", "uuid"),
)
# Functions that keep their name and mention the table or the discriminator in their body.
REWRITTEN_FUNCTIONS = (
    ("person_visible", "uuid"),
    ("entity_visible", "text, uuid, boolean"),
)

# Tables with a column that holds an entity type, and the column.
DISCRIMINATOR_TABLES = (
    "activities",
    "notes",
    "gmail_message_links",
    "email_drafts",
    "field_definitions",
)
NOTES_CHECK = "ck_notes_entity_type"


def _words(old: str, new: str) -> list[tuple[re.Pattern[str], str]]:
    """The substitutions that turn a piece of SQL from the `old` vocabulary to the `new`."""
    pairs = [(OLD_TABLE, NEW_TABLE)]
    pairs += [(a, b) for a, b, _ in RENAMED_FUNCTIONS]
    pairs = [(a, b) if old == "customers" else (b, a) for a, b in pairs]
    rules = [(re.compile(rf"\b{re.escape(a)}\b"), b) for a, b in pairs]
    literal = (
        ("'customer'", "'organization'") if old == "customers" else ("'organization'", "'customer'")
    )
    rules.append((re.compile(re.escape(literal[0])), literal[1]))
    return rules


FORWARD = _words("customers", "organizations")
BACKWARD = _words("organizations", "customers")


def _apply(sql: str, rules: list[tuple[re.Pattern[str], str]]) -> str:
    for pattern, replacement in rules:
        sql = pattern.sub(replacement, sql)
    return sql


def rewrite(sql: str) -> str:
    """A statement of an earlier revision, in the names this one gives. The test template
    runs the policy statements of 0068, 0070 and 0073 through it after `create_all`."""
    return _apply(sql, FORWARD)


def _rename_table_objects(table: str, old: str, new: str) -> None:
    """The constraints and indexes of `table` whose name carries `old`: a constraint first
    (which renames the index that backs a primary key), then the plain indexes."""
    bind = op.get_bind()
    constraints = bind.execute(
        sa.text(
            "SELECT conname FROM pg_constraint WHERE conrelid = CAST(:table AS regclass) "
            "AND (conname LIKE :plain OR conname LIKE :prefixed)"
        ),
        {
            "table": table,
            "plain": f"{old}\\_%",
            "prefixed": f"fk\\_{old}\\_%",
        },
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


def _rewrite_function(name: str, arguments: str, rules: list[tuple[re.Pattern[str], str]]) -> None:
    """Recreate a function from its own definition, as the catalog prints it, with the words
    of `rules` substituted: its attributes (`SECURITY DEFINER`, `SET`) come along."""
    definition = (
        op.get_bind()
        .execute(
            sa.text("SELECT pg_get_functiondef(CAST(:signature AS regprocedure))"),
            {"signature": f"{name}({arguments})"},
        )
        .scalar_one()
    )
    op.execute(sa.text(_apply(definition, rules).replace(":", "\\:")))


def _move(
    table_from: str,
    table_to: str,
    old: str,
    new: str,
    functions: tuple[tuple[str, str, str], ...],
    rules: list[tuple[re.Pattern[str], str]],
    value_from: str,
    value_to: str,
) -> None:
    op.drop_constraint(NOTES_CHECK, "notes", type_="check")
    op.rename_table(table_from, table_to)
    _rename_table_objects(table_to, old, new)

    for before, after, arguments in functions:
        op.execute(f"ALTER FUNCTION {before}({arguments}) RENAME TO {after}")
        _rewrite_function(after, arguments, rules)
    for name, arguments in REWRITTEN_FUNCTIONS:
        _rewrite_function(name, arguments, rules)

    for table in DISCRIMINATOR_TABLES:
        op.execute(
            f"UPDATE {table} SET entity_type = '{value_to}' WHERE entity_type = '{value_from}'"
        )
    op.create_check_constraint(
        NOTES_CHECK,
        "notes",
        f"entity_type IN ('deal', '{value_to}', 'person')",
    )


def upgrade() -> None:
    _move(
        OLD_TABLE,
        NEW_TABLE,
        "customers",
        "organizations",
        RENAMED_FUNCTIONS,
        FORWARD,
        "customer",
        "organization",
    )


def downgrade() -> None:
    backward = tuple((b, a, args) for a, b, args in RENAMED_FUNCTIONS)
    _move(
        NEW_TABLE,
        OLD_TABLE,
        "organizations",
        "customers",
        backward,
        BACKWARD,
        "organization",
        "customer",
    )
