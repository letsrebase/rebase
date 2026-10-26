"""`update_contract`, `set_contract_state` and `update_rate_card`, the agent's side of
what the contract page does (REB-741, REB-742), plus `create_rate_card`'s
`chiudi_precedente`: real MCP round trips, so a refusal reaches the client as the
domain sentence and not as a raw dump."""

from datetime import date

from mcp import Client
from sqlalchemy.orm import Session

from pigrocrm.core.contracts.models import Contract
from pigrocrm.core.customers.models import Customer


def _contract_id(mcp_session: Session) -> str:
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
    mcp_session.commit()
    return str(contract.id)


async def test_a_contract_is_edited_and_activated(server, mcp_session) -> None:
    contract_id = _contract_id(mcp_session)

    async with Client(server) as client:
        edited = await client.call_tool(
            "update_contract",
            {"contract_id": contract_id, "changes": {"preavviso_disdetta_giorni": 60}},
        )
        assert edited.structured_content["preavviso_disdetta_giorni"] == 60

        active = await client.call_tool(
            "set_contract_state", {"contract_id": contract_id, "stato": "attivo"}
        )
        assert active.structured_content["stato"] == "attivo"

        repeat = await client.call_tool(
            "set_contract_state",
            {"contract_id": contract_id, "stato": "attivo"},
        )
        assert repeat.is_error
        assert "già" in repeat.content[0].text


async def test_a_rate_card_is_replaced_and_edited(server, mcp_session) -> None:
    contract_id = _contract_id(mcp_session)

    async with Client(server) as client:
        first = await client.call_tool(
            "create_rate_card",
            {
                "contract_id": contract_id,
                "valido_da": "2026-01-01",
                "tipo": "giornaliero",
                "importo": "400.00",
                "unita": "giorno",
            },
        )
        second = await client.call_tool(
            "create_rate_card",
            {
                "contract_id": contract_id,
                "valido_da": "2027-01-01",
                "tipo": "giornaliero",
                "importo": "450.00",
                "unita": "giorno",
                "chiudi_precedente": True,
            },
        )
        listed = await client.call_tool("list_rate_cards", {"contract_id": contract_id})
        assert [(c["id"], c["valido_a"]) for c in listed.structured_content["items"]] == [
            (first.structured_content["id"], "2026-12-31"),
            (second.structured_content["id"], None),
        ]

        edited = await client.call_tool(
            "update_rate_card",
            {
                "contract_id": contract_id,
                "rate_card_id": second.structured_content["id"],
                "changes": {"importo": "475.00"},
            },
        )
        assert edited.structured_content["importo"] == "475.00"
