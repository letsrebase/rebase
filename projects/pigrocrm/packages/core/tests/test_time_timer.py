"""The running timer: one per person, and stopping it is an ordinary `log_time`."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.errors import Conflict, NotFound, PermissionDenied, ValidationFailed
from pigrocrm.core.timetracking import timer as timer_module
from pigrocrm.core.timetracking.models import TimeTimer
from pigrocrm.core.timetracking.schemas import (
    TimeEntryListQuery,
    TimerStart,
    TimerStop,
    TimerUpdate,
)
from pigrocrm.core.timetracking.service import TimeEntryService
from pigrocrm.core.timetracking.timer import TimerService, elapsed_hours


def _writer(user_id: UUID) -> Actor:
    return Actor(id=user_id, type="user", role="collaborator")


def test_elapsed_hours_rounds_to_hundredths_and_never_below_the_minimum() -> None:
    start = datetime(2026, 3, 10, 9, 0, tzinfo=UTC)
    assert elapsed_hours(start, start + timedelta(minutes=90)) == Decimal("1.50")
    assert elapsed_hours(start, start + timedelta(minutes=20)) == Decimal("0.33")
    # Twenty seconds is still an entry, not a 422 the person cannot act on.
    assert elapsed_hours(start, start + timedelta(seconds=20)) == Decimal("0.01")
    # A weekend left running is capped at the day the schema allows, and stays editable.
    assert elapsed_hours(start, start + timedelta(days=3)) == Decimal("24.00")


def test_a_person_has_one_timer_and_sees_only_their_own(
    db_session: Session, seeded_deal_id: UUID, seeded_user_id: UUID
) -> None:
    service = TimerService(db_session)
    actor = _writer(seeded_user_id)
    assert service.current(actor) is None

    started = service.start(TimerStart(deal_id=seeded_deal_id, description="Call"), actor)
    assert started.deal_id == seeded_deal_id
    assert service.current(actor) is not None

    with pytest.raises(Conflict):
        service.start(TimerStart(), actor)

    # Somebody else's clock is not this person's business, in either direction.
    other = _writer(uuid4())
    assert service.current(other) is None
    with pytest.raises(NotFound):
        service.discard(other)


def test_stopping_writes_an_entry_through_the_ordinary_path_and_removes_the_timer(
    db_session: Session, seeded_deal_id: UUID, seeded_user_id: UUID
) -> None:
    service = TimerService(db_session)
    actor = _writer(seeded_user_id)
    service.start(TimerStart(description="Analisi requisiti", billable=False), actor)
    # Back-date the start by hand: the clock is real, the test is not going to wait.
    row = db_session.execute(
        TimeTimer.__table__.select().where(TimeTimer.user_id == seeded_user_id)
    ).one()
    db_session.query(TimeTimer).filter_by(id=row.id).update(
        {"started_at": datetime.now(UTC) - timedelta(minutes=45)}
    )
    db_session.flush()

    # No deal yet: the timer is kept, the person is told what is missing.
    with pytest.raises(ValidationFailed) as refused:
        service.stop(TimerStop(), actor)
    assert refused.value.details["field"] == "deal_id"
    assert service.current(actor) is not None

    entry = service.stop(TimerStop(deal_id=seeded_deal_id), actor)
    assert entry.deal_id == seeded_deal_id
    assert entry.user_id == seeded_user_id
    assert entry.description == "Analisi requisiti"
    assert entry.billable is False
    assert entry.hours == Decimal("0.75")
    assert service.current(actor) is None

    # The entry is a real one: listed like any other, with its activity row.
    page = TimeEntryService(db_session).list(TimeEntryListQuery(user_id=seeded_user_id), actor)
    assert [item.id for item in page.items] == [entry.id]


def test_an_empty_description_gets_an_honest_default_and_update_changes_the_running_clock(
    db_session: Session, seeded_deal_id: UUID, seeded_user_id: UUID
) -> None:
    service = TimerService(db_session)
    actor = _writer(seeded_user_id)
    service.start(TimerStart(), actor)
    updated = service.update(TimerUpdate(deal_id=seeded_deal_id, billable=False), actor)
    assert updated.deal_id == seeded_deal_id and updated.billable is False

    entry = service.stop(TimerStop(), actor)
    assert entry.description == timer_module.DEFAULT_DESCRIPTION
    assert entry.hours == Decimal("0.01")


def test_a_reader_may_look_but_not_start(db_session: Session, seeded_user_id: UUID) -> None:
    reader = Actor(id=seeded_user_id, type="user", role="readonly")
    service = TimerService(db_session)
    assert service.current(reader) is None
    with pytest.raises(PermissionDenied):
        service.start(TimerStart(), reader)


def test_a_missing_deal_is_refused_at_start(db_session: Session, seeded_user_id: UUID) -> None:
    with pytest.raises(NotFound):
        TimerService(db_session).start(TimerStart(deal_id=uuid4()), _writer(seeded_user_id))
