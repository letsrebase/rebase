"""`savepoint` confines a lost race to the one write that lost it."""

from datetime import UTC, datetime
from uuid import UUID

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from pigrocrm.core.db.transactions import savepoint
from pigrocrm.core.errors import Conflict
from pigrocrm.core.timetracking.models import TimeTimer


def _timer(user_id: UUID) -> TimeTimer:
    return TimeTimer(user_id=user_id, started_at=datetime.now(UTC))


def test_conflict_rolls_back_only_the_savepoint(db_session: Session, seeded_user_id: UUID) -> None:
    db_session.add(_timer(seeded_user_id))
    db_session.flush()

    with pytest.raises(Conflict), savepoint(db_session, lambda exc: Conflict("timer", "running")):
        db_session.add(_timer(seeded_user_id))

    # The first timer is still pending in the caller's transaction, and the session is usable.
    assert db_session.scalar(select(func.count()).select_from(TimeTimer)) == 1


def test_returning_the_original_error_reraises_it(
    db_session: Session, seeded_user_id: UUID
) -> None:
    db_session.add(_timer(seeded_user_id))
    db_session.flush()

    with pytest.raises(IntegrityError), savepoint(db_session, lambda exc: exc):
        db_session.add(_timer(seeded_user_id))
