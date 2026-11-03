"""`contracts` and `rate_cards`, per the signed-off mapping in
`docs/superpowers/specs/2026-09-23-mastro-ledger-onto-pigrocrm-design.md` §§3-4.

`Deal` (`deals/models.py`) is the closest existing entity and is the wrong shape on
every axis that matters: no validity period, no renewal type, and its own docstring
frames it as a pre-sale opportunity that becomes `closed_at` when it leaves the
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
from pigrocrm.core.legal_entities.models import default_legal_entity_id

# Closed sets spelled `String(n)` + a CHECK, never a Postgres `ENUM` -- the same
# convention `documents.type`/`invoices.type` already use, for the same reason: a
# future value costs a schema constant rather than an `ALTER TYPE` migration.
RENEWAL_TYPES: tuple[str, ...] = ("none", "explicit", "counterparty_option", "tacit")
RATE_CARD_TYPES: tuple[str, ...] = ("fixed_recurring", "daily", "hourly", "one_off")
RATE_CARD_UNIT: tuple[str, ...] = ("hour", "day", "month", "year", "flat")
RATE_CARD_PERIODS: tuple[str, ...] = ("monthly", "quarterly", "annual", "one_off")


class Contract(Base, PrimaryKeyMixin, TimestampMixin, SoftDeleteMixin):
    """One customer engagement with its own validity period and renewal clause --
    mastro's `contract` (`contract.ts:109-262`), spec §3.

    `renewal_type`/`status` are Italian values matching every other closed set already
    on this schema (`invoices.status`); `billing_cadence` carries no CHECK on
    purpose -- its exact value set is the implementing issue's own concern to pin
    against real engagements, not invented here (spec §3's own note).

    `previous_contract_id` is the renewal chain (`contract.ts:244-261`): written only
    by `ContractService.renew` (REB-763), unique, so a contract is renewed once.
    """

    __tablename__ = "contracts"

    customer_id: Mapped[UUID] = mapped_column(
        ForeignKey("customers.id"), nullable=False, index=True
    )
    # The customer's azienda at creation (REB-623, §1.7), kept from then on.
    legal_entity_id: Mapped[UUID] = mapped_column(
        ForeignKey("legal_entities.id"),
        nullable=False,
        index=True,
        default=default_legal_entity_id,
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    start: Mapped[date] = mapped_column(Date, nullable=False)
    end: Mapped[date | None] = mapped_column(Date, default=None)
    renewal_type: Mapped[str] = mapped_column(String(20), nullable=False)
    # Required by a CHECK whenever `tipo_rinnovo <> 'nessuno'` -- see __table_args__.
    renewal_notice_days: Mapped[int | None] = mapped_column(Integer, default=None)
    termination_notice_days: Mapped[int] = mapped_column(Integer, nullable=False)
    # Reuses `Customer.payment_days`/`payment_end_of_month`'s exact shape
    # (REB-326) rather than a second payment-terms representation. Null cascades to
    # the customer's own term, the same way `Customer.payment_days` null already
    # cascades to `FiscalProfile.due_days`. Unlike `Customer`'s own copy,
    # `payment_end_of_month` is nullable here and tied to `payment_days` by the
    # "together" CHECK below -- the override is atomic: a contract states both of its
    # own payment-term facts or neither.
    payment_days: Mapped[int | None] = mapped_column(Integer, default=None)
    payment_end_of_month: Mapped[bool | None] = mapped_column(Boolean, default=None)
    billing_cadence: Mapped[str] = mapped_column(String(20), nullable=False)
    currency: Mapped[str] = mapped_column(
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
    # REB-761: the share of the azienda's invoiced revenue its customer must not reach over this
    # contract's own anniversary year, as a percentage the way its owner reads it
    # (`50.00`, the `FiscalProfile` rates' own shape), `NULL` for no cap. A clause of the
    # contract and not of the regime (mastro's invariant 2): it survives a change of pack.
    # What it is compared with is `ContractService.concentration_cap`'s share.
    customer_share_cap: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), default=None)
    # mastro's `ExpensePolicy` (`contract.ts:90-99`), kept as a tagged JSONB union
    # rather than flattened: unlike payment terms, this one genuinely needs a payload
    # (a cap amount) on only one of its variants. Not deeply validated here -- the
    # rebillable-expenses issue (§7) is what reads and enforces its shape.
    expense_policy: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="draft", server_default=text("'draft'")
    )
    previous_contract_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("contracts.id"), default=None, unique=True
    )
    note: Mapped[str | None] = mapped_column(Text, default=None)
    custom_fields: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    __table_args__ = (
        CheckConstraint(
            f"renewal_type IN {RENEWAL_TYPES!r}",
            name="ck_contracts_renewal_type",
        ),
        # The "together" shape this schema already uses for
        # `ck_invoices_year_number_together`/`ck_invoices_snapshot_together`
        # (invoices/models.py): a contract either states both of its own payment-term
        # facts or neither, never `payment_days` paired with a
        # `payment_end_of_month` the contract never set.
        CheckConstraint(
            "(payment_days IS NULL) = (payment_end_of_month IS NULL)",
            name="ck_contracts_payment_terms_together",
        ),
        CheckConstraint(
            "renewal_type = 'none' OR renewal_notice_days IS NOT NULL",
            name="ck_contracts_notice_renewal_required",
        ),
        CheckConstraint('"end" IS NULL OR start <= "end"', name="ck_contracts_end_ordered"),
        # A cap of 0 forbids the customer and one of 100 never binds: neither is a clause.
        CheckConstraint(
            "customer_share_cap IS NULL "
            "OR (customer_share_cap > 0 AND customer_share_cap < 100)",
            name="ck_contracts_customer_share_cap_range",
        ),
        Index("ix_contracts_custom_fields", "custom_fields", postgresql_using="gin"),
        # Residuo R9: one `(column, id)` B-tree per key CONTRACT_SORTS admits.
        Index("ix_contracts_created_at_id", "created_at", "id"),
        Index("ix_contracts_updated_at_id", "updated_at", "id"),
        Index("ix_contracts_title_id", "title", "id"),
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
    valid_from: Mapped[date] = mapped_column(Date, nullable=False)
    # Null is the open (current) card -- mastro's own convention.
    valid_to: Mapped[date | None] = mapped_column(Date, default=None)
    type: Mapped[str] = mapped_column(String(20), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    unit: Mapped[str] = mapped_column(String(10), nullable=False)
    allowed_fractions: Mapped[list[Decimal]] = mapped_column(
        ARRAY(Numeric(4, 2)),
        nullable=False,
        default=lambda: [Decimal("1")],
        server_default=text("ARRAY[1]::numeric(4,2)[]"),
    )
    # Meaningful, and CHECK-enforceable, only for `tipo = 'orario'`.
    minimum_hours: Mapped[Decimal | None] = mapped_column(Numeric(6, 2), default=None)
    # Meaningful, and CHECK-enforceable, only for `tipo = 'ricorrente_fisso'`.
    delivery_period: Mapped[str | None] = mapped_column(String(20), default=None)

    __table_args__ = (
        CheckConstraint(f"type IN {RATE_CARD_TYPES!r}", name="ck_rate_cards_type"),
        CheckConstraint(f"unit IN {RATE_CARD_UNIT!r}", name="ck_rate_cards_unit"),
        CheckConstraint(
            f"delivery_period IS NULL OR delivery_period IN {RATE_CARD_PERIODS!r}",
            name="ck_rate_cards_delivery_period_values",
        ),
        CheckConstraint(
            "type = 'hourly' OR minimum_hours IS NULL",
            name="ck_rate_cards_minimum_hours_only_hourly",
        ),
        CheckConstraint(
            "type = 'fixed_recurring' OR delivery_period IS NULL",
            name="ck_rate_cards_period_only_fixed_recurring",
        ),
        CheckConstraint(
            "valid_to IS NULL OR valid_from <= valid_to",
            name="ck_rate_cards_validity_ordered",
        ),
        # Non-overlapping validity per contract, database-enforced: a NULL bound in
        # `daterange(...)` is Postgres's own "unbounded on this side", which is
        # exactly the open-ended-current-card semantics `valido_a IS NULL` already
        # carries. `'[]'` (both bounds inclusive) matches how `valid_from`/`valid_to`
        # are read elsewhere -- two cards are "adjacent, not overlapping" when the
        # second's `valid_from` is the day after the first's `valid_to`.
        ExcludeConstraint(
            (column("contract_id"), "="),
            (func.daterange(column("valid_from"), column("valid_to"), text("'[]'")), "&&"),
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

    `probability` is `Integer`, 0-100, matching `Deal.probability`'s own shape
    (deals/models.py) rather than mastro's `number` 0-1 fraction -- the same percentage
    concept already has one representation on this schema, and a second one beside it
    is exactly the duplication this project's own rule forbids. The projection divides
    by 100 where mastro divides by 1.
    """

    __tablename__ = "renewal_assumptions"

    contract_id: Mapped[UUID] = mapped_column(
        ForeignKey("contracts.id"), nullable=False, unique=True
    )
    probability: Mapped[int] = mapped_column(Integer, nullable=False)
    expected_volume: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    horizon_until: Mapped[date] = mapped_column(Date, nullable=False)

    __table_args__ = (
        CheckConstraint(
            "probability >= 0 AND probability <= 100",
            name="ck_renewal_assumptions_probability_range",
        ),
        CheckConstraint(
            "expected_volume >= 0", name="ck_renewal_assumptions_expected_volume_not_negative"
        ),
    )


class ClauseNote(Base, PrimaryKeyMixin, TimestampMixin):
    """A reading of one clause of a contract, written down next to the words it was read
    from -- mastro's `clause_note` (`clause-note.ts`), REB-762.

    A real clause sometimes admits more than one honest reading (a renewal clause that is
    both «ends unless the client elects to renew» and «either party may refuse renewal
    with notice»), and the system must not pretend to know which governs. This is where
    the interpretation actually adopted lives, with the verbatim text it came from, so a
    later dispute rereads a decision instead of re-guessing one. It is read with the
    contract (`ContractDetail.note_clauses`), so an agent that opens the contract sees
    what the person decided about its clauses.

    Owned by its contract, like a rate card: no `SoftDeleteMixin` and no delete, a wrong
    note is corrected by editing it. `clause_reference` is free text, since numbering
    follows whatever convention the contract itself uses.
    """

    __tablename__ = "clause_notes"

    contract_id: Mapped[UUID] = mapped_column(
        ForeignKey("contracts.id"), nullable=False, index=True
    )
    # e.g. "Art. 8.3 Rinnovo".
    clause_reference: Mapped[str] = mapped_column(String(200), nullable=False)
    literal_text: Mapped[str] = mapped_column(Text, nullable=False)
    adopted_interpretation: Mapped[str] = mapped_column(Text, nullable=False)
    note: Mapped[str | None] = mapped_column(Text, default=None)
