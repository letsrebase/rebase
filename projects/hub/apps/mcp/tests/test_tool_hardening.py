"""What REB-803 and REB-892 ask of every tool: a malformed id is an Italian sentence, a
closed set is checked before the service, free text is bounded, what spends the model is
capped per admin, and a read of personal data leaves a line in the trail."""

from collections.abc import Iterator

import pytest
from fakes_cards import card_response
from mcp import Client
from mcp_tool_support import (
    IVAN,
    _payload,
    _seed_admin,
    _seed_company,
    _seed_freelancer,
    _seed_freelancer_with_text_cv,
    _wipe_team,
)
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from rebase_core.llm import RecordingCall
from rebase_core.models import AdminAction
from rebase_core.ratelimit import RateLimiter
from rebase_core.search import SEARCH_MAX_LENGTH
from rebase_mcp.server import build_server
from rebase_mcp.tools_common import CV_READ, EMAILS_READ, READ_ENTITY, read_entity_id

NOT_AN_ID = "non-un-uuid"


@pytest.fixture(autouse=True)
def clean(factory: sessionmaker[Session]) -> Iterator[sessionmaker[Session]]:
    _wipe_team(factory)
    yield factory
    _wipe_team(factory)


@pytest.mark.parametrize(
    ("tool", "field"),
    [
        ("get_freelancer", "freelancer_id"),
        ("read_freelancer_cv", "freelancer_id"),
        ("set_freelancer_status", "freelancer_id"),
        ("get_talent", "talent_id"),
        ("get_company", "company_id"),
        ("get_campaign", "campaign_id"),
        ("get_team_request", "request_id"),
        ("delete_note", "note_id"),
    ],
)
async def test_a_malformed_id_is_an_italian_sentence_naming_the_parameter(
    factory: sessionmaker[Session], tool: str, field: str
) -> None:
    """The service never sees it: `UUID(...)` used to raise a bare `ValueError` whose
    English text reached the agent."""
    arguments = {field: NOT_AN_ID}
    if tool == "set_freelancer_status":
        arguments["status"] = "active"
    async with Client(build_server(factory, lambda: IVAN)) as client:
        result = await client.call_tool(tool, arguments)
    assert result.is_error
    text = result.content[0].text  # type: ignore[union-attr]
    assert f"{field}: «{NOT_AN_ID}» is not a valid id" in text
    assert "hexadecimal" not in text and "ValueError" not in text


async def test_a_long_malformed_id_is_not_echoed_whole(factory: sessionmaker[Session]) -> None:
    async with Client(build_server(factory, lambda: IVAN)) as client:
        result = await client.call_tool("get_company", {"company_id": "x" * 5000})
    assert result.is_error and len(result.content[0].text) < 200  # type: ignore[union-attr]


async def test_remote_is_one_of_its_three_words_before_to_card_is_drafted(
    factory: sessionmaker[Session],
) -> None:
    async with Client(build_server(factory, lambda: IVAN)) as client:
        result = await client.call_tool(
            "create_freelancer_from_signup",
            {
                "signup_id": "01a00000-0000-7000-8000-000000000009",
                "first_name": "Ada",
                "last_name": "Lovelace",
                "sources": ["https://www.linkedin.com/in/ada"],
                "remote": "a casa",
            },
        )
    assert result.is_error
    text = result.content[0].text  # type: ignore[union-attr]
    assert "remote" in text and "on_site" in text and "Traceback" not in text


@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        ("list_talents", {"job_title": "x" * (SEARCH_MAX_LENGTH + 1)}),
        ("list_talents", {"utm_source": "x" * (SEARCH_MAX_LENGTH + 1)}),
        ("list_talents", {"origin_page": "x" * (SEARCH_MAX_LENGTH + 1)}),
        ("list_talents", {"cursor": "x" * 10_000}),
        ("list_companies", {"origin": "x" * (SEARCH_MAX_LENGTH + 1)}),
        ("list_companies", {"cursor": "x" * 10_000}),
        ("list_campaigns", {"cursor": "x" * 10_000}),
    ],
)
async def test_a_free_text_filter_past_its_bound_is_refused_before_a_query(
    factory: sessionmaker[Session], tool: str, arguments: dict[str, str]
) -> None:
    async with Client(build_server(factory, lambda: IVAN)) as client:
        result = await client.call_tool(tool, arguments)
    assert result.is_error
    assert "at most" in result.content[0].text  # type: ignore[union-attr]


async def test_what_asks_the_model_is_capped_per_admin(factory: sessionmaker[Session]) -> None:
    """REB-803: a token in a loop must not spend the day's euro cap. Two regenerations
    fit the allowance this server was built with; the third is a sentence, and nothing
    was asked of the model for it."""
    freelancer_id = _seed_freelancer_with_text_cv(
        factory, "Ada Lovelace, backend developer a Torino da nove anni: Python, FastAPI, AWS."
    )
    _seed_admin(factory)
    llm = RecordingCall([card_response(), card_response(), card_response()])
    server = build_server(
        factory, lambda: IVAN, card_llm=lambda: llm, model_calls=RateLimiter(2, 3600)
    )
    async with Client(server) as client:
        for _ in range(2):
            ok = await client.call_tool(
                "regenerate_freelancer_card", {"freelancer_id": freelancer_id}
            )
            assert not ok.is_error
        asked = len(llm.requests)
        refused = await client.call_tool(
            "regenerate_freelancer_card", {"freelancer_id": freelancer_id}
        )
    assert refused.is_error
    assert "Too many requests" in refused.content[0].text  # type: ignore[union-attr]
    assert len(llm.requests) == asked


async def test_the_allowance_is_the_calling_admins_own(factory: sessionmaker[Session]) -> None:
    """Another admin's calls do not use up this one's hour."""
    freelancer_id = _seed_freelancer_with_text_cv(
        factory, "Ada Lovelace, backend developer a Torino da nove anni: Python, FastAPI, AWS."
    )
    _seed_admin(factory)
    limiter = RateLimiter(1, 3600)
    assert limiter.spend("somebody-else")
    llm = RecordingCall([card_response()])
    server = build_server(factory, lambda: IVAN, card_llm=lambda: llm, model_calls=limiter)
    async with Client(server) as client:
        result = await client.call_tool(
            "regenerate_freelancer_card", {"freelancer_id": freelancer_id}
        )
    assert not result.is_error


async def test_a_cv_read_is_in_the_trail_without_the_text(factory: sessionmaker[Session]) -> None:
    freelancer_id = _seed_freelancer_with_text_cv(
        factory, "Ada Lovelace, backend developer a Torino da nove anni: Python, FastAPI, AWS."
    )
    _seed_admin(factory)
    async with Client(build_server(factory, lambda: IVAN)) as client:
        read = _payload(
            await client.call_tool("read_freelancer_cv", {"freelancer_id": freelancer_id})
        )
        trail = await client.call_tool("get_freelancer_audit", {"freelancer_id": freelancer_id})
    assert "Python" in read["text"]
    with factory() as session:
        [action] = session.scalars(select(AdminAction).where(AdminAction.kind == CV_READ)).all()
    assert (action.entity_type, str(action.entity_id), action.admin_id) == (
        "freelancer",
        freelancer_id,
        IVAN.id,
    )
    assert action.payload == {"source": "mcp"}
    assert not trail.is_error and CV_READ in trail.content[0].text  # type: ignore[union-attr]


async def test_a_list_that_carries_addresses_is_in_the_trail_and_one_that_does_not_is_not(
    factory: sessionmaker[Session],
) -> None:
    _seed_freelancer(factory)
    _seed_company(factory)
    _seed_admin(factory)
    async with Client(build_server(factory, lambda: IVAN)) as client:
        await client.call_tool("list_talents", {})
        await client.call_tool("list_talents", {"fields": ["band"]})
        assert _read_lines(factory, "list_talents") == []
        await client.call_tool("list_talents", {"fields": ["email"]})
        await client.call_tool("list_companies", {})
    [talents] = _read_lines(factory, "list_talents")
    [companies] = _read_lines(factory, "list_companies")
    assert talents.admin_id == companies.admin_id == IVAN.id
    assert talents.entity_type == READ_ENTITY and talents.kind == EMAILS_READ
    assert talents.payload == {"source": "mcp", "tool": "list_talents", "lines": 1}
    assert companies.payload["lines"] == 1


def _read_lines(factory: sessionmaker[Session], tool: str) -> list[AdminAction]:
    with factory() as session:
        return list(
            session.scalars(
                select(AdminAction).where(
                    AdminAction.entity_type == READ_ENTITY,
                    AdminAction.entity_id == read_entity_id(tool),
                )
            ).all()
        )


async def test_a_well_formed_id_nobody_has_is_not_found_not_an_id_error(
    factory: sessionmaker[Session],
) -> None:
    async with Client(build_server(factory, lambda: IVAN)) as client:
        result = await client.call_tool(
            "get_talent", {"talent_id": "01a00000-0000-7000-8000-0000000000aa"}
        )
    assert result.is_error
    assert "identificativo" not in result.content[0].text  # type: ignore[union-attr]
