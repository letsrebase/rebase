"""The collaborators tools over the MCP client (REB-976): they exist only while the module is
on, an administrator's token reads and updates a draft, creating, cancelling and closing need
`full_access`, and the overlap warning and the availability by week come back as the tools
answer them.
"""

import json
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from mcp import Client
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.collaborators.schemas import EngagementCreate
from pigrocrm.core.collaborators.service import EngagementService
from pigrocrm.core.config import Settings
from pigrocrm.core.legal_entities.repository import LegalEntityRepository
from pigrocrm.core.organizations.models import Organization
from pigrocrm.core.storage import LocalFileStorage
from pigrocrm_mcp.server import build_server

TOOLS = {
    "create_engagement",
    "update_engagement",
    "get_engagement",
    "get_engagement_report",
    "list_engagements",
    "cancel_engagement",
    "close_engagement",
    "get_freelancer_availability",
    "set_freelancer_tax_data",
}
PRIVILEGED = {
    "create_engagement",
    "cancel_engagement",
    "close_engagement",
    "set_freelancer_tax_data",
}
PERSON = Actor(id=uuid4(), type="user", role="admin")


def _server(session: Session, tmp_path: Path, *, full_access: bool = False, **settings: Any) -> Any:
    return build_server(
        lambda: session,
        lambda: Actor(id=uuid4(), type="mcp", role="admin", full_access=full_access),
        LocalFileStorage(tmp_path),
        Settings(_env_file=None, mcp_full_access=full_access, **settings),  # type: ignore[call-arg]
    )


def _payload(result: Any) -> dict[str, Any]:
    return result.structured_content or json.loads(result.content[0].text)


def _freelancer(session: Session) -> Organization:
    entity = LegalEntityRepository(session).default()
    assert entity is not None
    who = Organization(
        legal_name=f"Ada {uuid4()}",
        legal_entity_id=entity.id,
        roles=["freelancer"],
        hub_freelancer_id=uuid4(),
    )
    session.add(who)
    session.commit()
    return who


def _args(who: Organization, **overrides: Any) -> dict[str, Any]:
    args: dict[str, Any] = {
        "freelancer_id": str(who.id),
        "role": "Backend developer",
        "starts_on": "2026-11-02",
        "ends_on": "2026-11-29",
        "days_per_week": 3,
        "fee": 450,
        "fee_unit": "day",
        "payment_rule": "fixed_term",
        "payment_days": 30,
    }
    args.update(overrides)
    return args


def _activate(session: Session, engagement_id: str) -> None:
    service = EngagementService(session)
    service.transition(UUID(engagement_id), "awaiting_signature", PERSON)
    service.transition(UUID(engagement_id), "active", PERSON)


async def test_the_tools_exist_only_while_the_module_is_on(
    mcp_session: Session, tmp_path: Path
) -> None:
    off = await _server(mcp_session, tmp_path).list_tools()
    assert not {tool.name for tool in off} & TOOLS
    on = await _server(mcp_session, tmp_path, module_collaborators=True).list_tools()
    assert {tool.name for tool in on} >= TOOLS - PRIVILEGED
    # Creating, cancelling and closing an engagement and the tax data also need `mcp_full_access`.
    full = _server(mcp_session, tmp_path, full_access=True, module_collaborators=True)
    assert {tool.name for tool in await full.list_tools()} >= TOOLS
    assert not {tool.name for tool in on} & PRIVILEGED


async def test_two_overlapping_engagements_warn_and_availability_shows_the_committed_days(
    mcp_session: Session, tmp_path: Path
) -> None:
    """The card's «done when», over MCP."""
    who = _freelancer(mcp_session)
    server = _server(mcp_session, tmp_path, full_access=True, module_collaborators=True)
    async with Client(server) as client:
        first = _payload(await client.call_tool("create_engagement", _args(who)))
        second = _payload(
            await client.call_tool(
                "create_engagement",
                _args(who, starts_on="2026-11-16", ends_on="2026-12-13", days_per_week=2),
            )
        )
        for made in (first, second):
            _activate(mcp_session, made["id"])
        answer = _payload(
            await client.call_tool(
                "get_freelancer_availability",
                {
                    "hub_freelancer_id": str(who.hub_freelancer_id),
                    "date_from": "2026-11-02",
                    "date_to": "2026-12-06",
                },
            )
        )

    assert first["state"] == "draft" and first["warnings"] == []
    assert [w["code"] for w in second["warnings"]] == ["overlap"]
    assert second["warnings"][0]["peak_days_per_week"] == "5.0"
    assert "provenance" in second
    assert [(w["week"], w["committed_days"], w["status"]) for w in answer["weeks"]] == [
        ("2026-W45", "3.0", "committed"),
        ("2026-W46", "3.0", "committed"),
        ("2026-W47", "5.0", "committed"),
        ("2026-W48", "5.0", "committed"),
        ("2026-W49", "2.0", "committed"),
    ]


async def test_an_engagement_without_days_per_week_is_sconosciuta(
    mcp_session: Session, tmp_path: Path
) -> None:
    who = _freelancer(mcp_session)
    async with Client(
        _server(mcp_session, tmp_path, full_access=True, module_collaborators=True)
    ) as client:
        made = _payload(
            await client.call_tool("create_engagement", _args(who, ends_on="2026-11-08"))
        )
        updated = _payload(
            await client.call_tool(
                "update_engagement",
                {"engagement_id": made["id"], "changes": {"days_per_week": None}},
            )
        )
        _activate(mcp_session, made["id"])
        week = _payload(
            await client.call_tool(
                "get_freelancer_availability",
                {"freelancer_id": str(who.id), "date_from": "2026-11-02", "date_to": "2026-11-08"},
            )
        )["weeks"][0]

    assert "days_per_week" in updated["changed"] and updated["missing"] == [
        "days_per_week",
        "client",
    ]
    assert (week["status"], week["label"]) == ("unknown", "sconosciuta")


async def test_a_default_token_reads_and_updates_a_draft_but_cannot_create_or_end(
    mcp_session: Session, tmp_path: Path
) -> None:
    who = _freelancer(mcp_session)
    async with Client(
        _server(mcp_session, tmp_path, full_access=True, module_collaborators=True)
    ) as full:
        made = _payload(await full.call_tool("create_engagement", _args(who)))

    async with Client(_server(mcp_session, tmp_path, module_collaborators=True)) as client:
        read = _payload(await client.call_tool("get_engagement", {"engagement_id": made["id"]}))
        listed = _payload(await client.call_tool("list_engagements", {"state": "draft"}))
        updated = _payload(
            await client.call_tool(
                "update_engagement",
                {"engagement_id": made["id"], "changes": {"role": "Tech lead"}},
            )
        )
        availability = _payload(
            await client.call_tool("get_freelancer_availability", {"freelancer_id": str(who.id)})
        )
        refused = [
            await client.call_tool("create_engagement", _args(who)),
            await client.call_tool("cancel_engagement", {"engagement_id": made["id"]}),
            await client.call_tool("close_engagement", {"engagement_id": made["id"]}),
            await client.call_tool(
                "set_freelancer_tax_data", {"freelancer_id": str(who.id), "tax_code": "X"}
            ),
        ]

    assert read["id"] == made["id"] and "provenance" in read
    assert made["id"] in {item["id"] for item in listed["items"]}
    assert updated["role"] == "Tech lead" and updated["changed"] == ["role"]
    assert len(availability["weeks"]) == 12
    for result in refused:
        assert result.is_error, result.content


async def test_a_full_access_token_cancels_and_closes_and_sets_the_tax_data(
    mcp_session: Session, tmp_path: Path
) -> None:
    who = _freelancer(mcp_session)
    async with Client(
        _server(mcp_session, tmp_path, full_access=True, module_collaborators=True)
    ) as client:
        draft = _payload(await client.call_tool("create_engagement", _args(who)))
        active = _payload(await client.call_tool("create_engagement", _args(who, ends_on=None)))
        _activate(mcp_session, active["id"])
        cancelled = _payload(
            await client.call_tool("cancel_engagement", {"engagement_id": draft["id"]})
        )
        closed = _payload(
            await client.call_tool(
                "close_engagement", {"engagement_id": active["id"], "ended_on": "2026-12-04"}
            )
        )
        tax = _payload(
            await client.call_tool(
                "set_freelancer_tax_data",
                {
                    "freelancer_id": str(who.id),
                    "tax_code": "RSSMRA80A01H501U",
                    "fiscal_kind": "flat_rate",
                },
            )
        )
        again = await client.call_tool("cancel_engagement", {"engagement_id": draft["id"]})

    assert cancelled["state"] == "cancelled"
    assert (closed["state"], closed["ends_on"]) == ("concluded", "2026-12-04")
    assert (tax["tax_code"], tax["supplier_kind"]) == ("RSSMRA80A01H501U", "flat_rate")
    assert again.is_error and "non si passa" in again.content[0].text  # type: ignore[union-attr]


async def test_a_malformed_argument_comes_back_as_guidance(
    mcp_session: Session, tmp_path: Path
) -> None:
    who = _freelancer(mcp_session)
    async with Client(
        _server(mcp_session, tmp_path, full_access=True, module_collaborators=True)
    ) as client:
        results = [
            await client.call_tool("create_engagement", _args(who, days_per_week=0.7)),
            await client.call_tool("create_engagement", _args(who, starts_on="ieri")),
            await client.call_tool("create_engagement", _args(who, payment_days=61)),
            await client.call_tool("create_engagement", _args(who, freelancer_id="x")),
            await client.call_tool("get_engagement", {"engagement_id": str(uuid4())}),
            await client.call_tool("list_engagements", {"state": "paid"}),
            await client.call_tool(
                "update_engagement", {"engagement_id": str(uuid4()), "changes": {}}
            ),
        ]

    for refused in results:
        assert refused.is_error
        assert "errors.pydantic.dev" not in refused.content[0].text  # type: ignore[union-attr]


async def test_a_fee_too_long_for_its_column_comes_back_as_guidance(
    mcp_session: Session, tmp_path: Path
) -> None:
    who = _freelancer(mcp_session)
    async with Client(
        _server(mcp_session, tmp_path, full_access=True, module_collaborators=True)
    ) as client:
        refused = await client.call_tool("create_engagement", _args(who, fee="1" + "0" * 12))

    assert refused.is_error
    assert "errors.pydantic.dev" not in refused.content[0].text  # type: ignore[union-attr]


async def test_the_report_is_a_read_and_no_tool_records_or_approves_a_monthly_report(
    mcp_session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """REB-1002: an agent reads the days and what is payable; approving a month is a person's
    act, so no tool, with or without `full_access`, names it."""
    import pigrocrm.core.collaborators.days_report as days_report
    from pigrocrm.core.errors import NotFound

    class NotSetUp:
        def __init__(self, *_: Any) -> None: ...

        def report(self, *_: Any) -> None:
            raise NotFound("engagement", "x")

    monkeypatch.setattr(days_report, "EngagementService", NotSetUp)
    who = _freelancer(mcp_session)
    made = EngagementService(mcp_session).create(
        EngagementCreate.model_validate(_args(who)), PERSON
    )
    _activate(mcp_session, str(made.id))
    elsewhere = _server(mcp_session, tmp_path, module_collaborators=True)
    leads = _server(
        mcp_session,
        tmp_path,
        full_access=True,
        module_collaborators=True,
        leads_space="rebase",
        root_slug="rebase",
    )
    async with Client(elsewhere) as client:
        refused = await client.call_tool("get_engagement_report", {"engagement_id": str(made.id)})
    async with Client(leads) as client:
        report = _payload(
            await client.call_tool("get_engagement_report", {"engagement_id": str(made.id)})
        )
        names = {tool.name for tool in await client.list_tools()}

    assert refused.is_error and "PIGROCRM_LEADS_SPACE" in refused.content[0].text  # type: ignore[union-attr]
    assert report["available"] is False and report["days"] == [] and "provenance" in report
    assert not {name for name in names if "monthly_report" in name or "approve" in name}
