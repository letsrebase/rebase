"""organization_roles: roles on organizations, one row per VAT number, terms and fiscal kinds

Revision ID: 0078
Revises: 0077

REB-970, § 1 of `2026-11-09-suppliers-purchases-and-iva-design.md`. An organization is what it
is to us in a set of roles (`customer`, `supplier`, `freelancer`) instead of a table for each,
so a company that is both is one row.

What this revision adds to `organizations`:

- `roles`, a `varchar[]` with a check (at least one, only the three names) and a GIN index that
  serves the role filter. Every existing row becomes `{customer}`: that is the column's default,
  so no row is rewritten by hand;
- `vat_number` widened from 11 to 20 characters, so a foreign number such as `NL123456789B01`
  fits;
- `supplier_payment_days` and `supplier_payment_end_of_month`, the terms the organization grants
  us as a supplier (`payment_days` and `payment_end_of_month` stay what it is given as a
  customer);
- `supplier_kind`, `supplier_area` and `supplier_supplies`, what a payment to a supplier
  involves, and `customer_kind` and `is_withholding_agent`, what a sale to a customer involves,
  each kind checked against its list;
- the unique index `uq_organizations_country_vat` on the country and the VAT number in its
  normalised form (upper case, letters and digits only), over live rows with a VAT number.

**The index never fails a deploy.** Rows that already share a country and a normalised VAT
number cannot be made unique by a migration, and choosing which one is the real company is a
person's decision (design § 1, «Duplicates»). So the index is built only when no such rows
exist. Otherwise the revision says so on stderr, builds nothing, and the boot's
`ensure-space-defaults` builds it once the last duplicate is gone; `pigrocrm
duplicate-organizations --slug <space>` lists them, and `OrganizationService` refuses a new
duplicate in the meantime.

`downgrade` drops the index, the constraints and the columns. It does not narrow `vat_number`
again: a foreign number longer than 11 characters would be cut, and a wider column harms
nothing. A row that was only a supplier is a customer again, since that is all the old table
could say.

`statements()` is not needed: the test template creates the columns, the constraints and the
index with `create_all`.
"""

import sys
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0078"
down_revision: str | Sequence[str] | None = "0077"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "organizations"
INDEX = "uq_organizations_country_vat"
ROLES_INDEX = "ix_organizations_roles"

# The expression the index is built on, as SQL: the same rule as `roles.vat_key` and
# `roles.country_key` today, written out so this revision keeps meaning what it meant.
COUNTRY_KEY = "upper(country)"
VAT_KEY = "regexp_replace(upper(vat_number), '[^0-9A-Z]', '', 'g')"
LIVE = "deleted_at IS NULL AND vat_number IS NOT NULL"

CHECKS = {
    "ck_organizations_roles": (
        "cardinality(roles) >= 1 "
        "AND roles <@ ARRAY['customer', 'supplier', 'freelancer']::varchar[]"
    ),
    "ck_organizations_supplier_kind": (
        "supplier_kind IS NULL OR supplier_kind IN "
        "('company', 'professional', 'flat_rate', 'occasional', 'foreign')"
    ),
    "ck_organizations_supplier_area": (
        "supplier_area IS NULL OR supplier_area IN ('eu', 'extra_eu')"
    ),
    "ck_organizations_supplier_supplies": (
        "supplier_supplies IS NULL OR supplier_supplies IN ('goods', 'services')"
    ),
    "ck_organizations_customer_kind": (
        "customer_kind IS NULL OR customer_kind IN ('business', 'consumer', 'public_body')"
    ),
}


def upgrade() -> None:
    op.add_column(
        TABLE,
        sa.Column(
            "roles",
            postgresql.ARRAY(sa.String(20)),
            nullable=False,
            server_default=sa.text("ARRAY['customer']::varchar[]"),
        ),
    )
    op.alter_column(
        TABLE,
        "vat_number",
        existing_type=sa.String(11),
        type_=sa.String(20),
        existing_nullable=True,
    )
    op.add_column(TABLE, sa.Column("supplier_payment_days", sa.Integer(), nullable=True))
    op.add_column(
        TABLE,
        sa.Column(
            "supplier_payment_end_of_month",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    for column in ("supplier_kind", "supplier_area", "supplier_supplies", "customer_kind"):
        op.add_column(TABLE, sa.Column(column, sa.String(20), nullable=True))
    op.add_column(
        TABLE,
        sa.Column(
            "is_withholding_agent", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
    )
    for name, condition in CHECKS.items():
        op.create_check_constraint(name, TABLE, condition)
    op.create_index(ROLES_INDEX, TABLE, ["roles"], postgresql_using="gin")
    _build_vat_index()


def _build_vat_index() -> None:
    bind = op.get_bind()
    duplicated = bind.execute(
        sa.text(
            f"SELECT count(*) FROM (SELECT 1 FROM {TABLE} WHERE {LIVE} "
            f"GROUP BY {COUNTRY_KEY}, {VAT_KEY} HAVING count(*) > 1) AS shared"
        )
    ).scalar_one()
    if duplicated:
        print(
            f"organizations: {duplicated} VAT numbers are held by more than one organization, "
            f"so {INDEX} is not built; list them with `pigrocrm duplicate-organizations "
            "--slug <space>` and resolve them, the next boot builds the index",
            file=sys.stderr,
        )
        return
    op.execute(f"CREATE UNIQUE INDEX {INDEX} ON {TABLE} ({COUNTRY_KEY}, {VAT_KEY}) WHERE {LIVE}")


def downgrade() -> None:
    op.execute(f"DROP INDEX IF EXISTS {INDEX}")
    op.drop_index(ROLES_INDEX, table_name=TABLE)
    for name in CHECKS:
        op.drop_constraint(name, TABLE, type_="check")
    for column in (
        "is_withholding_agent",
        "customer_kind",
        "supplier_supplies",
        "supplier_area",
        "supplier_kind",
        "supplier_payment_end_of_month",
        "supplier_payment_days",
        "roles",
    ):
        op.drop_column(TABLE, column)
