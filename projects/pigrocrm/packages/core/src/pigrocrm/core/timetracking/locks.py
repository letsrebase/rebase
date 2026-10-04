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

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
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

MESI_ITALIANI = (
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


def period_label(anno: int, mese: int) -> str:
    """`marzo 2026`. Long-form Italian, carried over from the previous system's own period label:
    the monthly cut is what a client expects next to an invoice, and it is what gets
    agreed on."""
    return f"{MESI_ITALIANI[mese - 1]} {anno}"


class PeriodLockRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, anno: int, mese: int) -> PeriodLock | None:
        return self.session.get(PeriodLock, (anno, mese))

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
    def list(self, *, anno: int | None = None) -> list[PeriodLock]:
        stmt = select(PeriodLock)
        if anno is not None:
            stmt = stmt.where(PeriodLock.anno == anno)
        return list(
            self.session.execute(
                stmt.order_by(PeriodLock.anno.desc(), PeriodLock.mese.desc())
            ).scalars()
        )


class PeriodLockService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.repo = PeriodLockRepository(session)
        self.activities = ActivityService(session)

    def close_period(self, data: PeriodLockCreate, actor: Actor) -> PeriodLockRead:
        actor.require_unscoped_admin("close_period")
        if self.repo.get(data.anno, data.mese) is not None:
            raise Conflict(
                ENTITY,
                f"il periodo {period_label(data.anno, data.mese)} è già chiuso",
                anno=data.anno,
                mese=data.mese,
            )
        lock = PeriodLock(
            anno=data.anno, mese=data.mese, chiuso_il=datetime.now(UTC), chiuso_da=actor.id
        )
        try:
            self.repo.add(lock)
            self.activities.record(
                ENTITY,
                PERIOD_LOCK_TIMELINE_ID,
                "closed",
                actor,
                {"anno": data.anno, "mese": data.mese},
            )
            self.session.commit()
        except IntegrityError as exc:
            # The pre-check cannot cover two concurrent closes of the same month:
            # there the composite primary key is the only authority.
            self.session.rollback()
            raise Conflict(
                ENTITY,
                f"il periodo {period_label(data.anno, data.mese)} è già chiuso",
                anno=data.anno,
                mese=data.mese,
            ) from exc
        return PeriodLockRead.model_validate(lock)

    def reopen_period(self, anno: int, mese: int, actor: Actor) -> None:
        actor.require_unscoped_admin("reopen_period")
        lock = self.repo.get(anno, mese)
        if lock is None:
            raise NotFound(ENTITY, f"{anno}-{mese:02d}")
        self.repo.delete(lock)
        self.activities.record(
            ENTITY, PERIOD_LOCK_TIMELINE_ID, "reopened", actor, {"anno": anno, "mese": mese}
        )
        self.session.commit()

    def is_closed(self, giorno: date) -> PeriodLockRead | None:
        lock = self.repo.get(giorno.year, giorno.month)
        return PeriodLockRead.model_validate(lock) if lock is not None else None

    def assert_writable(self, entity: str, field: str, *giorni: date | None) -> None:
        """Refuses if **any** of the supplied days falls in a closed month.

        Variadic because an update has to pass both the stored date and the new one:
        moving a row *out* of a closed month is still a write into it, and checking
        only the destination would let somebody empty a reported month one row at a
        time. `None` days are skipped -- an update that does not touch `data` supplies
        `None`, meaning "unchanged", not "the epoch".

        Takes `entity` and `field` rather than hard-coding them so the `Conflict` names
        the caller's own table (`time_entry` or `cost`), which is what makes the
        problem document actionable on the right screen.
        """
        for giorno in giorni:
            if giorno is None:
                continue
            lock = self.repo.get(giorno.year, giorno.month)
            if lock is None:
                continue
            raise Conflict(
                entity,
                f"il periodo {period_label(lock.anno, lock.mese)} è chiuso: riaprilo per "
                "modificare voci datate in quel mese",
                field=field,
                anno=lock.anno,
                mese=lock.mese,
                chiuso_il=lock.chiuso_il.isoformat(),
                chiuso_da=str(lock.chiuso_da) if lock.chiuso_da else None,
            )

    def list_locks(self, *, anno: int | None = None) -> list[PeriodLockRead]:
        return [PeriodLockRead.model_validate(lock) for lock in self.repo.list(anno=anno)]
