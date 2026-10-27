"""`list_talenti` and `get_talento` as an agent matching a request uses them (REB-798,
REB-801): the `origine` filter is the value the row shows, `get_talento` carries the
list's own fields, the filters and the sort reach the service, and a page of rows is
small enough to read."""

import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest
from mcp import Client
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker
from test_tools import IVAN, PDF, _payload

import rebase_mcp.server as server_module
from rebase_core.errors import ValidationFailed
from rebase_core.freelancers import FreelancerService
from rebase_core.models import FreelancerCard
from rebase_core.schemas import (
    FreelancerCreate,
    FreelancerDraft,
    SignupCreate,
    SignupUtm,
    TalentoRead,
)
from rebase_core.service import SignupService
from rebase_mcp.server import build_server
from rebase_mcp.talenti_view import (
    SHORT_FIELDS,
    SHORT_SINTESI,
    SHORT_SKILLS,
    row_fields,
    talento_row,
)


@pytest.fixture(autouse=True)
def clean(factory: sessionmaker[Session]) -> sessionmaker[Session]:
    session = factory()
    for table in ("comments", "freelancers", "companies", "users", "signups"):
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
    nome: str = "Ada",
    rate: str = "450",
    page: str | None = None,
    card: dict[str, Any] | None = None,
) -> str:
    session = factory()
    try:
        row, _ = FreelancerService(session).apply(
            FreelancerCreate(
                nome=nome,
                cognome="Lovelace",
                email=email,
                tariffa_giornaliera=Decimal(rate),
                posizione="Backend developer",
                remoto="remoto",
                utm=SignupUtm(origine=page) if page else None,
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
            .subscribe(SignupCreate(email=email, nome="Giulia", cognome="Branda"))
            .id
        )
    finally:
        session.close()


def _admin_draft(factory: sessionmaker[Session], email: str) -> None:
    session = factory()
    try:
        signup = SignupService(session).subscribe(
            SignupCreate(email=email, nome="Ricerca", cognome="Fatta")
        )
        FreelancerService(session).draft_from_signup(
            signup.id,
            FreelancerDraft(
                nome="Ricerca",
                cognome="Fatta",
                posizione="Designer",
                links=[],
                fonti=["https://www.linkedin.com/in/ricerca"],
            ),
            "Claude",
        )
    finally:
        session.close()


FULL_CARD: dict[str, Any] = {
    "ruolo": "Data engineer",
    "seniority": "senior",
    "anni": 9,
    "competenze": [f"Skill {n}" for n in range(1, 13)],
    "settori": ["fintech", "sanità"],
    "lingue": ["inglese (C1)", "italiano"],
    "luogo": "Monza",
    "sintesi": "Costruisce pipeline dati per aziende finanziarie.",
}


# ---- the shape of a row, without a database --------------------------------------------


def _row(**fields: Any) -> dict[str, Any]:
    row = TalentoRead(
        id=UUID(int=1),
        email="ada@studio.it",
        stato="nuovo",
        origine="wizard",
        created_at=datetime(2026, 10, 1, tzinfo=UTC),
        **fields,
    )
    return row.model_dump(mode="json")


def test_the_short_row_is_the_card_without_addresses_dates_or_empty_keys() -> None:
    row = talento_row(_row(nome="Ada", cognome="Lovelace", **FULL_CARD), None)
    assert set(row) <= set(SHORT_FIELDS)
    assert "email" not in row and "linkedin_url" not in row and "created_at" not in row
    assert row["competenze"] == [f"Skill {n}" for n in range(1, SHORT_SKILLS + 1)]
    assert "settori" not in row and "lingue" not in row
    assert row["sintesi"] == FULL_CARD["sintesi"]
    lead = talento_row(_row(nome="Giulia", cognome="Branda"), None)
    assert set(lead) == {"id", "nome", "cognome", "stato"}


def test_a_long_summary_is_cut_at_a_word_in_the_short_row_and_whole_on_request() -> None:
    long = " ".join(["Costruisce pipeline dati per aziende finanziarie."] * 8)
    row = _row(sintesi=long)
    cut = talento_row(row, None)["sintesi"]
    assert len(cut) <= SHORT_SINTESI + 1 and cut.endswith("…") and long.startswith(cut[:-1])
    assert not cut[:-1].endswith(" ")
    assert talento_row(row, row_fields(["sintesi"]))["sintesi"] == long


def test_campi_names_the_fields_whole_with_the_id_and_tutti_is_the_full_row() -> None:
    row = _row(**FULL_CARD)
    picked = talento_row(row, row_fields(["settori", "competenze"]))
    assert list(picked) == ["id", "settori", "competenze"]
    assert picked["competenze"] == FULL_CARD["competenze"]
    assert set(talento_row(row, row_fields(["tutti"]))) == set(TalentoRead.model_fields)
    assert row_fields(None) is None and row_fields([]) is None and row_fields([" "]) is None


def test_an_unknown_field_is_refused_with_the_names_that_exist() -> None:
    with pytest.raises(ValidationFailed, match="«telefono» non esiste"):
        row_fields(["email", "telefono"])
    with pytest.raises(ValidationFailed, match="«telefono» non esiste"):
        row_fields(["tutti", "telefono"])


# ---- through the tools -----------------------------------------------------------------


async def test_the_default_row_is_short_and_campi_widens_it(clean: sessionmaker[Session]) -> None:
    _card(clean, "ada@studio.it", card=FULL_CARD)
    _lead(clean, "giulia@studio.it")
    async with _server(clean) as client:
        short = _payload(await client.call_tool("list_talenti", {}))
        wide = _payload(await client.call_tool("list_talenti", {"campi": ["tutti"]}))
        some = _payload(await client.call_tool("list_talenti", {"campi": ["email", "fascia"]}))
        bad = await client.call_tool("list_talenti", {"campi": ["telefono"]})
    by_stato = {item["stato"]: item for item in short["items"]}
    assert set(by_stato["lead"]) == {"id", "nome", "cognome", "stato"}
    ada = by_stato["nuovo"]
    assert ada["ruolo"] == "Data engineer" and ada["seniority"] == "senior" and ada["anni"] == 9
    assert ada["tariffa_giornaliera"] == "450.00" and ada["luogo"] == "Monza"
    assert len(ada["competenze"]) == SHORT_SKILLS and "email" not in ada
    assert {"email", "settori", "lingue", "fascia", "origine"} <= set(
        next(i for i in wide["items"] if i["stato"] == "nuovo")
    )
    assert all(list(item) == ["id", "email", "fascia"] for item in some["items"])
    assert short["totale"] == wide["totale"] == 2
    assert bad.is_error and "campi" in bad.content[0].text and "telefono" in bad.content[0].text


async def test_origine_filters_what_the_row_shows_and_pagina_origine_the_page(
    clean: sessionmaker[Session],
) -> None:
    _card(clean, "wizard@studio.it", page="home")
    _admin_draft(clean, "ricerca@studio.it")
    _lead(clean, "giulia@studio.it")
    async with _server(clean) as client:
        listed = _payload(await client.call_tool("list_talenti", {"campi": ["tutti"]}))
        for origine, email in (
            ("wizard", "wizard@studio.it"),
            ("admin", "ricerca@studio.it"),
            ("form", "giulia@studio.it"),
        ):
            found = _payload(
                await client.call_tool("list_talenti", {"origine": origine, "campi": ["email"]})
            )
            assert [item["email"] for item in found["items"]] == [email], origine
            assert found["totale"] == 1
        page = _payload(
            await client.call_tool("list_talenti", {"pagina_origine": "home", "campi": ["email"]})
        )
        refused = await client.call_tool("list_talenti", {"origine": "home"})
    assert {item["origine"] for item in listed["items"]} == {"wizard", "admin", "form"}
    assert [item["email"] for item in page["items"]] == ["wizard@studio.it"]
    assert refused.is_error and "form, wizard, admin" in refused.content[0].text


async def test_get_talento_carries_every_field_the_list_row_has(
    clean: sessionmaker[Session],
) -> None:
    """A card and a lead: each field of the full list row is the same in `get_talento`,
    the marketing page has its own key, and the card's `settori`, `lingue`, `sintesi`
    and `fascia` are there without a third call to `get_freelancer_card`."""
    ada = _card(clean, "ada@studio.it", page="home", card=FULL_CARD)
    lead = _lead(clean, "giulia@studio.it")
    async with _server(clean) as client:
        rows = {
            item["id"]: item
            for item in _payload(await client.call_tool("list_talenti", {"campi": ["tutti"]}))[
                "items"
            ]
        }
        card = _payload(await client.call_tool("get_talento", {"talento_id": ada}))
        bare = _payload(await client.call_tool("get_talento", {"talento_id": lead}))
    for field in TalentoRead.model_fields:
        assert card[field] == rows[ada][field], field
        assert bare[field] == rows[lead][field], field
    assert (card["origine"], card["pagina_origine"]) == ("wizard", "home")
    assert card["settori"] == ["fintech", "sanità"] and card["lingue"][0] == "inglese (C1)"
    assert card["sintesi"] == FULL_CARD["sintesi"]
    assert card["fascia"] is not None and card["fascia"]["min"] <= 450 * 1.3
    assert "commenti" in card and bare["origine"] == "form" and "pagina_origine" not in bare


async def test_the_card_filters_and_the_sort_answer_one_request_in_one_call(
    clean: sessionmaker[Session],
) -> None:
    """«Senior, 5+ years, English, under 400 a day, cheapest first.»"""

    def card(**fields: Any) -> dict[str, Any]:
        return {**FULL_CARD, **fields}

    _card(clean, "a@studio.it", nome="A", rate="380", card=card(anni=6))
    _card(clean, "b@studio.it", nome="B", rate="350", card=card(anni=9))
    _card(clean, "c@studio.it", nome="C", rate="500", card=card())
    _card(clean, "d@studio.it", nome="D", rate="300", card=card(seniority="mid"))
    _card(clean, "e@studio.it", nome="E", rate="300", card=card(anni=2))
    _card(clean, "f@studio.it", nome="F", rate="300", card=card(lingue=["francese"]))
    request = {
        "seniority": ["senior"],
        "anni_min": 5,
        "lingue": ["inglese"],
        "settori": ["fintech"],
        "tariffa_max": "400",
        "ordina": "tariffa_asc",
        "campi": ["email", "tariffa_giornaliera"],
    }
    async with _server(clean) as client:
        found = _payload(await client.call_tool("list_talenti", request))
        first = _payload(await client.call_tool("list_talenti", {**request, "limit": 1}))
        second = _payload(
            await client.call_tool(
                "list_talenti", {**request, "limit": 1, "cursor": first["next_cursor"]}
            )
        )
        wrong_order = await client.call_tool(
            "list_talenti",
            {**request, "limit": 1, "ordina": "anni_desc", "cursor": first["next_cursor"]},
        )
        bad_level = await client.call_tool("list_talenti", {"seniority": ["guru"]})
        too_many = await client.call_tool("list_talenti", {"lingue": ["x"] * 11})
        bad_scheda = await client.call_tool("list_talenti", {"scheda": "rotta"})
        bad_sort = await client.call_tool("list_talenti", {"ordina": "bellezza"})
    assert [item["email"] for item in found["items"]] == ["b@studio.it", "a@studio.it"]
    assert found["totale"] == 2 and found["next_cursor"] is None
    assert [item["email"] for item in first["items"] + second["items"]] == [
        "b@studio.it",
        "a@studio.it",
    ]
    assert first["next_cursor"] is not None and second["next_cursor"] is None
    assert wrong_order.is_error and "ordinamento" in wrong_order.content[0].text
    assert bad_level.is_error and "seniority" in bad_level.content[0].text
    assert too_many.is_error and "lingue: al massimo" in too_many.content[0].text
    assert bad_scheda.is_error and "scheda" in bad_scheda.content[0].text
    assert bad_sort.is_error and "ordina" in bad_sort.content[0].text


async def test_limit_has_a_ceiling_and_a_smaller_default(
    clean: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    for n in range(4):
        _lead(clean, f"lead{n}@studio.it")
    monkeypatch.setattr(server_module, "TALENTI_LIMIT_MAX", 2)
    async with _server(clean) as client:
        capped = _payload(await client.call_tool("list_talenti", {"limit": 500}))
        floor = _payload(await client.call_tool("list_talenti", {"limit": 0}))
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}
    assert len(capped["items"]) == 2 and capped["next_cursor"] is not None
    assert len(floor["items"]) == 1
    assert tools["list_talenti"].input_schema["properties"]["limit"]["default"] == 50


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
            nome=f"Persona{n}",
            card={
                "ruolo": "Full stack developer e tech lead",
                "seniority": "senior",
                "anni": 8,
                "competenze": [f"Tecnologia{k}" for k in range(20)],
                "settori": ["fintech", "sanità", "logistica"],
                "lingue": ["inglese (C1)", "italiano"],
                "luogo": "Milano e Brianza",
                "sintesi": "x" * 300,
            },
        )
    async with _server(clean) as client:
        short = (await client.call_tool("list_talenti", {})).content[0].text
        full = (await client.call_tool("list_talenti", {"campi": ["tutti"]})).content[0].text
    assert len(json.loads(short)["items"]) == 50
    # What every call answered before: the row without the four fields REB-798 adds,
    # in the server's usual indented text.
    old_row = set(TalentoRead.model_fields) - {"settori", "lingue", "sintesi", "fascia"}
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
    description = tools["list_talenti"].description or ""
    for word in ("seniority", "anni_min", "settori", "lingue", "ordina", "campi", "scheda"):
        assert word in description, word
    properties = tools["list_talenti"].input_schema["properties"]
    assert {"pagina_origine", "seniority", "anni_min", "anni_max", "settori", "lingue"} <= set(
        properties
    )
