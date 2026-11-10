"""Archiving, restoring, moving and updating records through the tools."""

import json
from uuid import uuid4

from mcp import Client
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.pipeline.service import PipelineService

SETUP = Actor(id=None, type="system", role="admin")  # setup is not an agent's (agent_actions.py)


def _payload(result) -> dict:
    return result.structured_content or json.loads(result.content[0].text)


async def test_archive_customer_is_reversible_and_blocks_on_active_deals(
    server, mcp_session: Session
) -> None:
    PipelineService(mcp_session).seed_defaults(SETUP)
    async with Client(server) as client:
        customer = _payload(await client.call_tool("create_organization", {"legal_name": "ACME"}))
        deal = _payload(
            await client.call_tool("create_deal", {"name": "X", "customer_id": customer["id"]})
        )

        blocked = await client.call_tool(
            "archive_organization", {"organization_id": customer["id"]}
        )
        assert blocked.is_error

        await client.call_tool("archive_deal", {"deal_id": deal["id"]})
        ok = await client.call_tool("archive_organization", {"organization_id": customer["id"]})
        assert not ok.is_error

        # "Reversibile" in the tool's own description, now actually reversed. The three
        # archive tools promised it while no restore tool existed at all: an agent that
        # archived the wrong customer had no way back through MCP, although a person had
        # one over REST the whole time. `test_mcp_surface_coverage.py` is what found the
        # asymmetry -- `OrganizationService.restore`, `DealService.restore` and
        # `PersonService.restore` were the only `restore` methods with no tool, while
        # `restore_purchase` and `restore_time_entry` have had one since slice 4.
        restored = _payload(
            await client.call_tool("restore_organization", {"organization_id": customer["id"]})
        )
        assert restored["id"] == customer["id"]
        deal_again = _payload(await client.call_tool("restore_deal", {"deal_id": deal["id"]}))
        assert deal_again["id"] == deal["id"]


async def test_a_person_archived_by_an_agent_can_be_restored_by_one(server) -> None:
    """The same round trip on `people`, which has no "deal attivi" guard to complicate
    it: archiving is exposed, so its inverse has to be, or the agent's own mistake is
    correctable only by a human with a browser."""
    async with Client(server) as client:
        person = _payload(await client.call_tool("create_person", {"first_name": "Giulia"}))
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
            await client.call_tool(
                "create_person", {"first_name": "Giulia", "last_name": "Bianchi"}
            )
        )

    assert person["customer_id"] is None


async def test_move_deal_to_a_won_stage_settles_probability_through_the_tool(
    server, mcp_session: Session
) -> None:
    """`move_deal` must not re-decide probability itself -- it delegates to
    `DealService.move_stage`, which is the single authority on 'won at 60%' being
    unreachable. This proves that delegation survives the MCP boundary end to end."""
    PipelineService(mcp_session).seed_defaults(SETUP)
    async with Client(server) as client:
        customer = _payload(await client.call_tool("create_organization", {"legal_name": "ACME"}))
        deal = _payload(
            await client.call_tool(
                "create_deal",
                {"name": "Progetto Y", "customer_id": customer["id"], "probability": 20},
            )
        )

        stages = _payload(await client.call_tool("list_pipeline_stages", {}))["stages"]
        won = next(s for s in stages if s["name"] == "Vinto")
        moved = _payload(
            await client.call_tool("move_deal", {"deal_id": deal["id"], "stage_id": won["id"]})
        )

    assert moved["probability"] == 100


# --- Fix round 1: update_* schemas must be discoverable, and every argument- ---
# --- conversion failure must render like every other domain error.          ---


async def test_update_customer_still_applies_a_valid_changes_payload(server) -> None:
    async with Client(server) as client:
        customer = _payload(await client.call_tool("create_organization", {"legal_name": "ACME"}))
        updated = _payload(
            await client.call_tool(
                "update_organization",
                {"organization_id": customer["id"], "changes": {"legal_name": "ACME Srl"}},
            )
        )

    assert updated["legal_name"] == "ACME Srl"


async def test_update_person_can_still_detach_via_changes(server) -> None:
    async with Client(server) as client:
        customer = _payload(await client.call_tool("create_organization", {"legal_name": "ACME"}))
        person = _payload(
            await client.call_tool(
                "create_person", {"first_name": "Mario", "customer_id": customer["id"]}
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
    PipelineService(mcp_session).seed_defaults(SETUP)
    async with Client(server) as client:
        customer = _payload(await client.call_tool("create_organization", {"legal_name": "ACME"}))
        deal = _payload(
            await client.call_tool("create_deal", {"name": "X", "customer_id": customer["id"]})
        )
        updated = _payload(
            await client.call_tool(
                "update_deal", {"deal_id": deal["id"], "changes": {"probability": 42}}
            )
        )

    assert updated["probability"] == 42


# --- Final review item 6 (CRITICAL, blast radius): one escaping exception must ---
# --- not brick the server for every later call. ----------------------------------


async def test_an_agent_sets_a_deals_owner_and_a_new_deal_defaults_to_its_creator(
    server, mcp_session: Session
) -> None:
    """REB-780: `create_deal` takes `owner_id`; `update_deal` takes it in `changes`, and
    `list_members` is where an agent finds the id of an active colleague."""
    from pigrocrm.core.auth.models import User

    colleague = User(
        email=f"c-{uuid4()}@example.test", password_hash="x", name="Collega", role="admin"
    )
    gone = User(
        email=f"g-{uuid4()}@example.test",
        password_hash="x",
        name="Andata",
        role="admin",
        active=False,
    )
    mcp_session.add_all([colleague, gone])
    mcp_session.flush()
    PipelineService(mcp_session).seed_defaults(SETUP)

    async with Client(server) as client:
        members = _payload(await client.call_tool("list_members", {}))["result"]
        assert str(colleague.id) in {m["id"] for m in members}
        assert str(gone.id) not in {m["id"] for m in members}
        assert set(members[0]) == {"id", "name", "role", "email"}

        customer = _payload(await client.call_tool("create_organization", {"legal_name": "ACME"}))
        named = _payload(
            await client.call_tool(
                "create_deal",
                {"name": "A", "customer_id": customer["id"], "owner_id": str(colleague.id)},
            )
        )
        assert named["owner_id"] == str(colleague.id)

        cleared = _payload(
            await client.call_tool(
                "update_deal", {"deal_id": named["id"], "changes": {"owner_id": None}}
            )
        )
        assert cleared["owner_id"] is None

        refused = await client.call_tool(
            "update_deal",
            {"deal_id": named["id"], "changes": {"owner_id": str(gone.id)}},
        )
        assert refused.is_error
