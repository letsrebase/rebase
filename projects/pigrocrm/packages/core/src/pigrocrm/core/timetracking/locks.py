"""Closed periods.

§5 stops a rate change from rewriting a margin somebody has already read. This stops
the other way of moving the same number: recording today an hour dated last March.
That is not an abuse -- it is the back-dating §6.3 explicitly allows, and it is
legitimate for exactly as long as the period is open.

Two deliberate properties. **Closing is not mandatory**: somebody who closes nothing
gets the previous behaviour, and no screen demands a ritual before it works. And
**closing does not freeze invoices**, which already have their own rules (slice 3 §4)
and do not want a second set: this table governs only `time_entries` and `costs`.
"""

from datetime import UTC, date, datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from pigrocrm.core.activities.service import ActivityService
from pigrocrm.core.actor import Actor
from pigrocrm.core.errors import Conflict, NotFound
from pigrocrm.core.timetracking.models import PeriodLock
from pigrocrm.core.timetracking.schemas import PeriodLockCreate, PeriodLockRead

ENTITY = "period_lock"

# `activities.entity_id` is a non-null UUID, and `period_locks` is the one table in
# this schema with no UUID key -- `(anno, mese)` is its whole identity (see the
# model's docstring). Every close and reopen therefore hangs off one fixed,
# well-known id, with the real month in the payload. The alternative -- deriving a
# UUID from the year and month -- would make the timeline of "the closures" impossible
# to read as one sequence, which is the only way anybody wants to read it.
PERIOD_LOCK_TIMELINE_ID = UUID("00000000-0000-0000-0000-0000706c6f63")

# The first key of the advisory locks taken per month (`PeriodLockRepository.lock_month`),
# a namespace of this project's own next to `gmail/repository.py` and `customers/repository.py`.
PERIOD_LOCK_NAMESPACE = 0x7093

MONTHS_ITALIAN = (
    "gennaio",
    "febbraio",
    "marzo",
    "aprile",
    "maggio",
    "giugno",
    "luglio",
    "agosto",
    "settembre",
    "ottobre",
    "novembre",
    "dicembre",
)


def period_label(year: int, month: int) -> str:
    """`marzo 2026`. Long-form Italian, carried over from the previous system's own period label:
    the monthly cut is what a client expects next to an invoice, and it is what gets
    agreed on."""
    return f"{MONTHS_ITALIAN[month - 1]} {year}"


class PeriodLockRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def lock_month(self, year: int, month: int, *, exclusive: bool) -> None:
        """Holds a transaction-scoped advisory lock on one month until the transaction ends.

        Why an advisory lock and not `SELECT ... FOR SHARE` on the lock row: a month that
        is open has no row, and a row lock cannot be taken on a row that does not exist yet,
        so a write that checked an open month and a close that inserts its row would never
        meet. The key is derived from the month itself, so it exists for every month. A
        write holds it shared (`assert_writable`), a close or reopen holds it exclusive
        (`close_period`, `reopen_period`): shared ones do not block each other, and the
        locks one session holds never conflict with each other."""
        take = func.pg_advisory_xact_lock if exclusive else func.pg_advisory_xact_lock_shared
        self.session.execute(select(take(PERIOD_LOCK_NAMESPACE, year * 100 + month)))

    def get(self, year: int, month: int) -> PeriodLock | None:
        """The row as it is now: overwritten from the database, so a lock another
        transaction closed or reopened is not read from a copy loaded earlier."""
        return self.session.get(PeriodLock, (year, month), populate_existing=True)

    def add(self, lock: PeriodLock) -> PeriodLock:
        self.session.add(lock)
        self.session.flush()
        return lock

    def delete(self, lock: PeriodLock) -> None:
        """The one physical delete in this slice, and it is correct: a lock is not a
        record of anything, it is a switch. Its history lives in the timeline, which
        is why `reopen_period` writes one. Keeping a tombstone row would mean
        `is_closed` had to distinguish "closed" from "was closed", which is precisely
        the ambiguity the primary key exists to rule out."""
        self.session.delete(lock)
        self.session.flush()

    # `list` stays the last method in this class -- the unconditional project rule.
    def list(self, *, year: int | None = None) -> list[PeriodLock]:
        stmt = select(PeriodLock)
        if year is not None:
            stmt = stmt.where(PeriodLock.year == year)
        return list(
            self.session.execute(
                stmt.order_by(PeriodLock.year.desc(), PeriodLock.month.desc())
            ).scalars()
        )


class PeriodLockService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.repo = PeriodLockRepository(session)
        self.activities = ActivityService(session)

    def close_period(self, data: PeriodLockCreate, actor: Actor) -> PeriodLockRead:
        actor.require_unscoped_admin("close_period")
        # Exclusive for the month, before the check: two closes of the same month queue and
        # the second finds the first one's row, and the writes that already checked the month
        # (they hold it shared) commit before this one inserts. The composite primary key
        # stays the database's own guard.
        self.repo.lock_month(data.year, data.month, exclusive=True)
        if self.repo.get(data.year, data.month) is not None:
            raise Conflict(
                ENTITY,
                f"il periodo {period_label(data.year, data.month)} è già chiuso",
                year=data.year,
                month=data.month,
            )
        lock = self.repo.add(
            PeriodLock(
                year=data.year, month=data.month, closed_at=datetime.now(UTC), closed_by=actor.id
            )
        )
        self.activities.record(
            ENTITY,
            PERIOD_LOCK_TIMELINE_ID,
            "closed",
            actor,
            {"year": data.year, "month": data.month},
        )
        self.session.commit()
        return PeriodLockRead.model_validate(lock)

    def reopen_period(self, year: int, month: int, actor: Actor) -> None:
        actor.require_unscoped_admin("reopen_period")
        self.repo.lock_month(year, month, exclusive=True)
        lock = self.repo.get(year, month)
        if lock is None:
            raise NotFound(ENTITY, f"{year}-{month:02d}")
        self.repo.delete(lock)
        self.activities.record(
            ENTITY, PERIOD_LOCK_TIMELINE_ID, "reopened", actor, {"year": year, "month": month}
        )
        self.session.commit()

    def is_closed(self, day: date) -> PeriodLockRead | None:
        lock = self.repo.get(day.year, day.month)
        return PeriodLockRead.model_validate(lock) if lock is not None else None

    def assert_writable(self, entity: str, field: str, *days: date | None) -> None:
        """Refuses if **any** of the supplied days falls in a closed month.

        Variadic because an update has to pass both the stored date and the new one:
        moving a row *out* of a closed month is still a write into it, and checking
        only the destination would let somebody empty a reported month one row at a
        time. `None` days are skipped -- an update that does not touch `date` supplies
        `None`, meaning "unchanged", not "the epoch".

        Takes `entity` and `field` rather than hard-coding them so the `Conflict` names
        the caller's own table (`time_entry` or `cost`), which is what makes the
        problem document actionable on the right screen.

        Each month checked is held shared until the caller's transaction ends
        (`PeriodLockRepository.lock_month`), so a close of that month waits for the write
        that checked it instead of committing between the check and the write.
        """
        for day in days:
            if day is None:
                continue
            self.repo.lock_month(day.year, day.month, exclusive=False)
            lock = self.repo.get(day.year, day.month)
            if lock is None:
                continue
            raise Conflict(
                entity,
                f"il periodo {period_label(lock.year, lock.month)} è chiuso: riaprilo per "
                "modificare voci datate in quel mese",
                field=field,
                year=lock.year,
                month=lock.month,
                closed_at=lock.closed_at.isoformat(),
                closed_by=str(lock.closed_by) if lock.closed_by else None,
            )

    def list_locks(self, *, year: int | None = None) -> list[PeriodLockRead]:
        return [PeriodLockRead.model_validate(lock) for lock in self.repo.list(year=year)]
