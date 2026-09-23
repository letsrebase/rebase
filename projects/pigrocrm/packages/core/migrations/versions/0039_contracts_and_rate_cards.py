"""contracts and rate_cards, and documents widened to a three-way owner

Revision ID: 0039
Revises: 0038

REB-358, the foundations issue of "Bring mastro's ledger, invoice import and
forecasting into PigroCRM" (REB-344's signed-off mapping,
`docs/superpowers/specs/2026-09-23-mastro-ledger-onto-pigrocrm-design.md` §§2-4, §11).
Two genuinely new tables and one widening of an existing one.

**`contracts`.** `Deal` is the closest existing entity and the wrong shape on every
axis that matters (§3): no validity period, no renewal type, and its own docstring
frames it as a pre-sale opportunity. The renewal-notice CHECK
(`ck_contracts_preavviso_rinnovo_required`) and the payment-terms "together" CHECK
(`ck_contracts_payment_terms_together`, the identical shape
`ck_invoices_anno_numero_together` already uses) are both database rules, not
application checks.

**`rate_cards`.** Non-overlapping validity per contract is a database rule too
(§4): `CREATE EXTENSION IF NOT EXISTS btree_gist` runs unconditionally, the same
style `0021_pg_trgm_search_indexes.py` already uses for `pg_trgm` -- the alternative
is a rate card that silently allows two overlapping cards on one contract, which
defeats the entire point of the exclusion constraint. `daterange(valido_da,
valido_a, '[]')` reads a `NULL` `valido_a` as Postgres's own "unbounded on this
side", exactly the open-ended-current-card semantics that column already carries.

**`documents`.** §11 widens `ck_documents_customer_xor_deal` from a two-way `<>` to
a three-way `num_nonnulls(...) = 1`, so a contract's own signed document (and any
later addendum) is discoverable from the contract, not only from the proposal row
that produced it. Same constraint name: this is a widening of the existing rule,
not a second one beside it -- the identical situation mastro already solved once for
its own single-discriminator `document.ownerType`
(`0011_approval_constraints.sql:50-57`).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import ExcludeConstraint

revision: str = "0039"
down_revision: str | Sequence[str] | None = "0038"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TIMESTAMPS = (
    sa.Column(
        "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
    ),
    sa.Column(
        "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
    ),
)


def upgrade() -> None:
    # No capability check, on purpose -- see 0021_pg_trgm_search_indexes.py's own
    # comment: on a managed Postgres whose allowlist forbids it, the deploy fails
    # loudly here, which is what is wanted. The alternative is a rate card that
    # silently allows two overlapping cards on one contract.
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")

    op.create_table(
        "contracts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("customer_id", sa.Uuid(), nullable=False),
        sa.Column("titolo", sa.String(length=255), nullable=False),
        sa.Column("inizio", sa.Date(), nullable=False),
        sa.Column("fine", sa.Date(), nullable=True),
        sa.Column("tipo_rinnovo", sa.String(length=20), nullable=False),
        sa.Column("preavviso_rinnovo_giorni", sa.Integer(), nullable=True),
        sa.Column("preavviso_disdetta_giorni", sa.Integer(), nullable=False),
        sa.Column("giorni_pagamento", sa.Integer(), nullable=True),
        sa.Column("pagamento_fine_mese", sa.Boolean(), nullable=True),
        sa.Column("cadenza_fatturazione", sa.String(length=20), nullable=False),
        sa.Column("divisa", sa.String(length=3), nullable=False, server_default=sa.text("'EUR'")),
        sa.Column(
            "requires_prior_approval",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "applies_social_charge", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column("politica_spese", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("stato", sa.String(length=20), nullable=False, server_default=sa.text("'bozza'")),
        sa.Column("contratto_precedente_id", sa.Uuid(), nullable=True, unique=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("custom_fields", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        *_TIMESTAMPS,
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["customer_id"], ["customers.id"]),
        sa.ForeignKeyConstraint(["contratto_precedente_id"], ["contracts.id"]),
        sa.CheckConstraint(
            "tipo_rinnovo IN ('nessuno', 'esplicito', 'opzione_controparte', 'tacito')",
            name="ck_contracts_tipo_rinnovo",
        ),
        sa.CheckConstraint(
            "(giorni_pagamento IS NULL) = (pagamento_fine_mese IS NULL)",
            name="ck_contracts_payment_terms_together",
        ),
        sa.CheckConstraint(
            "tipo_rinnovo = 'nessuno' OR preavviso_rinnovo_giorni IS NOT NULL",
            name="ck_contracts_preavviso_rinnovo_required",
        ),
        sa.CheckConstraint("fine IS NULL OR inizio <= fine", name="ck_contracts_fine_ordered"),
    )
    op.create_index("ix_contracts_customer_id", "contracts", ["customer_id"])
    # Autogenerate is known to silently drop a GIN index, so it (and the three
    # residuo-R9 sort indexes) are written by hand, mirroring every other entity's
    # own migration.
    op.create_index(
        "ix_contracts_custom_fields", "contracts", ["custom_fields"], postgresql_using="gin"
    )
    op.create_index("ix_contracts_created_at_id", "contracts", ["created_at", "id"])
    op.create_index("ix_contracts_updated_at_id", "contracts", ["updated_at", "id"])
    op.create_index("ix_contracts_titolo_id", "contracts", ["titolo", "id"])

    op.create_table(
        "rate_cards",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("contract_id", sa.Uuid(), nullable=False),
        sa.Column("valido_da", sa.Date(), nullable=False),
        sa.Column("valido_a", sa.Date(), nullable=True),
        sa.Column("tipo", sa.String(length=20), nullable=False),
        sa.Column("importo", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("unita", sa.String(length=10), nullable=False),
        sa.Column(
            "frazioni_ammesse",
            postgresql.ARRAY(sa.Numeric(precision=4, scale=2)),
            nullable=False,
            server_default=sa.text("ARRAY[1]::numeric(4,2)[]"),
        ),
        sa.Column("ore_minime", sa.Numeric(precision=6, scale=2), nullable=True),
        sa.Column("periodo_erogazione", sa.String(length=20), nullable=True),
        *_TIMESTAMPS,
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["contract_id"], ["contracts.id"]),
        sa.CheckConstraint(
            "tipo IN ('ricorrente_fisso', 'giornaliero', 'orario', 'una_tantum')",
            name="ck_rate_cards_tipo",
        ),
        sa.CheckConstraint(
            "unita IN ('ora', 'giorno', 'mese', 'anno', 'forfait')", name="ck_rate_cards_unita"
        ),
        sa.CheckConstraint(
            "periodo_erogazione IS NULL OR periodo_erogazione IN "
            "('mensile', 'trimestrale', 'annuale', 'una_tantum')",
            name="ck_rate_cards_periodo_erogazione_values",
        ),
        sa.CheckConstraint(
            "tipo = 'orario' OR ore_minime IS NULL", name="ck_rate_cards_ore_minime_only_orario"
        ),
        sa.CheckConstraint(
            "tipo = 'ricorrente_fisso' OR periodo_erogazione IS NULL",
            name="ck_rate_cards_periodo_only_ricorrente_fisso",
        ),
        sa.CheckConstraint(
            "valido_a IS NULL OR valido_da <= valido_a", name="ck_rate_cards_validity_ordered"
        ),
        # The non-overlap rule itself: refused by the database, not by a
        # service-level check (Done-when, REB-358). See models.py's RateCard for
        # the "adjacent, not overlapping" reasoning behind the inclusive `'[]'`
        # bounds.
        ExcludeConstraint(
            (sa.column("contract_id"), "="),
            (
                sa.func.daterange(sa.column("valido_da"), sa.column("valido_a"), sa.text("'[]'")),
                "&&",
            ),
            using="gist",
            name="ck_rate_cards_no_overlap",
        ),
    )
    op.create_index("ix_rate_cards_contract_id", "rate_cards", ["contract_id"])

    # documents §11: a contract's own signed document, and any later addendum,
    # should be discoverable from the contract itself.
    op.add_column("documents", sa.Column("contract_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "documents_contract_id_fkey", "documents", "contracts", ["contract_id"], ["id"]
    )
    op.create_index("ix_documents_contract_id", "documents", ["contract_id"])
    op.drop_constraint("ck_documents_customer_xor_deal", "documents", type_="check")
    op.create_check_constraint(
        "ck_documents_customer_xor_deal",
        "documents",
        "num_nonnulls(customer_id, deal_id, contract_id) = 1",
    )


def downgrade() -> None:
    # A document owned solely by a contract (customer_id and deal_id both NULL)
    # would violate the old two-way CHECK the instant it is restored below --
    # reassign it to its contract's own customer first, the same fallback owner
    # `_customer_of` already resolves through at read time.
    op.execute(
        "UPDATE documents SET customer_id = contracts.customer_id "
        "FROM contracts WHERE documents.contract_id = contracts.id"
    )
    op.drop_constraint("ck_documents_customer_xor_deal", "documents", type_="check")
    op.create_check_constraint(
        "ck_documents_customer_xor_deal",
        "documents",
        "(customer_id IS NOT NULL) <> (deal_id IS NOT NULL)",
    )
    op.drop_index("ix_documents_contract_id", "documents")
    op.drop_constraint("documents_contract_id_fkey", "documents", type_="foreignkey")
    op.drop_column("documents", "contract_id")

    op.drop_table("rate_cards")

    op.drop_index("ix_contracts_titolo_id", "contracts")
    op.drop_index("ix_contracts_updated_at_id", "contracts")
    op.drop_index("ix_contracts_created_at_id", "contracts")
    op.drop_index("ix_contracts_custom_fields", "contracts")
    op.drop_index("ix_contracts_customer_id", "contracts")
    op.drop_table("contracts")

    # The extension is left installed -- see 0021_pg_trgm_search_indexes.py's own
    # downgrade for why: dropping it would fail if anything else in the database
    # came to depend on it, and an extension costs nothing to leave behind.
