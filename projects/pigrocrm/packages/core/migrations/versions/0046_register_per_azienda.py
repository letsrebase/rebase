"""The invoice register, the import and the fiscal regime per azienda (REB-619).

Revision ID: 0046
Revises: 0045

Spec 2026-10-03 §1.3, §1.4, §1.5 and §2 (step 3 and the `invoices` part of step 4),
milestone 2 of §9. What changes:

1. `invoices` gains `azienda_id`, backfilled from the space's default azienda and then
   `NOT NULL`, indexed; the partial unique index on `(anno, numero)` becomes one on
   `(azienda_id, anno, numero)`, so «2026/1» exists once per azienda, and a plain
   partial index on `(anno, numero)` keeps the cross-azienda number search served.
2. `invoice_counters` is keyed `(azienda_id, anno)` instead of `anno`: a lock per
   azienda and year, so issuing for one never waits on the other.
3. `invoice_register_gaps` gains `azienda_id` and is unique on `(azienda_id, anno,
   numero)`.
4. `fiscal_profile.codice_regime` becomes nullable: a profile on the `non-it` pack
   has no FatturaPA regime code.
5. `emitter_profile.partita_iva` widens from 11 to 20, so a foreign VAT number kept
   in its own shape fits (`PARTITA_IVA_WIDTH` in `emitter/models.py`).

Every space has exactly one azienda when this runs (0045 made the one row the default
and `ensure_defaults` writes one where there was none), so every backfill is the one
row and no `NOT NULL` below can fail on a space the product provisioned. A database
with invoices and no default azienda is one this code has never seen, and the `NOT
NULL` is where it stops, loudly, rather than this migration picking an azienda for it.

The downgrade refuses on anything it could not give back: a second azienda in any
register, a profile with no regime code, a P.IVA longer than eleven characters.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0046"
down_revision: str | None = "0045"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DEFAULT_AZIENDA = "(SELECT id FROM emitter_profile WHERE predefinita LIMIT 1)"


def _add_azienda_column(table: str) -> None:
    op.add_column(table, sa.Column("azienda_id", sa.Uuid(), nullable=True))
    op.execute(f"UPDATE {table} SET azienda_id = {DEFAULT_AZIENDA}")  # noqa: S608
    op.alter_column(table, "azienda_id", nullable=False)
    op.create_foreign_key(
        f"fk_{table}_azienda_id_emitter_profile", table, "emitter_profile", ["azienda_id"], ["id"]
    )


def upgrade() -> None:
    # 1. invoices: the azienda on every document, and the register's unique index per
    # azienda. The index is dropped before the new one is created, in one transaction,
    # so no window exists in which a duplicate number could be written.
    _add_azienda_column("invoices")
    op.create_index("ix_invoices_azienda_id", "invoices", ["azienda_id"])
    op.drop_index("uq_invoices_anno_numero", table_name="invoices")
    op.create_index(
        "uq_invoices_azienda_anno_numero",
        "invoices",
        ["azienda_id", "anno", "numero"],
        unique=True,
        postgresql_where=sa.text("numero IS NOT NULL"),
    )
    # The cross-azienda number search (`SearchRepository._invoices_by_number`) used the
    # old unique index; the new one leads with the azienda and cannot serve it, so the
    # `(anno, numero)` path is kept as a plain partial index.
    op.create_index(
        "ix_invoices_anno_numero",
        "invoices",
        ["anno", "numero"],
        postgresql_where=sa.text("numero IS NOT NULL"),
    )

    # 2. invoice_counters: one lock per azienda and year.
    _add_azienda_column("invoice_counters")
    op.drop_constraint("invoice_counters_pkey", "invoice_counters", type_="primary")
    op.create_primary_key("invoice_counters_pkey", "invoice_counters", ["azienda_id", "anno"])

    # 3. invoice_register_gaps: a declared gap belongs to one register.
    _add_azienda_column("invoice_register_gaps")
    op.create_index("ix_invoice_register_gaps_azienda_id", "invoice_register_gaps", ["azienda_id"])
    op.drop_constraint(
        "uq_invoice_register_gaps_anno_numero", "invoice_register_gaps", type_="unique"
    )
    op.create_unique_constraint(
        "uq_invoice_register_gaps_azienda_anno_numero",
        "invoice_register_gaps",
        ["azienda_id", "anno", "numero"],
    )

    # 4. A foreign azienda's profile has no regime code.
    op.alter_column("fiscal_profile", "codice_regime", existing_type=sa.String(4), nullable=True)

    # 5. A foreign VAT number fits.
    op.alter_column(
        "emitter_profile",
        "partita_iva",
        existing_type=sa.String(11),
        type_=sa.String(20),
        existing_nullable=True,
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            "DO $$ BEGIN "
            "IF (SELECT count(DISTINCT azienda_id) FROM invoices) > 1 "
            "OR (SELECT count(DISTINCT azienda_id) FROM invoice_counters) > 1 "
            "OR (SELECT count(DISTINCT azienda_id) FROM invoice_register_gaps) > 1 THEN "
            "RAISE EXCEPTION 'the register spans more than one azienda: 0046 cannot be undone'; "
            "END IF; "
            "IF EXISTS (SELECT 1 FROM fiscal_profile WHERE codice_regime IS NULL) THEN "
            "RAISE EXCEPTION 'a fiscal profile has no regime code: 0046 cannot be undone'; "
            "END IF; "
            "IF EXISTS (SELECT 1 FROM emitter_profile WHERE length(partita_iva) > 11) THEN "
            "RAISE EXCEPTION 'a partita IVA is longer than 11 characters: 0046 cannot be undone'; "
            "END IF; END $$;"
        )
    )
    op.alter_column(
        "emitter_profile",
        "partita_iva",
        existing_type=sa.String(20),
        type_=sa.String(11),
        existing_nullable=True,
    )
    op.alter_column("fiscal_profile", "codice_regime", existing_type=sa.String(4), nullable=False)

    op.drop_constraint(
        "uq_invoice_register_gaps_azienda_anno_numero", "invoice_register_gaps", type_="unique"
    )
    op.create_unique_constraint(
        "uq_invoice_register_gaps_anno_numero", "invoice_register_gaps", ["anno", "numero"]
    )
    op.drop_index("ix_invoice_register_gaps_azienda_id", table_name="invoice_register_gaps")
    op.drop_constraint(
        "fk_invoice_register_gaps_azienda_id_emitter_profile",
        "invoice_register_gaps",
        type_="foreignkey",
    )
    op.drop_column("invoice_register_gaps", "azienda_id")

    op.drop_constraint("invoice_counters_pkey", "invoice_counters", type_="primary")
    op.create_primary_key("invoice_counters_pkey", "invoice_counters", ["anno"])
    op.drop_constraint(
        "fk_invoice_counters_azienda_id_emitter_profile", "invoice_counters", type_="foreignkey"
    )
    op.drop_column("invoice_counters", "azienda_id")

    op.drop_index("ix_invoices_anno_numero", table_name="invoices")
    op.drop_index("uq_invoices_azienda_anno_numero", table_name="invoices")
    op.create_index(
        "uq_invoices_anno_numero",
        "invoices",
        ["anno", "numero"],
        unique=True,
        postgresql_where=sa.text("numero IS NOT NULL"),
    )
    op.drop_index("ix_invoices_azienda_id", table_name="invoices")
    op.drop_constraint("fk_invoices_azienda_id_emitter_profile", "invoices", type_="foreignkey")
    op.drop_column("invoices", "azienda_id")
