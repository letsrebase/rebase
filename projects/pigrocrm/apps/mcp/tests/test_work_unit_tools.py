"""The day ledger on the MCP surface: two reads, and nothing that writes.

An agent proposes a day (`propose_day`) and a person confirms it; it never records or
moves one. What it can do is read, which is what these two tools are, and what is
asserted beyond their shape is the other half: no tool of this module's name writes, and
the day recording refuses an agent credential (`AGENT_FORBIDDEN_ACTIONS`; the other ledger
writes are refused in `test_work_units_service.py`, `test_approval_record.py` and
`test_approval_request.py`, and over REST in `test_work_units_api.py`).
"""

import json
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest
from mcp import Client
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.contracts.models import Contract
from pigrocrm.core.errors import AgentForbidden
from pigrocrm.core.work_units.schemas import WorkUnitCreate
from pigrocrm.core.work_units.service import WorkUnitService

HUMAN = Actor(id=None, type="system", role="admin")


def _payload(result: Any) -> Any:
    return result.structured_content or json.loads(result.content[0].text)


@pytest.fixture
def contract_id(mcp_session: Session, seeded_customer_id: str) -> UUID:
    contract = Contract(
        customer_id=UUID(seeded_customer_id),
        titolo="Consulenza MCP",
        inizio=date(2026, 1, 1),
        tipo_rinnovo="nessuno",
        preavviso_disdetta_giorni=30,
        cadenza_fatturazione="mensile",
        politica_spese={"kind": "non_rimborsabile"},
        requires_prior_approval=True,
    )
    mcp_session.add(contract)
    mcp_session.commit()
    return contract.id


def _record(session: Session, contract: UUID, giorno: date) -> UUID:
    return (
        WorkUnitService(session)
        .create(
            WorkUnitCreate(
                contract_id=contract,
                data=giorno,
                quantita=Decimal("0.50"),
                descrizione="Revisione",
                stato="lavorato",
            ),
            HUMAN,
        )
        .id
    )


async def test_the_two_reads_are_registered_and_no_write_is(server) -> None:
    async with Client(server) as client:
        names = {tool.name for tool in (await client.list_tools()).tools}

    assert {"get_work_unit", "list_work_units"} <= names
    # The four ledger writes have no tool: not under these names, not under any other.
    assert not {
        name
        for name in names
        if "work_unit" in name and name not in {"get_work_unit", "list_work_units"}
    }
    # And no tool carries the name of any operation an agent credential is refused: the ban
    # list is keyed on the operation, and a tool of the same name would make it a lie.
    from test_mcp_invoice_ban import REST_ONLY_FORBIDDEN

    assert not names & REST_ONLY_FORBIDDEN


async def test_an_agent_reads_a_contracts_days_and_one_days_history(
    server, mcp_session: Session, contract_id: UUID
) -> None:
    day_id = _record(mcp_session, contract_id, date(2026, 3, 2))
    async with Client(server) as client:
        listed = _payload(
            await client.call_tool("list_work_units", {"contract_id": str(contract_id)})
        )
        one = _payload(await client.call_tool("get_work_unit", {"work_unit_id": str(day_id)}))

    assert [(row["id"], row["stato"], row["contract_titolo"]) for row in listed["items"]] == [
        (str(day_id), "lavorato_senza_approvazione", "Consulenza MCP")
    ]
    assert one["requires_prior_approval"] is True
    assert [t["stato_nuovo"] for t in one["transizioni"]] == ["lavorato_senza_approvazione"]


async def test_a_list_with_no_contract_and_no_date_is_refused_in_words(server) -> None:
    async with Client(server) as client:
        result = await client.call_tool("list_work_units", {})

    assert result.is_error
    assert "contratto" in result.content[0].text


def test_the_ledger_writes_refuse_an_agent_credential(
    mcp_session: Session, contract_id: UUID
) -> None:
    agent = Actor(id=None, type="mcp", role="admin")
    with pytest.raises(AgentForbidden):
        WorkUnitService(mcp_session).create(
            WorkUnitCreate(
                contract_id=contract_id,
                data=date(2026, 3, 3),
                quantita=Decimal("1.00"),
                descrizione="Giornata",
                stato="lavorato",
            ),
            agent,
        )
