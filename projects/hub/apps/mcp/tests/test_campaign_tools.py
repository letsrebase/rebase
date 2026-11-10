"""The campaigns, read over the hub's MCP server (P-REB-41 phase 2): the list with its
numbers, one campaign with each person's outcome, and nothing that sends."""

import base64
import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from mcp import Client
from sqlalchemy import delete, select
from sqlalchemy.orm import Session, sessionmaker

from rebase_core.admin_tokens import AdminRead
from rebase_core.models import AdminAction, Campaign, CampaignRecipient, User
from rebase_mcp.server import build_server

IVAN = AdminRead(
    id=UUID("01a00000-0000-7000-8000-000000000001"),
    email="ivan@rebase.it",
    first_name="Ivan",
    active=True,
    created_at=datetime(2026, 9, 10, tzinfo=UTC),
)
SENT = datetime(2026, 9, 25, 7, 30, tzinfo=UTC)


def _payload(result: Any) -> dict[str, Any]:
    return result.structured_content or json.loads(result.content[0].text)


def _seed(factory: sessionmaker[Session]) -> tuple[UUID, UUID]:
    session = factory()
    try:
        owner = User(email="campagne-mcp@rebase.it", first_name="Ivan", last_name="S", role="admin")
        session.add(owner)
        session.flush()
        campaign = Campaign(
            created_by=owner.id,
            name="Manca il CV",
            slug="c-mcp-manca-il-cv",
            source="status",
            journey_status="missing_cv",
            subject="o",
            text="t",
            button_text="b",
            button_meta="area",
            action="cv",
            status="sent",
            content_at=SENT,
            sent_at=SENT,
        )
        session.add(campaign)
        session.flush()
        later = SENT + timedelta(hours=2)
        for email, done in (("ada@studio.it", later), ("bob@studio.it", None)):
            session.add(
                CampaignRecipient(
                    campaign_id=campaign.id,
                    email=email,
                    kind="freelancer",
                    code="1",
                    before={},
                    unsubscribe_token=f"t-{email}",
                    status="sent",
                    sent_at=SENT,
                    first_click_at=later,
                    signed_in_at=later,
                    action_at=done,
                )
            )
        session.commit()
        return campaign.id, owner.id
    finally:
        session.close()


def _drop(factory: sessionmaker[Session], campaign_id: UUID, owner_id: UUID) -> None:
    session = factory()
    try:
        session.execute(
            delete(CampaignRecipient).where(CampaignRecipient.campaign_id == campaign_id)
        )
        session.execute(delete(Campaign).where(Campaign.id == campaign_id))
        session.execute(delete(User).where(User.id == owner_id))
        session.commit()
    finally:
        session.close()


async def test_an_agent_reads_the_campaigns_and_each_persons_outcome(
    factory: sessionmaker[Session],
) -> None:
    campaign_id, owner_id = _seed(factory)
    try:
        async with Client(build_server(factory, lambda: IVAN)) as client:
            listed = _payload(await client.call_tool("list_campaigns", {}))
            one = _payload(
                await client.call_tool("get_campaign", {"campaign_id": str(campaign_id)})
            )
            names = {tool.name for tool in (await client.list_tools()).tools}
    finally:
        _drop(factory, campaign_id, owner_id)
    item = next(i for i in listed["items"] if i["id"] == str(campaign_id))
    counts = item["counts"]
    assert (counts["sent"], counts["clicked"], counts["entered"], counts["actions"]) == (
        2,
        2,
        2,
        1,
    )
    people = {p["email"]: p for p in one["recipients"]}
    assert people["ada@studio.it"]["action_at"] is not None
    assert people["bob@studio.it"]["action_at"] is None
    assert one["campaign"]["name"] == "Manca il CV"
    # Read-only: nothing here sends, schedules or cancels a campaign.
    assert not [n for n in names if "campagn" in n and n not in ("list_campaigns", "get_campaign")]


async def test_an_unknown_campaign_is_a_sentence(factory: sessionmaker[Session]) -> None:
    async with Client(build_server(factory, lambda: IVAN)) as client:
        result = await client.call_tool(
            "get_campaign", {"campaign_id": "01a00000-0000-7000-8000-00000000dead"}
        )
    assert result.is_error


async def test_a_link_campaign_shows_where_its_button_leads(
    factory: sessionmaker[Session],
) -> None:
    """REB-530: «Un link» reads back as the destination and its address, in the list and
    in the detail alike, with the click as its action."""
    session = factory()
    try:
        owner = User(
            email="campagne-link-mcp@rebase.it", first_name="Ivan", last_name="S", role="admin"
        )
        session.add(owner)
        session.flush()
        campaign = Campaign(
            created_by=owner.id,
            name="Casa",
            slug="c-mcp-casa",
            source="status",
            journey_status="complete",
            subject="o",
            text="t",
            button_text="Iscriviti",
            button_meta="link",
            button_url="https://lu.ma/rebase-house",
            action="click",
            status="draft",
            content_at=SENT,
        )
        session.add(campaign)
        session.commit()
        campaign_id, owner_id = campaign.id, owner.id
    finally:
        session.close()
    try:
        async with Client(build_server(factory, lambda: IVAN)) as client:
            listed = _payload(await client.call_tool("list_campaigns", {}))
            one = _payload(
                await client.call_tool("get_campaign", {"campaign_id": str(campaign_id)})
            )
    finally:
        _drop(factory, campaign_id, owner_id)
    item = next(i for i in listed["items"] if i["id"] == str(campaign_id))
    for shown in (item, one["campaign"]):
        assert (shown["button_meta"], shown["button_url"], shown["action"]) == (
            "link",
            "https://lu.ma/rebase-house",
            "click",
        )


def _add_recipients(factory: sessionmaker[Session], campaign_id: UUID, count: int) -> list[str]:
    session = factory()
    try:
        emails = [f"persona{n:02d}@studio.it" for n in range(count)]
        for email in emails:
            session.add(
                CampaignRecipient(
                    campaign_id=campaign_id,
                    email=email,
                    kind="freelancer",
                    code="1",
                    before={},
                    unsubscribe_token=f"t-{email}",
                    status="queued",
                )
            )
        session.commit()
        return emails
    finally:
        session.close()


def _add_campaigns(factory: sessionmaker[Session], owner_id: UUID, count: int) -> list[UUID]:
    session = factory()
    try:
        made = []
        for n in range(count):
            campaign = Campaign(
                created_by=owner_id,
                name=f"Altra {n}",
                slug=f"c-mcp-altra-{n}",
                source="status",
                journey_status="missing_cv",
                subject="o",
                text="t",
                button_text="b",
                button_meta="area",
                action="cv",
                status="draft",
                content_at=SENT,
            )
            session.add(campaign)
            session.flush()
            made.append(campaign.id)
        session.commit()
        return made
    finally:
        session.close()


async def test_the_campaign_tools_are_paged_in_a_fixed_order(
    factory: sessionmaker[Session],
) -> None:
    """REB-804: `list_campaigns` and `get_campaign` take `limit` and `cursor`; walking the
    cursors visits every campaign and every recipient once, in the same order as one big
    read, and the counts stay those of the whole campaign."""
    campaign_id, owner_id = _seed(factory)
    others = _add_campaigns(factory, owner_id, 2)
    emails = _add_recipients(factory, campaign_id, 3)
    try:
        async with Client(build_server(factory, lambda: IVAN)) as client:
            whole = _payload(await client.call_tool("list_campaigns", {}))
            seen: list[str] = []
            cursor = None
            while True:
                args: dict[str, Any] = {"limit": 1}
                if cursor:
                    args["cursor"] = cursor
                page = _payload(await client.call_tool("list_campaigns", args))
                assert len(page["items"]) == 1
                seen += [item["id"] for item in page["items"]]
                cursor = page["next_cursor"]
                if cursor is None:
                    break
            walked: list[str] = []
            cursor = None
            while True:
                args = {"campaign_id": str(campaign_id), "limit": 2}
                if cursor:
                    args["cursor"] = cursor
                page = _payload(await client.call_tool("get_campaign", args))
                assert page["counts"]["recipients"] == 5
                walked += [p["email"] for p in page["recipients"]]
                cursor = page["next_cursor"]
                if cursor is None:
                    break
            everything = _payload(
                await client.call_tool("get_campaign", {"campaign_id": str(campaign_id)})
            )
            foreign = await client.call_tool(
                "get_campaign",
                {"campaign_id": str(others[0]), "cursor": _foreign_cursor()},
            )
            garbage = await client.call_tool(
                "get_campaign", {"campaign_id": str(campaign_id), "cursor": "non-un-cursore"}
            )
            bad_list_cursor = await client.call_tool("list_campaigns", {"cursor": "xx"})
    finally:
        for other in others:
            session = factory()
            session.execute(delete(Campaign).where(Campaign.id == other))
            session.commit()
            session.close()
        _drop(factory, campaign_id, owner_id)
    assert seen == [item["id"] for item in whole["items"]] and len(seen) == 3
    assert walked == sorted(["ada@studio.it", "bob@studio.it", *emails])
    assert [p["email"] for p in everything["recipients"]] == walked
    assert foreign.is_error and "cursore non valido" in foreign.content[0].text
    assert garbage.is_error and "cursore non valido" in garbage.content[0].text
    assert bad_list_cursor.is_error


def _foreign_cursor() -> str:
    """A well-formed cursor naming a recipient that is in no campaign: the tool must
    refuse it rather than read it as «from the start»."""
    return base64.urlsafe_b64encode(uuid4().bytes).decode().rstrip("=")


async def test_reading_a_campaigns_recipients_leaves_a_line_in_the_trail(
    factory: sessionmaker[Session],
) -> None:
    """REB-803: names and addresses of a campaign's recipients are personal data; the
    read is an `AdminAction` of the admin behind the token, without any of them."""
    campaign_id, owner_id = _seed(factory)
    admin = AdminRead(
        id=owner_id, email="campagne-mcp@rebase.it", first_name="Ivan", active=True, created_at=SENT
    )
    try:
        async with Client(build_server(factory, lambda: admin)) as client:
            await client.call_tool("get_campaign", {"campaign_id": str(campaign_id)})
            await client.call_tool("list_campaigns", {})
        with factory() as session:
            [action] = session.scalars(
                select(AdminAction).where(AdminAction.kind == "recipients_read")
            ).all()
            session.execute(delete(AdminAction))
            session.commit()
    finally:
        _drop(factory, campaign_id, owner_id)
    assert (action.entity_type, action.entity_id, action.admin_id) == (
        "campaign",
        campaign_id,
        owner_id,
    )
    assert action.payload == {"source": "mcp", "lines": 2}
