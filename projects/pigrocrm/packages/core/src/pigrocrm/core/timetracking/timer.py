"""The running timer: start, adjust, stop into an entry, or throw away.

Slice 4 §13 argued that a stopwatch costs three mechanisms and does nothing about the
Tuesday nobody entered. The grid still attacks that failure; this module adds the
stopwatch beside it because Ivan asked for the Toggl/Clockify shape on 2026-09-09, and
it costs one mechanism here, not three: the session is a row (`TimeTimer`), the browser
closing loses nothing because the row is on the server, and the second device sees the
same row. Recovery is reading it back.

What this module never does is write an hour of its own. `stop` builds a
`TimeEntryCreate` and hands it to `TimeEntryService.create`, so the frozen rate, the
period lock, the "not in the future" check, the closed-deal warning and the activity
row are the same ones every other path produces. A timer is a way of *measuring* the
hours; the entry is still the entry.
"""

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.db.clock import today_local
from pigrocrm.core.db.transactions import savepoint
from pigrocrm.core.deals.repository import DealRepository
from pigrocrm.core.errors import Conflict, DomainError, NotFound, ValidationFailed
from pigrocrm.core.money import round_hours
from pigrocrm.core.timetracking.models import TimeTimer
from pigrocrm.core.timetracking.schemas import (
    HOURS_MAX,
    HOURS_MIN,
    TimeEntryCreate,
    TimeEntryRead,
    TimerRead,
    TimerStart,
    TimerStop,
    TimerUpdate,
)
from pigrocrm.core.timetracking.service import TimeEntryService

ENTITY = "timer"

# What an entry made from a stopped timer says when nobody typed anything: honest about
# where it came from, and replaceable from the entry itself, like the grid's default.
DEFAULT_DESCRIPTION = "Ore registrate dal timer"

# The unique index that allows one running timer per user (`models.py`).
TIMER_USER_INDEX = "ux_time_timers_user"


def _now() -> datetime:
    return datetime.now(UTC)


def elapsed_hours(started_at: datetime, stopped_at: datetime) -> Decimal:
    """Seconds between the two instants, as hours with two decimals.

    Clamped into the same range `TimeEntryCreate.hours` accepts: a timer stopped after
    twenty seconds is still an entry of one hundredth of an hour rather than a 422 the
    person cannot act on, and one left running over a weekend is capped at the day the
    schema allows -- the entry is then visibly wrong in the right direction, and editable.
    """
    seconds = Decimal(max((stopped_at - started_at).total_seconds(), 0))
    hours = round_hours(seconds / Decimal(3600))
    return min(max(hours, HOURS_MIN), HOURS_MAX)


class TimerService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.entries = TimeEntryService(session)
        self.deals = DealRepository(session)

    def _require_deal(self, deal_id: UUID) -> None:
        if self.deals.get(deal_id) is None:
            raise NotFound("deal", deal_id)

    def _row(self, actor: Actor, *, lock: bool = False) -> TimeTimer | None:
        if actor.id is None:
            # A system actor has no clock: a timer measures a person's hours.
            return None
        stmt = select(TimeTimer).where(TimeTimer.user_id == actor.id)
        if lock:
            # `populate_existing`: a copy loaded earlier in the session must not stand in
            # for the row as it is once the lock is granted.
            stmt = stmt.with_for_update().execution_options(populate_existing=True)
        return self.session.execute(stmt).scalar_one_or_none()

    def current(self, actor: Actor) -> TimerRead | None:
        """The running timer of whoever is asking, or nothing. Never somebody else's:
        the row is keyed on the actor, and there is no parameter to ask about another
        user -- an admin reads the team's hours through the entries, not their clocks."""
        row = self._row(actor)
        return TimerRead.model_validate(row) if row is not None else None

    def start(self, data: TimerStart, actor: Actor) -> TimerRead:
        actor.require_write("start_timer")
        if actor.id is None:
            raise Conflict(ENTITY, "solo un utente può avviare un timer")
        if self._row(actor) is not None:
            raise Conflict(ENTITY, "un timer è già in corso: fermalo o scartalo prima")
        if data.deal_id is not None:
            self._require_deal(data.deal_id)
        row = TimeTimer(
            user_id=actor.id,
            deal_id=data.deal_id,
            description=data.description,
            billable=data.billable,
            started_at=_now(),
        )

        def refuse(exc: IntegrityError) -> DomainError | IntegrityError:
            # Two starts at once both passed the check above; the unique index is the authority.
            if TIMER_USER_INDEX not in str(exc.orig):
                return exc
            return Conflict(ENTITY, "un timer è già in corso: fermalo o scartalo prima")

        with savepoint(self.session, refuse):
            self.session.add(row)
        self.session.commit()
        return TimerRead.model_validate(row)

    def update(self, data: TimerUpdate, actor: Actor) -> TimerRead:
        actor.require_write("update_timer")
        row = self._require(actor)
        if data.deal_id is not None:
            self._require_deal(data.deal_id)
            row.deal_id = data.deal_id
        if data.description is not None:
            row.description = data.description
        if data.billable is not None:
            row.billable = data.billable
        self.session.commit()
        return TimerRead.model_validate(row)

    def stop(self, data: TimerStop, actor: Actor) -> TimeEntryRead:
        """Turns the running timer into an entry and removes it.

        The deal may arrive now or have been set at start; without one there is nothing
        to attach the hours to, and the timer is left running rather than lost -- the
        person is told what is missing and can choose the deal and stop again.

        The timer row is locked and the entry and the deletion of the row commit together:
        a double click or a second device waits for the first stop and then finds no timer,
        so the hours are written once, and no failure leaves an hour either lost (timer
        gone, no entry) or counted twice (entry written, timer still running).
        """
        actor.require_write("stop_timer")
        row = self._require(actor, lock=True)
        deal_id = data.deal_id if data.deal_id is not None else row.deal_id
        if deal_id is None:
            raise ValidationFailed(
                ENTITY,
                "deal_id",
                "scegli il deal su cui registrare le ore prima di fermare il timer",
                expected="l'id di un deal esistente",
            )
        description = data.description if data.description is not None else row.description
        entry = self.entries.create(
            TimeEntryCreate(
                deal_id=deal_id,
                user_id=row.user_id,
                data=data.data if data.data is not None else today_local(),
                hours=elapsed_hours(row.started_at, _now()),
                description=description.strip() or DEFAULT_DESCRIPTION,
                billable=row.billable,
            ),
            actor,
            commit=False,
        )
        self.session.delete(row)
        self.session.commit()
        return entry

    def discard(self, actor: Actor) -> None:
        actor.require_write("discard_timer")
        row = self._require(actor)
        self.session.delete(row)
        self.session.commit()

    def _require(self, actor: Actor, *, lock: bool = False) -> TimeTimer:
        row = self._row(actor, lock=lock)
        if row is None:
            raise NotFound(ENTITY, "in_progress")
        return row
