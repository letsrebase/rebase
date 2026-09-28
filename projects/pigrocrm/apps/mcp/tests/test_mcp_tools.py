import json
from typing import Any
from uuid import UUID, uuid4

import pytest
from mcp import Client
from sqlalchemy import text
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.config import Settings
from pigrocrm.core.fields.schemas import FieldDefinitionCreate
from pigrocrm.core.fields.service import FieldDefinitionService
from pigrocrm.core.pipeline.service import PipelineService

ADMIN = Actor(id=None, type="mcp", role="admin")


def _payload(result) -> dict:
    return result.structured_content or json.loads(result.content[0].text)


async def test_every_tool_from_the_spec_is_exposed(server) -> None:
    async with Client(server) as client:
        names = {tool.name for tool in (await client.list_tools()).tools}

    assert {
        "describe_schema",
        "refresh_schema",
        "create_customer",
        "update_customer",
        "get_customer",
        "search_customers",
        "archive_customer",
        "create_person",
        "update_person",
        "get_person",
        "search_people",
        "archive_person",
        "create_deal",
        "update_deal",
        "get_deal",
        "search_deals",
        "move_deal",
        "archive_deal",
        "list_pipeline_stages",
        "get_timeline",
    } <= names


async def test_no_destructive_delete_tool_exists(server) -> None:
    """An agent misreading 'elimina i deal chiusi' must not be able to destroy rows."""
    async with Client(server) as client:
        names = {tool.name for tool in (await client.list_tools()).tools}
    assert not [name for name in names if name.startswith("delete_")]


async def test_the_full_journey_an_agent_would_take(server, mcp_session: Session) -> None:
    """Spec success criterion 2: Claude does the whole job through MCP, discovering a
    custom field nobody hardcoded."""
    FieldDefinitionService(mcp_session).create(
        FieldDefinitionCreate(
            entity_type="customer",
            key="settore",
            label="Settore",
            field_type="select",
            options=["IT", "Retail"],
        ),
        ADMIN,
    )
    PipelineService(mcp_session).seed_defaults(ADMIN)

    async with Client(server) as client:
        schema = _payload(await client.call_tool("describe_schema", {"entity_type": "customer"}))
        assert [f["key"] for f in schema["custom_fields"]] == ["settore"]

        customer = _payload(
            await client.call_tool(
                "create_customer",
                {
                    "ragione_sociale": "ACME Srl",
                    "partita_iva": "12345678901",
                    "custom_fields": {"settore": "IT"},
                },
            )
        )
        assert customer["custom_fields"] == {"settore": "IT"}

        person = _payload(
            await client.call_tool(
                "create_person",
                {"nome": "Mario", "cognome": "Rossi", "customer_id": customer["id"]},
            )
        )
        assert person["customer_id"] == customer["id"]

        deal = _payload(
            await client.call_tool(
                "create_deal", {"nome": "Progetto X", "customer_id": customer["id"]}
            )
        )

        stages = _payload(await client.call_tool("list_pipeline_stages", {}))["stages"]
        offerta = next(s for s in stages if s["nome"] == "Offerta")
        moved = _payload(
            await client.call_tool("move_deal", {"deal_id": deal["id"], "stage_id": offerta["id"]})
        )
        assert moved["pipeline_stage_id"] == offerta["id"]

        timeline = _payload(
            await client.call_tool("get_timeline", {"entity_type": "deal", "entity_id": deal["id"]})
        )["entries"]

    # Criterion 3: the timeline distinguishes the agent from a human.
    assert {entry["actor_type"] for entry in timeline} == {"mcp"}
    assert "stage_changed" in {entry["kind"] for entry in timeline}


async def test_an_invalid_custom_value_returns_guidance_naming_the_options(
    server, mcp_session: Session
) -> None:
    FieldDefinitionService(mcp_session).create(
        FieldDefinitionCreate(
            entity_type="customer",
            key="settore",
            label="Settore",
            field_type="select",
            options=["IT", "Retail"],
        ),
        ADMIN,
    )
    async with Client(server) as client:
        result = await client.call_tool(
            "create_customer",
            {"ragione_sociale": "Beta", "custom_fields": {"settore": "Altro"}},
        )

    assert result.is_error
    message = result.content[0].text
    assert "IT" in message and "Retail" in message
    assert "Valore atteso" in message


async def test_a_permission_error_tells_the_agent_to_ask_the_user(mcp_session: Session) -> None:
    from pigrocrm_mcp.server import build_server

    readonly = Actor(id=None, type="mcp", role="readonly")
    # `_env_file=None`: this test is about the permission refusal, not about whatever
    # document store the worktree's `.env` selects (a Drive backend without a service
    # account would refuse at storage construction before the tool ever ran).
    server = build_server(
        lambda: mcp_session,
        lambda: readonly,
        settings=Settings(_env_file=None),  # type: ignore[call-arg]
    )

    async with Client(server) as client:
        result = await client.call_tool("create_customer", {"ragione_sociale": "ACME"})

    assert result.is_error
    message = result.content[0].text
    assert "readonly" in message
    assert "manualmente" in message


async def test_search_customers_finds_by_free_text(server, mcp_session: Session) -> None:
    async with Client(server) as client:
        await client.call_tool("create_customer", {"ragione_sociale": "ACME Srl"})
        await client.call_tool("create_customer", {"ragione_sociale": "Beta Spa"})
        found = _payload(await client.call_tool("search_customers", {"search": "acme"}))

    assert [c["ragione_sociale"] for c in found["items"]] == ["ACME Srl"]


# --- Final review item 10 (MINOR): *ListQuery.custom was implemented, GIN- ------
# --- indexed and service-tested, but no MCP tool passed it. --------------------


async def test_search_customers_filters_by_a_custom_field(server, mcp_session: Session) -> None:
    FieldDefinitionService(mcp_session).create(
        FieldDefinitionCreate(
            entity_type="customer", key="settore", label="Settore", field_type="text"
        ),
        ADMIN,
    )
    async with Client(server) as client:
        await client.call_tool(
            "create_customer", {"ragione_sociale": "A", "custom_fields": {"settore": "IT"}}
        )
        await client.call_tool(
            "create_customer", {"ragione_sociale": "B", "custom_fields": {"settore": "Retail"}}
        )
        found = _payload(await client.call_tool("search_customers", {"custom": {"settore": "IT"}}))

    assert [c["ragione_sociale"] for c in found["items"]] == ["A"]


async def test_search_people_filters_by_a_custom_field(server, mcp_session: Session) -> None:
    FieldDefinitionService(mcp_session).create(
        FieldDefinitionCreate(
            entity_type="person", key="seniority", label="Seniority", field_type="text"
        ),
        ADMIN,
    )
    async with Client(server) as client:
        await client.call_tool(
            "create_person", {"nome": "A", "custom_fields": {"seniority": "senior"}}
        )
        await client.call_tool(
            "create_person", {"nome": "B", "custom_fields": {"seniority": "junior"}}
        )
        found = _payload(
            await client.call_tool("search_people", {"custom": {"seniority": "senior"}})
        )

    assert [p["nome"] for p in found["items"]] == ["A"]


async def test_search_deals_filters_by_a_custom_field(server, mcp_session: Session) -> None:
    PipelineService(mcp_session).seed_defaults(ADMIN)
    FieldDefinitionService(mcp_session).create(
        FieldDefinitionCreate(entity_type="deal", key="fonte", label="Fonte", field_type="text"),
        ADMIN,
    )
    async with Client(server) as client:
        customer = _payload(await client.call_tool("create_customer", {"ragione_sociale": "ACME"}))
        await client.call_tool(
            "create_deal",
            {"nome": "A", "customer_id": customer["id"], "custom_fields": {"fonte": "referral"}},
        )
        await client.call_tool(
            "create_deal",
            {"nome": "B", "customer_id": customer["id"], "custom_fields": {"fonte": "outbound"}},
        )
        found = _payload(await client.call_tool("search_deals", {"custom": {"fonte": "referral"}}))

    assert [d["nome"] for d in found["items"]] == ["A"]


# --- Final review item 7 (IMPORTANT): search tools returned next_cursor but had --
# --- no way to pass it back in -- an agent could never reach page 2. -----------


async def test_search_customers_can_walk_a_second_page_via_cursor(
    server, mcp_session: Session
) -> None:
    """Before this fix, `search_customers` took no `cursor` parameter at all: the
    SDK silently drops an argument a tool's signature does not declare, so passing
    `next_cursor` back returned page 1 again, with `is_error=False` -- an agent
    could never reach a record beyond the first page, and nothing in the response
    said so."""
    async with Client(server) as client:
        for index in range(3):
            await client.call_tool("create_customer", {"ragione_sociale": f"Cliente {index:02d}"})

        first = _payload(await client.call_tool("search_customers", {"limit": 2}))
        assert len(first["items"]) == 2
        assert first["next_cursor"] is not None

        second = _payload(
            await client.call_tool("search_customers", {"limit": 2, "cursor": first["next_cursor"]})
        )

    first_ids = {c["id"] for c in first["items"]}
    second_ids = {c["id"] for c in second["items"]}
    assert second_ids, "the second page must not be empty"
    assert first_ids.isdisjoint(second_ids), "the second page must reach records page 1 lacked"


async def test_search_people_can_walk_a_second_page_via_cursor(server) -> None:
    async with Client(server) as client:
        for index in range(3):
            await client.call_tool("create_person", {"nome": f"Persona {index:02d}"})

        first = _payload(await client.call_tool("search_people", {"limit": 2}))
        second = _payload(
            await client.call_tool("search_people", {"limit": 2, "cursor": first["next_cursor"]})
        )

    first_ids = {p["id"] for p in first["items"]}
    second_ids = {p["id"] for p in second["items"]}
    assert second_ids
    assert first_ids.isdisjoint(second_ids)


async def test_search_deals_can_walk_a_second_page_via_cursor(server, mcp_session: Session) -> None:
    PipelineService(mcp_session).seed_defaults(ADMIN)
    async with Client(server) as client:
        customer = _payload(await client.call_tool("create_customer", {"ragione_sociale": "ACME"}))
        for index in range(3):
            await client.call_tool(
                "create_deal", {"nome": f"Deal {index:02d}", "customer_id": customer["id"]}
            )

        first = _payload(await client.call_tool("search_deals", {"limit": 2}))
        second = _payload(
            await client.call_tool("search_deals", {"limit": 2, "cursor": first["next_cursor"]})
        )

    first_ids = {d["id"] for d in first["items"]}
    second_ids = {d["id"] for d in second["items"]}
    assert second_ids
    assert first_ids.isdisjoint(second_ids)


async def test_search_customers_with_a_malformed_cursor_produces_guidance_not_a_crash(
    server,
) -> None:
    async with Client(server) as client:
        result = await client.call_tool("search_customers", {"cursor": "not-a-uuid"})

    assert result.is_error
    message = result.content[0].text
    assert "errors.pydantic.dev" not in message
    # Since slice 6 this is a *better* error than it used to be, and the assertion
    # moved with it. `cursor` was a UUID, so a malformed one used to surface as the
    # generic `_malformed_identifier_error` ("...usa lo strumento di ricerca...", which
    # is where the old `"cerca"` assertion was matching, inside "ricerca"). It is now
    # an opaque string decoded by `db/sort.py`, which raises a domain `ValidationFailed`
    # naming `cursor` and saying what a valid one is -- so the assertion is on the
    # field and on the guidance, not on a substring of an unrelated sentence.
    assert "cursor" in message.lower()
    assert "cursore non valido" in message.lower()
    assert "riprova" in message.lower()


async def test_search_customers_accepts_sort_and_dir(server) -> None:
    """The tool's JSON Schema gained two parameters, and the SDK silently drops an
    argument a signature does not declare -- which is exactly how `cursor` shipped
    broken once already (see the cursor test above). Asserting the *order* rather than
    the status is what makes this fail in that case: a dropped `sort` returns 200 with
    the rows in creation order.

    Scoped by a per-run token rather than asserting on the whole table: at least one
    test in this suite commits a customer outside the rolled-back transaction, so a bare
    `search_customers` here sees rows it did not create. That made this test pass alone
    and fail in the suite, which is the worst of both.
    """
    token = f"zz{uuid4().hex[:8]}"
    async with Client(server) as client:
        for nome in ("Gamma", "Alfa", "Beta"):
            await client.call_tool("create_customer", {"ragione_sociale": f"{token} {nome}"})

        ascending = _payload(
            await client.call_tool(
                "search_customers",
                {"search": token, "sort": "ragione_sociale", "dir": "asc", "limit": 10},
            )
        )
        descending = _payload(
            await client.call_tool(
                "search_customers",
                {"search": token, "sort": "ragione_sociale", "dir": "desc", "limit": 10},
            )
        )

    assert [c["ragione_sociale"] for c in ascending["items"]] == [
        f"{token} Alfa",
        f"{token} Beta",
        f"{token} Gamma",
    ]
    assert [c["ragione_sociale"] for c in descending["items"]] == [
        f"{token} Gamma",
        f"{token} Beta",
        f"{token} Alfa",
    ]


async def test_search_customers_with_an_unknown_sort_key_produces_guidance(server) -> None:
    """`sort` is a plain `str` at the tool boundary on purpose: the whitelist answers
    with this project's own `ValidationFailed`, which `_guard` renders as readable
    guidance naming the field and listing the admissible keys. A `Literal` here would
    have been rejected by the SDK ahead of the guard, in raw English pydantic text."""
    async with Client(server) as client:
        result = await client.call_tool("search_customers", {"sort": "note"})

    assert result.is_error
    message = result.content[0].text
    assert "errors.pydantic.dev" not in message
    assert "ragione_sociale" in message


async def test_search_customers_with_an_unknown_direction_produces_guidance(server) -> None:
    """`dir` is `str` at the boundary and cast at the call, so an out-of-range value is
    rejected by `CustomerListQuery`'s own `SortDirection` *inside* the guard."""
    async with Client(server) as client:
        result = await client.call_tool("search_customers", {"dir": "sideways"})

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
    `test_search_customers_accepts_sort_and_dir` does: this suite's schema is shared and
    not every row in it belongs to the running test."""
    from pigrocrm.core.documents.models import Document

    token = f"zz{uuid4().hex[:8]}"
    for titolo in (f"Offerta {token}", f"Verbale {token}"):
        mcp_session.add(
            Document(
                customer_id=UUID(seeded_customer_id),
                tipo="documento",
                titolo=titolo,
                versione_corrente=1,
                custom_fields={},
            )
        )
    mcp_session.commit()

    async with Client(server) as client:
        found = _payload(await client.call_tool("list_documents", {"search": f"offerta {token}"}))

    assert [d["titolo"] for d in found["items"]] == [f"Offerta {token}"]


async def test_archive_customer_is_reversible_and_blocks_on_active_deals(
    server, mcp_session: Session
) -> None:
    PipelineService(mcp_session).seed_defaults(ADMIN)
    async with Client(server) as client:
        customer = _payload(await client.call_tool("create_customer", {"ragione_sociale": "ACME"}))
        deal = _payload(
            await client.call_tool("create_deal", {"nome": "X", "customer_id": customer["id"]})
        )

        blocked = await client.call_tool("archive_customer", {"customer_id": customer["id"]})
        assert blocked.is_error
        assert "deal attivi" in blocked.content[0].text

        await client.call_tool("archive_deal", {"deal_id": deal["id"]})
        ok = await client.call_tool("archive_customer", {"customer_id": customer["id"]})
        assert not ok.is_error

        # "Reversibile" in the tool's own description, now actually reversed. The three
        # archive tools promised it while no restore tool existed at all: an agent that
        # archived the wrong customer had no way back through MCP, although a person had
        # one over REST the whole time. `test_mcp_surface_coverage.py` is what found the
        # asymmetry -- `CustomerService.restore`, `DealService.restore` and
        # `PersonService.restore` were the only `restore` methods with no tool, while
        # `restore_cost` and `restore_time_entry` have had one since slice 4.
        restored = _payload(
            await client.call_tool("restore_customer", {"customer_id": customer["id"]})
        )
        assert restored["id"] == customer["id"]
        deal_again = _payload(await client.call_tool("restore_deal", {"deal_id": deal["id"]}))
        assert deal_again["id"] == deal["id"]


async def test_a_person_archived_by_an_agent_can_be_restored_by_one(server) -> None:
    """The same round trip on `people`, which has no "deal attivi" guard to complicate
    it: archiving is exposed, so its inverse has to be, or the agent's own mistake is
    correctable only by a human with a browser."""
    async with Client(server) as client:
        person = _payload(await client.call_tool("create_person", {"nome": "Giulia"}))
        assert not (await client.call_tool("archive_person", {"person_id": person["id"]})).is_error
        assert (await client.call_tool("get_person", {"person_id": person["id"]})).is_error

        restored = _payload(await client.call_tool("restore_person", {"person_id": person["id"]}))
        assert restored["id"] == person["id"]
        assert not (await client.call_tool("get_person", {"person_id": person["id"]})).is_error


async def test_create_person_without_a_customer_is_allowed(server) -> None:
    """A contact may exist before anyone knows their employer -- `customer_id` must
    stay optional at the actual MCP boundary, not merely on the underlying schema."""
    async with Client(server) as client:
        person = _payload(
            await client.call_tool("create_person", {"nome": "Giulia", "cognome": "Bianchi"})
        )

    assert person["customer_id"] is None


async def test_move_deal_to_a_won_stage_settles_probability_through_the_tool(
    server, mcp_session: Session
) -> None:
    """`move_deal` must not re-decide probability itself -- it delegates to
    `DealService.move_stage`, which is the single authority on 'won at 60%' being
    unreachable. This proves that delegation survives the MCP boundary end to end."""
    PipelineService(mcp_session).seed_defaults(ADMIN)
    async with Client(server) as client:
        customer = _payload(await client.call_tool("create_customer", {"ragione_sociale": "ACME"}))
        deal = _payload(
            await client.call_tool(
                "create_deal",
                {"nome": "Progetto Y", "customer_id": customer["id"], "probabilita": 20},
            )
        )

        stages = _payload(await client.call_tool("list_pipeline_stages", {}))["stages"]
        vinto = next(s for s in stages if s["nome"] == "Vinto")
        moved = _payload(
            await client.call_tool("move_deal", {"deal_id": deal["id"], "stage_id": vinto["id"]})
        )

    assert moved["probabilita"] == 100


# --- Fix round 1: update_* schemas must be discoverable, and every argument- ---
# --- conversion failure must render like every other domain error.          ---


async def test_update_customer_schema_lists_the_real_modifiable_fields(server) -> None:
    """A model calling list_tools() must see the actual field names it can send in
    `changes`, not an opaque `{"type": "object"}` it has to guess or infer from
    create_customer's sibling schema."""
    async with Client(server) as client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}

    changes_schema = tools["update_customer"].input_schema["properties"]["changes"]
    assert "ragione_sociale" in changes_schema["properties"]
    assert "partita_iva" in changes_schema["properties"]


async def test_update_deal_schema_lists_the_real_modifiable_fields(server) -> None:
    async with Client(server) as client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}

    changes_schema = tools["update_deal"].input_schema["properties"]["changes"]
    assert "probabilita" in changes_schema["properties"]
    assert "valore_previsto" in changes_schema["properties"]
    # move_deal, not update_deal, is the supported way to change stage -- the
    # schema must not invite an agent to try setting it here instead.
    assert "pipeline_stage_id" not in changes_schema["properties"]


async def test_update_person_schema_documents_what_detach_does(server) -> None:
    """`detach` reads as a bare boolean unless its schema explains the effect --
    the field name alone does not say it unlinks the person from their customer."""
    async with Client(server) as client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}

    changes_schema = tools["update_person"].input_schema["properties"]["changes"]
    detach_schema = changes_schema["properties"]["detach"]
    description = detach_schema.get("description", "").lower()
    assert description
    assert "client" in description or "cliente" in description


async def test_create_deal_documents_the_expected_date_format(server) -> None:
    async with Client(server) as client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}

    create_deal_tool = tools["create_deal"]
    assert "YYYY-MM-DD" in (create_deal_tool.description or "")


async def test_a_malformed_identifier_produces_guidance_not_a_stack_trace(
    server, mcp_session: Session
) -> None:
    """§8.2: an error must be actionable by a model. `uuid.UUID("not-a-uuid")`
    raises a bare `ValueError` with an English, unstructured message; the guard
    must render it the same way as every other domain error."""
    async with Client(server) as client:
        result = await client.call_tool("get_customer", {"customer_id": "not-a-uuid"})

    assert result.is_error
    message = result.content[0].text
    assert "errors.pydantic.dev" not in message
    assert "badly formed" not in message
    assert "Valore atteso" in message
    assert "cerca" in message.lower()


async def test_a_wrong_typed_value_inside_changes_produces_guidance(
    server, mcp_session: Session
) -> None:
    """Fix 1 makes `changes` a real, typed schema; this proves the type-checking
    that now happens when building the update model still comes back through the
    guard's rendering, not as a raw pydantic dump with a errors.pydantic.dev link."""
    PipelineService(mcp_session).seed_defaults(ADMIN)
    async with Client(server) as client:
        customer = _payload(await client.call_tool("create_customer", {"ragione_sociale": "ACME"}))
        deal = _payload(
            await client.call_tool("create_deal", {"nome": "X", "customer_id": customer["id"]})
        )
        result = await client.call_tool(
            "update_deal", {"deal_id": deal["id"], "changes": {"probabilita": "not-a-number"}}
        )

    assert result.is_error
    message = result.content[0].text
    assert "errors.pydantic.dev" not in message
    assert "probabilita" in message
    assert "Valore atteso" in message


async def test_a_limit_out_of_range_produces_guidance_naming_the_range(
    server, mcp_session: Session
) -> None:
    async with Client(server) as client:
        result = await client.call_tool("search_customers", {"limit": 500})

    assert result.is_error
    message = result.content[0].text
    assert "errors.pydantic.dev" not in message
    assert "200" in message
    assert "Valore atteso" in message


# --- Final review item 9 (IMPORTANT): the remaining wrong-TYPE scalars still ---
# --- leaked pydantic internals -- a wrong value on `limit` was already fixed  ---
# --- (Task 17), but a wrong *type* on `limit`/`probabilita`/the money fields  ---
# --- bypassed the guard entirely, at the SDK's own pre-call argument coercion. --


async def test_a_wrong_typed_limit_produces_guidance_not_a_raw_pydantic_dump(
    server, mcp_session: Session
) -> None:
    """The exact reproduction named in the review: before BoundedLimit existed,
    this came back as a raw, multi-line, English pydantic dump carrying an
    errors.pydantic.dev link, because the SDK's own pre-call coercion for a bare
    `limit: int` parameter rejected "molti" before `_guard` ever ran."""
    async with Client(server) as client:
        result = await client.call_tool("search_customers", {"limit": "molti"})

    assert result.is_error
    message = result.content[0].text
    assert "errors.pydantic.dev" not in message
    assert "limit" in message
    assert "Valore atteso" in message


async def test_a_wrong_typed_probabilita_on_create_deal_produces_guidance(
    server, mcp_session: Session
) -> None:
    PipelineService(mcp_session).seed_defaults(ADMIN)
    async with Client(server) as client:
        customer = _payload(await client.call_tool("create_customer", {"ragione_sociale": "ACME"}))
        result = await client.call_tool(
            "create_deal",
            {"nome": "X", "customer_id": customer["id"], "probabilita": "molto alta"},
        )

    assert result.is_error
    message = result.content[0].text
    assert "errors.pydantic.dev" not in message
    assert "probabilita" in message


async def test_a_wrong_typed_money_field_on_create_deal_produces_guidance(
    server, mcp_session: Session
) -> None:
    PipelineService(mcp_session).seed_defaults(ADMIN)
    async with Client(server) as client:
        customer = _payload(await client.call_tool("create_customer", {"ragione_sociale": "ACME"}))
        result = await client.call_tool(
            "create_deal",
            {"nome": "X", "customer_id": customer["id"], "valore_previsto": "un sacco"},
        )

    assert result.is_error
    message = result.content[0].text
    assert "errors.pydantic.dev" not in message
    assert "valore_previsto" in message


async def test_create_deal_still_accepts_a_normal_numeric_probabilita(
    server, mcp_session: Session
) -> None:
    """The permissive `int | str` type must not regress the ordinary path."""
    PipelineService(mcp_session).seed_defaults(ADMIN)
    async with Client(server) as client:
        customer = _payload(await client.call_tool("create_customer", {"ragione_sociale": "ACME"}))
        deal = _payload(
            await client.call_tool(
                "create_deal",
                {"nome": "X", "customer_id": customer["id"], "probabilita": 42},
            )
        )

    assert deal["probabilita"] == 42


# --- Final review item 5 (CRITICAL): get_timeline's limit was unbounded, unlike --
# --- REST's Query(ge=1, le=200) on all three timeline routes. ---------------------


async def test_get_timeline_rejects_a_negative_limit_with_guidance_not_a_crash(
    server, mcp_session: Session
) -> None:
    """Before ActivityService.timeline bounded its own `limit`, -1 reached Postgres
    raw as `psycopg.errors.InvalidRowCountInLimitClause` -- an exception `_guard`
    did not (and, per item 6, could not fully) recover from cleanly."""
    async with Client(server) as client:
        customer = _payload(await client.call_tool("create_customer", {"ragione_sociale": "ACME"}))
        result = await client.call_tool(
            "get_timeline",
            {"entity_type": "customer", "entity_id": customer["id"], "limit": -1},
        )

    assert result.is_error
    message = result.content[0].text
    assert "errors.pydantic.dev" not in message
    assert "1-200" in message


async def test_get_timeline_rejects_an_oversized_limit_matching_rest_exactly(
    server, mcp_session: Session
) -> None:
    """`10**9` used to succeed here and return everything, where REST would refuse
    the same request outright -- a parity divergence between the two adapters, not
    only a crash risk."""
    async with Client(server) as client:
        customer = _payload(await client.call_tool("create_customer", {"ragione_sociale": "ACME"}))
        result = await client.call_tool(
            "get_timeline",
            {"entity_type": "customer", "entity_id": customer["id"], "limit": 10**9},
        )

    assert result.is_error
    message = result.content[0].text
    assert "1-200" in message


async def test_get_timeline_still_accepts_an_in_range_limit(server, mcp_session: Session) -> None:
    async with Client(server) as client:
        customer = _payload(await client.call_tool("create_customer", {"ragione_sociale": "ACME"}))
        result = _payload(
            await client.call_tool(
                "get_timeline",
                {"entity_type": "customer", "entity_id": customer["id"], "limit": 50},
            )
        )

    assert isinstance(result["entries"], list)


# --- The WithJsonSchema override must not disturb the ordinary, valid path. ---


async def test_update_customer_still_applies_a_valid_changes_payload(server) -> None:
    async with Client(server) as client:
        customer = _payload(await client.call_tool("create_customer", {"ragione_sociale": "ACME"}))
        updated = _payload(
            await client.call_tool(
                "update_customer",
                {"customer_id": customer["id"], "changes": {"ragione_sociale": "ACME Srl"}},
            )
        )

    assert updated["ragione_sociale"] == "ACME Srl"


async def test_update_person_can_still_detach_via_changes(server) -> None:
    async with Client(server) as client:
        customer = _payload(await client.call_tool("create_customer", {"ragione_sociale": "ACME"}))
        person = _payload(
            await client.call_tool(
                "create_person", {"nome": "Mario", "customer_id": customer["id"]}
            )
        )
        updated = _payload(
            await client.call_tool(
                "update_person", {"person_id": person["id"], "changes": {"detach": True}}
            )
        )

    assert updated["customer_id"] is None


async def test_update_deal_still_applies_a_valid_changes_payload(
    server, mcp_session: Session
) -> None:
    PipelineService(mcp_session).seed_defaults(ADMIN)
    async with Client(server) as client:
        customer = _payload(await client.call_tool("create_customer", {"ragione_sociale": "ACME"}))
        deal = _payload(
            await client.call_tool("create_deal", {"nome": "X", "customer_id": customer["id"]})
        )
        updated = _payload(
            await client.call_tool(
                "update_deal", {"deal_id": deal["id"], "changes": {"probabilita": 42}}
            )
        )

    assert updated["probabilita"] == 42


# --- Final review item 6 (CRITICAL, blast radius): one escaping exception must ---
# --- not brick the server for every later call. ----------------------------------


async def test_server_survives_a_raw_database_error_and_serves_the_next_call(
    server, mcp_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`__main__.py` builds exactly one `Session` for the whole process and passes
    `lambda: session` to `build_server` -- the same shared session every tool call
    in this test suite's `server` fixture reuses too. Before this fix, `_guard`
    only rolled back a `DomainError`/`ValueError` it already knew how to render;
    any other exception -- a raw DBAPI failure being the realistic case, since
    items 1-5 of this same fix wave closed off the paths that used to produce one
    at the public tool surface -- escaped uncaught, leaving the session's
    transaction failed. Every later call on that session, including an unrelated
    read like `describe_schema`, then failed too (`psycopg.errors.
    InFailedSqlTransaction` under this test's savepoint-based session, the plain
    SQLAlchemy `PendingRollbackError` in `__main__.py`'s real, non-savepoint
    session -- same underlying bug, two renderings of it) until the process was
    restarted.

    Forces a genuine Postgres-level error (`SELECT 1/0`, integer division by
    zero) rather than a bare Python exception: only a real DBAPI failure actually
    leaves the SQLAlchemy transaction in the failed state this bug depends on --
    a plain `RuntimeError` raised in application code never touches the
    connection at all, and would pass even without the fix, proving nothing.
    """

    def _hit_a_real_database_error(self: PipelineService) -> list[Any]:
        self.session.execute(text("SELECT 1/0"))
        return []

    monkeypatch.setattr(PipelineService, "list", _hit_a_real_database_error)

    async with Client(server) as client:
        failing = await client.call_tool("list_pipeline_stages", {})
        assert failing.is_error

        # Undo the fault injection before the next call -- this proves the FIRST
        # call's aftermath (not a second, independent failure) is what is under
        # test: describe_schema never touches PipelineService either way, but
        # this keeps the test's intent unambiguous even if that changes later.
        monkeypatch.undo()

        ok = await client.call_tool("describe_schema", {"entity_type": "customer"})

    assert not ok.is_error, ok.content[0].text


async def test_the_broadened_rollback_guard_still_does_not_disguise_a_programming_error(
    server, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The trailing `except Exception` added for item 6 exists only to add a
    rollback -- it must never also translate the exception, or a real programming
    bug (a `KeyError`/`AttributeError` this codebase never intends to raise on
    purpose) would be misreported to the agent as if it had sent a bad value,
    exactly the property Task 17's review already verified about the two narrower
    `except` clauses above it in `_guard`.

    Re-raised untouched, the crash is then the SDK's to report, and since mcp 2.2 it
    keeps the exception's text on the server: `Tool.run` answers only «Error
    executing tool <name>» for anything that is not a `ToolError`, a `ResourceError`
    or an `MCPError`. That is the intended behaviour (REB-451): the assistant learns
    that the call failed and nothing about the code, where on mcp 2.0 it read the
    `KeyError`'s own text."""

    def _raise_a_programming_error(self: PipelineService) -> list[Any]:
        raise KeyError("boom")

    monkeypatch.setattr(PipelineService, "list", _raise_a_programming_error)

    async with Client(server) as client:
        result = await client.call_tool("list_pipeline_stages", {})

    assert result.is_error
    message = result.content[0].text
    assert message == "Error executing tool list_pipeline_stages"
    assert "boom" not in message
