"""`list_talenti` and `list_companies` take the same search, filters and cursor the
admin screens send (REB-288): the tools pass them to the same core services and hand
the page's `next_cursor` back so an agent can walk the list."""

import re
from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from mcp import Client
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker
from test_tools import IVAN, PDF, _payload

from rebase_core.companies import CompanyService
from rebase_core.freelancers import FreelancerService
from rebase_core.schemas import CompanyCreate, FreelancerCreate, SignupCreate
from rebase_core.service import SignupService
from rebase_mcp.server import build_server

# The words a tool description is written with, and the ones an English rewrite would
# bring in. A floor per description, not a pinned copy: an English sentence trips the
# second pattern, an Italian one cannot.
ITALIAN = re.compile(
    r"\b(che|con|per|non|sono|quando|come|senza|gli|le|li|da|fra|ad|su|la|il|un|una)\b",
    re.IGNORECASE,
)
ENGLISH = re.compile(
    r"\b(the|and|with|from|this|that|will|page|search|filter|latest|which)\b",
    re.IGNORECASE,
)


@pytest.fixture(autouse=True)
def clean(factory: sessionmaker[Session]) -> sessionmaker[Session]:
    session = factory()
    for table in ("comments", "freelancers", "companies", "users", "signups"):
        session.execute(text(f"DELETE FROM {table}"))
    session.commit()
    session.close()
    return factory


def _card(
    factory: sessionmaker[Session],
    nome: str,
    cognome: str,
    email: str,
    tariffa: str,
    posizione: str = "Backend developer",
) -> str:
    session = factory()
    try:
        row, _ = FreelancerService(session).apply(
            FreelancerCreate(
                nome=nome,
                cognome=cognome,
                email=email,
                tariffa_giornaliera=Decimal(tariffa),
                posizione=posizione,
                remoto="remoto",
            ),
            PDF,
            "cv.pdf",
            "application/pdf",
        )
        return str(row.id)
    finally:
        session.close()


def _lead(factory: sessionmaker[Session], nome: str, cognome: str, email: str) -> None:
    session = factory()
    try:
        SignupService(session).subscribe(SignupCreate(email=email, nome=nome, cognome=cognome))
    finally:
        session.close()


def _company(
    factory: sessionmaker[Session],
    azienda: str,
    email: str,
    progetto: str,
    budget: str,
) -> str:
    session = factory()
    try:
        row = CompanyService(session).request(
            CompanyCreate(
                nome_azienda=azienda,
                referente_nome="Wile",
                referente_cognome="E.",
                email=email,
                progetto=progetto,
                periodo_da=date(2026, 10, 1),
                durata="3 mesi",
                budget_giornaliero=Decimal(budget),
            )
        )
        return str(row.id)
    finally:
        session.close()


def _server(factory: sessionmaker[Session]) -> Client:
    return Client(build_server(factory, lambda: IVAN))


async def test_a_partial_surname_narrows_the_talent_list_to_the_matching_rows(
    clean: Any,
) -> None:
    _card(clean, "Ada", "Lovelace", "ada@studio.it", "450")
    _card(clean, "Bruno", "Neri", "bruno@studio.it", "600")
    _lead(clean, "Cara", "Bianchi", "cara@studio.it")
    async with _server(clean) as client:
        found = _payload(await client.call_tool("list_talenti", {"q": "lovel"}))
    assert [item["email"] for item in found["items"]] == ["ada@studio.it"]
    assert found["totale"] == 1 and found["next_cursor"] is None


async def test_a_filter_excludes_the_rows_it_does_not_match(clean: Any) -> None:
    _card(clean, "Ada", "Lovelace", "ada@studio.it", "450")
    _card(clean, "Bruno", "Neri", "bruno@studio.it", "600")
    _lead(clean, "Cara", "Bianchi", "cara@studio.it")
    async with _server(clean) as client:
        priced = _payload(await client.call_tool("list_talenti", {"tariffa_min": "500"}))
        leads = _payload(await client.call_tool("list_talenti", {"stato": "lead"}))
    # A tariffa a lead cannot have drops every lead outright, not just some.
    assert [item["email"] for item in priced["items"]] == ["bruno@studio.it"]
    assert priced["totale"] == 1
    assert [item["email"] for item in leads["items"]] == ["cara@studio.it"]


async def test_the_cursor_walks_a_talent_page_to_the_next(clean: Any) -> None:
    for index in range(3):
        _card(clean, f"Ada{index}", "Lovelace", f"ada{index}@studio.it", "450")
    async with _server(clean) as client:
        first = _payload(await client.call_tool("list_talenti", {"limit": 2}))
        assert len(first["items"]) == 2 and first["next_cursor"] is not None
        second = _payload(
            await client.call_tool("list_talenti", {"limit": 2, "cursor": first["next_cursor"]})
        )
    seen = [item["id"] for item in first["items"] + second["items"]]
    assert len(second["items"]) == 1 and second["next_cursor"] is None
    assert len(set(seen)) == 3


async def test_companies_search_filter_and_walk_the_same_page(clean: Any) -> None:
    _company(clean, "ACME Srl", "wile@acme.it", "Un backend developer per tre mesi.", "500")
    _company(clean, "Rossi Lab", "paperone@rossilab.it", "Una pipeline dati.", "700")
    _company(clean, "Beta Spa", "cq@beta.it", "Un CRM interno.", "650")
    async with _server(clean) as client:
        found = _payload(await client.call_tool("list_companies", {"q": "pipeline"}))
        assert [item["email"] for item in found["items"]] == ["paperone@rossilab.it"]

        rich = _payload(await client.call_tool("list_companies", {"budget_min": "600"}))
        assert sorted(item["email"] for item in rich["items"]) == [
            "cq@beta.it",
            "paperone@rossilab.it",
        ]

        first = _payload(await client.call_tool("list_companies", {"limit": 2}))
        assert len(first["items"]) == 2 and first["next_cursor"] is not None
        second = _payload(
            await client.call_tool("list_companies", {"limit": 2, "cursor": first["next_cursor"]})
        )
    assert len(second["items"]) == 1 and second["next_cursor"] is None
    ids = [item["id"] for item in first["items"] + second["items"]]
    assert len(set(ids)) == 3


async def test_a_garbage_decimal_or_date_answers_an_italian_sentence(clean: Any) -> None:
    async with _server(clean) as client:
        bad_number = await client.call_tool("list_talenti", {"tariffa_min": "tanto"})
        assert bad_number.is_error and "tariffa_min" in bad_number.content[0].text

        bad_date = await client.call_tool("list_companies", {"periodo_da": "ieri"})
        assert bad_date.is_error and "periodo_da" in bad_date.content[0].text


async def test_a_cursor_minted_while_browsing_is_refused_once_searching(clean: Any) -> None:
    _company(clean, "ACME Srl", "wile@acme.it", "Un backend developer.", "500")
    _company(clean, "Rossi Lab", "paperone@rossilab.it", "Una pipeline dati.", "700")
    async with _server(clean) as client:
        page = _payload(await client.call_tool("list_companies", {"limit": 1}))
        replay = await client.call_tool(
            "list_companies", {"limit": 1, "q": "pipeline", "cursor": page["next_cursor"]}
        )
    assert replay.is_error
    assert "ordinamento" in replay.content[0].text


async def test_the_tool_descriptions_stay_italian(clean: Any) -> None:
    async with _server(clean) as client:
        tools = (await client.list_tools()).tools
    listed = {tool.name: tool.description or "" for tool in tools}
    assert listed  # every registered tool carries a description
    for name, description in listed.items():
        assert not ENGLISH.findall(description), name
        assert len(ITALIAN.findall(description)) >= 3, name
    # The new surface is the one the card is about: it says what the cursor is for.
    assert "cursore" in listed["list_talenti"] and "cursore" in listed["list_companies"]
