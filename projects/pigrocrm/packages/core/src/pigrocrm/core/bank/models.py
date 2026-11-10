"""Bank accounts, statements, movements and the counterparties' IBANs (spec 2026-11-09, due
items and the cash schedule, § 2).

A `BankAccount` is ours and belongs to a legal entity. A `BankStatement` is one file read for
an account. A `BankMovement` is one line of a statement, held once per account whatever the
number of statements that carry it (`StatementMovement` links the two), so the same movement
in two overlapping statements is kept once. `OrganizationIban` is a counterparty's account.

Every table that reaches a legal entity carries it, and a statement or a movement names the
same one as its account by composite foreign key. The writer is `BankStatementService`.
"""

import datetime as dt
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Numeric,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from pigrocrm.core.bank.iban import IBAN_PATTERN
from pigrocrm.core.db import Base, PrimaryKeyMixin, TimestampMixin

# How statements arrive (§ 2); `api` is declared now and has no reader until a bank API is
# verified.
ARRIVALS: tuple[str, ...] = ("upload", "mailbox", "api")
CADENCES: tuple[str, ...] = ("monthly", "quarterly")
# `proposed` is a PDF reading awaiting a person; `reverted` is an undone import.
STATEMENT_STATES: tuple[str, ...] = ("proposed", "imported", "reverted")
MOVEMENT_STATES: tuple[str, ...] = ("unmatched", "matched", "transfer", "ignored")
# One proposal or decision about one movement. `proposed` waits for a person; `confirmed` is a
# person's act and settled the items; `dismissed` is a proposal another one replaced; `undone` is
# a confirmed match taken back. At most one live row (`proposed` or `confirmed`) per movement.
MATCH_STATES: tuple[str, ...] = ("proposed", "confirmed", "dismissed", "undone")
MATCH_LIVE_STATES: tuple[str, ...] = ("proposed", "confirmed")
# `accepted` is on the list for matching; `proposed` was learned from a matched movement and
# waits for a person.
IBAN_STATES: tuple[str, ...] = ("proposed", "accepted")
IBAN_SOURCES: tuple[str, ...] = ("supplier_xml", "movement", "manual")

CURRENCY_PATTERN = "^[A-Z]{3}$"
NAME_MAX_LENGTH = 255
REASON_MAX_LENGTH = 40
SENDER_MAX_LENGTH = 320
BALANCE_PRECISION = 14
# ISO 20022 `Max35Text`.
END_TO_END_ID_LENGTH = 35


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(value) for value in values)})"


class BankAccount(Base, PrimaryKeyMixin, TimestampMixin):
    """One of the space's bank accounts. `iban` is ours (`FiscalProfile.iban` stays the one
    printed on invoices and should name one of these). `senders` are the addresses its
    statements come from, for the `statements` mailbox purpose. An archived account keeps its
    rows and its IBAN."""

    __tablename__ = "bank_accounts"
    __table_args__ = (
        UniqueConstraint("id", "legal_entity_id", name="uq_bank_accounts_id_legal_entity_id"),
        CheckConstraint(f"iban ~ '{IBAN_PATTERN}'", name="ck_bank_accounts_iban"),
        CheckConstraint(f"currency ~ '{CURRENCY_PATTERN}'", name="ck_bank_accounts_currency"),
        CheckConstraint(_in("arrival", ARRIVALS), name="ck_bank_accounts_arrival"),
        CheckConstraint(_in("cadence", CADENCES), name="ck_bank_accounts_cadence"),
        # An opening balance is a figure on a date, never one without the other.
        CheckConstraint(
            "(opening_balance IS NULL) = (opening_balance_date IS NULL)",
            name="ck_bank_accounts_opening_pair",
        ),
        Index("uq_bank_accounts_iban", "iban", unique=True),
        Index("ix_bank_accounts_legal_entity_id", "legal_entity_id"),
    )

    legal_entity_id: Mapped[UUID] = mapped_column(ForeignKey("legal_entities.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(NAME_MAX_LENGTH), nullable=False)
    bank: Mapped[str] = mapped_column(String(NAME_MAX_LENGTH), nullable=False)
    iban: Mapped[str] = mapped_column(String(34), nullable=False)
    currency: Mapped[str] = mapped_column(
        String(3), nullable=False, default="EUR", server_default="EUR"
    )
    opening_balance: Mapped[Decimal | None] = mapped_column(
        Numeric(BALANCE_PRECISION, 2), default=None
    )
    opening_balance_date: Mapped[dt.date | None] = mapped_column(Date, default=None)
    arrival: Mapped[str] = mapped_column(
        String(10), nullable=False, default="upload", server_default="upload"
    )
    cadence: Mapped[str] = mapped_column(
        String(10), nullable=False, default="monthly", server_default="monthly"
    )
    senders: Mapped[list[str]] = mapped_column(
        ARRAY(String(SENDER_MAX_LENGTH)),
        nullable=False,
        default=list,
        server_default=text("'{}'::varchar[]"),
    )
    archived_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), default=None)


class BankStatement(Base, PrimaryKeyMixin, TimestampMixin):
    """One statement read for an account: the period it covers (from the file, or set by a
    person on import), the balances it states (either may be absent), the format of the file
    and the inbox item it came from."""

    __tablename__ = "bank_statements"
    __table_args__ = (
        UniqueConstraint("id", "legal_entity_id", name="uq_bank_statements_id_legal_entity_id"),
        ForeignKeyConstraint(
            ["bank_account_id", "legal_entity_id"],
            ["bank_accounts.id", "bank_accounts.legal_entity_id"],
            name="fk_bank_statements_same_legal_entity",
        ),
        CheckConstraint(_in("state", STATEMENT_STATES), name="ck_bank_statements_state"),
        CheckConstraint("period_to >= period_from", name="ck_bank_statements_period_order"),
        CheckConstraint(
            "(state = 'reverted') = (reverted_at IS NOT NULL)",
            name="ck_bank_statements_reverted_pair",
        ),
        Index("ix_bank_statements_account_period", "bank_account_id", "period_from", "period_to"),
        Index("ix_bank_statements_legal_entity_id", "legal_entity_id"),
        Index("ix_bank_statements_inbox_item_id", "inbox_item_id"),
    )

    bank_account_id: Mapped[UUID] = mapped_column(nullable=False)
    legal_entity_id: Mapped[UUID] = mapped_column(ForeignKey("legal_entities.id"), nullable=False)
    period_from: Mapped[dt.date] = mapped_column(Date, nullable=False)
    period_to: Mapped[dt.date] = mapped_column(Date, nullable=False)
    opening_balance: Mapped[Decimal | None] = mapped_column(
        Numeric(BALANCE_PRECISION, 2), default=None
    )
    closing_balance: Mapped[Decimal | None] = mapped_column(
        Numeric(BALANCE_PRECISION, 2), default=None
    )
    # The reader that produced it (`camt053`, `banco_bpm_xlsx`, ...), validated in code so a
    # reader is added without a migration.
    format: Mapped[str] = mapped_column(String(30), nullable=False)
    inbox_item_id: Mapped[UUID | None] = mapped_column(ForeignKey("inbox_items.id"), default=None)
    state: Mapped[str] = mapped_column(String(10), nullable=False, default="imported")
    reverted_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), default=None)


class BankMovement(Base, PrimaryKeyMixin, TimestampMixin):
    """One movement of an account. `amount` is signed (negative is money out) and never zero.
    `dedup_key` is `dedup.movement_key`: the bank's reference when it has one, otherwise the
    account's date, amount, description and the occurrence index within the file; unique per
    account, so a movement present in two statements is one row."""

    __tablename__ = "bank_movements"
    __table_args__ = (
        UniqueConstraint("id", "legal_entity_id", name="uq_bank_movements_id_legal_entity_id"),
        ForeignKeyConstraint(
            ["bank_account_id", "legal_entity_id"],
            ["bank_accounts.id", "bank_accounts.legal_entity_id"],
            name="fk_bank_movements_same_legal_entity",
        ),
        CheckConstraint("amount <> 0", name="ck_bank_movements_amount_not_zero"),
        CheckConstraint(f"currency ~ '{CURRENCY_PATTERN}'", name="ck_bank_movements_currency"),
        CheckConstraint(_in("state", MOVEMENT_STATES), name="ck_bank_movements_state"),
        CheckConstraint(
            f"counterparty_iban IS NULL OR counterparty_iban ~ '{IBAN_PATTERN}'",
            name="ck_bank_movements_counterparty_iban",
        ),
        # An ignored movement says why (a person's personal use of a solo's account, ...).
        CheckConstraint(
            "(state = 'ignored') = (ignored_reason IS NOT NULL)",
            name="ck_bank_movements_ignored_reason",
        ),
        Index("uq_bank_movements_account_dedup_key", "bank_account_id", "dedup_key", unique=True),
        Index("ix_bank_movements_account_booking_date", "bank_account_id", "booking_date"),
        Index("ix_bank_movements_legal_entity_id", "legal_entity_id"),
        # The alert counts `unmatched`, so that is the state a query looks for.
        Index(
            "ix_bank_movements_unmatched",
            "legal_entity_id",
            postgresql_where=text("state = 'unmatched'"),
        ),
        Index("ix_bank_movements_counterparty_iban", "counterparty_iban"),
        # Matching looks a payment up by the id the payer gave it.
        Index("ix_bank_movements_end_to_end_id", "end_to_end_id"),
    )

    bank_account_id: Mapped[UUID] = mapped_column(nullable=False)
    legal_entity_id: Mapped[UUID] = mapped_column(ForeignKey("legal_entities.id"), nullable=False)
    booking_date: Mapped[dt.date] = mapped_column(Date, nullable=False)
    value_date: Mapped[dt.date | None] = mapped_column(Date, default=None)
    amount: Mapped[Decimal] = mapped_column(Numeric(BALANCE_PRECISION, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    counterparty_name: Mapped[str | None] = mapped_column(String(NAME_MAX_LENGTH), default=None)
    counterparty_iban: Mapped[str | None] = mapped_column(String(34), default=None)
    description: Mapped[str | None] = mapped_column(String(1000), default=None)
    bank_reference: Mapped[str | None] = mapped_column(String(NAME_MAX_LENGTH), default=None)
    end_to_end_id: Mapped[str | None] = mapped_column(String(END_TO_END_ID_LENGTH), default=None)
    balance_after: Mapped[Decimal | None] = mapped_column(
        Numeric(BALANCE_PRECISION, 2), default=None
    )
    dedup_key: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(
        String(10), nullable=False, default="unmatched", server_default="unmatched"
    )
    ignored_reason: Mapped[str | None] = mapped_column(String(NAME_MAX_LENGTH), default=None)


class MovementMatch(Base, PrimaryKeyMixin, TimestampMixin):
    """A proposal that a movement pays some due items, and what became of it (spec 2026-11-09,
    § 3). `reasons` are the codes it was ranked by (`matching.REASONS`); `proposed_by_*` is who
    wrote it (the importer is `system`, an agent `mcp`); `confirmed_*` is the person who made it
    real and `closed_*` who dismissed or undid it, with `close_reason`. The writer is
    `MovementMatchService`; the movement's `state` follows the live row."""

    __tablename__ = "movement_matches"
    __table_args__ = (
        UniqueConstraint("id", "legal_entity_id", name="uq_movement_matches_id_legal_entity_id"),
        ForeignKeyConstraint(
            ["bank_movement_id", "legal_entity_id"],
            ["bank_movements.id", "bank_movements.legal_entity_id"],
            ondelete="CASCADE",
            name="fk_movement_matches_same_legal_entity",
        ),
        CheckConstraint(_in("state", MATCH_STATES), name="ck_movement_matches_state"),
        CheckConstraint(
            "(confirmed_at IS NOT NULL) = (state IN ('confirmed', 'undone')) "
            "AND (confirmed_at IS NOT NULL) = (confirmed_by_type IS NOT NULL)",
            name="ck_movement_matches_confirmed",
        ),
        CheckConstraint(
            "(closed_at IS NOT NULL) = (state IN ('dismissed', 'undone')) "
            "AND (closed_at IS NOT NULL) = (closed_by_type IS NOT NULL)",
            name="ck_movement_matches_closed",
        ),
        Index("ix_movement_matches_legal_entity_id", "legal_entity_id"),
        Index("ix_movement_matches_bank_movement_id", "bank_movement_id"),
        Index(
            "uq_movement_matches_live",
            "bank_movement_id",
            unique=True,
            postgresql_where=text("state IN ('proposed', 'confirmed')"),
        ),
    )

    bank_movement_id: Mapped[UUID] = mapped_column(nullable=False)
    legal_entity_id: Mapped[UUID] = mapped_column(ForeignKey("legal_entities.id"), nullable=False)
    state: Mapped[str] = mapped_column(String(10), nullable=False)
    reasons: Mapped[list[str]] = mapped_column(
        ARRAY(String(REASON_MAX_LENGTH)),
        nullable=False,
        default=list,
        server_default=text("'{}'::varchar[]"),
    )
    note: Mapped[str | None] = mapped_column(String(NAME_MAX_LENGTH), default=None)
    proposed_by_id: Mapped[UUID | None] = mapped_column(default=None)
    proposed_by_type: Mapped[str] = mapped_column(String(10), nullable=False)
    confirmed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    confirmed_by_id: Mapped[UUID | None] = mapped_column(default=None)
    confirmed_by_type: Mapped[str | None] = mapped_column(String(10), default=None)
    closed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    closed_by_id: Mapped[UUID | None] = mapped_column(default=None)
    closed_by_type: Mapped[str | None] = mapped_column(String(10), default=None)
    close_reason: Mapped[str | None] = mapped_column(String(NAME_MAX_LENGTH), default=None)


class MovementMatchItem(Base):
    """One due item a match pays. `amount` is the part of the movement that goes to it, a
    magnitude whatever the direction. Once confirmed, `settlement_id` is the payment that
    settled it, or the one marked paid by hand for the same amount that the match adopted
    (`adopted`): undoing the match reverses the first and only detaches the second."""

    __tablename__ = "movement_match_items"
    __table_args__ = (
        ForeignKeyConstraint(
            ["match_id", "legal_entity_id"],
            ["movement_matches.id", "movement_matches.legal_entity_id"],
            ondelete="CASCADE",
            name="fk_movement_match_items_match",
        ),
        ForeignKeyConstraint(
            ["due_item_id", "legal_entity_id"],
            ["due_items.id", "due_items.legal_entity_id"],
            name="fk_movement_match_items_due_item_same_legal_entity",
        ),
        CheckConstraint("amount > 0", name="ck_movement_match_items_amount_positive"),
        CheckConstraint(
            "NOT adopted OR settlement_id IS NOT NULL", name="ck_movement_match_items_adopted"
        ),
        Index("ix_movement_match_items_due_item_id", "due_item_id"),
        Index("ix_movement_match_items_legal_entity_id", "legal_entity_id"),
        Index("ix_movement_match_items_settlement_id", "settlement_id"),
    )

    match_id: Mapped[UUID] = mapped_column(primary_key=True)
    due_item_id: Mapped[UUID] = mapped_column(primary_key=True)
    legal_entity_id: Mapped[UUID] = mapped_column(ForeignKey("legal_entities.id"), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    settlement_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("due_item_settlements.id"), default=None
    )
    adopted: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )


class StatementMovement(Base):
    """A statement carries a movement. A movement in two statements has two rows here and one
    in `bank_movements`; reverting a statement drops its rows and the movements nothing else
    carries."""

    __tablename__ = "statement_movements"
    __table_args__ = (
        ForeignKeyConstraint(
            ["statement_id", "legal_entity_id"],
            ["bank_statements.id", "bank_statements.legal_entity_id"],
            ondelete="CASCADE",
            name="fk_statement_movements_statement",
        ),
        ForeignKeyConstraint(
            ["movement_id", "legal_entity_id"],
            ["bank_movements.id", "bank_movements.legal_entity_id"],
            ondelete="CASCADE",
            name="fk_statement_movements_movement",
        ),
        Index("ix_statement_movements_movement_id", "movement_id"),
        Index("ix_statement_movements_legal_entity_id", "legal_entity_id"),
    )

    statement_id: Mapped[UUID] = mapped_column(primary_key=True)
    movement_id: Mapped[UUID] = mapped_column(primary_key=True)
    legal_entity_id: Mapped[UUID] = mapped_column(ForeignKey("legal_entities.id"), nullable=False)


class OrganizationIban(Base, PrimaryKeyMixin, TimestampMixin):
    """A bank account of a counterparty, a list on the organisation like its contact points.
    Learned from supplier XML (`DatiPagamento`, `source` `supplier_xml`) it is `accepted`;
    proposed from a matched movement (`movement`) it waits for a person. `iban` is stored
    normalised (`iban.normalise_iban`)."""

    __tablename__ = "organization_ibans"
    __table_args__ = (
        CheckConstraint(f"iban ~ '{IBAN_PATTERN}'", name="ck_organization_ibans_iban"),
        CheckConstraint(_in("state", IBAN_STATES), name="ck_organization_ibans_state"),
        CheckConstraint(_in("source", IBAN_SOURCES), name="ck_organization_ibans_source"),
        Index("uq_organization_ibans_organization_iban", "organization_id", "iban", unique=True),
        # Matching looks a movement's counterparty up by IBAN.
        Index("ix_organization_ibans_iban", "iban"),
    )

    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    iban: Mapped[str] = mapped_column(String(34), nullable=False)
    holder_name: Mapped[str | None] = mapped_column(String(NAME_MAX_LENGTH), default=None)
    source: Mapped[str] = mapped_column(String(15), nullable=False)
    state: Mapped[str] = mapped_column(String(10), nullable=False, default="accepted")
