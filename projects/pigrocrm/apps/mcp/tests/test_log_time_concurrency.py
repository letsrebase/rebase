"""Criterion 11. Twenty simultaneous `log_time` calls against real Postgres produce
twenty rows and twenty activities with no session error. It is residual R1 closed, and
it is the condition on which this tool exists at all -- Task 4A-1 is what makes it
pass, and this is the test that proves the fix reached the tool rather than only the
provider.

Seeds its own deal and user through a plain, explicitly committed session against
`mcp_engine` -- not through the `seeded_deal_id`/`seeded_user_id` fixtures (which ride
on `mcp_session`'s per-test, rolled-back savepoint transaction, exactly the
"mcp_session savepoint fixture" `test_session_scope.py`'s own module docstring says
cannot express independent connections). Twenty threads each open their own connection
via `ScopedSessionProvider`; none of them would ever see a deal or a user that only
exists inside a transaction nobody has committed.
"""

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from uuid import UUID, uuid4

from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.auth.models import User
from pigrocrm.core.customers.models import Customer
from pigrocrm.core.db import session_factory
from pigrocrm.core.deals.models import Deal
from pigrocrm.core.pipeline.models import PipelineStage
from pigrocrm_mcp.context import McpContext, ScopedSessionProvider
from pigrocrm_mcp.tools import timetracking

CONCURRENCY = 20


def _seed_committed_deal_and_user(mcp_engine: Engine) -> tuple[UUID, UUID, UUID, UUID]:
    session: Session = session_factory(mcp_engine)()
    try:
        stage = PipelineStage(
            nome=f"Aperto {uuid4()}", position=0, default_probability=10, type="open"
        )
        customer = Customer(legal_name=f"Cliente concorrenza {uuid4()}")
        user = User(email=f"worker-{uuid4()}@example.test", password_hash="x", nome="Worker")
        session.add_all([stage, customer, user])
        session.flush()
        deal = Deal(
            nome="Progetto concorrenza",
            customer_id=customer.id,
            pipeline_stage_id=stage.id,
            probability=10,
        )
        session.add(deal)
        session.commit()
        return deal.id, user.id, customer.id, stage.id
    finally:
        session.close()


def _remove_committed_rows(
    mcp_engine: Engine, deal_id: UUID, user_id: UUID, customer_id: UUID, stage_id: UUID
) -> None:
    """The rows are committed on purpose (twenty connections must see them), so nothing
    rolls them back: a user left behind shows up as a second person in every later test
    that lists the space's users, and the unbilled hours as an extra reminder."""
    with session_factory(mcp_engine)() as cleanup:
        ids = {"deal": deal_id, "user": user_id, "customer": customer_id, "stage": stage_id}
        for statement in (
            "DELETE FROM activities WHERE actor_id = :user",
            "DELETE FROM time_entries WHERE deal_id = :deal",
            "DELETE FROM deals WHERE id = :deal",
            "DELETE FROM customers WHERE id = :customer",
            "DELETE FROM pipeline_stages WHERE id = :stage",
            "DELETE FROM users WHERE id = :user",
        ):
            cleanup.execute(text(statement), ids)
        cleanup.commit()


def test_twenty_concurrent_log_time_calls_produce_twenty_rows(
    mcp_engine: Engine, mcp_storage
) -> None:
    deal_id, user_id, customer_id, stage_id = _seed_committed_deal_and_user(mcp_engine)
    try:
        _run_concurrent_log_time(mcp_engine, mcp_storage, deal_id, user_id)
    finally:
        _remove_committed_rows(mcp_engine, deal_id, user_id, customer_id, stage_id)


def _run_concurrent_log_time(mcp_engine: Engine, mcp_storage, deal_id: UUID, user_id: UUID) -> None:
    provider = ScopedSessionProvider(session_factory(mcp_engine))
    actor = Actor(id=user_id, type="mcp", role="collaborator")
    context = McpContext(provider, lambda: actor, mcp_storage)
    barrier = threading.Barrier(CONCURRENCY)

    def write(index: int) -> str:
        barrier.wait(timeout=30)
        with provider.scope():
            return timetracking.log_time(
                context,
                {
                    "deal_id": deal_id,
                    "user_id": user_id,
                    "data": date(2026, 3, 10),
                    "hours": "1.00",
                    "description": f"Voce {index}",
                },
            )["id"]

    with ThreadPoolExecutor(max_workers=CONCURRENCY) as pool:
        ids = list(pool.map(write, range(CONCURRENCY)))

    assert len(set(ids)) == CONCURRENCY
    with session_factory(mcp_engine)() as check:
        rows = check.execute(
            text("SELECT count(*) FROM time_entries WHERE deal_id = :deal"),
            {"deal": deal_id},
        ).scalar_one()
        activities = check.execute(
            text(
                "SELECT count(*) FROM activities "
                "WHERE entity_type = 'time_entry' AND kind = 'created' "
                "  AND actor_type = 'mcp' AND entity_id = ANY(:ids)"
            ),
            {"ids": [UUID(i) for i in ids]},
        ).scalar_one()
    assert rows == CONCURRENCY
    assert activities == CONCURRENCY
