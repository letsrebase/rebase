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


async def test_a_cap_on_the_customers_share_is_set_read_and_simulated(
    server, mcp_session: Session
) -> None:
    """REB-761: an agent sets `tetto_quota_cliente`, reads it back with `get_contract`,
    sees the concentration compared with it and hears from `simulate_ceiling` that an
    addition takes the customer past it."""
    from pigrocrm.core.actor import Actor
    from pigrocrm.core.fiscal.schemas import FiscalProfileUpsert
    from pigrocrm.core.fiscal.service import FiscalProfileService

    FiscalProfileService(mcp_session).upsert(
        FiscalProfileUpsert(pack_id="it-standard"), Actor(id=None, type="mcp", role="admin")
    )
    mcp_session.commit()
    contract_id = _contract_id(mcp_session)

    async with Client(server) as client:
        edited = await client.call_tool(
            "update_contract",
            {"contract_id": contract_id, "changes": {"tetto_quota_cliente": "50.00"}},
        )
        assert edited.structured_content["tetto_quota_cliente"] == "50.00"
        read = await client.call_tool("get_contract", {"contract_id": contract_id})
        assert read.structured_content["tetto_quota_cliente"] == "50.00"

        concentration = await client.call_tool(
            "get_contract_concentration", {"contract_id": contract_id, "as_of": "2026-06-01"}
        )
        assert concentration.structured_content["soglia"] == 0.5
        assert concentration.structured_content["soglia_da_contratto"] is True

        simulated = await client.call_tool(
            "simulate_ceiling",
            {
                "anno": 2026,
                "valore_preventivato": "1000.00",
                "contract_id": contract_id,
                "data_prevista": "2026-06-01",
            },
        )
        assert simulated.structured_content["soglie"] == []
        assert simulated.structured_content["tetto_contratto"]["rientra"] is False
