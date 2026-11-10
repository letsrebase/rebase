"""The seam a statement reader plugs into (spec 2026-11-09, § 2, «Reading a statement»).

A reader turns the bytes of one format into a `ParsedStatement`; it knows nothing of accounts,
the database or dedup. `BankStatementService.import_statement` takes the parsed statement and
does the rest, so CAMT.053 (REB-999) and the Banco BPM export (REB-1017) are each a function
and a `register_reader` call.
"""

import datetime as dt
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal


@dataclass(frozen=True)
class ParsedMovement:
    """One line of a file. `amount` is signed (negative is money out)."""

    booking_date: dt.date
    amount: Decimal
    currency: str
    value_date: dt.date | None = None
    counterparty_name: str | None = None
    counterparty_iban: str | None = None
    description: str | None = None
    bank_reference: str | None = None
    # The payer's id for the instruction (SEPA `EndToEndId`), when the file or the description
    # carries one; never a dedup key, since payers reuse them.
    end_to_end_id: str | None = None
    balance_after: Decimal | None = None


@dataclass(frozen=True)
class ParsedStatement:
    """What a file says. `account_iban` is the account the file claims to be for, when the
    format says (a check against the account it is imported into). A period the file does not
    state is `None` and the import takes the one a person sets."""

    format: str
    movements: list[ParsedMovement] = field(default_factory=list)
    account_iban: str | None = None
    currency: str | None = None
    period_from: dt.date | None = None
    period_to: dt.date | None = None
    opening_balance: Decimal | None = None
    closing_balance: Decimal | None = None

    def balance_difference(self) -> Decimal | None:
        """Stated closing balance minus opening balance plus the movements, or `None` when the
        file does not state both. Zero is a statement that adds up; the reconcile alert of a
        later card reads the same figure."""
        if self.opening_balance is None or self.closing_balance is None:
            return None
        total = sum((m.amount for m in self.movements), Decimal("0.00"))
        return self.closing_balance - (self.opening_balance + total)


StatementReader = Callable[[bytes], ParsedStatement]

_READERS: dict[str, StatementReader] = {}


def register_reader(format_name: str, reader: StatementReader) -> None:
    if format_name in _READERS:
        raise ValueError(f"a reader for {format_name} is already registered")
    _READERS[format_name] = reader


def reader_for(format_name: str) -> StatementReader | None:
    return _READERS.get(format_name)


def registered_formats() -> tuple[str, ...]:
    return tuple(sorted(_READERS))
