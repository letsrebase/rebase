"""REB-387's two read-only MCP tools: an agent reads a freelancer's matches and
contracts, with links to the PDFs and never their bytes or the tax data."""

from datetime import date
from decimal import Decimal
from uuid import UUID

from fakes_contracts import FakeRenderer
from mcp import Client
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker
from test_tools import IVAN, PDF, _payload

from rebase_core.companies import CompanyService
from rebase_core.config import Settings
from rebase_core.contract_schemas import ClienteData, FiscalData, LetteraFields, MatchCreate
from rebase_core.fiscal import FiscalService
from rebase_core.freelancers import FreelancerService
from rebase_core.matches import MatchService
from rebase_core.models import User
from rebase_core.schemas import CompanyCreate, FreelancerCreate
from rebase_mcp.server import build_server

TABLES = (
    "admin_actions",
    "contract_documents",
    "matches",
    "contract_letter_counters",
    "freelancer_fiscal",
    "comments",
    "freelancers",
    "companies",
    "users",
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
        session.add(User(id=IVAN.id, email=IVAN.email, nome=IVAN.nome, cognome="", role="admin"))
        session.commit()
        card, _ = FreelancerService(session).apply(
            FreelancerCreate(
                nome="Ada",
                cognome="Lovelace",
                email="ada@studio.it",
                tariffa_giornaliera=Decimal("450"),
                posizione="Backend developer",
                remoto="remoto",
            ),
            PDF,
            "cv.pdf",
            "application/pdf",
        )
        request, _ = CompanyService(session).request(
            CompanyCreate(
                nome_azienda="ACME Srl",
                referente_nome="Wile",
                referente_cognome="E.",
                email="wile@acme.it",
                telefono="+39 345 1234567",
                figura_richiesta="Backend developer",
                progetto="Un backend developer per tre mesi.",
                periodo_da=date(2026, 10, 1),
                durata="3 mesi",
                budget_giornaliero=Decimal("500"),
                remoto="remoto",
                numero_risorse=1,
            )
        )
        FiscalService(session).save(
            card.id,
            FiscalData(
                codice_fiscale="LVLDAA85T50H501Z", partita_iva="01234567890", domicilio="Milano"
            ),
            IVAN.id,
        )
        match = MatchService(session, FakeRenderer()).create(
            card.id,
            MatchCreate(
                company_id=request.id,
                cliente=ClienteData(
                    cliente_ragione_sociale="ACME S.r.l.",
                    cliente_piva="01234567890",
                    cliente_sede="Milano",
                ),
                lettera=LetteraFields(
                    ruolo="Backend developer",
                    attivita="Le API.",
                    data_inizio=date(2026, 10, 1),
                    compenso=Decimal("450"),
                    giorni_pagamento=30,
                    fine_mese=True,
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
        assert "fiscale" not in body
        assert body["quadro"]["stato"] == "generato"
        assert body["quadro"]["pdf_url"] == (
            f"https://letsrebase.com/api/hub/contract-documents/{body['quadro']['id']}/pdf"
        )
        (item,) = body["matches"]
        assert item["id"] == match
        assert item["lettera"]["pdf_url"].endswith(
            f"/contract-documents/{item['lettera']['id']}/pdf"
        )
        assert "pdf" not in item["lettera"] and "data" not in item["lettera"]
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
        assert body["stato"] == "bozza"
        assert (
            body["lettera"]["pdf_url"] == f"/api/hub/contract-documents/{body['lettera']['id']}/pdf"
        )
        assert body["quadro"]["pdf_url"].startswith("/api/hub/contract-documents/")
        assert missing.is_error
    finally:
        _wipe(factory)
