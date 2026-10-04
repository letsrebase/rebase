"""The slice 10 surface: commitments an agent can write, and the month it can read.

The reason these tools exist is that «what falls due this week» is exactly the question
somebody asks an agent -- and the reason they are on the *default* surface is that none
of them consumes a number, touches the register or rewrites a rate. What is asserted
here is that, plus the two properties the tool descriptions promise: closing is two
different operations, and an undated commitment never lands in a day.
"""

import calendar
import json
from typing import Any
from uuid import uuid4

from mcp import Client

from pigrocrm.core.clock import oggi_in_italia

# Derived, never a literal: a suite with `2026-09` in it is a suite with an expiry date
# on it (see `packages/core/tests/periodo_fiscale.py`).
OGGI = oggi_in_italia()
MESE = OGGI.strftime("%Y-%m")
PRIMO = OGGI.replace(day=1)
ULTIMO = OGGI.replace(day=calendar.monthrange(OGGI.year, OGGI.month)[1])


def _payload(result: Any) -> Any:
    return result.structured_content or json.loads(result.content[0].text)


async def test_the_slice_10_tools_are_registered(server) -> None:
    async with Client(server) as client:
        names = {tool.name for tool in (await client.list_tools()).tools}

    assert {
        "create_attivita",
        "update_attivita",
        "get_attivita",
        "complete_attivita",
        "cancel_attivita",
        "reopen_attivita",
        "archive_attivita",
        "restore_attivita",
        "list_attivita",
        "get_calendar_month",
    } <= names


async def test_a_commitment_needs_no_date(server) -> None:
    """The commonest to-do, and the tool description says so: inventing a date fills
    every «what is due» list with noise."""
    async with Client(server) as client:
        result = await client.call_tool("create_attivita", {"titolo": "Chiedere il codice SDI"})

    assert not result.is_error, result.content[0].text
    created = _payload(result)
    assert created["scadenza"] is None
    assert created["stato"] == "aperta"
    assert created["origine"] == "manuale"


async def test_an_undated_commitment_is_outside_the_grid(server) -> None:
    async with Client(server) as client:
        await client.call_tool("create_attivita", {"titolo": "Senza data"})
        result = await client.call_tool("get_calendar_month", {"mese": MESE})

    month = _payload(result)
    assert month["giorni"] == []
    assert [item["titolo"] for item in month["attivita_senza_scadenza"]] == ["Senza data"]


async def test_a_dated_commitment_lands_on_its_day(server) -> None:
    async with Client(server) as client:
        await client.call_tool(
            "create_attivita", {"titolo": "Sollecitare Rossi", "scadenza": PRIMO.isoformat()}
        )
        result = await client.call_tool("get_calendar_month", {"mese": MESE})

    month = _payload(result)
    assert [day["giorno"] for day in month["giorni"]] == [PRIMO.isoformat()]
    assert [item["titolo"] for item in month["giorni"][0]["attivita"]] == ["Sollecitare Rossi"]


async def test_completing_and_cancelling_are_two_different_closures(server) -> None:
    """«Done» carries the day it was done; «no longer needed» carries neither a date nor
    a deletion. An agent that could only delete would destroy the answer to *why*."""
    async with Client(server) as client:
        first = _payload(await client.call_tool("create_attivita", {"titolo": "Da completare"}))
        done = _payload(await client.call_tool("complete_attivita", {"attivita_id": first["id"]}))

        second = _payload(await client.call_tool("create_attivita", {"titolo": "Da annullare"}))
        cancelled = _payload(
            await client.call_tool("cancel_attivita", {"attivita_id": second["id"]})
        )

    assert (done["stato"], done["completata_il"]) == ("completata", OGGI.isoformat())
    assert (cancelled["stato"], cancelled["completata_il"]) == ("annullata", None)


async def test_completing_a_cancelled_commitment_is_guidance_not_a_dump(server) -> None:
    async with Client(server) as client:
        created = _payload(await client.call_tool("create_attivita", {"titolo": "Annullata"}))
        await client.call_tool("cancel_attivita", {"attivita_id": created["id"]})
        result = await client.call_tool("complete_attivita", {"attivita_id": created["id"]})

    assert result.is_error
    message = result.content[0].text
    assert "riaprila" in message
    assert "errors.pydantic.dev" not in message


async def test_two_references_are_refused_with_a_sentence(server, seeded_customer_id: str) -> None:
    """At most one of customer, person, deal and invoice -- an activity that appeared on
    two records would be closed on one and left open on the other."""
    async with Client(server) as client:
        result = await client.call_tool(
            "create_attivita",
            {
                "titolo": "Due riferimenti",
                "customer_id": seeded_customer_id,
                "person_id": str(uuid4()),
            },
        )

    assert result.is_error
    assert "un solo" in result.content[0].text


async def test_the_date_filter_never_includes_the_undated_ones(server) -> None:
    """`NULL <= date` is NULL, which is not true -- and a filter that quietly included
    them would answer «due by Friday» with things that are not due at all."""
    async with Client(server) as client:
        await client.call_tool("create_attivita", {"titolo": "Senza data"})
        await client.call_tool(
            "create_attivita", {"titolo": "Con data", "scadenza": PRIMO.isoformat()}
        )
        result = await client.call_tool("list_attivita", {"scade_entro": ULTIMO.isoformat()})

    assert [item["titolo"] for item in _payload(result)["items"]] == ["Con data"]


async def test_the_state_cannot_be_written_through_update(server) -> None:
    """`update_attivita` says so, and the schema enforces it: «fatta» has a side effect
    -- the day -- that a caller must not be able to supply."""
    async with Client(server) as client:
        created = _payload(await client.call_tool("create_attivita", {"titolo": "Aperta"}))
        result = await client.call_tool(
            "update_attivita",
            {"attivita_id": created["id"], "changes": {"stato": "completata"}},
        )

    assert result.is_error
    # `extra="forbid"`: a key the schema does not declare is refused rather than ignored,
    # so nobody can believe they closed something.
    assert "errors.pydantic.dev" not in result.content[0].text


async def test_a_month_that_is_not_one_is_refused(server) -> None:
    async with Client(server) as client:
        result = await client.call_tool("get_calendar_month", {"mese": "2026-13"})

    assert result.is_error
    assert "mese" in result.content[0].text


async def test_the_month_is_read_and_never_written(server) -> None:
    """`get_calendar_month` has no way to log an hour: the tool that writes them is
    `log_time`, and the calendar reading them is a read."""
    async with Client(server) as client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}

    schema = tools["get_calendar_month"].input_schema
    # `azienda_id` since REB-624 narrows the hours and the invoices; still no way to write.
    assert set(schema.get("properties", {})) == {"mese", "tutti", "azienda_id"}
