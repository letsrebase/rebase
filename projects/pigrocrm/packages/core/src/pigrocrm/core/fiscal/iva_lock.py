"""Closing an IVA period against writes to its registers (REB-1036, suppliers design § 6).

A settlement is recomputed from the documents of its period, and nothing else stops a person or
an agent from adding, changing or cancelling a purchase or a sales document dated inside a period
already settled and paid, so the register drifts from what was paid. A person with the whole
space in view (an unscoped administrator, or the owner of a token with `full_access`) closes the
period and reopens it, each with a trace on the timeline; while it is closed every write that
changes the period's registers is refused with a sentence that names the period.

What a closed period refuses, and where each check is:

* a purchase invoice or credit note placed in the period by its `iva_period`: registering it,
  changing it (the old and the new period both), cancelling, deleting or restoring it
  (`PurchaseService`);
* an invoice issued, annulled or imported with an issue date in the period (`InvoiceEmission`,
  `IssuedInvoiceImporter`): the sales register places a sale by its issue date.

What it does not refuse, because it changes no register: a payment (`set_payment_state`, the due
items), `mark_transmitted_externally`, a draft or a proforma (not in a register until issued),
a receipt or an expense (no VAT lines), and the settlement itself, which recomputes the same
lines. The monthly `PeriodLock` of `timetracking/locks.py` is a different switch for hours and
the cost side of purchases; neither implies the other.

Closing settles the period first, in the same transaction
(`IvaSettlementService.settle_iva_period`),
so what is frozen is the figures the registers add up to now, and a settlement a payment froze
that would change refuses the close with the reason. A write holds its legal entity's year of locks
shared for the rest of its transaction and a close or a reopen holds it exclusive, so a close
never commits between a write's check and the write: a period that has no row to lock yet is
exactly why an advisory lock stands in for a row lock, as in `PeriodLockRepository.lock_month`.
"""

import datetime as dt
from uuid import UUID, uuid5

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from pigrocrm.core.activities.service import ActivityService
from pigrocrm.core.actor import Actor
from pigrocrm.core.errors import Conflict, NotFound, ValidationFailed
from pigrocrm.core.fiscal.iva_settlement import IvaSettlementService, label
from pigrocrm.core.fiscal.models import IvaPeriodLock
from pigrocrm.core.fiscal.schemas import IvaPeriod, IvaPeriodLockCreate, IvaPeriodLockRead
from pigrocrm.core.legal_entities.service import LegalEntityService

ENTITY = "iva_period_lock"
CLOSE_ACTION = "close_iva_period"
REOPEN_ACTION = "reopen_iva_period"
# The first key of the advisory locks taken per legal entity and year, a namespace of this
# project's own next to `timetracking/locks.py` and `gmail/repository.py`.
IVA_LOCK_NAMESPACE = 0x7094
# The namespace of `lock_id`: a lock's id is a name, so a period's closes and reopens share one
# timeline however many times it is closed.
LOCK_ID_NAMESPACE = UUID("2d6a8f04-91c3-5b7e-8a40-6f1e3c9d7b25")


def lock_id(legal_entity_id: UUID, frequency: str, year: int, number: int) -> UUID:
    """The id of the lock of one period: the same whenever that period is closed, so its timeline
    (`closed`, `reopened`, `closed`...) is one sequence."""
    return uuid5(LOCK_ID_NAMESPACE, f"{legal_entity_id}:{frequency}:{year}:{number}")


def closed_sentence(lock: IvaPeriodLock) -> str:
    """The sentence a refusal gives, naming the period."""
    return (
        f"il periodo IVA {label(lock.frequency, lock.year, lock.period_number)} è chiuso: "
        "riaprilo per modificare le registrazioni di quel periodo"
    )


def read_lock(lock: IvaPeriodLock) -> IvaPeriodLockRead:
    return IvaPeriodLockRead(
        id=lock.id,
        legal_entity_id=lock.legal_entity_id,
        frequency=lock.frequency,  # type: ignore[arg-type]
        year=lock.year,
        number=lock.period_number,
        label=label(lock.frequency, lock.year, lock.period_number),
        period_start=lock.period_start,
        period_end=lock.period_end,
        closed_at=lock.closed_at,
        closed_by=lock.closed_by,
    )


class IvaPeriodLockService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.activities = ActivityService(session)

    def _hold(self, legal_entity_id: UUID, year: int, *, exclusive: bool) -> None:
        """A transaction-scoped advisory lock on one legal entity's year of locks. Shared for a
        write (they do not block each other), exclusive for a close or a reopen. The locks one
        session holds never conflict with each other."""
        take = func.pg_advisory_xact_lock if exclusive else func.pg_advisory_xact_lock_shared
        self.session.execute(
            select(take(IVA_LOCK_NAMESPACE, func.hashtext(f"{legal_entity_id}:{year}")))
        )

    def _covering(self, legal_entity_id: UUID, day: dt.date) -> IvaPeriodLock | None:
        return self.session.scalars(
            select(IvaPeriodLock).where(
                IvaPeriodLock.legal_entity_id == legal_entity_id,
                IvaPeriodLock.period_start <= day,
                IvaPeriodLock.period_end >= day,
            )
        ).first()

    def is_closed(self, legal_entity_id: UUID | None, day: dt.date | None) -> bool:
        """Whether `day` is in a period `legal_entity_id` closed: a read for a page, which takes no
        lock (a write asks `assert_writable`)."""
        if legal_entity_id is None or day is None:
            return False
        return self._covering(legal_entity_id, day) is not None

    def assert_writable(
        self, entity: str, field: str, legal_entity_id: UUID | None, *days: dt.date | None
    ) -> None:
        """Refuses if any of `days` falls in a period `legal_entity_id` closed. Variadic because an
        update passes both the stored day and the new one: moving a document out of a closed
        period is still a write into it. A `None` day is skipped, and so is a `None` legal entity
        (a shared purchase has no register). `entity` and `field` are the caller's own, so the
        `Conflict` names the document being written.

        Each year checked is held shared until the caller's transaction ends, so a close of that
        period waits for the write that checked it instead of committing between the check and the
        write."""
        if legal_entity_id is None:
            return
        for day in days:
            if day is None:
                continue
            self._hold(legal_entity_id, day.year, exclusive=False)
            lock = self._covering(legal_entity_id, day)
            if lock is None:
                continue
            raise Conflict(
                entity,
                closed_sentence(lock),
                field=field,
                legal_entity_id=str(lock.legal_entity_id),
                frequency=lock.frequency,
                year=lock.year,
                number=lock.period_number,
                closed_at=lock.closed_at.isoformat(),
                closed_by=str(lock.closed_by) if lock.closed_by else None,
            )

    def close_iva_period(self, data: IvaPeriodLockCreate, actor: Actor) -> IvaPeriodLockRead:
        """Closes one period of one legal entity: settles it (so the frozen figures are current),
        then writes the lock and a timeline entry, in one transaction. Refused when the period
        has not ended, does not exist in the entity's frequency, the entity's pack settles no IVA
        by period, a payment froze a settlement that would change, or the period is closed."""
        actor.require_unscoped_admin(CLOSE_ACTION)
        legal_entity = LegalEntityService(self.session).require_single(
            data.legal_entity_id, entity=ENTITY
        )
        self._hold(legal_entity.id, data.year, exclusive=True)
        already = self.session.scalars(
            select(IvaPeriodLock).where(
                IvaPeriodLock.legal_entity_id == legal_entity.id,
                IvaPeriodLock.year == data.year,
                IvaPeriodLock.period_number == data.number,
            )
        ).first()
        if already is not None:
            raise Conflict(
                ENTITY,
                f"il periodo IVA {label(already.frequency, already.year, already.period_number)} "
                "è già chiuso",
                legal_entity_id=str(legal_entity.id),
                year=data.year,
                number=data.number,
            )
        settlement = IvaSettlementService(self.session).settle_iva_period(
            IvaPeriod(year=data.year, number=data.number),
            actor,
            legal_entity_id=legal_entity.id,
            commit=False,
        )
        if settlement is None:
            raise ValidationFailed(
                ENTITY,
                "year",
                "l'azienda non liquida l'IVA per periodo in quest'anno: non ha periodi da chiudere",
                expected="un anno in cui l'azienda liquida l'IVA per periodo",
            )
        overlapping = self.session.scalars(
            select(IvaPeriodLock).where(
                IvaPeriodLock.legal_entity_id == legal_entity.id,
                IvaPeriodLock.period_start <= settlement.period_end,
                IvaPeriodLock.period_end >= settlement.period_start,
            )
        ).first()
        if overlapping is not None:
            other = label(overlapping.frequency, overlapping.year, overlapping.period_number)
            raise Conflict(
                ENTITY,
                f"il periodo IVA {label(settlement.frequency, data.year, data.number)} "
                f"si sovrappone a {other}, che è già chiuso",
                legal_entity_id=str(legal_entity.id),
                year=data.year,
                number=data.number,
            )
        lock = IvaPeriodLock(
            id=lock_id(legal_entity.id, settlement.frequency, data.year, data.number),
            legal_entity_id=legal_entity.id,
            frequency=settlement.frequency,
            year=data.year,
            period_number=data.number,
            period_start=settlement.period_start,
            period_end=settlement.period_end,
            closed_at=dt.datetime.now(dt.UTC),
            closed_by=actor.id,
        )
        self.session.add(lock)
        self.session.flush()
        self.activities.record(
            ENTITY,
            lock.id,
            "iva_period_closed",
            actor,
            {
                "legal_entity_id": str(legal_entity.id),
                "frequency": lock.frequency,
                "year": data.year,
                "number": data.number,
            },
        )
        self.session.commit()
        return read_lock(lock)

    def reopen_iva_period(
        self, year: int, number: int, actor: Actor, *, legal_entity_id: UUID | None = None
    ) -> None:
        """Reopens a closed period and leaves a trace: writes dated in it are allowed again."""
        actor.require_unscoped_admin(REOPEN_ACTION)
        legal_entity = LegalEntityService(self.session).require_single(
            legal_entity_id, entity=ENTITY
        )
        self._hold(legal_entity.id, year, exclusive=True)
        lock = self.session.scalars(
            select(IvaPeriodLock).where(
                IvaPeriodLock.legal_entity_id == legal_entity.id,
                IvaPeriodLock.year == year,
                IvaPeriodLock.period_number == number,
            )
        ).first()
        if lock is None:
            raise NotFound(
                ENTITY,
                f"{year}-{number:02d}",
                message=f"il periodo IVA {number} del {year} non è chiuso",
            )
        self.activities.record(
            ENTITY,
            lock.id,
            "iva_period_reopened",
            actor,
            {
                "legal_entity_id": str(legal_entity.id),
                "frequency": lock.frequency,
                "year": year,
                "number": number,
            },
        )
        self.session.delete(lock)
        self.session.commit()


__all__ = [
    "CLOSE_ACTION",
    "ENTITY",
    "REOPEN_ACTION",
    "IvaPeriodLockService",
    "closed_sentence",
    "lock_id",
    "read_lock",
]
