"""The timer and the period locks, run for real.

Like `test_invoice_numbering_concurrency.py`, the sessions below are opened from
`db_engine` and commit for real: the shared `db_session` fixture binds one connection, and a
single connection cannot contend with itself.
"""

import threading
from collections.abc import Iterator
from datetime import date
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, text

from pigrocrm.core.actor import Actor
from pigrocrm.core.auth.models import User
from pigrocrm.core.customers.models import Customer
from pigrocrm.core.db import session_factory
from pigrocrm.core.deals.models import Deal
from pigrocrm.core.errors import Conflict, NotFound
from pigrocrm.core.pipeline.models import PipelineStage
from pigrocrm.core.timetracking.locks import PeriodLockService
from pigrocrm.core.timetracking.schemas import PeriodLockCreate, TimerStart, TimerStop
from pigrocrm.core.timetracking.timer import TimerService

# A month no other test closes, in the past so an entry dated in it is not "in the future".
MONTH = date(2020, 6, 15)


@pytest.fixture
def world(db_engine: Engine) -> Iterator[tuple[Any, UUID, UUID]]:
    """A committed user and deal, removed physically afterwards."""
    factory = session_factory(db_engine)
    with factory() as setup:
        user = User(
            email=f"timer-{uuid4()}@example.test",
            password_hash="x",
            nome="Timer",
            ruolo="admin",
        )
        stage = PipelineStage(
            nome=f"Aperto {uuid4()}", posizione=0, probabilita_default=10, tipo="open"
        )
        customer = Customer(ragione_sociale=f"Cliente {uuid4()}")
        setup.add_all([user, stage, customer])
        setup.flush()
        deal = Deal(
            nome="Progetto", customer_id=customer.id, pipeline_stage_id=stage.id, probabilita=10
        )
        setup.add(deal)
        setup.flush()
        ids = (user.id, deal.id, customer.id, stage.id)
        setup.commit()
    try:
        yield factory, ids[0], ids[1]
    finally:
        with factory() as cleanup:
            params = {"user": ids[0], "deal": ids[1], "customer": ids[2], "stage": ids[3]}
            cleanup.execute(text("DELETE FROM activities WHERE actor_id = :user"), params)
            cleanup.execute(text("DELETE FROM time_entries WHERE user_id = :user"), params)
            cleanup.execute(text("DELETE FROM time_timers WHERE user_id = :user"), params)
            cleanup.execute(text("DELETE FROM period_locks WHERE anno = 2020 AND mese = 6"))
            cleanup.execute(text("DELETE FROM deals WHERE id = :deal"), params)
            cleanup.execute(text("DELETE FROM customers WHERE id = :customer"), params)
            cleanup.execute(text("DELETE FROM pipeline_stages WHERE id = :stage"), params)
            cleanup.execute(text("DELETE FROM users WHERE id = :user"), params)
            cleanup.commit()


def test_two_stops_of_the_same_timer_write_the_hours_once(
    world: tuple[Any, UUID, UUID],
) -> None:
    factory, user_id, deal_id = world
    actor = Actor(id=user_id, type="user", role="collaboratore")
    with factory() as session:
        TimerService(session).start(TimerStart(deal_id=deal_id), actor)

    start = threading.Barrier(2)
    outcomes: list[str] = []
    errors: list[BaseException] = []
    guard = threading.Lock()

    def stop() -> None:
        try:
            start.wait(timeout=30)
            with factory() as session:
                TimerService(session).stop(TimerStop(), actor)
            with guard:
                outcomes.append("stopped")
        except NotFound:
            with guard:
                outcomes.append("no timer")
        except BaseException as exc:  # noqa: BLE001 - reported, not swallowed
            with guard:
                errors.append(exc)

    threads = [threading.Thread(target=stop) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)

    assert errors == []
    assert sorted(outcomes) == ["no timer", "stopped"]
    with factory() as check:
        entries = check.execute(
            text("SELECT count(*) FROM time_entries WHERE user_id = :user"), {"user": user_id}
        ).scalar_one()
        timers = check.execute(
            text("SELECT count(*) FROM time_timers WHERE user_id = :user"), {"user": user_id}
        ).scalar_one()
    assert (entries, timers) == (1, 0)


def test_a_second_start_that_lost_the_race_is_a_conflict_and_the_session_survives(
    world: tuple[Any, UUID, UUID], monkeypatch: pytest.MonkeyPatch
) -> None:
    factory, user_id, _ = world
    actor = Actor(id=user_id, type="user", role="collaboratore")
    with factory() as first:
        TimerService(first).start(TimerStart(), actor)

    with factory() as second:
        service = TimerService(second)
        # The other start committed between this one's check and its insert.
        monkeypatch.setattr(service, "_row", lambda *_, **__: None)
        with pytest.raises(Conflict):
            service.start(TimerStart(), actor)
        assert second.execute(text("SELECT 1")).scalar_one() == 1


def test_closing_a_month_waits_for_a_write_that_already_checked_it(
    world: tuple[Any, UUID, UUID],
) -> None:
    """The month has no lock row yet, so there is no row to lock: a write that has checked
    it and not yet committed must still make the close wait, and a write that comes after
    the close must see the month closed."""
    factory, user_id, _ = world
    admin = Actor(id=user_id, type="user", role="admin")
    done = threading.Event()
    errors: list[BaseException] = []

    writer = factory()
    try:
        PeriodLockService(writer).assert_writable("time_entry", "data", MONTH)  # open: allowed

        def close() -> None:
            try:
                with factory() as session:
                    PeriodLockService(session).close_period(
                        PeriodLockCreate(anno=2020, mese=6), admin
                    )
            except BaseException as exc:  # noqa: BLE001 - reported, not swallowed
                errors.append(exc)
            finally:
                done.set()

        closer = threading.Thread(target=close)
        closer.start()
        assert not done.wait(timeout=1.0), "the close did not wait for the write that checked"
    finally:
        writer.rollback()  # the write's transaction ends
        writer.close()
    closer.join(timeout=30)

    assert errors == []
    with factory() as late, pytest.raises(Conflict):
        PeriodLockService(late).assert_writable("time_entry", "data", MONTH)
