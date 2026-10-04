"""The azienda over MCP (REB-624, spec 2026-10-03 §1.6, §7): `propose_azienda`, the
`nazione` and `azienda_id` of `create_customer`, and the `azienda_id` every list and
search tool takes. The second azienda is written by row, since no tool creates one
before milestone 5 (§9).
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from mcp import Client
from sqlalchemy.orm import Session

from pigrocrm.core.emitter.models import Azienda
from pigrocrm.core.emitter.repository import AziendaRepository
from pigrocrm.core.timetracking.models import Cost, CostCategory

# The tools whose input schema must offer `azienda_id`, as the card lists them, plus
# the calendar read and the proposal that joined with them.
_FILTERED = (
    "search_everything",
    "search_customers",
    "search_deals",
    "search_contracts",
    "list_documents",
    "list_invoices",
    "list_costs",
    "list_time_entries",
    "get_calendar_month",
)


def _payload(result: Any) -> Any:
    return result.structured_content or json.loads(result.content[0].text)


def _second_azienda(session: Session, nome: str = "rebase ltd", nazione: str = "GB") -> str:
    row = Azienda(nome=nome, ragione_sociale=nome.title(), nazione=nazione)
    session.add(row)
    session.flush()
    return str(row.id)


def _default_id(session: Session) -> str:
    default = AziendaRepository(session).default()
    assert default is not None
    return str(default.id)


async def test_every_list_and_search_tool_offers_the_azienda(server: Any) -> None:
    async with Client(server) as client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}
    for name in _FILTERED:
        assert "azienda_id" in tools[name].input_schema["properties"], name
        assert "azienda_id" in (tools[name].description or ""), name
    create = tools["create_customer"].input_schema["properties"]
    assert {"nazione", "azienda_id"} <= set(create)
    assert "nazione" in tools["propose_azienda"].input_schema["properties"]


async def test_propose_azienda_answers_the_rule_create_customer_applies(
    server: Any, mcp_session: Session
) -> None:
    default = _default_id(mcp_session)
    british = _second_azienda(mcp_session)
    async with Client(server) as client:
        for nazione, expected in (
            ("GB", british),
            ("DE", british),
            ("IT", default),
            (None, default),
        ):
            args = {"nazione": nazione} if nazione else {}
            proposed = _payload(await client.call_tool("propose_azienda", args))
            assert proposed["id"] == expected, nazione
            assert set(proposed) == {
                "id",
                "nome",
                "ragione_sociale",
                "partita_iva",
                "nazione",
                "predefinita",
                "attiva",
            }
        created = _payload(
            await client.call_tool(
                "create_customer", {"ragione_sociale": "Overseas Ltd", "nazione": "GB"}
            )
        )
        assert created["azienda_id"] == british
        assert created["nazione"] == "GB"
        named = _payload(
            await client.call_tool(
                "create_customer",
                {"ragione_sociale": "Oltre Ltd", "nazione": "GB", "azienda_id": default},
            )
        )
        assert named["azienda_id"] == default
        plain = _payload(await client.call_tool("create_customer", {"ragione_sociale": "Qui"}))
        assert (plain["azienda_id"], plain["nazione"]) == (default, "IT")


async def test_a_value_that_is_not_an_id_is_refused_in_words_and_never_the_default(
    server: Any,
) -> None:
    async with Client(server) as client:
        refused = await client.call_tool(
            "create_customer", {"ragione_sociale": "X", "azienda_id": "la predefinita"}
        )
        assert refused.is_error
        assert "non e' l'id di un'azienda" in refused.content[0].text
        unknown = await client.call_tool(
            "create_customer", {"ragione_sociale": "X", "azienda_id": str(uuid4())}
        )
        assert unknown.is_error
        listed = await client.call_tool("search_customers", {"azienda_id": "tutte"})
        assert listed.is_error
        assert "non e' l'id di un'azienda" in listed.content[0].text


async def test_the_lists_narrow_to_the_azienda_named(
    server: Any, mcp_session: Session, seeded_deal_id: UUID
) -> None:
    """Customers, deals and the search through the tools; costs with the shared one that
    no azienda's list shows. The other lists share the repositories the core suite
    narrows row by row, and the schema test above holds the parameter on each."""
    default = _default_id(mcp_session)
    second = _second_azienda(mcp_session, nome="rebase", nazione="IT")
    category = CostCategory(nome="Viaggi")
    mcp_session.add(category)
    mcp_session.flush()
    on_deal = Cost(
        deal_id=seeded_deal_id,
        azienda_id=UUID(default),
        category_id=category.id,
        data=date(2026, 3, 1),
        importo=Decimal("10.00"),
        descrizione="Treno",
    )
    shared = Cost(
        category_id=category.id,
        data=date(2026, 3, 2),
        importo=Decimal("5.00"),
        descrizione="Software",
    )
    mcp_session.add_all([on_deal, shared])
    mcp_session.flush()

    async with Client(server) as client:
        beta = _payload(
            await client.call_tool(
                "create_customer", {"ragione_sociale": "Beta S.r.l.", "azienda_id": second}
            )
        )
        stage_id = _payload(await client.call_tool("list_pipeline_stages", {}))["stages"][0]["id"]
        await client.call_tool(
            "create_deal",
            {"nome": "Beta deal", "customer_id": beta["id"], "pipeline_stage_id": stage_id},
        )

        customers = _payload(await client.call_tool("search_customers", {"azienda_id": second}))
        assert [c["ragione_sociale"] for c in customers["items"]] == ["Beta S.r.l."]
        everyone = _payload(await client.call_tool("search_customers", {}))
        assert len(everyone["items"]) == 2

        deals = _payload(await client.call_tool("search_deals", {"azienda_id": second}))
        assert [d["nome"] for d in deals["items"]] == ["Beta deal"]
        default_deals = _payload(await client.call_tool("search_deals", {"azienda_id": default}))
        assert [d["nome"] for d in default_deals["items"]] == ["Progetto MCP"]

        found = _payload(
            await client.call_tool("search_everything", {"termine": "Beta", "azienda_id": default})
        )
        assert all(group["hits"] == [] for group in found["gruppi"])
        found = _payload(
            await client.call_tool("search_everything", {"termine": "Beta", "azienda_id": second})
        )
        assert next(g for g in found["gruppi"] if g["entity"] == "deal")["totale"] == 1

        own = _payload(
            await client.call_tool(
                "create_cost",
                {
                    "category_id": str(category.id),
                    "data": "2026-03-03",
                    "importo": "7.00",
                    "descrizione": "Licenza",
                    "azienda_id": second,
                },
            )
        )
        assert own["azienda_id"] == second

        costs = _payload(await client.call_tool("list_costs", {"azienda_id": default}))
        assert [c["descrizione"] for c in costs["items"]] == ["Treno"]
        second_costs = _payload(await client.call_tool("list_costs", {"azienda_id": second}))
        assert [c["descrizione"] for c in second_costs["items"]] == ["Licenza"]
        all_costs = _payload(await client.call_tool("list_costs", {}))
        assert {c["descrizione"] for c in all_costs["items"]} == {"Treno", "Software", "Licenza"}
