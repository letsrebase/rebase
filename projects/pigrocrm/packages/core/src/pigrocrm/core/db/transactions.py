"""One way to flush a write that can lose a race on a unique or exclusion constraint.

A service that catches the `IntegrityError` of its own flush and calls `session.rollback()`
throws away everything else the caller had pending in the same transaction. A SAVEPOINT
confines the failure to the one write: only the nested transaction is rolled back, and the
constraint violation becomes the domain error the caller can show.
"""

from collections.abc import Callable, Iterator
from contextlib import contextmanager

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from pigrocrm.core.errors import DomainError


@contextmanager
def savepoint(
    session: Session, conflict: Callable[[IntegrityError], DomainError | IntegrityError]
) -> Iterator[None]:
    """Runs the body in a SAVEPOINT and flushes it on exit.

    An `IntegrityError` rolls back to the savepoint (the caller's pending work stays) and is
    re-raised as whatever `conflict(exc)` returns, so a handler can refuse the constraints it
    knows and hand every other violation back unchanged by returning `exc` itself.
    """
    try:
        with session.begin_nested():
            yield
    except IntegrityError as exc:
        error = conflict(exc)
        if error is exc:
            raise
        raise error from exc
