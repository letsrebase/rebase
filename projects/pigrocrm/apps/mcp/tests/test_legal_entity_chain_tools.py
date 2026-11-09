"""The legal entity over MCP (REB-624, spec 2026-10-03 §1.6, §7): `suggest_legal_entity`, the
`country` and `legal_entity_id` of `create_organization`, and the `legal_entity_id` every list and
search tool takes. The second legal entity is written by row, since no tool creates one
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

from pigrocrm.core.legal_entities.models import LegalEntity
from pigrocrm.core.legal_entities.repository import LegalEntityRepository
from pigrocrm.core.timetracking.models import Cost, CostCategory

# The tools whose input schema must offer `legal_entity_id`, as the card lists them, plus
# the calendar read and the proposal that joined with them.
_FILTERED = (
    "search_everything",
    "search_organizations",
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


def _second_legal_entity(session: Session, name: str = "rebase ltd", country: str = "GB") -> str:
    row = LegalEntity(name=name, legal_name=name.title(), country=country)
    session.add(row)
    session.flush()
    return str(row.id)


def _default_id(session: Session) -> str:
    default = LegalEntityRepository(session).default()
    assert default is not None
    return str(default.id)


async def test_every_list_and_search_tool_offers_the_legal_entity(server: Any) -> None:
    async with Client(server) as client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}
    for name in _FILTERED:
        assert "legal_entity_id" in tools[name].input_schema["properties"], name
        assert "legal_entity_id" in (tools[name].description or ""), name
    create = tools["create_organization"].input_schema["properties"]
    assert {"country", "legal_entity_id"} <= set(create)
    assert "country" in tools["suggest_legal_entity"].input_schema["properties"]


async def test_suggest_legal_entity_answers_the_rule_create_customer_applies(
    server: Any, mcp_session: Session
) -> None:
    default = _default_id(mcp_session)
    british = _second_legal_entity(mcp_session)
    async with Client(server) as client:
        for country, expected in (
            ("GB", british),
            ("DE", british),
            ("IT", default),
            (None, default),
        ):
            args = {"country": country} if country else {}
            proposed = _payload(await client.call_tool("suggest_legal_entity", args))
            assert proposed["id"] == expected, country
            assert set(proposed) == {
                "id",
                "name",
                "legal_name",
                "vat_number",
                "country",
                "is_default",
                "active",
            }
        created = _payload(
            await client.call_tool(
                "create_organization", {"legal_name": "Overseas Ltd", "country": "GB"}
            )
        )
        assert created["legal_entity_id"] == british
        assert created["country"] == "GB"
        named = _payload(
            await client.call_tool(
                "create_organization",
                {"legal_name": "Oltre Ltd", "country": "GB", "legal_entity_id": default},
            )
        )
        assert named["legal_entity_id"] == default
        plain = _payload(await client.call_tool("create_organization", {"legal_name": "Qui"}))
        assert (plain["legal_entity_id"], plain["country"]) == (default, "IT")


async def test_a_value_that_is_not_an_id_is_refused_in_words_and_never_the_default(
    server: Any,
) -> None:
    async with Client(server) as client:
        refused = await client.call_tool(
            "create_organization", {"legal_name": "X", "legal_entity_id": "la is_default"}
        )
        assert refused.is_error
        assert "is not a company id" in refused.content[0].text
        unknown = await client.call_tool(
            "create_organization", {"legal_name": "X", "legal_entity_id": str(uuid4())}
        )
        assert unknown.is_error
        listed = await client.call_tool("search_organizations", {"legal_entity_id": "tutte"})
        assert listed.is_error
        assert "is not a company id" in listed.content[0].text


async def test_the_lists_narrow_to_the_legal_entity_named(
    server: Any, mcp_session: Session, seeded_deal_id: UUID
) -> None:
    """Organizations, deals and the search through the tools; costs with the shared one that
    no azienda's list shows. The other lists share the repositories the core suite
    narrows row by row, and the schema test above holds the parameter on each."""
    default = _default_id(mcp_session)
    second = _second_legal_entity(mcp_session, name="rebase", country="IT")
    category = CostCategory(name="Viaggi")
    mcp_session.add(category)
    mcp_session.flush()
    on_deal = Cost(
        deal_id=seeded_deal_id,
        legal_entity_id=UUID(default),
        category_id=category.id,
        date=date(2026, 3, 1),
        amount=Decimal("10.00"),
        description="Treno",
    )
    shared = Cost(
        category_id=category.id,
        date=date(2026, 3, 2),
        amount=Decimal("5.00"),
        description="Software",
    )
    mcp_session.add_all([on_deal, shared])
    mcp_session.flush()

    async with Client(server) as client:
        beta = _payload(
            await client.call_tool(
                "create_organization",
                {"legal_name": "Zeta Quattro S.r.l.", "legal_entity_id": second},
            )
        )
        # `create_deal` has no stage parameter: the deal starts in the pipeline's first. The
        # test used to pass `pipeline_stage_id`, which the SDK dropped without a word.
        created = await client.call_tool(
            "create_deal", {"name": "Zeta Quattro deal", "customer_id": beta["id"]}
        )
        assert not created.is_error, created.content[0].text

        organizations = _payload(
            await client.call_tool("search_organizations", {"legal_entity_id": second})
        )
        assert [c["legal_name"] for c in organizations["items"]] == ["Zeta Quattro S.r.l."]
        # Superset, not equality: a committed world of another file may have left rows
        # on this worker's database, all of them on the default azienda.
        everyone = _payload(await client.call_tool("search_organizations", {}))
        assert {"ACME S.r.l.", "Zeta Quattro S.r.l."} <= {
            c["legal_name"] for c in everyone["items"]
        }

        deals = _payload(await client.call_tool("search_deals", {"legal_entity_id": second}))
        assert [d["name"] for d in deals["items"]] == ["Zeta Quattro deal"]
        default_deals = _payload(
            await client.call_tool("search_deals", {"legal_entity_id": default})
        )
        default_names = {d["name"] for d in default_deals["items"]}
        assert "Progetto MCP" in default_names and "Zeta Quattro deal" not in default_names

        found = _payload(
            await client.call_tool(
                "search_everything", {"term": "Zeta Quattro", "legal_entity_id": default}
            )
        )
        assert all(group["hits"] == [] for group in found["groups"])
        found = _payload(
            await client.call_tool(
                "search_everything", {"term": "Zeta Quattro", "legal_entity_id": second}
            )
        )
        assert next(g for g in found["groups"] if g["entity"] == "deal")["total"] == 1

        own = _payload(
            await client.call_tool(
                "create_cost",
                {
                    "category_id": str(category.id),
                    "date": "2026-03-03",
                    "amount": "7.00",
                    "description": "Licenza",
                    "legal_entity_id": second,
                },
            )
        )
        assert own["legal_entity_id"] == second

        costs = _payload(await client.call_tool("list_costs", {"legal_entity_id": default}))
        default_costs = {c["description"] for c in costs["items"]}
        assert "Treno" in default_costs and not {"Software", "Licenza"} & default_costs
        second_costs = _payload(await client.call_tool("list_costs", {"legal_entity_id": second}))
        assert [c["description"] for c in second_costs["items"]] == ["Licenza"]
        all_costs = _payload(await client.call_tool("list_costs", {}))
        assert {"Treno", "Software", "Licenza"} <= {c["description"] for c in all_costs["items"]}
