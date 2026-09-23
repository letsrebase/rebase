"""REB-362's MCP surface: `propose_contract`/`propose_day` write a `proposals` row,
never a `Contract`/`WorkUnit` directly; `accept_proposal`/`reject_proposal` are the
confirm half. These tests exercise the real guarded call path (`Client(server)`), not
`ProposalService` directly -- `packages/core/tests/test_proposals_service.py` already
owns the business-rule edge cases; this file owns "the tool is registered, typed
correctly, and actually reaches the service."
"""

import json
from datetime import date
from typing import Any
from uuid import UUID

import pytest
from mcp import Client
from sqlalchemy.orm import Session

from pigrocrm.core.contracts.models import Contract
from pigrocrm.core.documents.models import Document
from pigrocrm.core.work_units.models import Approval, WorkUnit


def _payload(result: Any) -> dict[str, Any]:
    return result.structured_content or json.loads(result.content[0].text)


@pytest.fixture
def seeded_contract_id(mcp_session: Session, seeded_customer_id: str) -> str:
    contract = Contract(
        customer_id=UUID(seeded_customer_id),
        titolo="Consulenza MCP",
        inizio=date(2026, 1, 1),
        tipo_rinnovo="nessuno",
        preavviso_disdetta_giorni=30,
        cadenza_fatturazione="mensile",
        politica_spese={"kind": "non_rimborsabile"},
    )
    mcp_session.add(contract)
    mcp_session.commit()
    return str(contract.id)


@pytest.fixture
def seeded_contract_document_id(mcp_session: Session, seeded_contract_id: str) -> str:
    document = Document(
        contract_id=UUID(seeded_contract_id), tipo="contratto", titolo="Contratto firmato"
    )
    mcp_session.add(document)
    mcp_session.commit()
    return str(document.id)


@pytest.fixture
def seeded_customer_document_id(mcp_session: Session, seeded_customer_id: str) -> str:
    document = Document(
        customer_id=UUID(seeded_customer_id), tipo="contratto", titolo="Bozza di contratto"
    )
    mcp_session.add(document)
    mcp_session.commit()
    return str(document.id)


async def test_the_six_proposal_tools_are_registered(server) -> None:
    async with Client(server) as client:
        names = {tool.name for tool in (await client.list_tools()).tools}

    assert {
        "propose_contract",
        "propose_day",
        "get_proposal",
        "list_proposals",
        "accept_proposal",
        "reject_proposal",
    } <= names


async def test_propose_day_writes_only_a_proposal_never_a_work_unit(
    server, mcp_session: Session, seeded_contract_id: str, seeded_contract_document_id: str
) -> None:
    async with Client(server) as client:
        result = await client.call_tool(
            "propose_day",
            {
                "document_id": seeded_contract_document_id,
                "contract_id": seeded_contract_id,
                "estratto": "Confermiamo la giornata del 10 marzo.",
                "confidenza": 0.9,
                "data": "2026-03-10",
                "quantita": 1,
                "descrizione": "Giornata di consulenza",
                "canale": "email",
                "mittente": "cliente@example.it",
                "ricevuto_il": "2026-03-01T09:00:00Z",
            },
        )

    assert not result.is_error, result.content[0].text
    proposal = _payload(result)
    assert proposal["stato"] == "in_attesa"
    assert proposal["campi_accettati"] is None
    assert mcp_session.query(WorkUnit).count() == 0
    assert mcp_session.query(Approval).count() == 0


async def test_propose_day_then_accept_creates_the_approval_and_the_approved_work_unit(
    server, mcp_session: Session, seeded_contract_id: str, seeded_contract_document_id: str
) -> None:
    async with Client(server) as client:
        proposed = await client.call_tool(
            "propose_day",
            {
                "document_id": seeded_contract_document_id,
                "contract_id": seeded_contract_id,
                "estratto": "Confermiamo la giornata del 10 marzo.",
                "confidenza": 0.9,
                "data": "2026-03-10",
                "quantita": 1,
                "descrizione": "Giornata di consulenza",
                "canale": "email",
                "mittente": "cliente@example.it",
                "ricevuto_il": "2026-03-01T09:00:00Z",
            },
        )
        proposal_id = _payload(proposed)["id"]

        accepted = await client.call_tool(
            "accept_proposal", {"proposal_id": proposal_id, "deciso_da": "Lorenzo Fiore"}
        )

    assert not accepted.is_error, accepted.content[0].text
    result = _payload(accepted)
    assert result["stato"] == "accettata"

    work_unit = mcp_session.get(WorkUnit, UUID(result["id_risultato"]))
    assert work_unit is not None
    # Never left at 'proposto' -- accept_proposal's own single-transaction promise.
    assert work_unit.stato == "approvato"
    approval = mcp_session.get(Approval, work_unit.approval_id)
    assert approval is not None
    assert approval.origine == {"kind": "agente", "proposal_id": proposal_id}


async def test_propose_contract_then_accept_creates_the_contract_and_repoints_the_document(
    server, mcp_session: Session, seeded_customer_id: str, seeded_customer_document_id: str
) -> None:
    async with Client(server) as client:
        proposed = await client.call_tool(
            "propose_contract",
            {
                "document_id": seeded_customer_document_id,
                "estratto": "Il presente contratto ha durata annuale.",
                "confidenza": 0.85,
                "customer_id": seeded_customer_id,
                "titolo": "Consulenza annuale MCP",
                "inizio": "2026-01-01",
                "tipo_rinnovo": "nessuno",
                "preavviso_disdetta_giorni": 30,
                "cadenza_fatturazione": "mensile",
                "politica_spese": {"kind": "non_rimborsabile"},
                "rate_card_valido_da": "2026-01-01",
                "rate_card_tipo": "ricorrente_fisso",
                "rate_card_importo": "1000.00",
                "rate_card_unita": "mese",
                "rate_card_periodo_erogazione": "mensile",
            },
        )
        assert not proposed.is_error, proposed.content[0].text
        proposal_id = _payload(proposed)["id"]

        accepted = await client.call_tool(
            "accept_proposal", {"proposal_id": proposal_id, "deciso_da": "Lorenzo Fiore"}
        )

    assert not accepted.is_error, accepted.content[0].text
    result = _payload(accepted)
    contract = mcp_session.get(Contract, UUID(result["id_risultato"]))
    assert contract is not None
    assert contract.titolo == "Consulenza annuale MCP"

    document = mcp_session.get(Document, UUID(seeded_customer_document_id))
    mcp_session.refresh(document)
    assert document.contract_id == contract.id
    assert document.customer_id is None


async def test_reject_proposal_creates_nothing(
    server, mcp_session: Session, seeded_contract_id: str, seeded_contract_document_id: str
) -> None:
    async with Client(server) as client:
        proposed = await client.call_tool(
            "propose_day",
            {
                "document_id": seeded_contract_document_id,
                "contract_id": seeded_contract_id,
                "estratto": "Confermiamo la giornata del 12 marzo.",
                "confidenza": 0.9,
                "data": "2026-03-12",
                "quantita": 1,
                "descrizione": "Giornata di consulenza",
                "canale": "email",
                "mittente": "cliente@example.it",
                "ricevuto_il": "2026-03-01T09:00:00Z",
            },
        )
        proposal_id = _payload(proposed)["id"]

        rejected = await client.call_tool(
            "reject_proposal",
            {"proposal_id": proposal_id, "deciso_da": "Lorenzo Fiore", "motivo": "date errate"},
        )

    assert not rejected.is_error, rejected.content[0].text
    result = _payload(rejected)
    assert result["stato"] == "rifiutata"
    assert result["id_risultato"] is None
    assert mcp_session.query(WorkUnit).count() == 0
    assert mcp_session.query(Approval).count() == 0


async def test_list_proposals_filters_by_stato(
    server, seeded_contract_id: str, seeded_contract_document_id: str
) -> None:
    async with Client(server) as client:
        first = await client.call_tool(
            "propose_day",
            {
                "document_id": seeded_contract_document_id,
                "contract_id": seeded_contract_id,
                "estratto": "prima proposta",
                "confidenza": 0.9,
                "data": "2026-04-01",
                "quantita": 1,
                "descrizione": "Giornata",
                "canale": "email",
                "mittente": "cliente@example.it",
                "ricevuto_il": "2026-03-01T09:00:00Z",
            },
        )
        second = await client.call_tool(
            "propose_day",
            {
                "document_id": seeded_contract_document_id,
                "contract_id": seeded_contract_id,
                "estratto": "seconda proposta",
                "confidenza": 0.9,
                "data": "2026-04-02",
                "quantita": 1,
                "descrizione": "Giornata",
                "canale": "email",
                "mittente": "cliente@example.it",
                "ricevuto_il": "2026-03-01T09:00:00Z",
            },
        )
        second_id = _payload(second)["id"]
        await client.call_tool(
            "reject_proposal", {"proposal_id": second_id, "deciso_da": "Lorenzo Fiore"}
        )

        listed = await client.call_tool("list_proposals", {"stato": "in_attesa"})

    ids = {item["id"] for item in _payload(listed)["items"]}
    assert _payload(first)["id"] in ids
    assert second_id not in ids


async def test_a_wrong_typed_confidenza_produces_guidance_not_a_pydantic_dump(
    server, seeded_contract_id: str, seeded_contract_document_id: str
) -> None:
    """R2: the SDK validates some arguments before the guard runs. `Confidenza`'s
    `float | str` runtime type is what keeps this inside the guard."""
    async with Client(server) as client:
        result = await client.call_tool(
            "propose_day",
            {
                "document_id": seeded_contract_document_id,
                "contract_id": seeded_contract_id,
                "estratto": "qualcosa",
                "confidenza": "altissima",
                "data": "2026-04-05",
                "quantita": 1,
                "descrizione": "Giornata",
                "canale": "email",
                "mittente": "cliente@example.it",
                "ricevuto_il": "2026-03-01T09:00:00Z",
            },
        )

    assert result.is_error
    assert "errors.pydantic.dev" not in result.content[0].text
