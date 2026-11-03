"""`list_talents` and `get_talent` as an agent matching a request uses them (REB-798,
REB-801): the `origin` filter is the value the row shows, `get_talent` carries the
list's own fields, the filters and the sort reach the service, and a page of rows is
small enough to read."""

import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest
from mcp import Client
from mcp_tool_support import IVAN, PDF, _payload, _seed, _seed_admin, _seed_freelancer, _wipe
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

import rebase_mcp.tools_talents as tools_talents
from rebase_core.errors import ValidationFailed
from rebase_core.freelancer_schemas import FreelancerCreate, FreelancerDraft
from rebase_core.freelancers import FreelancerService
from rebase_core.models import FreelancerCard, User
from rebase_core.service import SignupService
from rebase_core.signup_schemas import SignupCreate, SignupUtm
from rebase_core.talent_schemas import TalentRead
from rebase_mcp.server import build_server
from rebase_mcp.talents_view import (
    SHORT_FIELDS,
    SHORT_PROFILE_SUMMARY,
    SHORT_SKILLS,
    row_fields,
    talent_row,
)


@pytest.fixture(autouse=True)
def clean(factory: sessionmaker[Session]) -> sessionmaker[Session]:
    session = factory()
    for table in ("admin_actions", "notes", "freelancers", "companies", "users", "signups"):
        session.execute(text(f"DELETE FROM {table}"))
    session.commit()
    session.close()
    return factory


def _server(factory: sessionmaker[Session]) -> Client:
    return Client(build_server(factory, lambda: IVAN))


def _card(
    factory: sessionmaker[Session],
    email: str,
    *,
    first_name: str = "Ada",
    rate: str = "450",
    page: str | None = None,
    card: dict[str, Any] | None = None,
) -> str:
    session = factory()
    try:
        row, _ = FreelancerService(session).apply(
            FreelancerCreate(
                first_name=first_name,
                last_name="Lovelace",
                email=email,
                daily_rate=Decimal(rate),
                job_title="Backend developer",
                remote="remote",
                utm=SignupUtm(origin=page) if page else None,
            ),
            PDF,
            "cv.pdf",
            "application/pdf",
        )
        if card is not None:
            session.add(FreelancerCard(freelancer_id=row.id, cv_sha256="0" * 64, card=card))
            session.commit()
        return str(row.id)
    finally:
        session.close()


def _lead(factory: sessionmaker[Session], email: str) -> str:
    session = factory()
    try:
        return str(
            SignupService(session)
            .subscribe(SignupCreate(email=email, first_name="Giulia", last_name="Branda"))
            .id
        )
    finally:
        session.close()


def _admin_id(session: Session) -> UUID:
    admin = User(email="claude@rebase.it", first_name="Claude", last_name="", role="admin")
    session.add(admin)
    session.commit()
    return admin.id


def _admin_draft(factory: sessionmaker[Session], email: str) -> None:
    session = factory()
    try:
        signup = SignupService(session).subscribe(
            SignupCreate(email=email, first_name="Ricerca", last_name="Fatta")
        )
        FreelancerService(session).draft_from_signup(
            signup.id,
            FreelancerDraft(
                first_name="Ricerca",
                last_name="Fatta",
                job_title="Designer",
                links=[],
                sources=["https://www.linkedin.com/in/ricerca"],
            ),
            _admin_id(session),
            "mcp",
        )
    finally:
        session.close()


FULL_CARD: dict[str, Any] = {
    "role": "Data engineer",
    "seniority": "senior",
    "experience_years": 9,
    "skills": [f"Skill {n}" for n in range(1, 13)],
    "industries": ["fintech", "sanità"],
    "languages": ["inglese (C1)", "italiano"],
    "location": "Monza",
    "profile_summary": "Costruisce pipeline dati per aziende finanziarie.",
}


# ---- the shape of a row, without a database --------------------------------------------


def _row(**fields: Any) -> dict[str, Any]:
    row = TalentRead(
        id=UUID(int=1),
        email="ada@studio.it",
        status="new",
        origin="wizard",
        created_at=datetime(2026, 10, 1, tzinfo=UTC),
        **fields,
    )
    return row.model_dump(mode="json")


def test_the_short_row_is_the_card_without_addresses_dates_or_empty_keys() -> None:
    row = talent_row(_row(first_name="Ada", last_name="Lovelace", **FULL_CARD), None)
    assert set(row) <= set(SHORT_FIELDS)
    assert "email" not in row and "linkedin_url" not in row and "created_at" not in row
    assert row["skills"] == [f"Skill {n}" for n in range(1, SHORT_SKILLS + 1)]
    assert "industries" not in row and "languages" not in row
    assert row["profile_summary"] == FULL_CARD["profile_summary"]
    lead = talent_row(_row(first_name="Giulia", last_name="Branda"), None)
    assert set(lead) == {"id", "first_name", "last_name", "status"}


def test_the_short_row_says_when_the_seniority_is_a_guess() -> None:
    """REB-799: a card with no dated years has no `experience_years` in the short row, and says its
    seniority is uncertain; a row that is sure never carries the flag, and an `experience_years` of
    zero is a value."""
    unsure = talent_row(
        _row(seniority="mid", experience_years=None, seniority_uncertain=True), None
    )
    assert unsure["seniority"] == "mid" and "experience_years" not in unsure
    assert unsure["seniority_uncertain"] is True
    sure = talent_row(_row(seniority="junior", experience_years=0), None)
    assert sure["experience_years"] == 0 and "seniority_uncertain" not in sure


def test_a_long_summary_is_cut_at_a_word_in_the_short_row_and_whole_on_request() -> None:
    long = " ".join(["Costruisce pipeline dati per aziende finanziarie."] * 8)
    row = _row(profile_summary=long)
    cut = talent_row(row, None)["profile_summary"]
    assert len(cut) <= SHORT_PROFILE_SUMMARY + 1 and cut.endswith("…") and long.startswith(cut[:-1])
    assert not cut[:-1].endswith(" ")
    assert talent_row(row, row_fields(["profile_summary"]))["profile_summary"] == long


def test_fields_names_the_fields_whole_with_the_id_and_all_is_the_full_row() -> None:
    row = _row(**FULL_CARD)
    picked = talent_row(row, row_fields(["industries", "skills"]))
    assert list(picked) == ["id", "industries", "skills"]
    assert picked["skills"] == FULL_CARD["skills"]
    assert set(talent_row(row, row_fields(["all"]))) == set(TalentRead.model_fields)
    assert row_fields(None) is None and row_fields([]) is None and row_fields([" "]) is None


def test_an_unknown_field_is_refused_with_the_names_that_exist() -> None:
    with pytest.raises(ValidationFailed, match="«phone» non esiste"):
        row_fields(["email", "phone"])
    with pytest.raises(ValidationFailed, match="«phone» non esiste"):
        row_fields(["all", "phone"])


# ---- through the tools -----------------------------------------------------------------


async def test_the_default_row_is_short_and_fields_widens_it(clean: sessionmaker[Session]) -> None:
    _card(clean, "ada@studio.it", card=FULL_CARD)
    _lead(clean, "giulia@studio.it")
    async with _server(clean) as client:
        short = _payload(await client.call_tool("list_talents", {}))
        wide = _payload(await client.call_tool("list_talents", {"fields": ["all"]}))
        some = _payload(await client.call_tool("list_talents", {"fields": ["email", "band"]}))
        bad = await client.call_tool("list_talents", {"fields": ["phone"]})
    by_status = {item["status"]: item for item in short["items"]}
    assert set(by_status["lead"]) == {"id", "first_name", "last_name", "status"}
    ada = by_status["new"]
    assert (
        ada["role"] == "Data engineer"
        and ada["seniority"] == "senior"
        and ada["experience_years"] == 9
    )
    assert ada["daily_rate"] == "450.00" and ada["location"] == "Monza"
    assert len(ada["skills"]) == SHORT_SKILLS and "email" not in ada
    assert {"email", "industries", "languages", "band", "origin"} <= set(
        next(i for i in wide["items"] if i["status"] == "new")
    )
    assert all(list(item) == ["id", "email", "band"] for item in some["items"])
    assert short["total"] == wide["total"] == 2
    assert bad.is_error and "fields" in bad.content[0].text and "phone" in bad.content[0].text


async def test_origin_filters_what_the_row_shows_and_origin_page_the_page(
    clean: sessionmaker[Session],
) -> None:
    _card(clean, "wizard@studio.it", page="home")
    _admin_draft(clean, "ricerca@studio.it")
    _lead(clean, "giulia@studio.it")
    async with _server(clean) as client:
        listed = _payload(await client.call_tool("list_talents", {"fields": ["all"]}))
        for origin, email in (
            ("wizard", "wizard@studio.it"),
            ("admin", "ricerca@studio.it"),
            ("form", "giulia@studio.it"),
        ):
            found = _payload(
                await client.call_tool("list_talents", {"origin": origin, "fields": ["email"]})
            )
            assert [item["email"] for item in found["items"]] == [email], origin
            assert found["total"] == 1
        page = _payload(
            await client.call_tool("list_talents", {"origin_page": "home", "fields": ["email"]})
        )
        refused = await client.call_tool("list_talents", {"origin": "home"})
    assert {item["origin"] for item in listed["items"]} == {"wizard", "admin", "form"}
    assert [item["email"] for item in page["items"]] == ["wizard@studio.it"]
    assert refused.is_error and "form, wizard, admin" in refused.content[0].text


async def test_get_talent_carries_every_field_the_list_row_has(
    clean: sessionmaker[Session],
) -> None:
    """A card and a lead: each field of the full list row is the same in `get_talent`,
    the marketing page has its own key, and the card's `industries`, `languages`, `profile_summary`
    and `band` are there without a third call to `get_freelancer_card`."""
    ada = _card(clean, "ada@studio.it", page="home", card=FULL_CARD)
    lead = _lead(clean, "giulia@studio.it")
    async with _server(clean) as client:
        rows = {
            item["id"]: item
            for item in _payload(await client.call_tool("list_talents", {"fields": ["all"]}))[
                "items"
            ]
        }
        card = _payload(await client.call_tool("get_talent", {"talent_id": ada}))
        bare = _payload(await client.call_tool("get_talent", {"talent_id": lead}))
    for field in TalentRead.model_fields:
        assert card[field] == rows[ada][field], field
        assert bare[field] == rows[lead][field], field
    assert (card["origin"], card["origin_page"]) == ("wizard", "home")
    assert card["industries"] == ["fintech", "sanità"] and card["languages"][0] == "inglese (C1)"
    assert card["profile_summary"] == FULL_CARD["profile_summary"]
    assert card["band"] is not None and card["band"]["min"] <= 450 * 1.3
    assert "note" in card and bare["origin"] == "form" and "origin_page" not in bare


async def test_the_card_filters_and_the_sort_answer_one_request_in_one_call(
    clean: sessionmaker[Session],
) -> None:
    """«Senior, 5+ years, English, under 400 a day, cheapest first.»"""

    def card(**fields: Any) -> dict[str, Any]:
        return {**FULL_CARD, **fields}

    _card(clean, "a@studio.it", first_name="A", rate="380", card=card(experience_years=6))
    _card(clean, "b@studio.it", first_name="B", rate="350", card=card(experience_years=9))
    _card(clean, "c@studio.it", first_name="C", rate="500", card=card())
    _card(clean, "d@studio.it", first_name="D", rate="300", card=card(seniority="mid"))
    _card(clean, "e@studio.it", first_name="E", rate="300", card=card(experience_years=2))
    _card(clean, "f@studio.it", first_name="F", rate="300", card=card(languages=["francese"]))
    request = {
        "seniority": ["senior"],
        "min_years": 5,
        "languages": ["inglese"],
        "industries": ["fintech"],
        "max_rate": "400",
        "sort": "rate_asc",
        "fields": ["email", "daily_rate"],
    }
    async with _server(clean) as client:
        found = _payload(await client.call_tool("list_talents", request))
        first = _payload(await client.call_tool("list_talents", {**request, "limit": 1}))
        second = _payload(
            await client.call_tool(
                "list_talents", {**request, "limit": 1, "cursor": first["next_cursor"]}
            )
        )
        wrong_order = await client.call_tool(
            "list_talents",
            {
                **request,
                "limit": 1,
                "sort": "experience_years_desc",
                "cursor": first["next_cursor"],
            },
        )
        bad_level = await client.call_tool("list_talents", {"seniority": ["guru"]})
        too_many = await client.call_tool("list_talents", {"languages": ["x"] * 11})
        bad_card = await client.call_tool("list_talents", {"card": "rotta"})
        bad_sort = await client.call_tool("list_talents", {"sort": "bellezza"})
    assert [item["email"] for item in found["items"]] == ["b@studio.it", "a@studio.it"]
    assert found["total"] == 2 and found["next_cursor"] is None
    assert [item["email"] for item in first["items"] + second["items"]] == [
        "b@studio.it",
        "a@studio.it",
    ]
    assert first["next_cursor"] is not None and second["next_cursor"] is None
    assert wrong_order.is_error and "ordinamento" in wrong_order.content[0].text
    assert bad_level.is_error and "seniority" in bad_level.content[0].text
    assert too_many.is_error and "languages: al massimo" in too_many.content[0].text
    assert bad_card.is_error and "card" in bad_card.content[0].text
    assert bad_sort.is_error and "sort" in bad_sort.content[0].text


async def test_a_corrected_seniority_is_the_one_every_read_shows(
    clean: sessionmaker[Session],
) -> None:
    """REB-799, REB-805: an admin's `seniority` and `experience_years` beat Claude's in the list
    row,
    in `get_talent` and in the `seniority` filter, so the three agree on one person."""
    ada = _card(clean, "ada@studio.it", card=FULL_CARD)
    _seed_admin(clean)
    async with _server(clean) as client:
        await client.call_tool(
            "override_freelancer",
            {"freelancer_id": ada, "seniority": "junior", "experience_years": "2"},
        )
        row = _payload(await client.call_tool("list_talents", {"fields": ["all"]}))["items"][0]
        detail = _payload(await client.call_tool("get_talent", {"talent_id": ada}))
        junior = _payload(await client.call_tool("list_talents", {"seniority": ["junior"]}))
        senior = _payload(await client.call_tool("list_talents", {"seniority": ["senior"]}))
    assert (row["seniority"], row["experience_years"]) == ("junior", 2)
    assert (detail["seniority"], detail["experience_years"]) == ("junior", 2)
    assert junior["total"] == 1 and senior["total"] == 0


async def test_limit_has_a_ceiling_and_a_smaller_default(
    clean: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    for n in range(4):
        _lead(clean, f"lead{n}@studio.it")
    monkeypatch.setattr(tools_talents, "TALENTS_LIMIT_MAX", 2)
    async with _server(clean) as client:
        capped = _payload(await client.call_tool("list_talents", {"limit": 500}))
        floor = _payload(await client.call_tool("list_talents", {"limit": 0}))
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}
    assert len(capped["items"]) == 2 and capped["next_cursor"] is not None
    assert len(floor["items"]) == 1
    assert tools["list_talents"].input_schema["properties"]["limit"]["default"] == 50


async def test_fifty_rows_of_a_realistic_list_fit_in_a_prompt(
    clean: sessionmaker[Session],
) -> None:
    """The audit measured 156 rows at 162 KB, which truncated an agent. Fifty cards with
    twenty skills and a three-hundred-character summary each: the short row stays under
    30 KB, and the full row (what every call used to answer) is several times that."""
    for n in range(50):
        _card(
            clean,
            f"persona{n}@studio.it",
            first_name=f"Persona{n}",
            card={
                "role": "Full stack developer e tech lead",
                "seniority": "senior",
                "experience_years": 8,
                "skills": [f"Tecnologia{k}" for k in range(20)],
                "industries": ["fintech", "sanità", "logistica"],
                "languages": ["inglese (C1)", "italiano"],
                "location": "Milano e Brianza",
                "profile_summary": "x" * 300,
            },
        )
    async with _server(clean) as client:
        short = (await client.call_tool("list_talents", {})).content[0].text
        full = (await client.call_tool("list_talents", {"fields": ["all"]})).content[0].text
    assert len(json.loads(short)["items"]) == 50
    # What every call answered before: the row without the four fields REB-798 adds,
    # in the server's usual indented text.
    old_row = set(TalentRead.model_fields) - {"industries", "languages", "profile_summary", "band"}
    old_page = {
        **json.loads(full),
        "items": [
            {k: v for k, v in item.items() if k in old_row} for item in json.loads(full)["items"]
        ],
    }
    before = len(json.dumps(old_page, indent=2, ensure_ascii=False).encode())
    after = len(short.encode())
    print(f"50 rows: before {before} bytes, after {after} bytes")  # noqa: T201
    assert after < 30_000
    assert after * 2 < before


async def test_the_tool_text_names_the_new_surface(clean: sessionmaker[Session]) -> None:
    async with _server(clean) as client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}
    description = tools["list_talents"].description or ""
    for word in ("seniority", "min_years", "industries", "languages", "sort", "fields", "card"):
        assert word in description, word
    properties = tools["list_talents"].input_schema["properties"]
    assert {"origin_page", "seniority", "min_years", "max_years", "industries", "languages"} <= set(
        properties
    )


async def test_the_talent_list_is_newest_first_and_the_total_counts_everything(
    factory: sessionmaker[Session],
) -> None:
    _seed(factory, ["uno@studio.it", "due@studio.it"])
    async with Client(build_server(factory, lambda: IVAN)) as client:
        result = await client.call_tool(
            "list_talents",
            {"limit": 1, "fields": ["first_name", "last_name", "status", "email", "linkedin_url"]},
        )
    body = _payload(result)
    assert body["total"] == 2
    assert [item["email"] for item in body["items"]] == ["due@studio.it"]
    item = body["items"][0]
    assert (item["first_name"], item["last_name"]) == ("Ada", "Lovelace")
    assert item["linkedin_url"] == "https://www.linkedin.com/in/due"
    assert item["status"] == "lead"


async def test_get_talent_answers_to_card_with_the_detail_and_to_lead_with_the_row(
    factory: sessionmaker[Session],
) -> None:
    """`get_talent` is the detail screen one id away: a card gets the full
    `FreelancerDetail` the admin page reads (REB-284), a bare sign-up gets its
    `list_talents` row back, and an id in neither table is a sentence."""
    freelancer_id = _seed_freelancer(factory)
    _seed(factory, ["grace@studio.it"])
    async with Client(build_server(factory, lambda: IVAN)) as client:
        lead_id = _payload(await client.call_tool("list_talents", {"status": "lead"}))["items"][0][
            "id"
        ]

        card = _payload(await client.call_tool("get_talent", {"talent_id": freelancer_id}))
        assert card["id"] == freelancer_id
        assert card["email"] == "ada@studio.it"
        # The detail's own fields, the ones the list row does not carry. Without a
        # `settings`/`http` on this server the Pigro lookup is off, not an error.
        assert "note" in card and "signup_utm" in card and "last_logins" in card
        assert card["pigro_slug"] is None

        lead = _payload(await client.call_tool("get_talent", {"talent_id": lead_id}))
        assert (lead["id"], lead["status"], lead["origin"]) == (lead_id, "lead", "form")
        assert lead["email"] == "grace@studio.it"

        missing = await client.call_tool(
            "get_talent", {"talent_id": "01a00000-0000-7000-8000-0000000000fe"}
        )
        assert missing.is_error
        assert "talento" in missing.content[0].text and "non trovato" in missing.content[0].text
    _wipe(factory)
