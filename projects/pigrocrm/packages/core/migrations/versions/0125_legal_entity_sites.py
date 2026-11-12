"""legal entity sites and where a staff member works, for the IRAP split between regions

Revision ID: 0125
Revises: 0124

REB-1092. A company with establishments in more than one region splits the IRAP base by the pay of
the staff assigned to each of them for at least three months (art. 4 c. 2 D.Lgs. 446/1997).

* `legal_entity_sites`: a place of business beside the registered office, with its `region` (the
  key of the IRAP rate table: an ISTAT region code, or `BZ`/`TN`), and an optional city and
  address. Unique on `(id, legal_entity_id)` so the assignment below can point at the pair.
* `staff_members.site_id` and `staff_members.at_registered_office`: where the person works. Both
  empty is «not stated»; a person works at one place, a check says so, and the composite foreign
  key keeps the site inside the person's own legal entity.

Nothing is backfilled: no row says where a person works. The new table carries the row-level policy
of every table that reaches a legal entity (`legal_entity_visible`, forced); `statements()` is
what the test template applies after `create_all`. `downgrade` drops the columns and the table,
the sites with them.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0125"
down_revision: str | Sequence[str] | None = "0124"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SITES = "legal_entity_sites"
POLICY_EXPRESSION = "legal_entity_visible(legal_entity_id)"


def statements() -> list[str]:
    """The row-level security of the sites table, applied after `create_all` by the test
    template (`projects/pigrocrm/conftest.py`) exactly as `upgrade` applies it."""
    return [
        f"CREATE POLICY legal_entity_scope ON {SITES} FOR ALL "
        f"USING ({POLICY_EXPRESSION}) WITH CHECK ({POLICY_EXPRESSION})",
        f"ALTER TABLE {SITES} ENABLE ROW LEVEL SECURITY",
        f"ALTER TABLE {SITES} FORCE ROW LEVEL SECURITY",
    ]


def upgrade() -> None:
    op.create_table(
        SITES,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("legal_entity_id", sa.Uuid(), nullable=False),
        sa.Column("region", sa.String(length=2), nullable=False),
        sa.Column("city", sa.String(length=120), nullable=True),
        sa.Column("address", sa.String(length=255), nullable=True),
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
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["legal_entity_id"], ["legal_entities.id"]),
        sa.UniqueConstraint(
            "id", "legal_entity_id", name="uq_legal_entity_sites_id_legal_entity_id"
        ),
    )
    op.create_index("ix_legal_entity_sites_legal_entity_id", SITES, ["legal_entity_id"])
    for statement in statements():
        op.execute(statement)

    op.add_column("staff_members", sa.Column("site_id", sa.Uuid(), nullable=True))
    op.add_column(
        "staff_members",
        sa.Column(
            "at_registered_office", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
    )
    op.create_check_constraint(
        "ck_staff_members_one_workplace",
        "staff_members",
        "NOT (at_registered_office AND site_id IS NOT NULL)",
    )
    op.create_foreign_key(
        "fk_staff_members_site_same_legal_entity",
        "staff_members",
        SITES,
        ["site_id", "legal_entity_id"],
        ["id", "legal_entity_id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_staff_members_site_same_legal_entity", "staff_members", type_="foreignkey"
    )
    op.drop_constraint("ck_staff_members_one_workplace", "staff_members", type_="check")
    op.drop_column("staff_members", "at_registered_office")
    op.drop_column("staff_members", "site_id")
    op.drop_table(SITES)
