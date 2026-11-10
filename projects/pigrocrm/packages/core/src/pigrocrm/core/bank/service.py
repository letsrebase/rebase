"""The only writer of `bank_statements`, `bank_movements` and `statement_movements`, and of
the counterparties' IBANs (spec 2026-11-09, due items and the cash schedule, § 2).

Like `DueItemService`'s writer half it takes no actor and never commits: the caller (a tool,
a route, the inbox) authorises, and commits with what it did. `import_statement` is the seam
a reader plugs into (`readers.py`).
"""

import datetime as dt
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from pigrocrm.core.bank.dedup import keys_for_file
from pigrocrm.core.bank.iban import is_valid_iban, normalise_iban
from pigrocrm.core.bank.models import (
    BankAccount,
    BankMovement,
    BankStatement,
    OrganizationIban,
    StatementMovement,
)
from pigrocrm.core.bank.readers import ParsedStatement
from pigrocrm.core.db.base import uuid7
from pigrocrm.core.errors import Conflict, NotFound, ValidationFailed
from pigrocrm.core.organizations.models import Organization

STATEMENT = "bank_statement"
ORGANIZATION_IBAN = "organization_iban"


@dataclass(frozen=True)
class ImportResult:
    statement: BankStatement
    added: int
    duplicates: int


class BankStatementService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def import_statement(
        self,
        account_id: UUID,
        parsed: ParsedStatement,
        *,
        period_from: dt.date | None = None,
        period_to: dt.date | None = None,
        inbox_item_id: UUID | None = None,
    ) -> ImportResult:
        """Records a read statement as `imported` and its movements, each once per account.

        The period is the file's, else the one passed (a person sets it on import); without
        either the import is refused: the covered period is what the «missing» check reads, so
        it is never guessed. A movement already held for the account
        (same dedup key, from an earlier statement) is linked to this statement, not stored
        again. Flushes, does not commit."""
        account = self.session.get(BankAccount, account_id)
        if account is None:
            raise NotFound("bank_account", account_id)
        if account.archived_at is not None:
            raise Conflict(STATEMENT, "il conto è archiviato")
        if parsed.account_iban is not None and normalise_iban(parsed.account_iban) != account.iban:
            raise ValidationFailed(
                STATEMENT, "account_iban", "l'IBAN del file non è quello del conto"
            )
        if parsed.currency is not None and parsed.currency != account.currency:
            raise ValidationFailed(
                STATEMENT, "currency", "la valuta del file non è quella del conto"
            )
        start = parsed.period_from or period_from
        end = parsed.period_to or period_to
        if start is None or end is None:
            raise ValidationFailed(STATEMENT, "period", "il periodo coperto va indicato")
        if end < start:
            raise ValidationFailed(STATEMENT, "period", "il periodo finisce prima di iniziare")
        for movement in parsed.movements:
            if movement.amount == 0:
                raise ValidationFailed(STATEMENT, "amount", "un movimento non può essere nullo")
            if movement.currency != account.currency:
                raise ValidationFailed(
                    STATEMENT, "currency", "un movimento ha una valuta diversa da quella del conto"
                )
            if movement.counterparty_iban is not None and not is_valid_iban(
                movement.counterparty_iban
            ):
                raise ValidationFailed(
                    STATEMENT, "counterparty_iban", "un IBAN del file non è valido"
                )

        statement = BankStatement(
            bank_account_id=account.id,
            legal_entity_id=account.legal_entity_id,
            period_from=start,
            period_to=end,
            opening_balance=parsed.opening_balance,
            closing_balance=parsed.closing_balance,
            format=parsed.format,
            inbox_item_id=inbox_item_id,
            state="imported",
        )
        self.session.add(statement)
        self.session.flush()

        keys = list(
            keys_for_file(
                (m.booking_date, m.amount, m.description, m.bank_reference)
                for m in parsed.movements
            )
        )
        by_key: dict[str, dict[str, object]] = {}
        for key, movement in zip(keys, parsed.movements, strict=True):
            by_key.setdefault(
                key,
                {
                    "bank_account_id": account.id,
                    "legal_entity_id": account.legal_entity_id,
                    "booking_date": movement.booking_date,
                    "value_date": movement.value_date,
                    "amount": movement.amount,
                    "currency": movement.currency,
                    "counterparty_name": movement.counterparty_name,
                    "counterparty_iban": (
                        normalise_iban(movement.counterparty_iban)
                        if movement.counterparty_iban
                        else None
                    ),
                    "description": movement.description,
                    "bank_reference": movement.bank_reference or None,
                    "end_to_end_id": movement.end_to_end_id or None,
                    "balance_after": movement.balance_after,
                    "dedup_key": key,
                    "state": "unmatched",
                },
            )
        # `ON CONFLICT DO NOTHING` on the account's dedup key: two imports racing for the same
        # movement keep one row. The ids are read back by key, so a movement that was already
        # there and one just stored are linked the same way.
        stored = 0
        if by_key:
            inserted = self.session.execute(
                insert(BankMovement)
                .values([{**row, "id": uuid7()} for row in by_key.values()])
                .on_conflict_do_nothing(index_elements=["bank_account_id", "dedup_key"])
                .returning(BankMovement.id)
            ).all()
            stored = len(inserted)
            ids = self.session.execute(
                select(BankMovement.id).where(
                    BankMovement.bank_account_id == account.id,
                    BankMovement.dedup_key.in_(list(by_key)),
                )
            ).scalars()
            self.session.execute(
                insert(StatementMovement).values(
                    [
                        {
                            "statement_id": statement.id,
                            "movement_id": movement_id,
                            "legal_entity_id": account.legal_entity_id,
                        }
                        for movement_id in ids
                    ]
                )
            )
        self.session.flush()
        return ImportResult(statement, added=stored, duplicates=len(keys) - stored)


class OrganizationIbanService:
    """The IBANs of a counterparty. Writer half: no actor, no commit."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def add(
        self,
        organization_id: UUID,
        iban: str,
        *,
        source: str,
        holder_name: str | None = None,
    ) -> OrganizationIban:
        """Adds an IBAN to the organisation, or returns the one it already has. A `movement`
        source is stored `proposed`; the others are `accepted`. An address that is already
        accepted is never demoted to proposed by a later proposal."""
        normalised = normalise_iban(iban)
        if not is_valid_iban(normalised):
            raise ValidationFailed(ORGANIZATION_IBAN, "iban", "IBAN non valido")
        if self.session.get(Organization, organization_id) is None:
            raise NotFound("organization", organization_id)
        existing = self.session.execute(
            select(OrganizationIban).where(
                OrganizationIban.organization_id == organization_id,
                OrganizationIban.iban == normalised,
            )
        ).scalar_one_or_none()
        if existing is not None:
            return existing
        row = OrganizationIban(
            organization_id=organization_id,
            iban=normalised,
            holder_name=holder_name,
            source=source,
            state="proposed" if source == "movement" else "accepted",
        )
        self.session.add(row)
        self.session.flush()
        return row

    def accept(self, iban_id: UUID) -> OrganizationIban:
        row = self.session.get(OrganizationIban, iban_id)
        if row is None:
            raise NotFound(ORGANIZATION_IBAN, iban_id)
        row.state = "accepted"
        self.session.flush()
        return row

    def list_for(self, organization_id: UUID) -> list[OrganizationIban]:
        return list(
            self.session.execute(
                select(OrganizationIban)
                .where(OrganizationIban.organization_id == organization_id)
                .order_by(OrganizationIban.created_at, OrganizationIban.id)
            ).scalars()
        )
