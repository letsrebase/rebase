"""The statistics tools (guide downloads, logins) and what no tool does on somebody's behalf."""

from datetime import UTC, datetime
from uuid import UUID

from mcp import Client
from mcp_tool_support import IVAN, _payload, _seed_freelancer, _wipe
from sqlalchemy.orm import Session, sessionmaker

from rebase_mcp.server import build_server


async def test_no_tool_subscribes_or_applies_on_somebody_elses_behalf(
    factory: sessionmaker[Session],
) -> None:
    async with Client(build_server(factory, lambda: IVAN)) as client:
        names = {tool.name for tool in (await client.list_tools()).tools}
    # `team_request` (REB-520) is legitimate: an admin's own screen on a request a
    # company or the public page already filed, never a way to file one as if the
    # applicant had.
    assert not [
        name
        for name in names
        if "subscribe" in name
        or "apply" in name
        or ("request" in name and "team_request" not in name)
    ]


async def test_the_guide_stats_are_zero_on_an_empty_hub_and_read_only(
    factory: sessionmaker[Session],
) -> None:
    """ORB-156: the counter the admin area shows is one tool away for an agent too."""
    async with Client(build_server(factory, lambda: IVAN)) as client:
        result = await client.call_tool("guide_stats", {})
        names = {tool.name for tool in (await client.list_tools()).tools}
    body = _payload(result)
    assert body == {
        "total": 0,
        "members": 0,
        "total_members": 0,
        "last_7_days": 0,
        "recent": [],
    }
    assert "record_guide_download" not in names


async def test_login_stats_reads_an_empty_hub_and_a_card_carries_its_count(
    factory: sessionmaker[Session],
) -> None:
    freelancer_id = _seed_freelancer(factory)
    async with Client(build_server(factory, lambda: IVAN)) as client:
        stats = _payload(await client.call_tool("login_stats", {}))
        assert (stats["total"], stats["members"], stats["total_members"]) == (0, 0, 1)
        assert stats["recent"] == []
        card = _payload(await client.call_tool("get_freelancer", {"freelancer_id": freelancer_id}))
        assert card["logins"] == 0 and card["last_access"] is None
    _wipe(factory)


async def test_login_stats_says_which_campaign_each_login_came_from(
    factory: sessionmaker[Session],
) -> None:
    """REB-426: a login that started from a tracked link carries its campaign, one that
    did not carries `None`, in the same keys the wizards' rows use."""
    from rebase_core.models import Freelancer, Login

    freelancer_id = _seed_freelancer(factory)
    session = factory()
    try:
        card = session.get(Freelancer, UUID(freelancer_id))
        assert card is not None
        session.add(Login(user_id=card.user_id, logged_at=datetime(2026, 9, 24, 9, tzinfo=UTC)))
        session.add(
            Login(
                user_id=card.user_id,
                logged_at=datetime(2026, 9, 25, 9, tzinfo=UTC),
                utm_source="email",
                utm_campaign="outreach-2026-09-r2",
                utm_content="cv",
                utm_term="11425b70",
            )
        )
        session.commit()
    finally:
        session.close()
    async with Client(build_server(factory, lambda: IVAN)) as client:
        recent = _payload(await client.call_tool("login_stats", {}))["recent"]
        assert [row["utm_campaign"] for row in recent] == ["outreach-2026-09-r2", None]
        assert (recent[0]["utm_source"], recent[0]["utm_content"], recent[0]["utm_term"]) == (
            "email",
            "cv",
            "11425b70",
        )
        assert recent[1]["utm_term"] is None and recent[1]["origin"] is None
    _wipe(factory)
