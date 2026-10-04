"""**Criterion 12.** Ten simultaneous dashboard reads on one MCP server, on real
PostgreSQL.

This is residuo **R1** verified from the read side, and the condition under which the
dashboard tools are allowed to exist at all. `test_session_scope.py` and
`test_log_time_concurrency.py` already close R1 for a *writer*; the reason it is worth
re-asking for a reader is that an aggregation query holds its connection far longer than
a `get` does, which widens the window in which two calls overlap, and because §7.1's "one
dashboard, one transaction, one instant" is a property **of the session**: with one shared
session and no per-call transaction boundary two dashboards can each read half their
figures inside the other's transaction and produce a total that was true at no instant --
the worst case, because re-reading `DashboardService` would never reveal it.

**Ten reads that merely complete would prove nothing.** Three things make these tests able
to fail, and they are the reason this file is not simply ten `asyncio.gather`ed calls:

1. a **committed** corpus and **real, separate connections**, exactly as
   `test_log_time_concurrency.py` established -- the package's `mcp_session` savepoint
   fixture is a single connection inside one outer transaction and cannot host a race at
   all, and `DashboardService` refuses it outright anyway;
2. a **barrier**, so the ten callers are provably inside their own scope simultaneously
   rather than merely dispatched in a loop that happened to finish; and
3. an assertion on what concurrency is supposed to buy -- ten *distinct backend PIDs*, each
   reporting `repeatable read` **from inside its own still-open snapshot transaction** --
   rather than on the mere absence of an exception. The engine's default is `read
   committed` (`create_engine_from_settings` sets no isolation level), so that assertion
   fails the moment a caller stops getting its own snapshot.

`test_a_shared_session_cannot_serve_ten_readers` is the negative control for all of it: the
same ten reads, wired the way the process used to be wired, and the failure is measured
rather than asserted from memory. Without it "ten reads succeeded" is a claim about this
file's plumbing and not about the cure.

**The two measurements this file rests on**, taken against the installed SDK (mcp==2.0.0)
before the assertions were written rather than assumed from the plan: ten `asyncio.gather`ed
`call_tool`s on one client reached a **peak of 10 simultaneous tool bodies**, and the same
ten against a server wired `build_server(lambda: shared_session, ...)` produced **9 errors
out of 10**, reading `This session is provisioning a new connection; concurrent operations
are not permitted` -- which is why `test_no_call_fails_with_a_session_error` lists
`concurrent operations` among the symptoms it refuses.
"""

from __future__ import annotations

import asyncio
import json
import sys
import threading
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from mcp import Client
from sqlalchemy import Engine, delete, text
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.config import Settings
from pigrocrm.core.customers.models import Customer
from pigrocrm.core.dashboard.schemas import PeriodoQuery
from pigrocrm.core.db import session_factory, today_local
from pigrocrm.core.deals.models import Deal
from pigrocrm.core.invoices.models import Invoice
from pigrocrm.core.pipeline.models import PipelineStage
from pigrocrm.core.storage import LocalFileStorage
from pigrocrm_mcp.context import McpContext, ScopedSessionProvider
from pigrocrm_mcp.server import build_server
from pigrocrm_mcp.tools import dashboard as dashboard_tools

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "packages" / "core" / "tests"))
from fakes.azienda_fixtures import committed_default_azienda, remove_azienda  # noqa: E402

ADMIN = Actor(id=None, type="mcp", role="admin")

CONCURRENCY = 10
# Generous, because it is not a performance assertion: it is the difference between a
# deadlock reported as a failure and a suite that hangs forever.
BARRIER_TIMEOUT = 60.0

_PREFIX = "MCPCONC"
STAGE_VINTO = f"{_PREFIX} stato vinto"

# The instant is the one field criterion 12 exempts: each call is its own transaction and
# therefore its own `transaction_timestamp()`, so demanding byte-for-byte equality of the
# whole payload would fail *without* a defect -- and passing only on the defect that makes
# ten calls share one transaction.
INSTANT = "calcolato_alle"


def _payload(result: Any) -> dict[str, Any]:
    return result.structured_content or json.loads(result.content[0].text)


@pytest.fixture
def committed_corpus(mcp_engine: Engine) -> Iterator[None]:
    """Committed, additive, and removed by prefix -- `test_mcp_dashboard.py`'s pattern and
    for its reasons.

    Committed because a scoped session per call never sees another transaction's
    uncommitted rows, which is the whole point of it. Additive because `mcp_engine` is
    session-scoped and earlier files in this package commit deals that outlive them, so
    this file creates its own stage instead of seeding or emptying the defaults, and every
    "the corpus produced something" guard below is keyed on rows it created itself.

    The invoice exists so the economic dashboard is comparing something. Ten identical
    *empty* payloads is an equality between ten copies of nothing, and it would pass with
    every figure hard-coded to zero. `anno=2099` with a random `numero` keeps it clear of
    the fiscal register any other file may have committed into, while `data_emissione`
    (which is what attributes it to a period) is today.
    """
    factory = session_factory(mcp_engine)
    with factory() as session:
        azienda_id = committed_default_azienda(session)
        # `code=None`: a user-created stage as far as `seed_defaults` is concerned, so it
        # never collides with the seeded `lead`/`vinto` on the unique index. `posizione`
        # well past the defaults leaves any existing ordering alone.
        vinto = PipelineStage(
            nome=STAGE_VINTO, posizione=910, probabilita_default=100, tipo="won", code=None
        )
        customer = Customer(ragione_sociale=f"{_PREFIX} Cliente", nazione="IT", custom_fields={})
        session.add_all([vinto, customer])
        session.flush()
        deal = Deal(
            nome=f"{_PREFIX} deal vinto",
            customer_id=customer.id,
            pipeline_stage_id=vinto.id,
            valore_previsto=Decimal("4000.00"),
            probabilita=100,
            chiuso_il=today_local(),
            custom_fields={},
        )
        session.add(deal)
        session.flush()
        oggi = today_local()
        session.add(
            Invoice(
                customer_id=customer.id,
                deal_id=deal.id,
                tipo="fattura",
                stato="emessa",
                anno=2099,
                numero=uuid4().int % 1_000_000 + 1,
                data_emissione=oggi,
                # Deliberately in the past, so the invoice is both `da_incassare` and
                # `scaduto` and neither figure is a zero that would compare equal to
                # itself ten times over for the wrong reason.
                data_scadenza=oggi - timedelta(days=30),
                imponibile=Decimal("1234.00"),
                imposta=Decimal("271.48"),
                totale=Decimal("1505.48"),
                custom_fields={},
            )
        )
        session.commit()
    try:
        yield
    finally:
        with factory() as session:
            session.execute(
                delete(Invoice).where(
                    Invoice.customer_id.in_(
                        session.query(Customer.id).filter(
                            Customer.ragione_sociale.like(f"{_PREFIX} %")
                        )
                    )
                )
            )
            session.execute(delete(Deal).where(Deal.nome.like(f"{_PREFIX} %")))
            session.execute(delete(Customer).where(Customer.ragione_sociale.like(f"{_PREFIX} %")))
            session.execute(delete(PipelineStage).where(PipelineStage.nome.like(f"{_PREFIX} %")))
            remove_azienda(session, azienda_id)
            session.commit()


@pytest.fixture
def concurrent_server(mcp_engine: Engine, tmp_path: Path, committed_corpus: None) -> Any:
    """Wired the way `__main__.py` wires production: a `ScopedSessionProvider`, one session
    per logical call, opened untouched and closed on the way out. The package's own `server`
    fixture shares a single `Session` held inside an outer transaction, which is the defect
    this file measures rather than the harness it can use.
    """
    return build_server(
        ScopedSessionProvider(session_factory(mcp_engine)),
        lambda: ADMIN,
        LocalFileStorage(tmp_path),
    )


def _agree_except_on_the_instant(payloads: list[dict[str, Any]]) -> list[Any]:
    """Strip and return the instants, asserting everything else is one single answer."""
    instants = [payload.pop(INSTANT) for payload in payloads]
    first = payloads[0]
    for index, payload in enumerate(payloads[1:], start=1):
        assert payload == first, f"response {index} differs from response 0"
    return instants


# -- the concurrency itself, with a barrier so it is real -------------------------


def test_ten_simultaneous_readers_each_get_their_own_snapshot(
    mcp_engine: Engine, mcp_storage: LocalFileStorage, committed_corpus: None
) -> None:
    """The core of criterion 12, at the layer where a barrier can be placed.

    Ten threads rendezvous *inside* their own `ScopedSessionProvider.scope()` and only then
    read the dashboard, so the overlap is a fact of the test rather than a hope about
    scheduling. What is then asserted is what the concurrency was supposed to buy:

    * ten **distinct backend PIDs** -- ten real connections, not one session handed round.
      This is the assertion a shared session fails first and unambiguously.
    * every one of them reporting **`repeatable read`**, read back from inside the snapshot
      transaction the dashboard opened and nothing has closed (a read-only dashboard
      commits nothing, and `provider.scope()` only closes the session on the way out). The
      engine sets no isolation level, so `read committed` is what this returns the moment a
      caller stops getting its own `_open_snapshot`.
    * the ten payloads agreeing on every figure, differing only on the instant.

    Reading the two `SHOW`/`SELECT` values *after* the call and not before is deliberate:
    before it, the session has no transaction at all -- and touching it would begin one and
    make `_open_snapshot` refuse the session outright.
    """
    provider = ScopedSessionProvider(session_factory(mcp_engine))
    context = McpContext(provider, lambda: ADMIN, mcp_storage)
    barrier = threading.Barrier(CONCURRENCY)

    def read(_: int) -> tuple[dict[str, Any], int, str]:
        with provider.scope() as session:
            # Every thread waits until all ten hold their own session. `provider.scope()`
            # has constructed the `Session` but no statement has run, so nothing here has
            # yet opened a transaction the dashboard would refuse.
            barrier.wait(timeout=BARRIER_TIMEOUT)
            payload = dashboard_tools.get_economic_dashboard(context, PeriodoQuery())
            pid: int = session.execute(text("SELECT pg_backend_pid()")).scalar_one()
            level: str = session.execute(text("SHOW transaction_isolation")).scalar_one()
            return payload, pid, level

    with ThreadPoolExecutor(max_workers=CONCURRENCY) as pool:
        observed = list(pool.map(read, range(CONCURRENCY)))

    payloads = [payload for payload, _, _ in observed]
    pids = [pid for _, pid, _ in observed]
    levels = {level for _, _, level in observed}

    assert len(set(pids)) == CONCURRENCY, f"ten readers shared {CONCURRENCY - len(set(pids))}"
    assert levels == {"repeatable read"}, levels
    # Keyed on rows this file committed, so the equality below is not an equality between
    # ten copies of an empty dashboard.
    assert payloads[0]["fatture_emesse"] >= 1, payloads[0]
    assert payloads[0]["da_incassare"] != "0.00", payloads[0]
    instants = _agree_except_on_the_instant(payloads)
    # Ten transactions, so more than one instant. Asserting all ten *differ* would be
    # asserting that ten backends cannot share a microsecond; asserting they are all equal
    # would be asserting the defect.
    assert len(set(instants)) > 1, instants


def test_a_shared_session_cannot_serve_ten_readers(
    mcp_engine: Engine, mcp_storage: LocalFileStorage, committed_corpus: None
) -> None:
    """The negative control, and the reason the test above is evidence rather than a claim.

    Wired the way the process was wired before Task 4A-1 -- `lambda: session`, one `Session`
    for every call, which `build_server` still supports and falls back to a `nullcontext`
    scope for. This session is *fresh* (not the package's savepoint fixture), so the first
    reader is not refused for having a transaction already: it succeeds, opens `REPEATABLE
    READ`, and -- a read-only dashboard commits nothing -- leaves the session in it, so
    every later reader is refused by `_open_snapshot`. Nine failures out of ten, measured.

    If this ever passes, the assertion above has stopped being able to fail and the file
    stops proving anything.
    """
    session: Session = session_factory(mcp_engine)()
    context = McpContext(lambda: session, lambda: ADMIN, mcp_storage)
    barrier = threading.Barrier(CONCURRENCY)

    def read(_: int) -> str | None:
        barrier.wait(timeout=BARRIER_TIMEOUT)
        try:
            dashboard_tools.get_economic_dashboard(context, PeriodoQuery())
        except Exception as exc:  # noqa: BLE001 -- the shape is the measurement
            return f"{type(exc).__name__}: {exc}"
        return None

    try:
        with ThreadPoolExecutor(max_workers=CONCURRENCY) as pool:
            outcomes = list(pool.map(read, range(CONCURRENCY)))
    finally:
        session.close()

    failures = [outcome for outcome in outcomes if outcome is not None]
    assert len(failures) >= CONCURRENCY - 1, outcomes
    assert any(
        "no transaction in progress" in failure or "REPEATABLE READ" in failure
        for failure in failures
    ), failures


# -- the same thing end to end, over the MCP transport ----------------------------


async def _gather_calls(server: Any, tool: str, arguments: dict[str, Any]) -> list[Any]:
    """Ten calls in flight on **one** client session, which is what "ten concurrent reads
    over MCP" means: ten separate clients would be ten transports and would say nothing
    about a server dispatching more than one request at a time. JSON-RPC ids are what make
    this legal, and the installed SDK runs a sync tool body on a worker thread
    (`anyio.to_thread.run_sync`), so the ten really do overlap.
    """
    async with Client(server) as client:
        return list(
            await asyncio.gather(*(client.call_tool(tool, arguments) for _ in range(CONCURRENCY)))
        )


async def test_the_transport_really_dispatches_ten_calls_at_once(
    mcp_engine: Engine, tmp_path: Path, committed_corpus: None
) -> None:
    """The barrier at the MCP layer, and the guard that keeps every `_gather_calls` test
    below from quietly becoming a loop.

    `asyncio.gather` requests concurrency; it does not create it. If a future SDK answered
    the ten requests one after another, every assertion in this file's transport half would
    still pass while measuring nothing. So ten calls are made to rendezvous **inside** the
    server: `actor_provider` is read exactly once per `get_economic_dashboard` call
    (`DashboardService(context.session).get_economic_dashboard(query, context.actor)`), after
    `_guard` has opened that call's own session scope, and a `threading.Barrier` of ten there
    can only be crossed if ten calls are genuinely in flight together. Serialisation shows up
    as `BrokenBarrierError` on the timeout -- a named failure -- instead of silence.

    The barrier deliberately runs **no SQL**: touching the session here would begin a
    transaction and `_open_snapshot` would then refuse the session outright, which is a
    different failure and would hide this one.

    Measured against the installed SDK before this was written: peak simultaneous calls 10
    (sync tool bodies go through `anyio.to_thread.run_sync`, whose default thread limiter is
    40, so ten is nowhere near it).
    """
    barrier = threading.Barrier(CONCURRENCY)

    def actor_provider() -> Actor:
        barrier.wait(timeout=BARRIER_TIMEOUT)
        return ADMIN

    # The barrier counts on one `actor_provider` read per call, from the tool's thread.
    # With PostHog installed (ORB-186) the identity callback reads it once more, on the
    # event loop, before the tool is dispatched: the first such read would block the
    # loop waiting for nine others that cannot come. A developer's shell may export the
    # key for a live check; this test measures the transport, so it runs without one.
    settings = Settings(_env_file=None, posthog_key="")  # type: ignore[call-arg]

    server = build_server(
        ScopedSessionProvider(session_factory(mcp_engine)),
        actor_provider,
        LocalFileStorage(tmp_path),
        settings,
    )
    results = await _gather_calls(server, "get_economic_dashboard", {})
    assert all(not result.is_error for result in results), [
        str(result.content) for result in results if result.is_error
    ]
    assert not barrier.broken


async def test_ten_concurrent_economic_dashboards_agree_except_on_the_instant(
    concurrent_server: Any,
) -> None:
    """Criterion 12 as it is written: ten simultaneous `get_economic_dashboard` calls on one
    server, through the SDK's own client rather than through threads of our own, because
    the concurrency that matters is the one the transport actually produces. The installed
    SDK dispatches a sync tool body via `anyio.to_thread.run_sync`, so these ten really do
    run on ten threads and really do take ten sessions out of `ScopedSessionProvider`.
    """
    results = await _gather_calls(concurrent_server, "get_economic_dashboard", {})
    assert all(not result.is_error for result in results), [
        str(result.content) for result in results if result.is_error
    ]
    payloads = [_payload(result) for result in results]
    assert payloads[0]["fatture_emesse"] >= 1, payloads[0]
    instants = _agree_except_on_the_instant(payloads)
    assert len(set(instants)) > 1, instants


async def test_no_call_fails_with_a_session_error(concurrent_server: Any) -> None:
    """The shape R1 actually produces, refused by name.

    With a shared session a concurrent call lands mid-transaction on another call's session
    and SQLAlchemy raises. The list is not guesswork: reproduced here against a
    `build_server(lambda: shared_session, ...)` server, nine of ten calls came back carrying
    `This session is provisioning a new connection; concurrent operations are not
    permitted`, which is the `concurrent operations` entry below; the others are the shapes
    the same collision takes when the timing differs (`PendingRollbackError`,
    `InvalidRequestError`, a session already begun). Matched on the rendered message because
    `_guard` converts the exception to an agent-facing string before a test can see its
    type, and asserted on the *operational* dashboard because it is the longest-running of
    the three and therefore the one with the widest overlap window.
    """
    results = await _gather_calls(concurrent_server, "get_operational_dashboard", {})
    for result in results:
        rendered = str(result.content)
        for symptom in (
            "rollback",
            "prepared state",
            "already begun",
            "concurrent operations",
            "InvalidRequestError",
            "PendingRollbackError",
        ):
            assert symptom not in rendered, rendered


async def test_ten_concurrent_searches_also_succeed(concurrent_server: Any) -> None:
    """The lighter case, kept because it is the one that would still pass on a shared
    session and therefore tells the two failure modes apart: if the searches pass and the
    dashboards do not, the problem is the longer-held connection of an aggregation query
    rather than the session sharing itself.
    """
    results = await _gather_calls(concurrent_server, "search_everything", {"termine": _PREFIX})
    assert all(not result.is_error for result in results), [
        str(result.content) for result in results if result.is_error
    ]


@pytest.mark.parametrize(
    "tool",
    ["get_commercial_dashboard", "get_economic_dashboard", "get_operational_dashboard"],
)
async def test_each_dashboard_tool_survives_concurrency_individually(
    concurrent_server: Any, tool: str
) -> None:
    """Per tool, so a failure names which one rather than "the dashboards"."""
    results = await _gather_calls(concurrent_server, tool, {})
    assert all(not result.is_error for result in results), [
        str(result.content) for result in results if result.is_error
    ]
    payloads = [_payload(result) for result in results]
    _agree_except_on_the_instant(payloads)
