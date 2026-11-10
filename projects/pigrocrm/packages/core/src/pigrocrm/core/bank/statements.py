"""Statements and movements as an administrator reads and acts on them (spec 2026-11-09, due
items and the cash schedule, § 4): the statements of an account with what the server checks on
each, its movements, the upload of a file and the revert of an import.

`BankStatementService` is the writer and holds no actor; this is the half the routes and the
tools call. The statement's check and every count are computed here, in the database, and
returned as they are.
"""

import datetime as dt
from collections.abc import Iterable, Sequence
from decimal import Decimal
from uuid import UUID

from sqlalchemy import ColumnElement, exists, func, select
from sqlalchemy.orm import Session

from pigrocrm.core.activities.service import ActivityService
from pigrocrm.core.actor import Actor
from pigrocrm.core.bank import banco_bpm_xlsx as xlsx_reader
from pigrocrm.core.bank import camt053 as camt053_reader
from pigrocrm.core.bank import readers
from pigrocrm.core.bank.constants import UNMATCHED_OLD_DAYS
from pigrocrm.core.bank.models import (
    BankAccount,
    BankMovement,
    BankStatement,
    StatementMovement,
)
from pigrocrm.core.bank.schemas import (
    PAGE_LIMIT_DEFAULT,
    PAGE_LIMIT_MAX,
    MovementFilter,
    MovementPage,
    MovementRead,
    StatementCheck,
    StatementImportResult,
    StatementPage,
    StatementRead,
)
from pigrocrm.core.bank.service import BankStatementService
from pigrocrm.core.db.clock import today_local
from pigrocrm.core.db.sort import (
    SortSpec,
    decode_cursor,
    encode_cursor,
    keyset_predicate,
    order_by,
)
from pigrocrm.core.errors import NotFound, ValidationFailed

STATEMENT = "bank_statement"
ACCOUNT = "bank_account"
# A statement file is a month of movements: a spreadsheet or an XML of a few hundred kilobytes.
# The cap keeps a request from buying memory with a huge body; the route reads one byte past it.
STATEMENT_SIZE_MAX = 10 * 1024 * 1024
_ZERO = Decimal("0.00")

_STATEMENT_SORT = SortSpec("period_to", BankStatement.period_to, "date", nullable=False)
_MOVEMENT_SORT = SortSpec("booking_date", BankMovement.booking_date, "date", nullable=False)


def _page_limit(limit: int) -> int:
    if not 1 <= limit <= PAGE_LIMIT_MAX:
        raise ValidationFailed(
            STATEMENT, "limit", "fuori intervallo", expected=f"1-{PAGE_LIMIT_MAX}"
        )
    return limit


def sniff_format(content: bytes) -> str:
    """The reader a file is for, told by what it holds and never by its name: an XLSX is a ZIP,
    a CAMT.053 is XML. Anything else is refused."""
    if content.startswith(b"PK\x03\x04"):
        return xlsx_reader.FORMAT
    if content.lstrip(b"\xef\xbb\xbf \t\r\n").startswith(b"<"):
        return camt053_reader.FORMAT
    raise ValidationFailed(
        STATEMENT,
        "file",
        "formato non riconosciuto",
        expected="un CAMT.053 (XML) o l'export «Saldo e movimenti» di Banco BPM (XLSX)",
    )


class _Shapes:
    """The shapes both services answer in, built here so a figure has one source."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def _require_account(self, account_id: UUID) -> None:
        if self.session.get(BankAccount, account_id) is None:
            raise NotFound(ACCOUNT, account_id)

    def _statement_reads(
        self, account_id: UUID, statements: Sequence[BankStatement]
    ) -> list[StatementRead]:
        if not statements:
            return []
        carried: dict[UUID, tuple[int, Decimal]] = {
            row[0]: (row[1], row[2] or _ZERO)
            for row in self.session.execute(
                select(
                    StatementMovement.statement_id,
                    func.count(),
                    func.coalesce(func.sum(BankMovement.amount), 0),
                )
                .join(BankMovement, BankMovement.id == StatementMovement.movement_id)
                .where(StatementMovement.statement_id.in_([s.id for s in statements]))
                .group_by(StatementMovement.statement_id)
            )
        }
        imported = list(
            self.session.execute(
                select(
                    BankStatement.period_from,
                    BankStatement.period_to,
                    BankStatement.closing_balance,
                )
                .where(
                    BankStatement.bank_account_id == account_id,
                    BankStatement.state == "imported",
                )
                .order_by(BankStatement.period_to, BankStatement.created_at)
            )
        )
        reads: list[StatementRead] = []
        for statement in statements:
            count, total = carried.get(statement.id, (0, _ZERO))
            reads.append(
                StatementRead(
                    id=statement.id,
                    bank_account_id=statement.bank_account_id,
                    period_from=statement.period_from,
                    period_to=statement.period_to,
                    format=statement.format,
                    state=statement.state,  # type: ignore[arg-type]
                    opening_balance=statement.opening_balance,
                    closing_balance=statement.closing_balance,
                    movement_count=count,
                    check=_check(statement, total, imported),
                    created_at=statement.created_at,
                )
            )
        return reads

    def _movement_reads(self, movements: Sequence[BankMovement]) -> list[MovementRead]:
        if not movements:
            return []
        today = today_local()
        by_movement: dict[UUID, list[UUID]] = {m.id: [] for m in movements}
        for movement_id, statement_id in self.session.execute(
            select(StatementMovement.movement_id, StatementMovement.statement_id)
            .where(StatementMovement.movement_id.in_(list(by_movement)))
            .order_by(StatementMovement.statement_id)
        ):
            by_movement[movement_id].append(statement_id)
        return [_movement_read(m, by_movement[m.id], today) for m in movements]


class BankStatementReadService(_Shapes):
    """The statements and the movements of an account, as an administrator reads them."""

    def list_statements(
        self,
        account_id: UUID,
        actor: Actor,
        *,
        cursor: str | None = None,
        limit: int = PAGE_LIMIT_DEFAULT,
    ) -> StatementPage:
        """The account's statements, newest `period_to` first, a reverted one included."""
        actor.require_admin("list_statements")
        self._require_account(account_id)
        limit = _page_limit(limit)
        scope = BankStatement.bank_account_id == account_id
        total = self.session.scalar(select(func.count()).select_from(BankStatement).where(scope))
        stmt = select(BankStatement).where(scope).order_by(*order_by(_STATEMENT_SORT, "desc"))
        if cursor is not None:
            value, row_id = decode_cursor(_STATEMENT_SORT, cursor)
            stmt = stmt.where(keyset_predicate(_STATEMENT_SORT, "desc", value, row_id))
        rows = list(self.session.scalars(stmt.limit(limit + 1)))
        page = rows[:limit]
        return StatementPage(
            items=self._statement_reads(account_id, page),
            next_cursor=(
                encode_cursor(_STATEMENT_SORT, page[-1].period_to, page[-1].id)
                if len(rows) > limit
                else None
            ),
            total_count=total or 0,
        )

    def list_movements(
        self,
        account_id: UUID,
        actor: Actor,
        *,
        state: MovementFilter = "all",
        statement_id: UUID | None = None,
        cursor: str | None = None,
        limit: int = PAGE_LIMIT_DEFAULT,
    ) -> MovementPage:
        """The account's movements, newest booking first. A movement present in two statements
        is one row. `unmatched_count` counts the ones that wait, whatever `state` says."""
        actor.require_admin("list_bank_movements")
        self._require_account(account_id)
        limit = _page_limit(limit)
        scope: list[ColumnElement[bool]] = [BankMovement.bank_account_id == account_id]
        if statement_id is not None:
            scope.append(
                exists().where(
                    StatementMovement.movement_id == BankMovement.id,
                    StatementMovement.statement_id == statement_id,
                )
            )
        unmatched = self.session.scalar(
            select(func.count())
            .select_from(BankMovement)
            .where(*scope, BankMovement.state == "unmatched")
        )
        if state == "unmatched":
            scope.append(BankMovement.state == "unmatched")
        total = self.session.scalar(select(func.count()).select_from(BankMovement).where(*scope))
        stmt = select(BankMovement).where(*scope).order_by(*order_by(_MOVEMENT_SORT, "desc"))
        if cursor is not None:
            value, row_id = decode_cursor(_MOVEMENT_SORT, cursor)
            stmt = stmt.where(keyset_predicate(_MOVEMENT_SORT, "desc", value, row_id))
        rows = list(self.session.scalars(stmt.limit(limit + 1)))
        page = rows[:limit]
        return MovementPage(
            items=self._movement_reads(page),
            next_cursor=(
                encode_cursor(_MOVEMENT_SORT, page[-1].booking_date, page[-1].id)
                if len(rows) > limit
                else None
            ),
            total_count=total or 0,
            unmatched_count=unmatched or 0,
        )


class StatementImportService(_Shapes):
    """Reading a statement file into an account and undoing it: the actor-facing half of
    `BankStatementService`."""

    def __init__(self, session: Session) -> None:
        super().__init__(session)
        self.writer = BankStatementService(session)
        self.activities = ActivityService(session)

    def import_file(
        self,
        account_id: UUID,
        content: bytes,
        actor: Actor,
        *,
        period_from: dt.date | None = None,
        period_to: dt.date | None = None,
    ) -> StatementImportResult:
        """Reads a statement file into the account: the reader is chosen by what the file
        holds, the movements it already knows are linked and not stored again. The period is
        the file's, else the one passed (both or none)."""
        actor.require_admin("import_statement")
        if len(content) > STATEMENT_SIZE_MAX:
            raise ValidationFailed(
                STATEMENT,
                "file",
                f"il file supera {STATEMENT_SIZE_MAX} byte",
                expected=f"al massimo {STATEMENT_SIZE_MAX} byte",
            )
        if (period_from is None) != (period_to is None):
            raise ValidationFailed(
                STATEMENT, "period", "il periodo va indicato con la prima e l'ultima data"
            )
        format_name = sniff_format(content)
        reader = readers.reader_for(format_name)
        assert reader is not None  # registered by `core/bank/__init__.py`
        parsed = reader(content)
        # The account is locked first: an import and a revert of its statements queue.
        self.session.get(BankAccount, account_id, with_for_update=True)
        result = self.writer.import_statement(
            account_id, parsed, period_from=period_from, period_to=period_to
        )
        self.activities.record(
            STATEMENT,
            result.statement.id,
            "imported",
            actor,
            {
                "bank_account_id": str(account_id),
                "format": parsed.format,
                "period_from": result.statement.period_from.isoformat(),
                "period_to": result.statement.period_to.isoformat(),
                "added": result.added,
                "duplicates": result.duplicates,
            },
        )
        self.session.commit()
        return StatementImportResult(
            statement=self._statement_reads(account_id, [result.statement])[0],
            added=result.added,
            duplicates=result.duplicates,
        )

    def revert_statement(self, statement_id: UUID, actor: Actor) -> StatementRead:
        """Removes the statement's movements and the automatic matches they carried (none exist
        yet): the statement stays, as `reverted`."""
        actor.require_admin("revert_statement")
        result = self.writer.revert_statement(statement_id)
        self.activities.record(
            STATEMENT,
            result.statement.id,
            "reverted",
            actor,
            {"bank_account_id": str(result.statement.bank_account_id), "removed": result.removed},
        )
        self.session.commit()
        return self._statement_reads(result.statement.bank_account_id, [result.statement])[0]


def _check(
    statement: BankStatement,
    carried_total: Decimal,
    imported: Iterable[tuple[dt.date, dt.date, Decimal | None]],
) -> StatementCheck | None:
    """What the stated balances say about the statement: the stated closing against the stated
    opening plus the movements it carries, then the stated opening against the closing of the
    statement before it. `None` when it states no balance, when it is not an import that
    stands, or when there is nothing to compare."""
    if statement.state != "imported":
        return None
    opening, closing = statement.opening_balance, statement.closing_balance
    if opening is not None and closing is not None:
        difference = closing - (opening + carried_total)
        if difference != 0:
            return StatementCheck(ok=False, difference=difference, kind="balance")
    if opening is not None:
        before = [row for row in imported if row[1] < statement.period_from]
        if before and before[-1][2] is not None:
            gap = opening - before[-1][2]
            if gap != 0:
                return StatementCheck(ok=False, difference=gap, kind="gap")
            return StatementCheck(ok=True, difference=_ZERO, kind=None)
    if opening is not None and closing is not None:
        return StatementCheck(ok=True, difference=_ZERO, kind=None)
    return None


def _movement_read(
    movement: BankMovement, statement_ids: list[UUID], today: dt.date
) -> MovementRead:
    unmatched_days = (
        max((today - movement.booking_date).days, 0) if movement.state == "unmatched" else None
    )
    return MovementRead(
        id=movement.id,
        bank_account_id=movement.bank_account_id,
        booking_date=movement.booking_date,
        value_date=movement.value_date,
        amount=movement.amount,
        currency=movement.currency,
        counterparty_name=movement.counterparty_name,
        description=movement.description,
        bank_reference=movement.bank_reference,
        state=movement.state,  # type: ignore[arg-type]
        ignored_reason=movement.ignored_reason,
        unmatched_days=unmatched_days,
        is_overdue_unmatched=unmatched_days is not None and unmatched_days >= UNMATCHED_OLD_DAYS,
        statement_ids=statement_ids,
    )
