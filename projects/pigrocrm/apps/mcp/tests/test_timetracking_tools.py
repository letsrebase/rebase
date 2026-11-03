"""`log_time` is the most valuable agentic operation in this product and the safest one:
"Claude, ho fatto tre ore ieri sul progetto Rossi" is the weekly time saving slice 1 §1
makes the criterion of existence for every feature. It is the opposite of
`issue_invoice`: reversible, attributed, and bounded to one deal and one day."""

from pathlib import Path

from mcp import Client
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.config import Settings
from pigrocrm.core.storage import LocalFileStorage
from pigrocrm.core.timetracking.locks import PeriodLockService
from pigrocrm.core.timetracking.schemas import PeriodLockCreate
from pigrocrm_mcp.server import build_server

ADMIN = Actor(id=None, type="mcp", role="admin")
# The person's half. Setting up a fixture by taking a decision the product reserves to
# a human needs a human actor, or the setup contradicts what the test then asserts.
UMANO = Actor(id=None, type="user", role="admin")


async def test_log_time_writes_and_is_attributable_to_the_agent(
    server, seeded_deal_id, seeded_user_id, mcp_session
) -> None:
    async with Client(server) as client:
        created = await client.call_tool(
            "log_time",
            {
                "deal_id": str(seeded_deal_id),
                "user_id": str(seeded_user_id),
                "data": "2026-03-10",
                # A string with two decimal places, not a bare `3`: `HoursArg` accepts
                # `float | str`, and a JSON integer arrives as a Python `float` whose
                # `Decimal` conversion carries only as many places as the float itself
                # (`Decimal('3.0')`, not `Decimal('3.00')`) -- this asserts the exact
                # wire value `TimeEntryRead` returns, so the input is written the same
                # way a client's own numeric text field would send it.
                "ore": "3.50",
                "descrizione": "Analisi requisiti",
            },
        )
        entry = created.structured_content
        assert entry["ore"] == "3.50"

        timeline = await client.call_tool(
            "get_timeline", {"entity_type": "time_entry", "entity_id": entry["id"]}
        )
        assert timeline.structured_content["entries"][0]["actor_type"] == "mcp"


async def test_the_eleven_excluded_tools_do_not_exist(server) -> None:
    """Criterion 9's first half, from the client's own point of view: an agent trying to
    recalculate rates finds no tool to call. Eleven, not the ten slice 4 §11 enumerated:
    `unarchive_cost_category` was missing from that list by omission -- it is
    `archive_cost_category` in the other direction, and settles the same question of
    which categories the CRM offers -- and was added to the ban rather than left as a
    method nobody had decided about."""
    async with Client(server) as client:
        names = {tool.name for tool in (await client.list_tools()).tools}
    for forbidden in (
        "recalculate_rates",
        "update_user_rates",
        "update_deal_rate",
        "create_cost_category",
        "update_cost_category",
        "archive_cost_category",
        "unarchive_cost_category",
        "bind_time_to_invoice",
        "close_period",
        "reopen_period",
        "get_fiscal_estimate",
    ):
        assert forbidden not in names
    # The reads beside them, and the line they draw: an agent may see which categories
    # exist and which months are closed, and may change neither.
    assert {"log_time", "describe_rates", "list_cost_categories", "list_period_locks"} <= names


async def test_list_period_locks_shows_the_months_log_time_will_refuse(
    server, seeded_deal_id, seeded_user_id, mcp_session: Session
) -> None:
    """The read that turns a refusal into a plan. `log_time`'s `Conflict` names the one
    month it hit, which leaves an agent with a backlog of entries to write discovering
    the closed months one rejection at a time. Closing a period is still absent from the
    surface: seeing which months are closed is not deciding which ones are.

    The close is performed here through the service with a **user** actor, exactly as
    `test_full_cycle.py` drives the human half of its own cycle -- the point is that the
    agent can *read* a decision a person took, not that it could take it.

    `UMANO` and not this file's `ADMIN`, which is `type="mcp"`: `close_period` is one of
    the operations `FULL_ACCESS_ACTIONS` refuses to any agent credential
    whatever its role, so an mcp actor here would be setting up the fixture by doing the
    very thing the test says an agent cannot do. It passed until that ban existed."""
    PeriodLockService(mcp_session).close_period(PeriodLockCreate(anno=2026, mese=1), UMANO)

    async with Client(server) as client:
        listed = (await client.call_tool("list_period_locks", {})).structured_content
        assert [(lock["anno"], lock["mese"]) for lock in listed["locks"]] == [(2026, 1)]

        # The same month, from the writing end: the two answers have to agree, or the
        # list is decoration.
        blocked = await client.call_tool(
            "log_time",
            {
                "deal_id": str(seeded_deal_id),
                "user_id": str(seeded_user_id),
                "data": "2026-01-15",
                "ore": "2.00",
                "descrizione": "Voce arretrata",
            },
        )
        assert blocked.is_error
        assert "chiuso" in blocked.content[0].text

        other_year = (
            await client.call_tool("list_period_locks", {"anno": 2025})
        ).structured_content
        assert other_year["locks"] == []


async def test_list_period_locks_rejects_a_wrong_typed_year_with_guidance(server) -> None:
    """`list_locks` has no schema of its own -- `anno` goes straight into a `WHERE` --
    so a non-numeric year would reach the query as a raw database error. `OptionalAnno`
    keeps the SDK from rejecting it ahead of `_guard`, and `_ANNO` validates it inside
    the guarded call so the answer is a diagnosis rather than a stack trace."""
    async with Client(server) as client:
        result = await client.call_tool("list_period_locks", {"anno": "scorso"})

    assert result.is_error
    message = result.content[0].text
    assert "numero intero" in message
    assert "errors.pydantic.dev" not in message


async def test_a_bad_argument_comes_back_as_guidance_not_a_pydantic_dump(
    server, seeded_deal_id, seeded_user_id
) -> None:
    """Spec §8.2: an LLM that receives a numeric code retries at random; one that
    receives a diagnosis stops or corrects. `HoursArg` keeps the SDK from rejecting the
    value ahead of `_guard`. Checked the same way every other tool's identical guard
    test does (`test_mcp_tools.py`) -- a rejected tool call comes back as a
    `CallToolResult` with `is_error=True`, not as a raised Python exception from
    `call_tool` itself."""
    async with Client(server) as client:
        result = await client.call_tool(
            "log_time",
            {
                "deal_id": str(seeded_deal_id),
                "user_id": str(seeded_user_id),
                "data": "2026-03-10",
                "ore": "molte",
                "descrizione": "x",
            },
        )
    assert result.is_error
    assert "errors.pydantic.dev" not in result.content[0].text


async def test_the_deal_resource_carries_the_hours_block(
    server, seeded_deal_id, seeded_user_id
) -> None:
    async with Client(server) as client:
        await client.call_tool(
            "log_time",
            {
                "deal_id": str(seeded_deal_id),
                "user_id": str(seeded_user_id),
                "data": "2026-03-10",
                "ore": "8.00",
                "descrizione": "Sviluppo",
            },
        )
        rendered = (await client.read_resource(f"deal://{seeded_deal_id}")).contents[0].text
    assert "## Ore" in rendered
    assert "Ore consuntivate: 8.00" in rendered
    assert "Stato: **in corso**" in rendered


async def test_the_timer_is_the_agents_clock_too(
    mcp_session: Session, seeded_deal_id, seeded_user_id, tmp_path: Path
) -> None:
    """«Claude, ho iniziato a lavorare sul progetto Rossi» is `start_timer`, and «ho
    finito» is `stop_timer`: the entry it writes is a `log_time` entry in every respect,
    frozen rate and activity row included.

    Built with an actor that *has* an id, unlike the module's `ADMIN`: a timer is a
    person's clock, keyed on `actor.id`, and a PAT in production always resolves to its
    owner. `ADMIN` with `id=None` is what `start_timer` must refuse, and does."""
    owner = Actor(id=seeded_user_id, type="mcp", role="admin")
    server = build_server(
        lambda: mcp_session,
        lambda: owner,
        LocalFileStorage(tmp_path),
        Settings(_env_file=None),  # type: ignore[call-arg]
    )
    async with Client(server) as client:
        assert (await client.call_tool("get_running_timer", {})).structured_content in (
            None,
            {"result": None},
        )
        started = await client.call_tool(
            "start_timer", {"deal_id": str(seeded_deal_id), "descrizione": "Riunione"}
        )
        assert started.structured_content["deal_id"] == str(seeded_deal_id)

        stopped = await client.call_tool("stop_timer", {})
        entry = stopped.structured_content
        assert entry["descrizione"] == "Riunione"
        assert entry["ore"] == "0.01"

        timeline = await client.call_tool(
            "get_timeline", {"entity_type": "time_entry", "entity_id": entry["id"]}
        )
        assert timeline.structured_content["entries"][0]["actor_type"] == "mcp"
