"""§11.1's dashboard tools, and the one deliberate absence.

The dashboards are on the MCP surface not for symmetry but because their shape is already
§3's arithmetic-free composition: the tool returns the same figures from the same owning
service, never a second version. Leaving them off would force an agent to make six reads
and add them up itself -- the second source of truth reached by another road.

**This file builds its own server, and that is the subject as much as the setup.** The
package's `server` fixture wires `build_server(lambda: mcp_session, ...)`, one `Session`
shared by every call and held inside an outer transaction -- and `DashboardService`
refuses such a session by design, because a dashboard that cannot set `REPEATABLE READ`
is a dashboard whose cards can disagree with their own drill-throughs. So the server here
is wired the way `__main__.py` wires production: a `ScopedSessionProvider`, one session
per logical call, opened untouched and closed on the way out.
`test_the_shared_session_fixture_is_refused_loudly` pins that this is a real property of
the service and not a quirk of this file's plumbing.

The corpus is committed, because a scoped session never sees another transaction's
uncommitted rows -- which is the point of it -- and it is **additive**. `mcp_engine` is
session-scoped and earlier files in this package commit deals that outlive them
(`test_full_cycle.py`, `test_log_time_concurrency.py`), so this file creates two stages of
its own instead of seeding the defaults, keys every figure assertion on those, and removes
only its own rows. Emptying `pipeline_stages` -- what the two dashboard files under
`packages/core/tests` do, where they are the only writers -- fails here with a foreign key
violation on somebody else's leftovers, and would be the wrong thing to do even if it did
not.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from mcp import Client
from sqlalchemy import Engine, delete

from pigrocrm.core.actor import Actor
from pigrocrm.core.customers.models import Customer
from pigrocrm.core.dashboard.schemas import PeriodQuery
from pigrocrm.core.dashboard.service import DashboardService
from pigrocrm.core.db import month_bounds, session_factory, today_local
from pigrocrm.core.deals.models import Deal
from pigrocrm.core.pipeline.models import PipelineStage
from pigrocrm.core.storage import LocalFileStorage
from pigrocrm_mcp.context import ScopedSessionProvider
from pigrocrm_mcp.server import build_server

ADMIN = Actor(id=None, type="mcp", role="admin")
_PREFIX = "MCPDASH"
STAGE_OPEN = f"{_PREFIX} stato aperto"
STAGE_WON = f"{_PREFIX} stato vinto"


def _payload(result: Any) -> dict[str, Any]:
    return result.structured_content or json.loads(result.content[0].text)


@pytest.fixture
def dashboard_server(mcp_engine: Engine, tmp_path: Path) -> Iterator[Any]:
    factory = session_factory(mcp_engine)
    with factory() as session:
        # `code=None`, so these are user-created stages as far as `seed_defaults` is
        # concerned and never collide with the seeded `lead`/`vinto` on the unique index.
        # `position` well past the defaults, so the ordering of anything already there is
        # left alone.
        open = PipelineStage(
            nome=STAGE_OPEN, position=900, default_probability=50, type="open", code=None
        )
        won = PipelineStage(
            nome=STAGE_WON, position=901, default_probability=100, type="won", code=None
        )
        session.add_all([open, won])
        customer = Customer(legal_name=f"{_PREFIX} Cliente", country="IT", custom_fields={})
        session.add(customer)
        session.flush()
        session.add(
            Deal(
                nome=f"{_PREFIX} aperto",
                customer_id=customer.id,
                pipeline_stage_id=open.id,
                expected_value=Decimal("1000.00"),
                probability=50,
                custom_fields={},
            )
        )
        session.add(
            Deal(
                nome=f"{_PREFIX} vinto",
                customer_id=customer.id,
                pipeline_stage_id=won.id,
                expected_value=Decimal("5000.00"),
                probability=100,
                closed_at=today_local(),
                custom_fields={},
            )
        )
        session.commit()
    try:
        yield build_server(
            ScopedSessionProvider(factory), lambda: ADMIN, LocalFileStorage(tmp_path)
        )
    finally:
        with factory() as session:
            session.execute(delete(Deal).where(Deal.nome.like(f"{_PREFIX} %")))
            session.execute(delete(Customer).where(Customer.legal_name.like(f"{_PREFIX} %")))
            session.execute(delete(PipelineStage).where(PipelineStage.nome.like(f"{_PREFIX} %")))
            session.commit()
    # `automation_config` is deliberately left alone. `describe_automations` creates the
    # single row on demand and this file never changes it, so what would be left behind is
    # a row carrying the defaults -- indistinguishable from the one the next reader would
    # create. Deleting a single-row table that another file may equally have created is the
    # worse leftover of the two.


# -- what is on the surface, and what is not -------------------------------------


async def test_the_dashboard_and_automation_tools_are_registered(dashboard_server: Any) -> None:
    async with Client(dashboard_server) as client:
        names = {tool.name for tool in (await client.list_tools()).tools}
    assert "get_commercial_dashboard" in names
    assert "get_receivables_dashboard" in names
    assert "describe_automations" in names


async def test_the_receivables_tool_adds_up_to_the_economic_total(dashboard_server: Any) -> None:
    """Slice 8 §2.2 on the wire (REB-329): the six buckets, every amount a string, their
    codes in the page's order, and `total` the very string the economic tool answers as
    `unpaid` -- two tools, one figure."""
    async with Client(dashboard_server) as client:
        page = _payload(await client.call_tool("get_receivables_dashboard", {}))
        economic = _payload(await client.call_tool("get_economic_dashboard", {}))
    assert [f["code"] for f in page["bands"]] == [
        "expired",
        "entro_30",
        "da_31_a_60",
        "da_61_a_90",
        "oltre_90",
        "without_due_date",
    ]
    assert all(isinstance(f["amount"], str) for f in page["bands"])
    assert isinstance(page["total"], str)
    assert page["total"] == economic["unpaid"]


async def test_update_automation_config_has_no_tool(dashboard_server: Any) -> None:
    """§11.1's single exclusion. It changes what the system will do to future data with no
    human in the loop (slice 4 §11 reason 2), and while residuo R10 is open -- a PAT has no
    scopes and inherits its owner's full role -- *not registering the tool* is the only
    enforcement that actually holds. An authorisation check would let an admin token
    straight through.

    Two assertions, because the first one alone is a spelling test: the second refuses any
    tool that writes the configuration under a different name.
    """
    async with Client(dashboard_server) as client:
        names = {tool.name for tool in (await client.list_tools()).tools}
    assert "update_automation_config" not in names
    assert not any("automation_config" in name and "update" in name for name in names)


async def test_the_tool_promises_the_agent_a_date_and_a_default(dashboard_server: Any) -> None:
    """A tool's docstring and schema are the only documentation an agent ever sees, so
    both promises are executable here rather than merely written.

    The schema advertises `format: date` on each bound -- which is what stops an agent
    inventing `01/03/2026` -- and the docstring says that without either bound the period
    is the current month. `test_mcp_search.py` tests its own docstring the same way and for
    the same reason.
    """
    today = today_local()
    first, last = month_bounds(today.year, today.month)
    async with Client(dashboard_server) as client:
        tool = next(
            candidate
            for candidate in (await client.list_tools()).tools
            if candidate.name == "get_commercial_dashboard"
        )
        payload = _payload(await client.call_tool("get_commercial_dashboard", {}))

    assert payload["period"] == {"date_from": first.isoformat(), "date_to": last.isoformat()}
    for bound in ("da", "a"):
        advertised = tool.input_schema["properties"][bound]
        formats = {branch.get("format") for branch in advertised.get("anyOf", [advertised])}
        assert "date" in formats, advertised


# -- the figures -----------------------------------------------------------------


async def test_the_tool_returns_the_same_figures_as_the_service(
    dashboard_server: Any, mcp_engine: Engine
) -> None:
    """The adapter is thin. Compared against the service on its own fresh session over the
    same committed corpus -- not against numbers recomputed here, which would make this
    file a second source of truth for figures §3 says one place may produce."""
    async with Client(dashboard_server) as client:
        result = await client.call_tool("get_commercial_dashboard", {})
    payload = _payload(result)

    with session_factory(mcp_engine)() as session:
        direct = DashboardService(session).get_commercial_dashboard(PeriodQuery(), ADMIN)
    expected = direct.model_dump(mode="json")
    assert payload["pipeline"] == expected["pipeline"]
    assert payload["closings"] == expected["closings"]
    # Keyed on this file's own stage, so the assertion means something whatever else the
    # session-scoped database happens to be carrying: an equality between two empty lists
    # would be an equality all the same.
    mine = next(row for row in payload["pipeline"] if row["stage_name"] == STAGE_OPEN)
    assert mine["number"] == 1
    assert mine["total_value"] == "1000.00"


async def test_money_crosses_the_wire_as_a_string(dashboard_server: Any) -> None:
    """A JSON number is a float in whatever parses it on the other side, and a float total
    is the defect this slice exists to prevent.

    What this actually guards is the *schema*, not the adapter: measured, the SDK's own
    serialisation renders a `Decimal` as a string whether the tool dumps in `"json"` mode
    or not, so removing `mode="json"` does not fail here -- but declaring any of these
    fields `float` does, immediately, on both surfaces. Stated rather than left implied,
    because a test whose stated reason is not its real one is a test the next reader will
    delete for the wrong cause.
    """
    async with Client(dashboard_server) as client:
        payload = _payload(await client.call_tool("get_commercial_dashboard", {}))
    assert payload["pipeline"], "the corpus produced no stage rows, so this proved nothing"
    for row in payload["pipeline"]:
        assert isinstance(row["total_value"], str), row
        assert isinstance(row["weighted_value"], str), row
    assert isinstance(payload["closings"]["won_value"], str)


async def test_the_period_is_taken_from_the_arguments_and_echoed_back(
    dashboard_server: Any,
) -> None:
    async with Client(dashboard_server) as client:
        payload = _payload(
            await client.call_tool(
                "get_commercial_dashboard", {"date_from": "2026-03-01", "date_to": "2026-03-31"}
            )
        )
    assert payload["period"] == {"date_from": "2026-03-01", "date_to": "2026-03-31"}


async def test_two_calls_in_a_row_both_succeed(dashboard_server: Any) -> None:
    """One session per logical call, not one per process (residuo R1, closed by Task
    4A-1). Nothing commits a dashboard's read-only transaction, so a server sharing one
    session would answer the first call and raise on the second -- and an agent would see
    an intermittent failure with no pattern to it."""
    async with Client(dashboard_server) as client:
        first = _payload(await client.call_tool("get_commercial_dashboard", {}))
        second = _payload(await client.call_tool("get_commercial_dashboard", {}))
    assert first["pipeline"] == second["pipeline"]
    assert first["calculated_at"] != second["calculated_at"]


@pytest.mark.parametrize(
    ("arguments", "perche"),
    [
        ({"date_from": "2026-03-31", "date_to": "2026-03-01"}, "il periodo è invertito"),
        ({"date_from": "2026-03-01"}, "metà periodo non è un periodo"),
        ({"date_from": "non-una-data", "date_to": "2026-03-31"}, "la data non è ISO"),
    ],
)
async def test_a_bad_period_is_an_error_the_agent_can_read(
    dashboard_server: Any, arguments: dict[str, str], why: str
) -> None:
    """Guidance, not a stack trace, and from two different sources.

    The first two are `ValidationFailed` out of `PeriodQuery.resolve`. The third never
    reaches the service at all: `da` is typed `IsoDateStr`, whose runtime type is `str`, so
    the malformed value is rejected by `PeriodQuery.model_validate` *inside* the guarded
    call as a `pydantic.ValidationError` -- a `ValueError` subclass, which is why `_guard`
    renders it the same way instead of letting the SDK answer in wording that is not ours.
    """
    async with Client(dashboard_server) as client:
        result = await client.call_tool("get_commercial_dashboard", arguments)
    assert result.is_error, why


async def test_the_session_survives_a_rejected_period(dashboard_server: Any) -> None:
    """The failure mode `_guard`'s rollback exists for: a refused call must not leave the
    next one broken. Asserted here because a dashboard opens an isolation level on its
    session, which is exactly the kind of state that outlives a naive error path."""
    async with Client(dashboard_server) as client:
        rejected = await client.call_tool(
            "get_commercial_dashboard", {"date_from": "2026-03-31", "date_to": "2026-03-01"}
        )
        assert rejected.is_error
        after = await client.call_tool("get_commercial_dashboard", {})
    assert not after.is_error
    assert _payload(after)["pipeline"]


async def test_describe_automations_names_every_rule(dashboard_server: Any) -> None:
    async with Client(dashboard_server) as client:
        payload = _payload(await client.call_tool("describe_automations", {}))
    assert [rule["code"] for rule in payload["rules"]] == [
        "A1",
        "A2",
        "sent_offer_follow_up",
        "stalled_deal",
        "overdue_invoice",
    ]
    assert all(rule["description"] for rule in payload["rules"])
    assert set(payload) == {"configuration", "rules", "runs"}


# -- the plumbing this file depends on, asserted rather than assumed --------------


async def test_the_shared_session_fixture_is_refused_loudly(
    server: Any,
) -> None:
    """The reason `dashboard_server` exists.

    The package's own `server` fixture shares one `Session` held inside an outer
    transaction -- the wiring `build_server`'s docstring calls the plain-callable fallback.
    `DashboardService` refuses it rather than running in `READ COMMITTED` and returning a
    total that was true at no instant. If this ever stops being an error, the guarantee
    has been lost somewhere and `dashboard_server` is no longer buying anything.
    """
    async with Client(server) as client:
        result = await client.call_tool("get_commercial_dashboard", {})
    assert result.is_error


# -- the sidebar's azienda (REB-631, spec 2026-10-03 §7) ---------------------------------


async def test_every_dashboard_tool_offers_the_legal_entity_and_echoes_it(
    dashboard_server: Any,
) -> None:
    """The four tools take `legal_entity_id` as a filter, say so in their descriptions, and
    echo it back; under an azienda nobody owns the corpus's own figures read empty and
    the page keeps its shape, the same wire check `test_dashboard_api.py` makes."""
    nobody = str(uuid4())
    async with Client(dashboard_server) as client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}
        for name in (
            "get_commercial_dashboard",
            "get_economic_dashboard",
            "get_operational_dashboard",
            "get_receivables_dashboard",
        ):
            assert "legal_entity_id" in tools[name].input_schema["properties"], name
            assert "legal_entity_id" in (tools[name].description or ""), name
            tutte = _payload(await client.call_tool(name, {}))
            assert tutte["legal_entity_id"] is None, name
            scoped = _payload(await client.call_tool(name, {"legal_entity_id": nobody}))
            assert scoped["legal_entity_id"] == nobody, name
        commercial = _payload(
            await client.call_tool("get_commercial_dashboard", {"legal_entity_id": nobody})
        )
        assert all(row["number"] == 0 for row in commercial["pipeline"])
        malformed = await client.call_tool(
            "get_commercial_dashboard", {"legal_entity_id": "non-id"}
        )
    assert malformed.is_error
    assert "legal_entity_id" in malformed.content[0].text


# -- REB-811: the feed an agent reads carries no account events -------------------


async def test_the_operational_feed_hides_account_events_from_an_agent_token(
    dashboard_server: Any, mcp_engine: Engine
) -> None:
    from pigrocrm.core.activities.models import Activity
    from pigrocrm.core.activities.service import ActivityService
    from pigrocrm.core.dashboard.service import DashboardService

    marker = f"{_PREFIX}-invitee@example.it"
    factory = session_factory(mcp_engine)
    with factory() as session:
        service = ActivityService(session)
        rows = [
            service.record("invitation", uuid4(), "created", ADMIN, {"email": marker}),
            service.record("user", uuid4(), "pat_created", ADMIN, {"token_id": f"{_PREFIX}-tok"}),
            service.record("digest", uuid4(), "digest.inviato", ADMIN, {"to": marker}),
            service.record("customer", uuid4(), f"{_PREFIX}.updated", ADMIN, {"changed": ["note"]}),
        ]
        session.commit()
        ids = [row.id for row in rows]
    try:
        async with Client(dashboard_server) as client:
            feed = _payload(await client.call_tool("get_operational_dashboard", {}))
        raw = json.dumps(feed["recent_task"])
        assert marker not in raw
        assert f"{_PREFIX}-tok" not in raw
        assert {row["entity_type"] for row in feed["recent_task"]}.isdisjoint(
            {"invitation", "user", "digest"}
        )
        # The business row is still there: the filter narrows the feed, it does not empty it.
        assert f"{_PREFIX}.updated" in [row["kind"] for row in feed["recent_task"]]

        # A person administering the space keeps the whole feed.
        person = Actor(id=None, type="user", role="admin")
        with factory() as session:
            full = DashboardService(session).get_operational_dashboard(person)
        assert "invitation" in {row.entity_type for row in full.recent_task}
    finally:
        with factory() as session:
            session.execute(delete(Activity).where(Activity.id.in_(ids)))
            session.commit()
