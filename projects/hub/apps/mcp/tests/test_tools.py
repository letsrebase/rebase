"""The hub's MCP tools over its own database: the reads, the status moves, the comments."""

import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from mcp import Client
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from rebase_core.admin_tokens import AdminRead
from rebase_core.perks import guide_bytes
from rebase_core.schemas import SignupCreate
from rebase_core.service import SignupService
from rebase_mcp.server import build_server

# The admin every test calls as: the transport resolves a token to this and hands it in.
IVAN = AdminRead(
    id=UUID("01a00000-0000-7000-8000-000000000001"),
    email="ivan@rebase.it",
    nome="Ivan",
    attivo=True,
    created_at=datetime(2026, 9, 10, tzinfo=UTC),
)


def _payload(result: Any) -> dict[str, Any]:
    return result.structured_content or json.loads(result.content[0].text)


def _seed(factory: sessionmaker[Session], addresses: list[str]) -> None:
    session = factory()
    try:
        for address in addresses:
            SignupService(session).subscribe(
                SignupCreate(
                    email=address,
                    nome="Ada",
                    cognome="Lovelace",
                    linkedin_url="https://www.linkedin.com/in/ada",
                )
            )
    finally:
        session.close()


async def test_the_talent_list_is_newest_first_and_the_total_counts_everything(
    factory: sessionmaker[Session],
) -> None:
    _seed(factory, ["uno@studio.it", "due@studio.it"])
    async with Client(build_server(factory, lambda: IVAN)) as client:
        result = await client.call_tool("list_talenti", {"limit": 1})
    body = _payload(result)
    assert body["totale"] == 2
    assert [item["email"] for item in body["items"]] == ["due@studio.it"]
    item = body["items"][0]
    assert (item["nome"], item["cognome"]) == ("Ada", "Lovelace")
    assert item["linkedin_url"] == "https://www.linkedin.com/in/ada"
    assert item["stato"] == "lead"


async def test_no_tool_subscribes_or_applies_on_somebody_elses_behalf(
    factory: sessionmaker[Session],
) -> None:
    async with Client(build_server(factory, lambda: IVAN)) as client:
        names = {tool.name for tool in (await client.list_tools()).tools}
    assert not [
        name for name in names if "subscribe" in name or "apply" in name or "request" in name
    ]


PDF = b"%PDF-1.7\n1 0 obj<<>>endobj\n%%EOF\n"


async def test_the_guide_stats_are_zero_on_an_empty_hub_and_read_only(
    factory: sessionmaker[Session],
) -> None:
    """ORB-156: the counter the admin area shows is one tool away for an agent too."""
    async with Client(build_server(factory, lambda: IVAN)) as client:
        result = await client.call_tool("guide_stats", {})
        names = {tool.name for tool in (await client.list_tools()).tools}
    body = _payload(result)
    assert body == {
        "totale": 0,
        "membri": 0,
        "membri_totali": 0,
        "ultimi_7_giorni": 0,
        "recenti": [],
    }
    assert "record_guide_download" not in names


async def test_the_admin_tools_read_and_move_a_candidate_without_the_cv(
    factory: sessionmaker[Session],
) -> None:
    from decimal import Decimal

    from rebase_core.freelancers import FreelancerService
    from rebase_core.schemas import FreelancerCreate

    session = factory()
    try:
        row, _ = FreelancerService(session).apply(
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
    finally:
        session.close()

    async with Client(build_server(factory, lambda: IVAN)) as client:
        listed = _payload(await client.call_tool("list_talenti", {}))
        assert listed["totale"] == 1
        assert listed["items"][0]["email"] == "ada@studio.it"
        assert "cv_bytes" not in listed["items"][0]

        moved = _payload(
            await client.call_tool(
                "set_freelancer_status",
                {"freelancer_id": str(row.id), "stato": "contattato", "note": "scritto oggi"},
            )
        )
        assert (moved["stato"], moved["note"]) == ("contattato", "scritto oggi")

        refused = await client.call_tool(
            "set_freelancer_status", {"freelancer_id": str(row.id), "stato": "forse"}
        )
        assert refused.is_error
        assert "stato" in refused.content[0].text

        missing = await client.call_tool("get_company", {"company_id": str(row.id)})
        assert missing.is_error

        names = {tool.name for tool in (await client.list_tools()).tools}
        assert names == {
            "create_freelancer_from_signup",
            "list_talenti",
            "get_talento",
            "get_freelancer",
            "read_freelancer_cv",
            "list_pigro_spaces",
            "set_freelancer_status",
            "override_freelancer",
            "delete_freelancer",
            "restore_freelancer",
            "clear_freelancer_cv",
            "get_freelancer_audit",
            "revert_freelancer_action",
            "add_freelancer_comment",
            "list_aziende",
            "get_company",
            "set_company_status",
            "override_company",
            "delete_company",
            "restore_company",
            "get_company_audit",
            "revert_company_action",
            "add_company_comment",
            "guide_stats",
            "login_stats",
            "list_matches",
            "get_match",
            "preview_match",
            "create_match",
            "send_match_for_signature",
            "resend_signing_mail",
            "refresh_contract",
            "cancel_contract",
            "record_notice",
            "cancel_match",
            "close_match",
            "link_match_to_pigro",
            "get_match_report",
            "set_freelancer_tax_data",
        }
    _wipe(factory)


def _wipe(factory: sessionmaker[Session]) -> None:
    session = factory()
    for table in ("comments", "freelancers", "companies", "users"):
        session.execute(text(f"DELETE FROM {table}"))
    session.commit()
    session.close()


def _seed_freelancer(factory: sessionmaker[Session]) -> str:
    from decimal import Decimal

    from rebase_core.freelancers import FreelancerService
    from rebase_core.schemas import FreelancerCreate

    session = factory()
    try:
        row, _ = FreelancerService(session).apply(
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
        return str(row.id)
    finally:
        session.close()


def _seed_company(factory: sessionmaker[Session]) -> str:
    from datetime import date
    from decimal import Decimal

    from rebase_core.companies import CompanyService
    from rebase_core.schemas import CompanyCreate

    session = factory()
    try:
        row, _ = CompanyService(session).request(
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
        return str(row.id)
    finally:
        session.close()


async def test_a_comment_from_the_mcp_is_signed_mcp_by_default_and_get_returns_the_thread(
    factory: sessionmaker[Session],
) -> None:
    freelancer_id = _seed_freelancer(factory)
    async with Client(build_server(factory, lambda: IVAN)) as client:
        first = _payload(
            await client.call_tool(
                "add_freelancer_comment",
                {"freelancer_id": freelancer_id, "testo": "Sentito al telefono."},
            )
        )
        assert (first["autore"], first["testo"]) == ("Ivan", "Sentito al telefono.")
        assert first["entity_type"] == "freelancer" and first["entity_id"] == freelancer_id
        second = _payload(
            await client.call_tool(
                "add_freelancer_comment",
                {"freelancer_id": freelancer_id, "testo": "Portfolio ricevuto.", "autore": "Ivan"},
            )
        )
        assert second["autore"] == "Ivan"

        detail = _payload(
            await client.call_tool("get_freelancer", {"freelancer_id": freelancer_id})
        )
        assert [c["id"] for c in detail["commenti"]] == [second["id"], first["id"]]
        # The note is untouched, and the list does not carry the thread.
        assert detail["note"] is None
        listed = _payload(await client.call_tool("list_talenti", {}))
        assert "commenti" not in listed["items"][0]
    _wipe(factory)


async def test_a_company_comment_lands_on_the_company_and_a_bad_one_is_a_sentence(
    factory: sessionmaker[Session],
) -> None:
    company_id = _seed_company(factory)
    freelancer_id = _seed_freelancer(factory)
    async with Client(build_server(factory, lambda: IVAN)) as client:
        posted = _payload(
            await client.call_tool(
                "add_company_comment", {"company_id": company_id, "testo": "Budget confermato."}
            )
        )
        assert (posted["autore"], posted["entity_type"]) == ("Ivan", "company")
        detail = _payload(await client.call_tool("get_company", {"company_id": company_id}))
        assert [c["testo"] for c in detail["commenti"]] == ["Budget confermato."]

        # A freelancer id is not a company: not found, as a sentence.
        wrong = await client.call_tool(
            "add_company_comment", {"company_id": freelancer_id, "testo": "x"}
        )
        assert wrong.is_error and "non trovato" in wrong.content[0].text
        empty = await client.call_tool(
            "add_freelancer_comment", {"freelancer_id": freelancer_id, "testo": "   "}
        )
        assert empty.is_error and "testo" in empty.content[0].text
        long = await client.call_tool(
            "add_freelancer_comment", {"freelancer_id": freelancer_id, "testo": "x" * 4001}
        )
        assert long.is_error and "4000" in long.content[0].text
        untouched = _payload(
            await client.call_tool("get_freelancer", {"freelancer_id": freelancer_id})
        )
        assert untouched["commenti"] == []

        # Nothing edits or deletes a comment, from here or anywhere.
        names = {tool.name for tool in (await client.list_tools()).tools}
        assert not [n for n in names if "comment" in n and not n.startswith("add_")]
    _wipe(factory)


async def test_a_card_is_written_from_a_signup_with_its_sources_in_the_thread(
    factory: sessionmaker[Session],
) -> None:
    _seed(factory, ["ada@studio.it"])
    async with Client(build_server(factory, lambda: IVAN)) as client:
        lead_rows = _payload(await client.call_tool("list_talenti", {"stato": "lead"}))
        signup_id = lead_rows["items"][0]["id"]
        created = _payload(
            await client.call_tool(
                "create_freelancer_from_signup",
                {
                    "signup_id": signup_id,
                    "nome": "Ada",
                    "cognome": "Lovelace",
                    "posizione": "Backend developer",
                    "fonti": ["https://www.linkedin.com/in/ada"],
                },
            )
        )
        assert created["email"] == "ada@studio.it"
        assert created["compilata_da"] == "admin" and created["completa"] is False
        assert created["cv_filename"] is None and created["tariffa_giornaliera"] is None
        assert created["commenti"][0]["autore"] == "Ivan"
        assert "https://www.linkedin.com/in/ada" in created["commenti"][0]["testo"]
        # The card replaces the lead in the merged list, and the lead's own id now
        # answers the card: what «Talenti» shows for that person.
        listed = _payload(await client.call_tool("list_talenti", {}))
        assert [item["id"] for item in listed["items"]] == [created["id"]]
        answered = _payload(await client.call_tool("get_talento", {"talento_id": signup_id}))
        assert answered["id"] == created["id"] and answered["email"] == "ada@studio.it"

        refused = await client.call_tool(
            "create_freelancer_from_signup",
            {"signup_id": signup_id, "nome": "Ada", "cognome": "Lovelace", "fonti": []},
        )
        assert refused.is_error
    _wipe(factory)


async def test_login_stats_reads_an_empty_hub_and_a_card_carries_its_count(
    factory: sessionmaker[Session],
) -> None:
    freelancer_id = _seed_freelancer(factory)
    async with Client(build_server(factory, lambda: IVAN)) as client:
        stats = _payload(await client.call_tool("login_stats", {}))
        assert (stats["totale"], stats["membri"], stats["membri_totali"]) == (0, 0, 1)
        assert stats["recenti"] == []
        card = _payload(await client.call_tool("get_freelancer", {"freelancer_id": freelancer_id}))
        assert card["accessi"] == 0 and card["ultimo_accesso"] is None
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
        recenti = _payload(await client.call_tool("login_stats", {}))["recenti"]
        assert [row["utm_campaign"] for row in recenti] == ["outreach-2026-09-r2", None]
        assert (recenti[0]["utm_source"], recenti[0]["utm_content"], recenti[0]["utm_term"]) == (
            "email",
            "cv",
            "11425b70",
        )
        assert recenti[1]["utm_term"] is None and recenti[1]["origine"] is None
    _wipe(factory)


async def test_the_cv_is_read_as_text_and_a_card_without_one_answers_a_sentence(
    factory: sessionmaker[Session],
) -> None:
    from decimal import Decimal

    from rebase_core.freelancers import FreelancerService
    from rebase_core.schemas import FreelancerCreate, FreelancerDraft

    session = factory()
    try:
        with_cv, _ = FreelancerService(session).apply(
            FreelancerCreate(
                nome="Ada",
                cognome="Lovelace",
                email="ada@studio.it",
                tariffa_giornaliera=Decimal("450"),
                posizione="Backend developer",
                remoto="remoto",
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
                nome="Grace",
                cognome="Hopper",
                posizione="Compiler engineer",
                fonti=["https://example.com/grace"],
            ),
            "MCP",
        )
    finally:
        session.close()

    async with Client(build_server(factory, lambda: IVAN)) as client:
        read = _payload(
            await client.call_tool("read_freelancer_cv", {"freelancer_id": str(with_cv.id)})
        )
        assert read["filename"] == "cv.pdf"
        assert read["pagine"] == 6
        assert "prima fattura" in read["testo"]
        assert read["troncato"] is False

        refused = await client.call_tool(
            "read_freelancer_cv", {"freelancer_id": str(without_cv.id)}
        )
        assert refused.is_error
        assert "cv" in refused.content[0].text.lower()

        listed = _payload(await client.call_tool("list_talenti", {}))
        assert all("testo" not in item and "cv_bytes" not in item for item in listed["items"])
    _wipe(factory)


async def test_get_talento_answers_a_card_with_the_detail_and_a_lead_with_the_row(
    factory: sessionmaker[Session],
) -> None:
    """`get_talento` is the detail screen one id away: a card gets the full
    `FreelancerDetail` the admin page reads (REB-284), a bare sign-up gets its
    `list_talenti` row back, and an id in neither table is a sentence."""
    freelancer_id = _seed_freelancer(factory)
    _seed(factory, ["grace@studio.it"])
    async with Client(build_server(factory, lambda: IVAN)) as client:
        lead_id = _payload(await client.call_tool("list_talenti", {"stato": "lead"}))["items"][0][
            "id"
        ]

        card = _payload(await client.call_tool("get_talento", {"talento_id": freelancer_id}))
        assert card["id"] == freelancer_id
        assert card["email"] == "ada@studio.it"
        # The detail's own fields, the ones the list row does not carry. Without a
        # `settings`/`http` on this server the Pigro lookup is off, not an error.
        assert "commenti" in card and "iscrizione_utm" in card and "ultimi_accessi" in card
        assert card["pigro_slug"] is None

        lead = _payload(await client.call_tool("get_talento", {"talento_id": lead_id}))
        assert (lead["id"], lead["stato"], lead["origine"]) == (lead_id, "lead", "form")
        assert lead["email"] == "grace@studio.it"

        missing = await client.call_tool(
            "get_talento", {"talento_id": "01a00000-0000-7000-8000-0000000000fe"}
        )
        assert missing.is_error
        assert "talento" in missing.content[0].text and "non trovato" in missing.content[0].text
    _wipe(factory)


# ---- Pigro: the hours report and the link (REB-501) ------------------------------------


def _activate_and_sign(
    factory: sessionmaker[Session],
    match_id: str,
    pigro_stato: str | None = "da_collegare",
    **columns: Any,
) -> None:
    """A match as a signature leaves it: `test_match_tools._seed` writes a draft, so its
    letter and its match are pushed to `firmato`/`attivo` by hand here, the way
    `test_matches_api.py`'s own `_signed_match` does over HTTP. `pigro_stato` defaults to
    `da_collegare`, the state `SigningService._confirm_completion` stamps beside
    `attivo` (B3), which a raw SQL update like this one does not run on its own."""
    from sqlalchemy import update

    from rebase_core.models import ContractDocument, Match

    session = factory()
    try:
        session.execute(
            update(ContractDocument)
            .where(ContractDocument.match_id == UUID(match_id))
            .values(stato="firmato")
        )
        session.execute(
            update(Match)
            .where(Match.id == UUID(match_id))
            .values(stato="attivo", pigro_stato=pigro_stato, **columns)
        )
        session.commit()
    finally:
        session.close()


async def test_link_match_to_pigro_links_an_active_match_as_the_calling_admin(
    factory: sessionmaker[Session],
) -> None:
    """«Riprova su Pigro» over MCP: the recorded `HttpCall` (`fakes_pigro`, moved here on
    this branch) plays the CRM's answer, and the trail says which admin asked."""
    from fakes_pigro import DEAL, DEAL_URL, PIGRO, TOKEN, RecordedPigro, linked_body
    from test_match_tools import _seed
    from test_match_tools import _wipe as _wipe_matches

    from rebase_core.audit import AdminActionService
    from rebase_core.config import Settings
    from rebase_core.engagements import EngagementService

    _card, match_id = _seed(factory)
    _activate_and_sign(factory, match_id)
    fake = RecordedPigro([(201, linked_body())])
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None, pigro_api_url=PIGRO, pigro_engagements_token=TOKEN
    )
    server = build_server(
        factory,
        lambda: IVAN,
        settings=settings,
        engagements=lambda session: EngagementService(session, settings, fake, sender=None),
    )
    try:
        async with Client(server) as client:
            body = _payload(await client.call_tool("link_match_to_pigro", {"match_id": match_id}))
        assert (body["stato"], body["pigro_stato"]) == ("attivo", "collegato")
        assert (body["pigro_slug"], body["pigro_deal_id"], body["pigro_url"]) == (
            "ada-lovelace",
            str(DEAL),
            DEAL_URL,
        )
        [(method, url, headers, _sent)] = fake.calls
        assert (method, url) == ("PUT", f"{PIGRO}/api/rebase/engagements/{match_id}")
        assert headers["Authorization"] == f"Bearer {TOKEN}"
        session = factory()
        try:
            # `_seed` already left a `match_created` entry; the trail is newest first.
            newest = AdminActionService(session).timeline("match", UUID(match_id))[0]
            assert (newest.kind, newest.admin_id) == ("pigro_link", IVAN.id)
        finally:
            session.close()
    finally:
        _wipe_matches(factory)


async def test_link_match_to_pigro_without_a_token_is_a_sentence(
    factory: sessionmaker[Session],
) -> None:
    from test_match_tools import _seed
    from test_match_tools import _wipe as _wipe_matches

    _card, match_id = _seed(factory)
    _activate_and_sign(factory, match_id)
    try:
        async with Client(build_server(factory, lambda: IVAN)) as client:
            refused = await client.call_tool("link_match_to_pigro", {"match_id": match_id})
        assert refused.is_error
        assert "Consuntivo non configurato" in refused.content[0].text
    finally:
        _wipe_matches(factory)


async def test_get_match_report_groups_the_crms_rows_by_day_week_and_month(
    factory: sessionmaker[Session],
) -> None:
    """«Consuntivo» over MCP, against a recorded `GET .../report`: the CRM's rows summed
    the way the admin's own page reads them, with the invoice each day sits on."""
    from fakes_pigro import DEAL, DEAL_URL, PIGRO, TOKEN, RecordedPigro
    from test_match_tools import _seed
    from test_match_tools import _wipe as _wipe_matches

    from rebase_core.config import Settings
    from rebase_core.engagements import EngagementService

    fattura = {
        "id": "0192e0a0-0000-7000-8000-0000000f0012",
        "tipo": "fattura",
        "anno": 2026,
        "numero": 12,
        "stato": "emessa",
        "stato_pagamento": "da_incassare",
        "data": "2026-10-31",
    }
    crm_report = {
        "slug": "ada-lovelace",
        "deal_url": DEAL_URL,
        "deal": {"id": str(DEAL), "nome": "Lettera n. 2026-001", "stato": "in corso"},
        "giorni": [
            {"data": "2026-10-01", "ore": "8.00", "descrizione": "Setup", "fattura": fattura},
            {"data": "2026-10-02", "ore": "4.00", "descrizione": "API", "fattura": None},
        ],
        "totale_ore": "12.00",
        "ore_fatturate": "8.00",
        "ore_non_fatturate": "4.00",
        "fatture": [{**fattura, "ore": "8.00"}],
    }
    _card, match_id = _seed(factory)
    _activate_and_sign(
        factory, match_id, pigro_stato="collegato", pigro_url=DEAL_URL, giorni_previsti=40
    )
    fake = RecordedPigro([(200, json.dumps(crm_report).encode())])
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None, pigro_api_url=PIGRO, pigro_engagements_token=TOKEN
    )
    server = build_server(
        factory,
        lambda: IVAN,
        settings=settings,
        engagements=lambda session: EngagementService(session, settings, fake, sender=None),
    )
    try:
        async with Client(server) as client:
            body = _payload(
                await client.call_tool(
                    "get_match_report",
                    {"match_id": match_id, "da": "2026-10-01", "a": "2026-10-31"},
                )
            )
        assert (body["match_id"], body["pigro_stato"]) == (match_id, "collegato")
        assert (body["giorni_previsti"], body["ore_previste"]) == (40, "320.00")
        assert (body["totale_ore"], body["avanzamento"]) == ("12.00", "3.75")
        assert body["per_giorno"][0] == {
            "data": "2026-10-01",
            "ore": "8.00",
            "descrizioni": ["Setup"],
            "fatture": ["12/2026"],
            "ore_per_fattura": [{"numero": "12/2026", "tipo": "fattura", "ore": "8.00"}],
        }
        assert body["fatture"] == [
            {
                "numero": "12/2026",
                "tipo": "fattura",
                "data": "2026-10-31",
                "stato": "emessa",
                "stato_pagamento": "da_incassare",
                "ore": "8.00",
            }
        ]
        [(method, url, _headers, _sent)] = fake.calls
        assert (method, url) == (
            "GET",
            f"{PIGRO}/api/rebase/engagements/{match_id}/report?da=2026-10-01&a=2026-10-31",
        )
    finally:
        _wipe_matches(factory)


async def test_get_match_report_of_an_unlinked_match_is_the_states_sentence(
    factory: sessionmaker[Session],
) -> None:
    from test_match_tools import _seed
    from test_match_tools import _wipe as _wipe_matches

    from rebase_core.config import Settings
    from rebase_core.engagements import EngagementService

    _card, match_id = _seed(factory)
    _activate_and_sign(factory, match_id)
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None, pigro_api_url="https://pigro.test", pigro_engagements_token="un-token"
    )
    server = build_server(
        factory,
        lambda: IVAN,
        settings=settings,
        engagements=lambda session: EngagementService(
            session, settings, lambda *a: (200, b"{}"), sender=None
        ),
    )
    try:
        async with Client(server) as client:
            refused = await client.call_tool("get_match_report", {"match_id": match_id})
        assert refused.is_error
        assert "Pigro non ha ancora il deal" in refused.content[0].text
    finally:
        _wipe_matches(factory)
