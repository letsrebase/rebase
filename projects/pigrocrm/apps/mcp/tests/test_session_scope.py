"""R1, reproduced and then closed.

The slice 1A reviewer measured 10 concurrent `create_organization` calls against one
shared Session as 0 successes and 0 rows. `log_time` (slice 4) is a write tool and
the centre of this slice's agentic surface, so that measurement is the exact
behaviour this test refuses to ship.

Uses `Engine`-backed real sessions, not the `mcp_session` savepoint fixture: the
whole point is that each thread gets its own connection and its own transaction,
which a shared savepoint session cannot express.
"""

import threading
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

from sqlalchemy import Engine, text

from pigrocrm.core.actor import Actor
from pigrocrm.core.db import session_factory
from pigrocrm.core.organizations.schemas import OrganizationCreate
from pigrocrm.core.organizations.service import OrganizationService
from pigrocrm.core.storage import LocalFileStorage
from pigrocrm_mcp.context import McpContext, ScopedSessionProvider

CONCURRENCY = 20


def test_twenty_concurrent_writes_all_succeed(
    mcp_engine: Engine, mcp_storage: LocalFileStorage
) -> None:
    provider = ScopedSessionProvider(session_factory(mcp_engine))
    actor = Actor(id=None, type="mcp", role="admin")
    context = McpContext(provider, lambda: actor, mcp_storage)
    barrier = threading.Barrier(CONCURRENCY)
    names = [f"Concorrente {uuid4()}" for _ in range(CONCURRENCY)]

    def write(name: str) -> str:
        barrier.wait(timeout=30)
        with provider.scope():
            return str(
                OrganizationService(context.session)
                .create(OrganizationCreate(legal_name=name), context.actor)
                .id
            )

    with ThreadPoolExecutor(max_workers=CONCURRENCY) as pool:
        ids = list(pool.map(write, names))

    assert len(set(ids)) == CONCURRENCY
    with session_factory(mcp_engine)() as check:
        stored = check.execute(
            text("SELECT count(*) FROM organizations WHERE legal_name = ANY(:names)"),
            {"names": names},
        ).scalar_one()
    assert stored == CONCURRENCY


def test_every_read_of_session_inside_one_scope_is_the_same_session(
    mcp_engine: Engine, mcp_storage: LocalFileStorage
) -> None:
    """`resources/entities.py` reads `context.session` up to four times while
    rendering one resource. A provider that opened a fresh session per read would
    leak that many un-closed sessions per call -- the concrete objection recorded
    in `server.py`'s own comment against a naive session-per-call design."""
    provider = ScopedSessionProvider(session_factory(mcp_engine))
    context = McpContext(provider, Actor.system, mcp_storage)
    with provider.scope():
        assert context.session is context.session is provider()


def test_leaving_the_scope_closes_the_session(
    mcp_engine: Engine, mcp_storage: LocalFileStorage
) -> None:
    provider = ScopedSessionProvider(session_factory(mcp_engine))
    with provider.scope() as session:
        pass
    assert not session.is_active or session.get_transaction() is None
    # And a read outside any scope is a programming error, not a silent new session.
    try:
        provider()
    except RuntimeError as exc:
        assert "scope" in str(exc)
    else:
        raise AssertionError("expected RuntimeError outside a scope")
