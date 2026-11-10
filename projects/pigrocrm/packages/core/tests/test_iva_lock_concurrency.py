"""Closing an IVA period against a write that already checked it, run for real (REB-1036).

Like `test_time_concurrency.py`, the sessions below are opened from `db_engine` and commit for
real: the shared `db_session` fixture binds one connection, and a single connection cannot contend
with itself. A period that has no lock row yet has no row to lock, so the check and the close
meet on an advisory lock of the legal entity's year.
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
from pigrocrm.core.db import session_factory
from pigrocrm.core.errors import Conflict
from pigrocrm.core.fiscal.iva_lock import IvaPeriodLockService
from pigrocrm.core.fiscal.models import FiscalProfile
from pigrocrm.core.fiscal.schemas import IvaPeriodLockCreate
from pigrocrm.core.legal_entities.repository import LegalEntityRepository

# The first quarter of 2026: it has ended whatever day the suite runs on from April 2026 on.
INSIDE = date(2026, 2, 10)


@pytest.fixture
def world(db_engine: Engine) -> Iterator[tuple[Any, UUID, UUID]]:
    """A committed administrator and a fiscal profile for the default legal entity, removed
    physically afterwards together with everything closing the quarter wrote."""
    factory = session_factory(db_engine)
    with factory() as setup:
        user = User(
            email=f"iva-lock-{uuid4()}@example.test", password_hash="x", name="Lock", role="admin"
        )
        setup.add(user)
        entity = LegalEntityRepository(setup).default()
        assert entity is not None
        setup.add(
            FiscalProfile(
                legal_entity_id=entity.id,
                pack_id="it-standard",
                regime_code="RF01",
                iva_frequency="quarterly",
            )
        )
        setup.flush()
        ids = (user.id, entity.id)
        setup.commit()
    try:
        yield factory, ids[0], ids[1]
    finally:
        with factory() as cleanup:
            params = {"user": ids[0], "entity": ids[1]}
            cleanup.execute(
                text("DELETE FROM iva_period_locks WHERE legal_entity_id = :entity"), params
            )
            cleanup.execute(
                text(
                    "DELETE FROM due_item_settlements WHERE due_item_id IN (SELECT id FROM "
                    "due_items WHERE legal_entity_id = :entity AND source_kind = 'iva_settlement')"
                ),
                params,
            )
            cleanup.execute(
                text(
                    "DELETE FROM due_items WHERE legal_entity_id = :entity "
                    "AND source_kind = 'iva_settlement'"
                ),
                params,
            )
            cleanup.execute(
                text("DELETE FROM iva_settlements WHERE legal_entity_id = :entity"), params
            )
            cleanup.execute(text("DELETE FROM activities WHERE actor_id = :user"), params)
            cleanup.execute(
                text("DELETE FROM fiscal_profile WHERE legal_entity_id = :entity"), params
            )
            cleanup.execute(text("DELETE FROM users WHERE id = :user"), params)
            cleanup.commit()


def test_closing_a_period_waits_for_a_write_that_already_checked_it(
    world: tuple[Any, UUID, UUID],
) -> None:
    """A write that has checked the period and not yet committed makes the close wait, and a
    write that comes after the close sees the period closed."""
    factory, user_id, entity_id = world
    admin = Actor(id=user_id, type="user", role="admin")
    done = threading.Event()
    errors: list[BaseException] = []

    writer = factory()
    try:
        IvaPeriodLockService(writer).assert_writable(
            "purchase", "iva_period", entity_id, INSIDE
        )  # open: allowed

        def close() -> None:
            try:
                with factory() as session:
                    IvaPeriodLockService(session).close_iva_period(
                        IvaPeriodLockCreate(year=2026, number=1, legal_entity_id=entity_id), admin
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
    closer.join(timeout=60)

    assert errors == []
    with factory() as late, pytest.raises(Conflict) as refused:
        IvaPeriodLockService(late).assert_writable("purchase", "iva_period", entity_id, INSIDE)
    assert "1° trimestre 2026" in refused.value.details["reason"]
