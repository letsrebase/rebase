"""The space's bank accounts: create, change, archive and read (spec 2026-11-09, due items and
the cash schedule, §§ 2 and 4).

Every method is an administrator's (`require_admin`) and, for an agent, a `full_access`
token's: an account, its balance and its movements are the company's position. An account is
never deleted: archiving hides it from the list and from every balance, and its statements
and movements stay («I movimenti restano»). The balance and its freshness are computed by
`balances.py`, the missing months by `coverage.py`; nothing here adds a figure of its own.
"""

import datetime as dt
from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import exists, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from pigrocrm.core.activities.service import ActivityService
from pigrocrm.core.actor import Actor
from pigrocrm.core.bank.balances import AccountBalance, account_balances
from pigrocrm.core.bank.coverage import account_coverage
from pigrocrm.core.bank.models import BankAccount, BankMovement, BankStatement
from pigrocrm.core.bank.schemas import (
    AccountCoverageRead,
    BankAccountCreate,
    BankAccountRead,
    BankAccountUpdate,
    CoverageMonthRead,
)
from pigrocrm.core.db.clock import today_local
from pigrocrm.core.errors import Conflict, NotFound, ValidationFailed
from pigrocrm.core.legal_entities.models import LegalEntity
from pigrocrm.core.legal_entities.service import LegalEntityService

ENTITY = "bank_account"
LIST_ACTION = "list_bank_accounts"
COVERAGE_ACTION = "get_bank_account_coverage"


class BankAccountService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.legal_entities = LegalEntityService(session)
        self.activities = ActivityService(session)

    def list_bank_accounts(
        self,
        actor: Actor,
        *,
        include_archived: bool = False,
        legal_entity_id: UUID | None = None,
    ) -> list[BankAccountRead]:
        """The accounts by name, the active ones unless `include_archived`."""
        actor.require_admin(LIST_ACTION)
        stmt = (
            select(BankAccount, LegalEntity.name)
            .join(LegalEntity, LegalEntity.id == BankAccount.legal_entity_id)
            .order_by(BankAccount.name, BankAccount.created_at, BankAccount.id)
        )
        if not include_archived:
            stmt = stmt.where(BankAccount.archived_at.is_(None))
        if legal_entity_id is not None:
            stmt = stmt.where(BankAccount.legal_entity_id == legal_entity_id)
        return self._reads(self.session.execute(stmt).all())

    def get_bank_account(self, account_id: UUID, actor: Actor) -> BankAccountRead:
        actor.require_admin(LIST_ACTION)
        return self._read_one(account_id)

    def create_bank_account(self, data: BankAccountCreate, actor: Actor) -> BankAccountRead:
        actor.require_admin("create_bank_account")
        legal_entity = self.legal_entities.require_single(data.legal_entity_id, entity=ENTITY)
        if not legal_entity.active:
            raise ValidationFailed(
                ENTITY,
                "legal_entity_id",
                "l'azienda non e' attiva: un conto si assegna a un'azienda attiva",
                expected="l'id di un'azienda attiva",
            )
        row = BankAccount(
            legal_entity_id=legal_entity.id,
            name=data.name,
            bank=data.bank,
            iban=data.iban,
            currency=data.currency,
            opening_balance=data.opening_balance,
            opening_balance_date=data.opening_balance_date,
            arrival=data.arrival,
            cadence=data.cadence,
            senders=data.senders,
        )
        self._store(row)
        self.activities.record(
            ENTITY,
            row.id,
            "created",
            actor,
            {"name": row.name, "bank": row.bank, "currency": row.currency},
        )
        self.session.commit()
        return self._read_one(row.id)

    def update_bank_account(
        self, account_id: UUID, data: BankAccountUpdate, actor: Actor
    ) -> BankAccountRead:
        actor.require_admin("update_bank_account")
        row = self._lock(account_id)
        changed: list[str] = []
        for field in ("name", "bank", "arrival", "cadence", "senders"):
            value = getattr(data, field)
            if value is not None and value != getattr(row, field):
                setattr(row, field, value)
                changed.append(field)
        if data.iban is not None and data.iban != row.iban:
            self._require_no_statement(row, "iban", "l'IBAN")
            row.iban = data.iban
            changed.append("iban")
        if data.currency is not None and data.currency != row.currency:
            self._require_no_statement(row, "currency", "la valuta")
            row.currency = data.currency
            changed.append("currency")
        if data.legal_entity_id is not None and data.legal_entity_id != row.legal_entity_id:
            self._require_no_statement(row, "legal_entity_id", "l'azienda")
            legal_entity = self.legal_entities.resolve(data.legal_entity_id)
            if not legal_entity.active:
                raise ValidationFailed(
                    ENTITY,
                    "legal_entity_id",
                    "l'azienda non e' attiva: un conto si assegna a un'azienda attiva",
                    expected="l'id di un'azienda attiva",
                )
            row.legal_entity_id = legal_entity.id
            changed.append("legal_entity_id")
        if "opening_balance" in data.model_fields_set and (
            (data.opening_balance, data.opening_balance_date)
            != (row.opening_balance, row.opening_balance_date)
        ):
            row.opening_balance = data.opening_balance
            row.opening_balance_date = data.opening_balance_date
            changed.append("opening_balance")
        if changed:
            self._store(row)
            self.activities.record(ENTITY, row.id, "updated", actor, {"changed": changed})
        self.session.commit()
        return self._read_one(row.id)

    def archive_bank_account(self, account_id: UUID, actor: Actor) -> BankAccountRead:
        """Hides the account from the list and from every balance. Nothing is deleted."""
        actor.require_admin("archive_bank_account")
        row = self._lock(account_id)
        if row.archived_at is not None:
            raise Conflict(ENTITY, "il conto e' gia' archiviato", id=str(account_id))
        row.archived_at = dt.datetime.now(dt.UTC)
        self.session.flush()
        self.activities.record(ENTITY, row.id, "archived", actor)
        self.session.commit()
        return self._read_one(row.id)

    def get_coverage(self, account_id: UUID, actor: Actor) -> AccountCoverageRead:
        """The last twelve months of the account, each `covered`, `missing` or `pending`."""
        actor.require_admin(COVERAGE_ACTION)
        row = self._get(account_id)
        coverage = account_coverage(self.session, row)
        return AccountCoverageRead(
            grace_days=coverage.grace_days,
            months=[
                CoverageMonthRead(
                    month=month.month,
                    period_from=month.period_from,
                    period_to=month.period_to,
                    state=month.state,
                )
                for month in coverage.months
            ],
        )

    def _get(self, account_id: UUID) -> BankAccount:
        row = self.session.get(BankAccount, account_id)
        if row is None:
            raise NotFound(ENTITY, account_id)
        return row

    def _lock(self, account_id: UUID) -> BankAccount:
        """The account under a row lock: a change and an import or a revert of its statements
        queue behind one another."""
        row = self.session.get(BankAccount, account_id, with_for_update=True)
        if row is None:
            raise NotFound(ENTITY, account_id)
        return row

    def _store(self, row: BankAccount) -> None:
        """Writes the row, turning the IBAN's unique index into a sentence. The savepoint
        keeps the rest of the transaction usable after the refusal."""
        try:
            with self.session.begin_nested():
                self.session.add(row)
                self.session.flush()
        except IntegrityError as exc:
            if "uq_bank_accounts_iban" not in str(exc.orig):
                raise
            raise Conflict(ENTITY, "esiste gia' un conto con questo IBAN") from exc

    def _require_no_statement(self, row: BankAccount, field: str, words: str) -> None:
        has_statement = self.session.scalar(
            select(exists().where(BankStatement.bank_account_id == row.id))
        )
        if has_statement:
            raise Conflict(
                ENTITY,
                f"{words} non si cambia dopo che e' stato importato un estratto conto",
                field=field,
            )

    def _read_one(self, account_id: UUID) -> BankAccountRead:
        row = self.session.execute(
            select(BankAccount, LegalEntity.name)
            .join(LegalEntity, LegalEntity.id == BankAccount.legal_entity_id)
            .where(BankAccount.id == account_id)
        ).first()
        if row is None:
            raise NotFound(ENTITY, account_id)
        return self._reads([row])[0]

    def _reads(self, rows: Sequence[tuple[BankAccount, str]]) -> list[BankAccountRead]:
        if not rows:
            return []
        ids = [account.id for account, _ in rows]
        today = today_local()
        balances = account_balances(self.session, account_ids=ids, today=today)
        unmatched = {
            account_id: count
            for account_id, count in self.session.execute(
                select(BankMovement.bank_account_id, func.count())
                .where(BankMovement.bank_account_id.in_(ids), BankMovement.state == "unmatched")
                .group_by(BankMovement.bank_account_id)
            )
        }
        return [
            _read(account, entity_name, balances[account.id], unmatched.get(account.id, 0))
            for account, entity_name in rows
        ]


def _read(
    account: BankAccount, entity_name: str, balance: AccountBalance, unmatched_count: int
) -> BankAccountRead:
    return BankAccountRead(
        id=account.id,
        legal_entity_id=account.legal_entity_id,
        legal_entity_name=entity_name,
        name=account.name,
        bank=account.bank,
        iban=account.iban,
        currency=account.currency,
        opening_balance=account.opening_balance,
        opening_balance_date=account.opening_balance_date,
        arrival=account.arrival,  # type: ignore[arg-type]
        cadence=account.cadence,  # type: ignore[arg-type]
        senders=list(account.senders),
        archived_at=account.archived_at,
        balance=balance.amount,
        balance_as_of=balance.as_of,
        stale_days=balance.stale_days,
        is_stale=balance.is_stale,
        below_zero=balance.amount is not None and balance.amount < 0,
        unmatched_count=unmatched_count,
        created_at=account.created_at,
    )
