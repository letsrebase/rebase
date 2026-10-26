"""`get_contract_register`: the billed days of a contract with the proof behind each, read
by an agent. A read only: the PDF and the CSV are for the page (`test_mcp_surface_coverage`
lists them as bytes the MCP never returns), and nothing here writes."""

from datetime import date
from decimal import Decimal

from mcp import Client
from sqlalchemy.orm import Session

from pigrocrm.core.contracts.models import Contract
from pigrocrm.core.customers.models import Customer
from pigrocrm.core.work_units.models import WorkUnit


def _contract(mcp_session: Session) -> Contract:
    customer = Customer(ragione_sociale="ACME Srl")
    mcp_session.add(customer)
    mcp_session.flush()
    contract = Contract(
        customer_id=customer.id,
        titolo="Consulenza",
        inizio=date(2026, 1, 1),
        tipo_rinnovo="nessuno",
        preavviso_disdetta_giorni=30,
        cadenza_fatturazione="mensile",
        politica_spese={"kind": "non_rimborsabile"},
    )
    mcp_session.add(contract)
    mcp_session.flush()
    return contract


async def test_the_register_lists_the_billed_days_and_their_total(server, mcp_session) -> None:
    contract = _contract(mcp_session)
    billed = WorkUnit(
        contract_id=contract.id,
        data=date(2026, 10, 7),
        quantita=Decimal("1.50"),
        descrizione="Giornata",
        stato="lavorato",
    )
    mcp_session.add(billed)
    mcp_session.flush()
    billed.stato = "fatturato"
    mcp_session.add(
        WorkUnit(
            contract_id=contract.id,
            data=date(2026, 10, 8),
            quantita=Decimal("1.00"),
            descrizione="Non ancora fatturata",
            stato="lavorato",
        )
    )
    mcp_session.commit()

    async with Client(server) as client:
        result = await client.call_tool(
            "get_contract_register",
            {"contract_id": str(contract.id), "da": "2026-10-01", "a": "2026-10-31"},
        )

    body = result.structured_content
    assert body["totale_quantita"] == "1.50"
    assert [v["data"] for v in body["voci"]] == ["2026-10-07"]
    assert body["senza_approvazione"] == 1


async def test_a_malformed_date_comes_back_as_guidance(server, mcp_session) -> None:
    contract = _contract(mcp_session)
    mcp_session.commit()

    async with Client(server) as client:
        result = await client.call_tool(
            "get_contract_register",
            {"contract_id": str(contract.id), "da": "ottobre", "a": "2026-10-31"},
        )

    assert result.is_error
    assert "errors.pydantic.dev" not in result.content[0].text
