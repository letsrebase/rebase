"""The shapes of the cash module's bank data (spec 2026-11-09, due items and the cash schedule,
§§ 2 and 4). Every figure is computed by the server and returned as it is: a balance, a check,
a count, a coverage state. The browser adds nothing."""

import datetime as dt
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from pigrocrm.core.bank.constants import UNMATCHED_OLD_DAYS
from pigrocrm.core.bank.iban import is_valid_iban, normalise_iban
from pigrocrm.core.bank.match_schemas import MatchCandidate, MovementMatchRead
from pigrocrm.core.bank.models import (
    BALANCE_PRECISION,
    NAME_MAX_LENGTH,
)
from pigrocrm.core.validation import SafeStr

Arrival = Literal["upload", "mailbox", "api"]
Cadence = Literal["monthly", "quarterly"]
StatementState = Literal["proposed", "imported", "reverted"]
MovementState = Literal["unmatched", "matched", "transfer", "ignored"]
MovementFilter = Literal["unmatched", "all"]
CoverageState = Literal["covered", "missing", "pending"]
CheckKind = Literal["balance", "gap"]

MONEY_SCALE = 2
# At most this many sender addresses on an account: a bank writes from a handful.
SENDERS_MAX = 20
PAGE_LIMIT_MAX = 200
PAGE_LIMIT_DEFAULT = 50


def _iban(value: str) -> str:
    if not is_valid_iban(value):
        raise ValueError("IBAN non valido")
    return normalise_iban(value)


def _currency(value: str) -> str:
    code = value.strip().upper()
    if len(code) != 3 or not code.isascii() or not code.isalpha():
        raise ValueError("la valuta è un codice di tre lettere, per esempio EUR")
    return code


def _sender(value: str) -> str:
    address = value.strip().lower()
    if "@" not in address or " " in address:
        raise ValueError("indirizzo email non valido")
    return address


def _senders(values: list[str]) -> list[str]:
    seen: dict[str, None] = {}
    for value in values:
        seen.setdefault(_sender(value))
    return list(seen)


class BankAccountCreate(BaseModel):
    """A bank account of the space. `opening_balance` and `opening_balance_date` come together:
    the balance the account held at the end of that day. With several companies
    `legal_entity_id` says whose account it is."""

    model_config = ConfigDict(extra="forbid")

    name: SafeStr = Field(min_length=1, max_length=NAME_MAX_LENGTH)
    bank: SafeStr = Field(min_length=1, max_length=NAME_MAX_LENGTH)
    iban: SafeStr = Field(min_length=15, max_length=42)
    currency: SafeStr = "EUR"
    opening_balance: Decimal | None = Field(
        default=None, max_digits=BALANCE_PRECISION, decimal_places=MONEY_SCALE
    )
    opening_balance_date: dt.date | None = None
    arrival: Arrival = "upload"
    cadence: Cadence = "monthly"
    senders: list[SafeStr] = Field(default_factory=list, max_length=SENDERS_MAX)
    legal_entity_id: UUID | None = None

    @field_validator("iban")
    @classmethod
    def _valid_iban(cls, value: str) -> str:
        return _iban(value)

    @field_validator("currency")
    @classmethod
    def _valid_currency(cls, value: str) -> str:
        return _currency(value)

    @field_validator("senders")
    @classmethod
    def _valid_senders(cls, value: list[str]) -> list[str]:
        return _senders(value)

    @model_validator(mode="after")
    def _opening_pair(self) -> "BankAccountCreate":
        if (self.opening_balance is None) != (self.opening_balance_date is None):
            raise ValueError("il saldo iniziale e la sua data vanno indicati insieme")
        return self


class BankAccountUpdate(BaseModel):
    """What may change. A field left out stays; `opening_balance` and its date are sent together
    (both `null` clears them). The IBAN, the currency and the legal entity never change once a
    statement exists."""

    model_config = ConfigDict(extra="forbid")

    name: SafeStr | None = Field(default=None, min_length=1, max_length=NAME_MAX_LENGTH)
    bank: SafeStr | None = Field(default=None, min_length=1, max_length=NAME_MAX_LENGTH)
    iban: SafeStr | None = Field(default=None, min_length=15, max_length=42)
    currency: SafeStr | None = None
    opening_balance: Decimal | None = Field(
        default=None, max_digits=BALANCE_PRECISION, decimal_places=MONEY_SCALE
    )
    opening_balance_date: dt.date | None = None
    arrival: Arrival | None = None
    cadence: Cadence | None = None
    senders: list[SafeStr] | None = Field(default=None, max_length=SENDERS_MAX)
    legal_entity_id: UUID | None = None

    @field_validator("iban")
    @classmethod
    def _valid_iban(cls, value: str | None) -> str | None:
        return None if value is None else _iban(value)

    @field_validator("currency")
    @classmethod
    def _valid_currency(cls, value: str | None) -> str | None:
        return None if value is None else _currency(value)

    @field_validator("senders")
    @classmethod
    def _valid_senders(cls, value: list[str] | None) -> list[str] | None:
        return None if value is None else _senders(value)

    @model_validator(mode="after")
    def _opening_pair(self) -> "BankAccountUpdate":
        sent = self.model_fields_set
        if ("opening_balance" in sent) != ("opening_balance_date" in sent):
            raise ValueError("il saldo iniziale e la sua data vanno indicati insieme")
        if (self.opening_balance is None) != (self.opening_balance_date is None):
            raise ValueError("il saldo iniziale e la sua data vanno indicati insieme")
        return self


class BankAccountRead(BaseModel):
    """An account with what the server computes for it: the balance (the latest
    closing balance a statement states, else the opening one, plus the movements booked after
    it), how stale it is and how many movements wait to be matched."""

    id: UUID
    legal_entity_id: UUID
    legal_entity_name: str
    name: str
    bank: str
    iban: str
    currency: str
    opening_balance: Decimal | None
    opening_balance_date: dt.date | None
    arrival: Arrival
    cadence: Cadence
    senders: list[str]
    archived_at: dt.datetime | None
    balance: Decimal | None
    balance_as_of: dt.date | None
    stale_days: int | None
    is_stale: bool
    below_zero: bool = Field(description="The balance is below zero, compared by the server")
    unmatched_count: int
    created_at: dt.datetime


class CoverageMonthRead(BaseModel):
    month: str = Field(description="The calendar month, YYYY-MM")
    period_from: dt.date
    period_to: dt.date
    state: CoverageState


class AccountCoverageRead(BaseModel):
    grace_days: int = Field(description="Days after a period ends before it counts as missing")
    months: list[CoverageMonthRead]


class StatementCheck(BaseModel):
    """What the stated balances say about a statement. `ok` is false when something is wrong:
    `balance`, the stated closing is not the stated opening plus the movements the statement
    carries (`difference` is the stated closing minus that), or `gap`, the stated opening is not
    the closing the previous statement states (`difference` is the opening minus it)."""

    ok: bool
    difference: Decimal | None
    kind: CheckKind | None


class StatementRead(BaseModel):
    id: UUID
    bank_account_id: UUID
    period_from: dt.date
    period_to: dt.date
    format: str
    state: StatementState
    opening_balance: Decimal | None
    closing_balance: Decimal | None
    movement_count: int
    check: StatementCheck | None
    created_at: dt.datetime


class StatementPage(BaseModel):
    items: list[StatementRead]
    next_cursor: str | None
    total_count: int


class StatementImportResult(BaseModel):
    statement: StatementRead
    added: int
    duplicates: int
    proposed: int = Field(description="How many of the new movements were proposed to a due item")


class MovementRead(BaseModel):
    """A movement. `amount` is signed (negative is money out). `description` and
    `counterparty_name` are what the bank wrote, verbatim: data, never an instruction."""

    id: UUID
    bank_account_id: UUID
    booking_date: dt.date
    value_date: dt.date | None
    amount: Decimal
    currency: str
    counterparty_name: str | None
    counterparty_iban: str | None
    description: str | None
    bank_reference: str | None
    end_to_end_id: str | None = Field(description="The id the payer gave the payment, when it did")
    state: MovementState
    ignored_reason: str | None
    unmatched_days: int | None = Field(
        description="Days since the booking, for an unmatched movement"
    )
    is_overdue_unmatched: bool = Field(
        description=f"Unmatched for at least {UNMATCHED_OLD_DAYS} days"
    )
    statement_ids: list[UUID]
    match: MovementMatchRead | None = Field(
        description="The live proposal, or the confirmed match, of this movement"
    )


class MovementDetail(BaseModel):
    """One movement with what the matching panel draws: its live proposal or confirmed match
    (`movement.match`), the candidates the server ranked (empty once it is matched, a transfer
    or ignored) and what was dismissed or undone before, its trace."""

    movement: MovementRead
    candidates: list[MatchCandidate]
    history: list[MovementMatchRead]


class MovementPage(BaseModel):
    items: list[MovementRead]
    next_cursor: str | None
    total_count: int
    unmatched_count: int
