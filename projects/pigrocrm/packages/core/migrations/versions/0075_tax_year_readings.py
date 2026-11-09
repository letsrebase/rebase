"""taxpayer parameters on the profile, the fiscal year end on the legal entity, frozen estimates

Revision ID: 0075
Revises: 0074

REB-974 (taxes and contributions design § 1). Three additions, none of which changes a figure
that is computed today:

- `fiscal_profile.inps_category` and `isa`: the taxpayer's parameters that are not the three
  the estimate already reads. Nullable, `NULL` meaning «not stated»: no row is given a value
  it never had.
- `legal_entities.fiscal_year_end_month` and `fiscal_year_end_day`: 31 December for every
  row that exists, by the column default, which is what a calendar year already was.
- `fiscal_estimates`: one row per legal entity and tax year, the year's income estimate frozen
  with the pack, its version, the law year and the parameters it was computed with. Empty: a
  year is frozen the first time its estimate is computed after this revision.

The new table carries the row-level policy of every table that reaches a legal entity
(spec 2026-10-03 § 4): `legal_entity_visible(legal_entity_id)`, `FORCE ROW LEVEL SECURITY`,
exactly the `fiscal_profile` policy. `statements()` is what the test template applies after
`create_all`.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0075"
down_revision: str | Sequence[str] | None = "0074"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "fiscal_estimates"
POLICY_EXPRESSION = "legal_entity_visible(legal_entity_id)"
FISCAL_YEAR_END_CHECK = (
    "(fiscal_year_end_month IN (1, 3, 5, 7, 8, 10, 12)"
    " AND fiscal_year_end_day BETWEEN 1 AND 31)"
    " OR (fiscal_year_end_month IN (4, 6, 9, 11)"
    " AND fiscal_year_end_day BETWEEN 1 AND 30)"
    " OR (fiscal_year_end_month = 2 AND fiscal_year_end_day BETWEEN 1 AND 29)"
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


def upgrade() -> None:
    op.add_column("fiscal_profile", sa.Column("inps_category", sa.String(length=30), nullable=True))
    op.add_column("fiscal_profile", sa.Column("isa", sa.Boolean(), nullable=True))
    op.create_check_constraint(
        "ck_fiscal_profile_inps_category",
        "fiscal_profile",
        "inps_category IN ('gestione_separata', 'artigiani_commercianti')",
    )

    op.add_column(
        "legal_entities",
        sa.Column(
            "fiscal_year_end_month", sa.SmallInteger(), server_default=sa.text("12"), nullable=False
        ),
    )
    op.add_column(
        "legal_entities",
        sa.Column(
            "fiscal_year_end_day", sa.SmallInteger(), server_default=sa.text("31"), nullable=False
        ),
    )
    op.create_check_constraint(
        "ck_legal_entities_fiscal_year_end", "legal_entities", FISCAL_YEAR_END_CHECK
    )

    op.create_table(
        TABLE,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("legal_entity_id", sa.Uuid(), nullable=False),
        sa.Column("tax_year", sa.Integer(), nullable=False),
        sa.Column("pack_id", sa.String(length=40), nullable=False),
        sa.Column("pack_version", sa.String(length=10), nullable=False),
        sa.Column("law_from_year", sa.Integer(), nullable=False),
        sa.Column("parameters", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("figures", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
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
        sa.UniqueConstraint("legal_entity_id", "tax_year", name="uq_fiscal_estimates_year"),
    )
    for statement in statements():
        op.execute(statement)


def downgrade() -> None:
    op.drop_table(TABLE)
    op.drop_constraint("ck_legal_entities_fiscal_year_end", "legal_entities", type_="check")
    op.drop_column("legal_entities", "fiscal_year_end_day")
    op.drop_column("legal_entities", "fiscal_year_end_month")
    op.drop_constraint("ck_fiscal_profile_inps_category", "fiscal_profile", type_="check")
    op.drop_column("fiscal_profile", "isa")
    op.drop_column("fiscal_profile", "inps_category")
