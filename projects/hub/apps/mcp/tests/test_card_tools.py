"""The CV and the card tools: the CV's text, the card, and a regeneration that asks the model."""

from uuid import UUID

from fakes_cards import CARD, MODEL, card_response
from mcp import Client
from mcp_tool_support import (
    IVAN,
    _payload,
    _seed,
    _seed_admin,
    _seed_freelancer_with_text_cv,
    _wipe,
)
from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

from rebase_core.llm import RecordingCall
from rebase_core.models import AdminAction, User
from rebase_core.perks import guide_bytes
from rebase_mcp.server import build_server


def _ensure_admin(session: Session) -> UUID:
    """IVAN as a `users` row: a note and a trail entry name who wrote them, by foreign key."""
    if session.get(User, IVAN.id) is None:
        session.add(
            User(
                id=IVAN.id,
                email=IVAN.email,
                first_name=IVAN.first_name,
                last_name="Sala",
                role="admin",
            )
        )
        session.commit()
    return IVAN.id


async def test_the_cv_is_read_as_text_and_a_card_without_one_answers_a_sentence(
    factory: sessionmaker[Session],
) -> None:
    from decimal import Decimal

    from rebase_core.freelancer_schemas import FreelancerCreate, FreelancerDraft
    from rebase_core.freelancers import FreelancerService

    session = factory()
    try:
        with_cv, _ = FreelancerService(session).apply(
            FreelancerCreate(
                first_name="Ada",
                last_name="Lovelace",
                email="ada@studio.it",
                daily_rate=Decimal("450"),
                job_title="Backend developer",
                remote="remote",
            ),
            guide_bytes(),
            "cv.pdf",
            "application/pdf",
        )
    finally:
        session.close()
    _seed(factory, ["grace@studio.it"])
    session = factory()
    try:
        signup_id = session.execute(text("SELECT id FROM signups")).scalar_one()
        without_cv = FreelancerService(session).draft_from_signup(
            signup_id,
            FreelancerDraft(
                first_name="Grace",
                last_name="Hopper",
                job_title="Compiler engineer",
                sources=["https://example.com/grace"],
            ),
            _ensure_admin(session),
            "mcp",
        )
    finally:
        session.close()

    async with Client(build_server(factory, lambda: IVAN)) as client:
        read = _payload(
            await client.call_tool("read_freelancer_cv", {"freelancer_id": str(with_cv.id)})
        )
        assert read["filename"] == "cv.pdf"
        assert read["pages"] == 6
        assert "prima fattura" in read["text"]
        assert read["truncated"] is False

        refused = await client.call_tool(
            "read_freelancer_cv", {"freelancer_id": str(without_cv.id)}
        )
        assert refused.is_error
        assert "cv" in refused.content[0].text.lower()

        listed = _payload(await client.call_tool("list_talents", {}))
        assert all("text" not in item and "cv_bytes" not in item for item in listed["items"])
    _wipe(factory)


async def test_the_model_is_asked_on_each_call_not_when_the_server_is_built(
    factory: sessionmaker[Session],
) -> None:
    """REB-831: an admin can configure a model in «Modelli AI» while the MCP server
    runs, so the tool asks for the seam on each call: off first, a card after."""
    freelancer_id = _seed_freelancer_with_text_cv(
        factory, "Ada Lovelace, backend developer a Torino da nove anni: Python, FastAPI, AWS."
    )
    _seed_admin(factory)
    configured: list[RecordingCall] = []
    source = lambda: configured[0] if configured else None  # noqa: E731
    async with Client(build_server(factory, lambda: IVAN, card_llm=source)) as client:
        off = await client.call_tool("regenerate_freelancer_card", {"freelancer_id": freelancer_id})
        assert off.is_error and "Il team builder è spento." in off.content[0].text

        configured.append(RecordingCall([card_response()]))
        written = _payload(
            await client.call_tool("regenerate_freelancer_card", {"freelancer_id": freelancer_id})
        )
        assert written["card"]["role"] == CARD["role"]
    # REB-861: signed in the talent's trail, as the admin behind the token, by the MCP door.
    with factory() as session:
        [action] = session.scalars(
            select(AdminAction).where(AdminAction.kind == "card_regenerated")
        ).all()
    assert (action.entity_type, str(action.entity_id), action.admin_id) == (
        "freelancer",
        freelancer_id,
        IVAN.id,
    )
    assert action.payload["source"] == "mcp" and action.payload["bulk"] is False
    assert (action.payload["outcome"], action.payload["model"]) == ("written", MODEL)
    assert "start_action_id" not in action.payload
    _wipe(factory)


async def test_freelancer_card_tools_read_write_and_off_is_the_sentence(
    factory: sessionmaker[Session],
) -> None:
    freelancer_id = _seed_freelancer_with_text_cv(
        factory, "Ada Lovelace, backend developer a Torino da nove anni: Python, FastAPI, AWS."
    )
    async with Client(build_server(factory, lambda: IVAN)) as client:
        empty = _payload(
            await client.call_tool("get_freelancer_card", {"freelancer_id": freelancer_id})
        )
        assert empty["card"] is None and empty["error"] is None
        assert empty["mode"] == "remote"

        off = await client.call_tool("regenerate_freelancer_card", {"freelancer_id": freelancer_id})
        assert off.is_error
        assert "Il team builder è spento." in off.content[0].text

    _seed_admin(factory)
    llm = RecordingCall([card_response(), card_response()])
    async with Client(build_server(factory, lambda: IVAN, card_llm=lambda: llm)) as client:
        written = _payload(
            await client.call_tool("regenerate_freelancer_card", {"freelancer_id": freelancer_id})
        )
        assert written["card"]["role"] == CARD["role"]
        assert written["model"] == MODEL
        assert len(llm.requests) == 1

        read_back = _payload(
            await client.call_tool("get_freelancer_card", {"freelancer_id": freelancer_id})
        )
        assert read_back["card"] == written["card"]
        assert (written["card"]["seniority"], written["card"]["experience_years"]) == ("senior", 9)
        assert (written["seniority_admin"], written["experience_years_admin"]) == (None, None)

        # REB-799: the admin's level beats Claude's in the card read and survives a
        # regenerate; an empty string takes it off again.
        await client.call_tool(
            "override_freelancer",
            {"freelancer_id": freelancer_id, "seniority": "junior", "experience_years": "2"},
        )
        corrected = _payload(
            await client.call_tool("get_freelancer_card", {"freelancer_id": freelancer_id})
        )
        assert (corrected["card"]["seniority"], corrected["card"]["experience_years"]) == (
            "junior",
            2,
        )
        assert (corrected["seniority_admin"], corrected["experience_years_admin"]) == ("junior", 2)
        again = _payload(
            await client.call_tool("regenerate_freelancer_card", {"freelancer_id": freelancer_id})
        )
        assert (again["card"]["seniority"], again["card"]["experience_years"]) == ("junior", 2)
        await client.call_tool(
            "override_freelancer",
            {"freelancer_id": freelancer_id, "seniority": "", "experience_years": ""},
        )
        cleared = _payload(
            await client.call_tool("get_freelancer_card", {"freelancer_id": freelancer_id})
        )
        assert (cleared["card"]["seniority"], cleared["card"]["experience_years"]) == ("senior", 9)
        refused = await client.call_tool(
            "override_freelancer", {"freelancer_id": freelancer_id, "experience_years": "molti"}
        )
        assert refused.is_error

        listed = _payload(await client.call_tool("list_talents", {"fields": ["all"]}))
        assert listed["items"][0]["has_anonymous_card"] is True
        detail = _payload(await client.call_tool("get_talent", {"talent_id": freelancer_id}))
        assert detail["has_anonymous_card"] is True

        missing = await client.call_tool(
            "get_freelancer_card", {"freelancer_id": "01a00000-0000-7000-8000-0000000000fe"}
        )
        assert missing.is_error
    _wipe(factory)
