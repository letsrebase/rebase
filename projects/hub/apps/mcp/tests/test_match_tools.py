"""REB-387's two read-only MCP tools: an agent reads a freelancer's matches and
contracts, with links to the PDFs and never their bytes or the tax data."""

from datetime import date
from decimal import Decimal
from uuid import UUID

from fakes_contracts import FakeRenderer
from mcp import Client
from mcp_tool_support import IVAN, PDF, _payload
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from rebase_core.companies import CompanyService
from rebase_core.company_schemas import CompanyCreate
from rebase_core.config import Settings
from rebase_core.contract_schemas import CustomerDate, FiscalData, LetterFields, MatchCreate
from rebase_core.fiscal import FiscalService
from rebase_core.freelancer_schemas import FreelancerCreate
from rebase_core.freelancers import FreelancerService
from rebase_core.matches import MatchService
from rebase_core.models import User
from rebase_mcp.server import build_server

TABLES = (
    "admin_actions",
    "contract_documents",
    "matches",
    "contract_letter_counters",
    "freelancer_fiscal",
    "notes",
    "freelancers",
    "companies",
    "users",
    "signups",
)


def _wipe(factory: sessionmaker[Session]) -> None:
    session = factory()
    for table in TABLES:
        session.execute(text(f"DELETE FROM {table}"))
    session.commit()
    session.close()


def _seed(factory: sessionmaker[Session]) -> tuple[str, str]:
    """A card, a request, the tax data and one draft match, written by `IVAN`, whose
    `users` row this file needs because the match names its author."""
    session = factory()
    try:
        session.add(User(id=IVAN.id, email=IVAN.email, nome=IVAN.nome, last_name="", role="admin"))
        session.commit()
        card, _ = FreelancerService(session).apply(
            FreelancerCreate(
                nome="Ada",
                last_name="Lovelace",
                email="ada@studio.it",
                daily_rate=Decimal("450"),
                posizione="Backend developer",
                remote="remote",
            ),
            PDF,
            "cv.pdf",
            "application/pdf",
        )
        request, _ = CompanyService(session).request(
            CompanyCreate(
                company_name="ACME Srl",
                contact_first_name="Wile",
                contact_last_name="E.",
                email="wile@acme.it",
                phone="+39 345 1234567",
                requested_role="Backend developer",
                project="Un backend developer per tre mesi.",
                period_from=date(2026, 10, 1),
                duration="3 mesi",
                daily_budget=Decimal("500"),
                remote="remote",
                headcount=1,
            )
        )
        FiscalService(session).save(
            card.id,
            FiscalData(tax_code="LVLDAA85T50H501Z", vat_number="01234567890", domicile="Milano"),
            IVAN.id,
        )
        match = MatchService(session, FakeRenderer()).create(
            card.id,
            MatchCreate(
                company_id=request.id,
                customer=CustomerDate(
                    customer_legal_name="ACME S.r.l.",
                    customer_vat_number="01234567890",
                    customer_registered_office="Milano",
                ),
                letter=LetterFields(
                    role="Backend developer",
                    activities="Le API.",
                    start_date=date(2026, 10, 1),
                    mode="to_work_unit",
                    fee=Decimal("450"),
                    payment_days=30,
                    month_end=True,
                ),
            ),
            IVAN.id,
        )
        return str(card.id), str(match.id)
    finally:
        session.close()


async def test_list_matches_reads_the_page_with_links_and_no_tax_data(
    factory: sessionmaker[Session],
) -> None:
    card, match = _seed(factory)
    try:
        server = build_server(factory, lambda: IVAN, settings=Settings(_env_file=None))  # type: ignore[call-arg]
        async with Client(server) as client:
            body = _payload(await client.call_tool("list_matches", {"freelancer_id": card}))
        assert "tax" not in body
        assert body["framework"]["status"] == "generated"
        assert body["framework"]["pdf_url"] == (
            f"https://letsrebase.com/api/hub/contract-documents/{body['framework']['id']}/pdf"
        )
        (item,) = body["matches"]
        assert item["id"] == match
        assert item["letter"]["pdf_url"].endswith(f"/contract-documents/{item['letter']['id']}/pdf")
        assert "pdf" not in item["letter"] and "data" not in item["letter"]
    finally:
        _wipe(factory)


async def test_get_match_answers_the_letter_and_the_framework_with_links(
    factory: sessionmaker[Session],
) -> None:
    _card, match = _seed(factory)
    try:
        async with Client(build_server(factory, lambda: IVAN)) as client:
            body = _payload(await client.call_tool("get_match", {"match_id": match}))
            missing = await client.call_tool("get_match", {"match_id": str(UUID(int=7))})
        assert body["status"] == "draft"
        assert body["situation"].startswith("La lettera n. ")
        assert body["situation"].endswith(" è pronta: il freelance non ha ancora ricevuto nulla.")
        assert (body["next_action"], body["other_actions"]) == ("send", ["cancel"])
        assert (
            body["letter"]["pdf_url"] == f"/api/hub/contract-documents/{body['letter']['id']}/pdf"
        )
        assert body["framework"]["pdf_url"].startswith("/api/hub/contract-documents/")
        assert missing.is_error
    finally:
        _wipe(factory)


async def test_get_match_of_a_soft_deleted_freelancer_answers_the_matchs_own_message(
    factory: sessionmaker[Session],
) -> None:
    """The admin API answers this same case with `match {id} non trovato`
    (`rebase_core.matches.require_live_freelancer`); the MCP tool used to answer with
    the freelancer's own not-found message instead, from `for_freelancer` (REB-417)."""
    card, match = _seed(factory)
    try:
        async with Client(build_server(factory, lambda: IVAN)) as client:
            await client.call_tool("delete_freelancer", {"freelancer_id": card})
            refused = await client.call_tool("get_match", {"match_id": match})
        assert refused.is_error
        assert f"match {match} non trovato" in refused.content[0].text
    finally:
        _wipe(factory)
