"""The paged, sorted and filtered reads of organizations, people, deals and documents."""

import json
from uuid import UUID, uuid4

from mcp import Client
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.fields.schemas import FieldDefinitionCreate
from pigrocrm.core.fields.service import FieldDefinitionService
from pigrocrm.core.pipeline.service import PipelineService

SETUP = Actor(id=None, type="system", role="admin")  # setup is not an agent's (agent_actions.py)


def _payload(result) -> dict:
    return result.structured_content or json.loads(result.content[0].text)


async def test_search_organizations_finds_by_free_text(server, mcp_session: Session) -> None:
    async with Client(server) as client:
        await client.call_tool("create_organization", {"legal_name": "ACME Srl"})
        await client.call_tool("create_organization", {"legal_name": "Beta Spa"})
        found = _payload(await client.call_tool("search_organizations", {"search": "acme"}))

    assert [c["legal_name"] for c in found["items"]] == ["ACME Srl"]


# --- Final review item 10 (MINOR): *ListQuery.custom was implemented, GIN- ------
# --- indexed and service-tested, but no MCP tool passed it. --------------------


async def test_search_organizations_filters_by_a_custom_field(server, mcp_session: Session) -> None:
    FieldDefinitionService(mcp_session).create(
        FieldDefinitionCreate(
            entity_type="organization", key="settore", label="Settore", field_type="text"
        ),
        SETUP,
    )
    async with Client(server) as client:
        await client.call_tool(
            "create_organization", {"legal_name": "A", "custom_fields": {"settore": "IT"}}
        )
        await client.call_tool(
            "create_organization", {"legal_name": "B", "custom_fields": {"settore": "Retail"}}
        )
        found = _payload(
            await client.call_tool("search_organizations", {"custom": {"settore": "IT"}})
        )

    assert [c["legal_name"] for c in found["items"]] == ["A"]


async def test_search_people_filters_by_a_custom_field(server, mcp_session: Session) -> None:
    FieldDefinitionService(mcp_session).create(
        FieldDefinitionCreate(
            entity_type="person", key="seniority", label="Seniority", field_type="text"
        ),
        SETUP,
    )
    async with Client(server) as client:
        await client.call_tool(
            "create_person", {"first_name": "A", "custom_fields": {"seniority": "senior"}}
        )
        await client.call_tool(
            "create_person", {"first_name": "B", "custom_fields": {"seniority": "junior"}}
        )
        found = _payload(
            await client.call_tool("search_people", {"custom": {"seniority": "senior"}})
        )

    assert [p["first_name"] for p in found["items"]] == ["A"]


async def test_search_deals_filters_by_a_custom_field(server, mcp_session: Session) -> None:
    PipelineService(mcp_session).seed_defaults(SETUP)
    FieldDefinitionService(mcp_session).create(
        FieldDefinitionCreate(entity_type="deal", key="source", label="Fonte", field_type="text"),
        SETUP,
    )
    async with Client(server) as client:
        customer = _payload(await client.call_tool("create_organization", {"legal_name": "ACME"}))
        await client.call_tool(
            "create_deal",
            {"name": "A", "customer_id": customer["id"], "custom_fields": {"source": "referral"}},
        )
        await client.call_tool(
            "create_deal",
            {"name": "B", "customer_id": customer["id"], "custom_fields": {"source": "outbound"}},
        )
        found = _payload(await client.call_tool("search_deals", {"custom": {"source": "referral"}}))

    assert [d["name"] for d in found["items"]] == ["A"]


# --- Final review item 7 (IMPORTANT): search tools returned next_cursor but had --
# --- no way to pass it back in -- an agent could never reach page 2. -----------


async def test_search_organizations_can_walk_a_second_page_via_cursor(
    server, mcp_session: Session
) -> None:
    """Before this fix, `search_organizations` took no `cursor` parameter at all: the
    SDK silently drops an argument a tool's signature does not declare, so passing
    `next_cursor` back returned page 1 again, with `is_error=False` -- an agent
    could never reach a record beyond the first page, and nothing in the response
    said so."""
    async with Client(server) as client:
        for index in range(3):
            await client.call_tool("create_organization", {"legal_name": f"Cliente {index:02d}"})

        first = _payload(await client.call_tool("search_organizations", {"limit": 2}))
        assert len(first["items"]) == 2
        assert first["next_cursor"] is not None

        second = _payload(
            await client.call_tool(
                "search_organizations", {"limit": 2, "cursor": first["next_cursor"]}
            )
        )

    first_ids = {c["id"] for c in first["items"]}
    second_ids = {c["id"] for c in second["items"]}
    assert second_ids, "the second page must not be empty"
    assert first_ids.isdisjoint(second_ids), "the second page must reach records page 1 lacked"


async def test_search_people_can_walk_a_second_page_via_cursor(server) -> None:
    async with Client(server) as client:
        for index in range(3):
            await client.call_tool("create_person", {"first_name": f"Persona {index:02d}"})

        first = _payload(await client.call_tool("search_people", {"limit": 2}))
        second = _payload(
            await client.call_tool("search_people", {"limit": 2, "cursor": first["next_cursor"]})
        )

    first_ids = {p["id"] for p in first["items"]}
    second_ids = {p["id"] for p in second["items"]}
    assert second_ids
    assert first_ids.isdisjoint(second_ids)


async def test_search_deals_can_walk_a_second_page_via_cursor(server, mcp_session: Session) -> None:
    PipelineService(mcp_session).seed_defaults(SETUP)
    async with Client(server) as client:
        customer = _payload(await client.call_tool("create_organization", {"legal_name": "ACME"}))
        for index in range(3):
            await client.call_tool(
                "create_deal", {"name": f"Deal {index:02d}", "customer_id": customer["id"]}
            )

        first = _payload(await client.call_tool("search_deals", {"limit": 2}))
        second = _payload(
            await client.call_tool("search_deals", {"limit": 2, "cursor": first["next_cursor"]})
        )

    first_ids = {d["id"] for d in first["items"]}
    second_ids = {d["id"] for d in second["items"]}
    assert second_ids
    assert first_ids.isdisjoint(second_ids)


async def test_search_organizations_with_a_malformed_cursor_produces_guidance_not_a_crash(
    server,
) -> None:
    async with Client(server) as client:
        result = await client.call_tool("search_organizations", {"cursor": "not-a-uuid"})

    assert result.is_error
    message = result.content[0].text
    assert "errors.pydantic.dev" not in message
    # An opaque string decoded by `db/sort.py`, which raises a domain `ValidationFailed`
    # naming `cursor`: the assertion is on the field, not on the sentence around it.
    assert "cursor" in message.lower()


async def test_search_organizations_accepts_sort_and_dir(server) -> None:
    """The tool's JSON Schema gained two parameters, and the SDK silently drops an
    argument a signature does not declare -- which is exactly how `cursor` shipped
    broken once already (see the cursor test above). Asserting the *order* rather than
    the status is what makes this fail in that case: a dropped `sort` returns 200 with
    the rows in creation order.

    Scoped by a per-run token rather than asserting on the whole table: at least one
    test in this suite commits a customer outside the rolled-back transaction, so a bare
    `search_organizations` here sees rows it did not create. That made this test pass alone
    and fail in the suite, which is the worst of both.
    """
    token = f"zz{uuid4().hex[:8]}"
    async with Client(server) as client:
        for name in ("Gamma", "Alfa", "Beta"):
            await client.call_tool("create_organization", {"legal_name": f"{token} {name}"})

        ascending = _payload(
            await client.call_tool(
                "search_organizations",
                {"search": token, "sort": "legal_name", "dir": "asc", "limit": 10},
            )
        )
        descending = _payload(
            await client.call_tool(
                "search_organizations",
                {"search": token, "sort": "legal_name", "dir": "desc", "limit": 10},
            )
        )

    assert [c["legal_name"] for c in ascending["items"]] == [
        f"{token} Alfa",
        f"{token} Beta",
        f"{token} Gamma",
    ]
    assert [c["legal_name"] for c in descending["items"]] == [
        f"{token} Gamma",
        f"{token} Beta",
        f"{token} Alfa",
    ]


async def test_search_organizations_with_an_unknown_sort_key_produces_guidance(server) -> None:
    """`sort` is a plain `str` at the tool boundary on purpose: the whitelist answers
    with this project's own `ValidationFailed`, which `_guard` renders as readable
    guidance naming the field and listing the admissible keys. A `Literal` here would
    have been rejected by the SDK ahead of the guard, in raw English pydantic text."""
    async with Client(server) as client:
        result = await client.call_tool("search_organizations", {"sort": "note"})

    assert result.is_error
    message = result.content[0].text
    assert "errors.pydantic.dev" not in message
    assert "legal_name" in message


async def test_search_organizations_with_an_unknown_direction_produces_guidance(server) -> None:
    """`dir` is `str` at the boundary and cast at the call, so an out-of-range value is
    rejected by `OrganizationListQuery`'s own `SortDirection` *inside* the guard."""
    async with Client(server) as client:
        result = await client.call_tool("search_organizations", {"dir": "sideways"})

    assert result.is_error
    message = result.content[0].text
    assert "errors.pydantic.dev" not in message
    assert "dir" in message.lower()


async def test_list_documents_accepts_a_search_term(
    server, mcp_session: Session, seeded_customer_id: str
) -> None:
    """Seeded through the session rather than a tool: MCP deliberately exposes no
    `create_document` (a document is created from a template, or over REST with real
    bytes), so there is no tool call that would put rows here.

    The search term carries a per-run token for the same reason
    `test_search_organizations_accepts_sort_and_dir` does: this suite's schema is shared and
    not every row in it belongs to the running test."""
    from pigrocrm.core.documents.models import Document

    token = f"zz{uuid4().hex[:8]}"
    for title in (f"Offerta {token}", f"Verbale {token}"):
        mcp_session.add(
            Document(
                customer_id=UUID(seeded_customer_id),
                type="document",
                title=title,
                current_version=1,
                custom_fields={},
            )
        )
    mcp_session.commit()

    async with Client(server) as client:
        found = _payload(await client.call_tool("list_documents", {"search": f"offerta {token}"}))

    assert [d["title"] for d in found["items"]] == [f"Offerta {token}"]
