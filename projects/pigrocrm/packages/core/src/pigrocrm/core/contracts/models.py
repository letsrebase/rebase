"""`contracts` and `rate_cards`, per the signed-off mapping in
`docs/superpowers/specs/2026-09-23-mastro-ledger-onto-pigrocrm-design.md` §§3-4.

`Deal` (`deals/models.py`) is the closest existing entity and is the wrong shape on
every axis that matters: no validity period, no renewal type, and its own docstring
frames it as a pre-sale opportunity that becomes `chiuso_il` when it leaves the
pipeline. A contract outlives any one sales cycle and can renew into a new term with
its own rate card -- genuinely new tables, not a widening of `Deal`.

A rate card's own non-overlap invariant is a database rule, not an application check
(mastro's own admission that this "resolves unambiguously" only because of "the
exclusion constraint in the accompanying custom migration",
`rate-card.ts:33-37`) -- the same reasoning slice 6's day-lifecycle triggers apply
one level up. `ExcludeConstraint` needs `btree_gist` (Postgres has no exclusion
constraint over a plain `date` range plus a `uuid` equality column without it); the
migration that adds this table also runs `CREATE EXTENSION IF NOT EXISTS btree_gist`,
unconditionally, the same style `0021_pg_trgm_search_indexes.py` already uses for
`pg_trgm`.
"""

from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    column,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, ExcludeConstraint
from sqlalchemy.orm import Mapped, mapped_column

from pigrocrm.core.db import Base, PrimaryKeyMixin, SoftDeleteMixin, TimestampMixin

# Closed sets spelled `String(n)` + a CHECK, never a Postgres `ENUM` -- the same
# convention `documents.tipo`/`invoices.tipo` already use, for the same reason: a
# future value costs a schema constant rather than an `ALTER TYPE` migration.
RENEWAL_TYPES: tuple[str, ...] = ("nessuno", "esplicito", "opzione_controparte", "tacito")
RATE_CARD_TIPI: tuple[str, ...] = ("ricorrente_fisso", "giornaliero", "orario", "una_tantum")
RATE_CARD_UNITA: tuple[str, ...] = ("ora", "giorno", "mese", "anno", "forfait")
RATE_CARD_PERIODI: tuple[str, ...] = ("mensile", "trimestrale", "annuale", "una_tantum")


class Contract(Base, PrimaryKeyMixin, TimestampMixin, SoftDeleteMixin):
    """One customer engagement with its own validity period and renewal clause --
    mastro's `contract` (`contract.ts:109-262`), spec §3.

    `tipo_rinnovo`/`stato` are Italian values matching every other closed set already
    on this schema (`invoices.stato`); `cadenza_fatturazione` carries no CHECK on
    purpose -- its exact value set is the implementing issue's own concern to pin
    against real engagements, not invented here (spec §3's own note).

    `contratto_precedente_id` is the renewal chain (`contract.ts:244-261`) and is
    deliberately left unset by this spike: nothing here writes it. The column exists
    so a later, dedicated renewal-automation issue has somewhere to write.
    """

    __tablename__ = "contracts"

    customer_id: Mapped[UUID] = mapped_column(
        ForeignKey("customers.id"), nullable=False, index=True
    )
    titolo: Mapped[str] = mapped_column(String(255), nullable=False)
    inizio: Mapped[date] = mapped_column(Date, nullable=False)
    fine: Mapped[date | None] = mapped_column(Date, default=None)
    tipo_rinnovo: Mapped[str] = mapped_column(String(20), nullable=False)
    # Required by a CHECK whenever `tipo_rinnovo <> 'nessuno'` -- see __table_args__.
    preavviso_rinnovo_giorni: Mapped[int | None] = mapped_column(Integer, default=None)
    preavviso_disdetta_giorni: Mapped[int] = mapped_column(Integer, nullable=False)
    # Reuses `Customer.giorni_pagamento`/`pagamento_fine_mese`'s exact shape
    # (REB-326) rather than a second payment-terms representation. Null cascades to
    # the customer's own term, the same way `Customer.giorni_pagamento` null already
    # cascades to `FiscalProfile.giorni_scadenza`. Unlike `Customer`'s own copy,
    # `pagamento_fine_mese` is nullable here and tied to `giorni_pagamento` by the
    # "together" CHECK below -- the override is atomic: a contract states both of its
    # own payment-term facts or neither.
    giorni_pagamento: Mapped[int | None] = mapped_column(Integer, default=None)
    pagamento_fine_mese: Mapped[bool | None] = mapped_column(Boolean, default=None)
    cadenza_fatturazione: Mapped[str] = mapped_column(String(20), nullable=False)
    divisa: Mapped[str] = mapped_column(
        String(3), nullable=False, default="EUR", server_default=text("'EUR'")
    )
    requires_prior_approval: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    # The rivalsa INPS election (spec §9), read at invoice-line-construction time by a
    # later issue; nothing here consumes it yet.
    applies_social_charge: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    # mastro's `ExpensePolicy` (`contract.ts:90-99`), kept as a tagged JSONB union
    # rather than flattened: unlike payment terms, this one genuinely needs a payload
    # (a cap amount) on only one of its variants. Not deeply validated here -- the
    # rebillable-expenses issue (§7) is what reads and enforces its shape.
    politica_spese: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    stato: Mapped[str] = mapped_column(
        String(20), nullable=False, default="bozza", server_default=text("'bozza'")
    )
    contratto_precedente_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("contracts.id"), default=None, unique=True
    )
    note: Mapped[str | None] = mapped_column(Text, default=None)
    custom_fields: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    __table_args__ = (
        CheckConstraint(
            f"tipo_rinnovo IN {RENEWAL_TYPES!r}",
            name="ck_contracts_tipo_rinnovo",
        ),
        # The "together" shape this schema already uses for
        # `ck_invoices_anno_numero_together`/`ck_invoices_snapshot_together`
        # (invoices/models.py): a contract either states both of its own payment-term
        # facts or neither, never `giorni_pagamento` paired with a
        # `pagamento_fine_mese` the contract never set.
        CheckConstraint(
            "(giorni_pagamento IS NULL) = (pagamento_fine_mese IS NULL)",
            name="ck_contracts_payment_terms_together",
        ),
        CheckConstraint(
            "tipo_rinnovo = 'nessuno' OR preavviso_rinnovo_giorni IS NOT NULL",
            name="ck_contracts_preavviso_rinnovo_required",
        ),
        CheckConstraint("fine IS NULL OR inizio <= fine", name="ck_contracts_fine_ordered"),
        Index("ix_contracts_custom_fields", "custom_fields", postgresql_using="gin"),
        # Residuo R9: one `(column, id)` B-tree per key CONTRACT_SORTS admits.
        Index("ix_contracts_created_at_id", "created_at", "id"),
        Index("ix_contracts_updated_at_id", "updated_at", "id"),
        Index("ix_contracts_titolo_id", "titolo", "id"),
    )


class RateCard(Base, PrimaryKeyMixin, TimestampMixin):
    """One priced term of a contract -- mastro's `rate_card` (`rate-card.ts:39-60`),
    spec §4. A rate change at renewal is a new card, not a new contract, which is why
    non-overlapping validity is enforced here rather than flattened onto `Contract`
    itself.

    No `SoftDeleteMixin`: a rate card is an owned child row of a contract, the same
    shape as `InvoiceLine` under `Invoice`, never independently archived.
    """

    __tablename__ = "rate_cards"

    contract_id: Mapped[UUID] = mapped_column(
        ForeignKey("contracts.id"), nullable=False, index=True
    )
    valido_da: Mapped[date] = mapped_column(Date, nullable=False)
    # Null is the open (current) card -- mastro's own convention.
    valido_a: Mapped[date | None] = mapped_column(Date, default=None)
    tipo: Mapped[str] = mapped_column(String(20), nullable=False)
    importo: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    unita: Mapped[str] = mapped_column(String(10), nullable=False)
    frazioni_ammesse: Mapped[list[Decimal]] = mapped_column(
        ARRAY(Numeric(4, 2)),
        nullable=False,
        default=lambda: [Decimal("1")],
        server_default=text("ARRAY[1]::numeric(4,2)[]"),
    )
    # Meaningful, and CHECK-enforceable, only for `tipo = 'orario'`.
    ore_minime: Mapped[Decimal | None] = mapped_column(Numeric(6, 2), default=None)
    # Meaningful, and CHECK-enforceable, only for `tipo = 'ricorrente_fisso'`.
    periodo_erogazione: Mapped[str | None] = mapped_column(String(20), default=None)

    __table_args__ = (
        CheckConstraint(f"tipo IN {RATE_CARD_TIPI!r}", name="ck_rate_cards_tipo"),
        CheckConstraint(f"unita IN {RATE_CARD_UNITA!r}", name="ck_rate_cards_unita"),
        CheckConstraint(
            f"periodo_erogazione IS NULL OR periodo_erogazione IN {RATE_CARD_PERIODI!r}",
            name="ck_rate_cards_periodo_erogazione_values",
        ),
        CheckConstraint(
            "tipo = 'orario' OR ore_minime IS NULL",
            name="ck_rate_cards_ore_minime_only_orario",
        ),
        CheckConstraint(
            "tipo = 'ricorrente_fisso' OR periodo_erogazione IS NULL",
            name="ck_rate_cards_periodo_only_ricorrente_fisso",
        ),
        CheckConstraint(
            "valido_a IS NULL OR valido_da <= valido_a",
            name="ck_rate_cards_validity_ordered",
        ),
        # Non-overlapping validity per contract, database-enforced: a NULL bound in
        # `daterange(...)` is Postgres's own "unbounded on this side", which is
        # exactly the open-ended-current-card semantics `valido_a IS NULL` already
        # carries. `'[]'` (both bounds inclusive) matches how `valido_da`/`valido_a`
        # are read elsewhere -- two cards are "adjacent, not overlapping" when the
        # second's `valido_da` is the day after the first's `valido_a`.
        ExcludeConstraint(
            (column("contract_id"), "="),
            (func.daterange(column("valido_da"), column("valido_a"), text("'[]'")), "&&"),
            using="gist",
            name="ck_rate_cards_no_overlap",
        ),
    )


class RenewalAssumption(Base, PrimaryKeyMixin, TimestampMixin):
    """A contract's own human-recorded belief about revenue beyond its known term --
    mastro's `RenewalAssumption` (`certainty.ts:133-147`), REB-352 spec §1.7/§1.3,
    REB-375. `projected` never invents a fourth certainty tier by estimating a pace
    nobody recorded (mastro's own "Assumptions and pace" design note): this table is
    the human's own probability, expected volume and horizon, nothing else, and it
    is read by `contracts/projection.py` -- never written there.

    One row per contract, not a history: the belief is revised in place (`unique=True`
    below), the same "single row, admin decides" shape as `FiscalProfile`, scoped here
    to one contract instead of the whole installation. No `SoftDeleteMixin`, matching
    `RateCard`: an owned fact of its contract, cleared by overwriting it, not archived.

    `probabilita` is `Integer`, 0-100, matching `Deal.probabilita`'s own shape
    (deals/models.py) rather than mastro's `number` 0-1 fraction -- the same percentage
    concept already has one representation on this schema, and a second one beside it
    is exactly the duplication this project's own rule forbids. The projection divides
    by 100 where mastro divides by 1.
    """

    __tablename__ = "renewal_assumptions"

    contract_id: Mapped[UUID] = mapped_column(
        ForeignKey("contracts.id"), nullable=False, unique=True
    )
    probabilita: Mapped[int] = mapped_column(Integer, nullable=False)
    volume_atteso: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    orizzonte_al: Mapped[date] = mapped_column(Date, nullable=False)

    __table_args__ = (
        CheckConstraint(
            "probabilita >= 0 AND probabilita <= 100",
            name="ck_renewal_assumptions_probabilita_range",
        ),
        CheckConstraint(
            "volume_atteso >= 0", name="ck_renewal_assumptions_volume_atteso_non_negative"
        ),
    )
