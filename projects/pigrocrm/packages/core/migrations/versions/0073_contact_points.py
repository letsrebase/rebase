"""contact_points: several ways to reach a person or a customer

Revision ID: 0073
Revises: 0072

REB-940 (REB-939). A person had one `email`, one `phone` and one `linkedin`, a customer one
`email` and one `phone`. They now own an ordered list of contact points (a kind, a value, a
label from a fixed list, a note, one primary per kind), and those five columns are gone: the
primary point of a kind is what `Person.email`, `.phone`, `.linkedin` and `Customer.email`,
`.phone` answer.

`normalized` is what every lookup and both unique indexes compare: the lowercased address,
the digits of a phone with a leading `+` kept, the profile as typed in lower case (the
service writes the canonical LinkedIn form from now on; a value that predates it is not
rewritten here, since SQL cannot do what the hub's `normalise_linkedin` does).

The table carries the `legal_entity_scope` policy through its parent, `person_visible` or
`customer_visible`, behind the `scope_all()` fast path, with `FORCE ROW LEVEL SECURITY`, as
every child table does (0048, 0068, 0070); `statements()` is what the test template applies
after `create_all`.

Existing data: every non-blank value of the five columns becomes a primary point labelled
`work`, email first, then phone, then LinkedIn. A phone with no digit is not a phone, and is not
dropped in silence: it becomes the note of the owner's email point (`Telefono: <text>`) when
the owner has one, and otherwise is left out and named in the migration's log, one line per
owner id (`legacy phone with no digit not carried over`). Nothing else is validated: a value
that today's rules would refuse is carried over as it is. The lowercase and trigram indexes on
`email` move to `contact_points`.
`downgrade` restores the five columns, their indexes and the primary values; the points beyond
the first of a kind, labels and notes are lost with the table.
"""

import logging
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

log = logging.getLogger("alembic.runtime.migration")

revision: str = "0073"
down_revision: str | Sequence[str] | None = "0072"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "contact_points"
# The «all» fast path first, as every through-the-parent policy since 0049 has it.
POLICY_EXPRESSION = (
    "scope_all() OR (CASE WHEN person_id IS NOT NULL THEN person_visible(person_id) "
    "ELSE customer_visible(customer_id) END)"
)

PHONE_KEY = (
    "CASE WHEN btrim({c}) LIKE '+%' THEN '+' ELSE '' END || regexp_replace({c}, '\\D', '', 'g')"
)


def statements() -> list[str]:
    """The row-level security of the table, applied after `create_all` by the test
    template (`projects/pigrocrm/conftest.py`) exactly as `upgrade` applies it."""
    return [
        f"CREATE POLICY legal_entity_scope ON {TABLE} FOR ALL "
        f"USING ({POLICY_EXPRESSION}) WITH CHECK ({POLICY_EXPRESSION})",
        f"ALTER TABLE {TABLE} ENABLE ROW LEVEL SECURITY",
        f"ALTER TABLE {TABLE} FORCE ROW LEVEL SECURITY",
    ]


def _backfill(owner: str, table: str, kinds: Sequence[tuple[str, str, str]]) -> None:
    """One primary `work` point per non-blank value, in the order of `kinds`
    (`(kind, column, comparison key)`), numbered from 0 per owner."""
    owner_column = f"{owner}_id"
    other = "customer_id" if owner == "person" else "person_id"
    parts = " UNION ALL ".join(
        f"SELECT id AS owner_id, '{kind}' AS kind, btrim({column}) AS value, "
        f"{key} AS normalized, {weight} AS weight FROM {table} "
        f"WHERE btrim(coalesce({column}, '')) <> ''"
        + (f" AND regexp_replace({column}, '\\D', '', 'g') <> ''" if kind == "phone" else "")
        for weight, (kind, column, key) in enumerate(kinds)
    )
    op.execute(
        f"INSERT INTO {TABLE} (id, {owner_column}, {other}, kind, value, normalized, label, "
        "note, is_primary, position) "
        "SELECT gen_random_uuid(), owner_id, NULL, kind, value, normalized, 'work', NULL, true, "
        "row_number() OVER (PARTITION BY owner_id ORDER BY weight) - 1 "
        f"FROM ({parts}) AS found"
    )


def _carry_digitless_phones(owner: str, table: str) -> None:
    """A phone with no digit goes to its owner's email point as a note, or into the log."""
    owner_column = f"{owner}_id"
    digitless = (
        "btrim(coalesce(t.phone, '')) <> '' AND regexp_replace(t.phone, '\\D', '', 'g') = ''"
    )
    op.execute(
        f"UPDATE {TABLE} p SET note = left('Telefono: ' || btrim(t.phone), 200) "
        f"FROM {table} t WHERE p.{owner_column} = t.id AND p.kind = 'email' AND p.is_primary "
        f"AND {digitless}"
    )
    left_out = (
        op.get_bind()
        .execute(
            sa.text(
                f"SELECT t.id, btrim(t.phone) FROM {table} t WHERE {digitless} "
                f"AND NOT EXISTS (SELECT 1 FROM {TABLE} p "
                f"WHERE p.{owner_column} = t.id AND p.kind = 'email')"
            )
        )
        .all()
    )
    for owner_id, phone in left_out:
        log.info(
            "0073: legacy phone with no digit not carried over: %s %s, %r", owner, owner_id, phone
        )


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("person_id", sa.Uuid(), nullable=True),
        sa.Column("customer_id", sa.Uuid(), nullable=True),
        sa.Column("kind", sa.String(length=10), nullable=False),
        sa.Column("value", sa.String(length=320), nullable=False),
        sa.Column("normalized", sa.String(length=320), nullable=False),
        sa.Column("label", sa.String(length=10), nullable=False),
        sa.Column("note", sa.String(length=200), nullable=True),
        sa.Column("is_primary", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["person_id"], ["people.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["customer_id"], ["customers.id"], ondelete="CASCADE"),
        sa.CheckConstraint(
            "num_nonnulls(person_id, customer_id) = 1", name="ck_contact_points_one_owner"
        ),
        sa.CheckConstraint("kind IN ('email', 'phone', 'linkedin')", name="ck_contact_points_kind"),
        sa.CheckConstraint(
            "label IN ('work', 'personal', 'billing', 'other')", name="ck_contact_points_label"
        ),
    )
    op.create_index("ix_contact_points_person_id", TABLE, ["person_id"])
    op.create_index("ix_contact_points_customer_id", TABLE, ["customer_id"])
    op.create_index(
        "uq_contact_points_person_value",
        TABLE,
        ["person_id", "kind", "normalized"],
        unique=True,
        postgresql_where=sa.text("person_id IS NOT NULL"),
    )
    op.create_index(
        "uq_contact_points_customer_value",
        TABLE,
        ["customer_id", "kind", "normalized"],
        unique=True,
        postgresql_where=sa.text("customer_id IS NOT NULL"),
    )
    op.create_index(
        "uq_contact_points_person_primary",
        TABLE,
        ["person_id", "kind"],
        unique=True,
        postgresql_where=sa.text("is_primary AND person_id IS NOT NULL"),
    )
    op.create_index(
        "uq_contact_points_customer_primary",
        TABLE,
        ["customer_id", "kind"],
        unique=True,
        postgresql_where=sa.text("is_primary AND customer_id IS NOT NULL"),
    )
    op.create_index("ix_contact_points_kind_normalized", TABLE, ["kind", "normalized"])
    op.create_index(
        "ix_contact_points_value_trgm",
        TABLE,
        ["value"],
        postgresql_using="gin",
        postgresql_ops={"value": "gin_trgm_ops"},
    )
    op.create_index(
        "ix_contact_points_normalized_trgm",
        TABLE,
        ["normalized"],
        postgresql_using="gin",
        postgresql_ops={"normalized": "gin_trgm_ops"},
    )

    # Before the policy goes on and before the columns go: the owner is bound by `FORCE`.
    _backfill(
        "person",
        "people",
        (
            ("email", "email", "lower(btrim(email))"),
            ("phone", "phone", PHONE_KEY.format(c="phone")),
            ("linkedin", "linkedin", "lower(btrim(linkedin))"),
        ),
    )
    _backfill(
        "customer",
        "customers",
        (
            ("email", "email", "lower(btrim(email))"),
            ("phone", "phone", PHONE_KEY.format(c="phone")),
        ),
    )
    _carry_digitless_phones("person", "people")
    _carry_digitless_phones("customer", "customers")
    for statement in statements():
        op.execute(statement)

    for index in ("ix_people_email", "ix_people_email_lower", "ix_people_email_trgm"):
        op.execute(f"DROP INDEX {index}")
    for index in ("ix_customers_email", "ix_customers_email_lower", "ix_customers_email_trgm"):
        op.execute(f"DROP INDEX {index}")
    op.drop_column("people", "email")
    op.drop_column("people", "phone")
    op.drop_column("people", "linkedin")
    op.drop_column("customers", "email")
    op.drop_column("customers", "phone")


def downgrade() -> None:
    op.add_column("people", sa.Column("email", sa.String(length=320), nullable=True))
    op.add_column("people", sa.Column("phone", sa.String(length=40), nullable=True))
    op.add_column("people", sa.Column("linkedin", sa.String(length=255), nullable=True))
    op.add_column("customers", sa.Column("email", sa.String(length=320), nullable=True))
    op.add_column("customers", sa.Column("phone", sa.String(length=40), nullable=True))
    for table, owner, columns in (
        ("people", "person_id", ("email", "phone", "linkedin")),
        ("customers", "customer_id", ("email", "phone")),
    ):
        for column in columns:
            op.execute(
                f"UPDATE {table} SET {column} = left(point.value, "
                f"{255 if column == 'linkedin' else 40 if column == 'phone' else 320}) "
                f"FROM {TABLE} AS point WHERE point.{owner} = {table}.id "
                f"AND point.kind = '{column}' AND point.is_primary"
            )
    op.create_index("ix_people_email", "people", ["email"])
    op.create_index("ix_customers_email", "customers", ["email"])
    for table in ("people", "customers"):
        op.execute(
            f"CREATE INDEX ix_{table}_email_lower ON {table} (lower(email)) "
            "WHERE deleted_at IS NULL"
        )
        op.execute(
            f"CREATE INDEX ix_{table}_email_trgm ON {table} "
            "USING gin (email gin_trgm_ops) WHERE deleted_at IS NULL"
        )
    op.drop_table(TABLE)
