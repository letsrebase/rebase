"""The hub's MCP tools over its own database: the reads, the status moves, the comments."""

import json
import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from fakes_cards import CARD, MODEL, card_response, text_pdf
from mcp import Client
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from rebase_core.admin_tokens import AdminRead
from rebase_core.config import Settings
from rebase_core.llm import LlmResponse, RecordingCall
from rebase_core.mail import RecordingSender
from rebase_core.models import Freelancer, FreelancerCard, TeamProposal, User
from rebase_core.perks import guide_bytes
from rebase_core.schemas import SignupCreate
from rebase_core.service import SignupService
from rebase_core.team_requests import TeamRequestService
from rebase_core.team_schemas import TeamRequestCreate
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
            "set_freelancer_vetted",
            "get_freelancer_card",
            "regenerate_freelancer_card",
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
            "list_team_requests",
            "get_team_request",
            "set_team_request_summary",
            "contact_team_talents",
            "set_team_request_status",
            "propose_team",
            "grant_talent_cloud",
            "revoke_talent_cloud",
            "list_talent_cloud_grants",
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


async def test_a_card_from_a_signup_reads_its_rate_with_italian_thousands(
    factory: sessionmaker[Session],
) -> None:
    """REB-485: through the same reading as every other amount tool, and a rate that is
    not one is refused in a sentence naming the field, not a `decimal` traceback."""
    _seed(factory, ["ada@studio.it"])
    async with Client(build_server(factory, lambda: IVAN)) as client:
        lead_rows = _payload(await client.call_tool("list_talenti", {"stato": "lead"}))
        signup_id = lead_rows["items"][0]["id"]
        draft = {
            "signup_id": signup_id,
            "nome": "Ada",
            "cognome": "Lovelace",
            "fonti": ["https://www.linkedin.com/in/ada"],
        }
        refused = await client.call_tool(
            "create_freelancer_from_signup", {**draft, "tariffa_giornaliera": "tanto"}
        )
        assert refused.is_error and "tariffa_giornaliera" in refused.content[0].text
        created = _payload(
            await client.call_tool(
                "create_freelancer_from_signup", {**draft, "tariffa_giornaliera": "1.500"}
            )
        )
        # The draft answers the value it wrote, before the column's two decimals.
        assert Decimal(created["tariffa_giornaliera"]) == Decimal("1500")
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


# ---- the cards, the vetted flag, the team requests and the cloud (REB-520, D4) --------------


def _team_settings(**overrides: Any) -> Settings:
    return Settings(_env_file=None, **overrides)  # type: ignore[call-arg]


def _seed_freelancer_with_text_cv(factory: sessionmaker[Session], text: str) -> str:
    """Like `_seed_freelancer`, but with a CV `pypdf` reads text out of (`PDF`, the
    module's own fixture, is a scan with none): the card writer needs a real CV to
    write anything from."""
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
            text_pdf(text),
            "cv.pdf",
            "application/pdf",
        )
        return str(row.id)
    finally:
        session.close()


def _talent_with_card(session: Session, n: int = 1) -> UUID:
    """A live freelancer with an anonymous card already written (`CARD`/`MODEL`,
    `fakes_cards.py`), the way `TeamBuilder.catalogue_lines` and `.get`'s
    `cloud_visible` need one to keep a proposal's member in a read."""
    user = User(email=f"talento{n}@studio.it", nome=f"Ada{n}", cognome=f"Lovelace{n}")
    session.add(user)
    session.flush()
    row = Freelancer(
        user_id=user.id,
        remoto="remoto",
        tariffa_giornaliera=Decimal("450.00"),
        stato="nuovo",
        posizione="Backend developer",
    )
    session.add(row)
    session.flush()
    session.add(
        FreelancerCard(
            freelancer_id=row.id,
            cv_sha256="0" * 64,
            card=CARD,
            model=MODEL,
            input_tokens=1200,
            output_tokens=180,
            generated_at=datetime.now(UTC),
        )
    )
    session.commit()
    return row.id


def _proposal_row(session: Session, members: list[UUID], *, origine: str = "pubblico") -> UUID:
    row = TeamProposal(
        descrizione=(
            "Rifacciamo il gestionale degli ordini: un backend in Python con FastAPI, "
            "sei mesi, da remoto."
        ),
        riassunto="Un'azienda di logistica rifà il gestionale degli ordini, backend in Python.",
        luogo={"locale": False, "dove": None},
        team=[
            {
                "posizione": index,
                "freelancer_id": str(freelancer_id),
                "ruolo": "Backend developer",
                "motivazione": "Nove anni di API in Python.",
                "giorni_settimana": 5,
            }
            for index, freelancer_id in enumerate(members, start=1)
        ],
        economia={"giorno": None, "mese": None, "giorni_mese": 22},
        model=MODEL,
        input_tokens=5200,
        output_tokens=640,
        cache_read_tokens=0,
        origine=origine,
        created_at=datetime.now(UTC) - timedelta(minutes=5),
    )
    session.add(row)
    session.commit()
    return row.id


def _team_request(
    factory: sessionmaker[Session], members: list[UUID], *, azienda: str = "Uno Srl"
) -> str:
    session = factory()
    try:
        proposal_id = _proposal_row(session, members)
        read, _mail = TeamRequestService(session, settings=_team_settings()).create(
            TeamRequestCreate(
                proposal_id=proposal_id,
                azienda=azienda,
                email="wile@acme.it",
                telefono="+39 345 1234567",
            ),
            origine="pubblico",
            user_id=None,
            company_id=None,
        )
        return str(read.id)
    finally:
        session.close()


def _proposal_response(position: str = "t1") -> LlmResponse:
    body = {
        "riassunto": "Un'azienda di logistica rifà il gestionale, backend in Python.",
        "luogo": {"locale": False, "dove": None},
        "team": [
            {
                "id": position,
                "ruolo": "Backend developer",
                "motivazione": "Nove anni di API in Python e FastAPI.",
                "giorni_settimana": 5,
            }
        ],
    }
    return LlmResponse(
        text=json.dumps(body),
        stop_reason="end_turn",
        refusal_category=None,
        model=MODEL,
        input_tokens=5200,
        output_tokens=640,
        cache_read_tokens=4800,
    )


def _wipe_team(factory: sessionmaker[Session]) -> None:
    session = factory()
    for table in (
        "team_request_talents",
        "team_requests",
        "team_proposals",
        "admin_actions",
        "talent_cloud_grants",
        "freelancer_cards",
        "comments",
        "freelancers",
        "companies",
        "users",
    ):
        session.execute(text(f"DELETE FROM {table}"))
    session.commit()
    session.close()


def _seed_admin(factory: sessionmaker[Session]) -> None:
    """`IVAN`'s own `users` row: `Freelancer.vetted_by`, `TeamProposal.user_id`,
    `TalentCloudGrant.granted_by`/`revoked_by` and `AdminAction.admin_id` are all a
    real foreign key to `users.id`, which the fake `AdminRead` every test calls as is
    not, on its own -- only the tools that never write one of those (a read, or
    `CardWriter`, which takes no admin at all) can skip this."""
    session = factory()
    session.add(User(id=IVAN.id, email=IVAN.email, nome=IVAN.nome, cognome="Sala", role="admin"))
    session.commit()
    session.close()


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
        assert empty["modalita"] == "remoto"

        off = await client.call_tool("regenerate_freelancer_card", {"freelancer_id": freelancer_id})
        assert off.is_error
        assert "Il team builder è spento." in off.content[0].text

    llm = RecordingCall([card_response()])
    async with Client(build_server(factory, lambda: IVAN, llm=llm)) as client:
        written = _payload(
            await client.call_tool("regenerate_freelancer_card", {"freelancer_id": freelancer_id})
        )
        assert written["card"]["ruolo"] == CARD["ruolo"]
        assert written["model"] == MODEL
        assert len(llm.requests) == 1

        read_back = _payload(
            await client.call_tool("get_freelancer_card", {"freelancer_id": freelancer_id})
        )
        assert read_back["card"] == written["card"]

        listed = _payload(await client.call_tool("list_talenti", {}))
        assert listed["items"][0]["ha_scheda_anonima"] is True
        detail = _payload(await client.call_tool("get_talento", {"talento_id": freelancer_id}))
        assert detail["ha_scheda_anonima"] is True

        missing = await client.call_tool(
            "get_freelancer_card", {"freelancer_id": "01a00000-0000-7000-8000-0000000000fe"}
        )
        assert missing.is_error
    _wipe(factory)


async def test_set_freelancer_vetted_toggles_and_a_repeat_keeps_the_first_date(
    factory: sessionmaker[Session],
) -> None:
    freelancer_id = _seed_freelancer(factory)
    _seed_admin(factory)
    async with Client(build_server(factory, lambda: IVAN)) as client:
        before = _payload(await client.call_tool("list_talenti", {}))["items"][0]
        assert before["vetted_at"] is None and before["ha_scheda_anonima"] is False
        card = _payload(await client.call_tool("get_talento", {"talento_id": freelancer_id}))
        assert card["vetted_at"] is None and card["ha_scheda_anonima"] is False

        vetted = _payload(
            await client.call_tool(
                "set_freelancer_vetted", {"freelancer_id": freelancer_id, "vetted": True}
            )
        )
        assert vetted["vetted_at"] is not None
        first = vetted["vetted_at"]

        again = _payload(
            await client.call_tool(
                "set_freelancer_vetted", {"freelancer_id": freelancer_id, "vetted": True}
            )
        )
        assert again["vetted_at"] == first

        listed = _payload(await client.call_tool("list_talenti", {}))
        assert listed["items"][0]["vetted_at"] == first

        off = _payload(
            await client.call_tool(
                "set_freelancer_vetted", {"freelancer_id": freelancer_id, "vetted": False}
            )
        )
        assert off["vetted_at"] is None
    _wipe_team(factory)


async def test_team_request_tools_list_and_read_the_admins_page(
    factory: sessionmaker[Session],
) -> None:
    session = factory()
    member = _talent_with_card(session)
    session.close()
    ids = [
        _team_request(factory, [member], azienda=azienda)
        for azienda in ("Uno Srl", "Due Srl", "Tre Srl")
    ]

    async with Client(build_server(factory, lambda: IVAN, settings=_team_settings())) as client:
        page = _payload(await client.call_tool("list_team_requests", {"limit": 2}))
        assert [item["id"] for item in page["items"]] == [ids[2], ids[1]]
        rest = _payload(
            await client.call_tool(
                "list_team_requests", {"limit": 2, "cursor": page["next_cursor"]}
            )
        )
        assert [item["id"] for item in rest["items"]] == [ids[0]]
        assert rest["next_cursor"] is None
        item = rest["items"][0]
        assert (item["azienda"], item["origine"], item["stato"]) == ("Uno Srl", "pubblico", "nuova")
        assert (item["talenti_totale"], item["talenti_si"]) == (1, 0)

        only = _payload(await client.call_tool("list_team_requests", {"stato": "chiusa"}))
        assert only["items"] == []
        bad = await client.call_tool("list_team_requests", {"stato": "persa"})
        assert bad.is_error and "stato" in bad.content[0].text

        detail = _payload(await client.call_tool("get_team_request", {"request_id": ids[0]}))
        assert detail["email"] == "wile@acme.it" and detail["telefono"] == "+39 345 1234567"
        assert detail["proposal"]["team"][0]["freelancer_id"] == str(member)
        [talento] = detail["talenti"]
        assert (talento["nome"], talento["cognome"]) == ("Ada1", "Lovelace1")
        assert talento["tariffa_giornaliera"] == "450.00"

        missing = await client.call_tool(
            "get_team_request", {"request_id": "01a00000-0000-7000-8000-0000000000fe"}
        )
        assert missing.is_error
    _wipe_team(factory)


async def test_set_team_request_summary_saves_it_and_refuses_one_naming_the_company(
    factory: sessionmaker[Session],
) -> None:
    session = factory()
    member = _talent_with_card(session)
    session.close()
    request_id = _team_request(factory, [member], azienda="Acme Srl")
    _seed_admin(factory)

    async with Client(build_server(factory, lambda: IVAN, settings=_team_settings())) as client:
        summary = "Un'azienda rifà il gestionale, sei mesi."
        edited = _payload(
            await client.call_tool(
                "set_team_request_summary", {"request_id": request_id, "riassunto": summary}
            )
        )
        assert edited["riassunto"] == summary

        refused = await client.call_tool(
            "set_team_request_summary",
            {"request_id": request_id, "riassunto": "ACME rifà il gestionale."},
        )
        assert refused.is_error
        assert "nomina l'azienda" in refused.content[0].text

        blank = await client.call_tool(
            "set_team_request_summary", {"request_id": request_id, "riassunto": "   "}
        )
        assert blank.is_error
    _wipe_team(factory)


async def test_set_team_request_status_moves_it_and_records_an_optional_note(
    factory: sessionmaker[Session],
) -> None:
    session = factory()
    member = _talent_with_card(session)
    session.close()
    request_id = _team_request(factory, [member])
    _seed_admin(factory)

    async with Client(build_server(factory, lambda: IVAN, settings=_team_settings())) as client:
        moved = _payload(
            await client.call_tool(
                "set_team_request_status",
                {"request_id": request_id, "stato": "contattata", "note": "Richiamare."},
            )
        )
        assert moved["stato"] == "contattata" and moved["contacted_at"] is not None
        assert moved["note"] == "Richiamare."

        cleared = _payload(
            await client.call_tool(
                "set_team_request_status", {"request_id": request_id, "stato": "chiusa"}
            )
        )
        assert cleared["stato"] == "chiusa" and cleared["closed_at"] is not None
        # `note` omitted: the note from the previous call is untouched.
        assert cleared["note"] == "Richiamare."

        refused = await client.call_tool(
            "set_team_request_status", {"request_id": request_id, "stato": "persa"}
        )
        assert refused.is_error
    _wipe_team(factory)


async def test_set_team_request_status_refuses_a_bad_note_before_moving_anything(
    factory: sessionmaker[Session],
) -> None:
    """The note is checked before the state is written: a refused note leaves the
    request where it was and answers the field's sentence, not Pydantic's."""
    session = factory()
    member = _talent_with_card(session)
    session.close()
    request_id = _team_request(factory, [member])
    _seed_admin(factory)

    async with Client(build_server(factory, lambda: IVAN, settings=_team_settings())) as client:
        long = await client.call_tool(
            "set_team_request_status",
            {"request_id": request_id, "stato": "contattata", "note": "x" * 4001},
        )
        assert long.is_error
        assert long.content[0].text.endswith(": note: al massimo 4000 caratteri")
        nul = await client.call_tool(
            "set_team_request_status",
            {"request_id": request_id, "stato": "chiusa", "note": "Richiamare\x00."},
        )
        assert nul.is_error
        assert nul.content[0].text.endswith(
            ": note: il testo contiene un carattere nullo (\\x00), non ammesso"
        )

        untouched = _payload(await client.call_tool("get_team_request", {"request_id": request_id}))
        assert untouched["stato"] == "nuova" and untouched["note"] is None
        assert untouched["contacted_at"] is None and untouched["closed_at"] is None
    _wipe_team(factory)


async def test_contact_team_talents_mails_once_then_rimanda_only_the_silent(
    factory: sessionmaker[Session],
) -> None:
    session = factory()
    members = [_talent_with_card(session, 1), _talent_with_card(session, 2)]
    session.close()
    request_id = _team_request(factory, members)
    _seed_admin(factory)
    sender = RecordingSender()

    async with Client(
        build_server(factory, lambda: IVAN, settings=_team_settings(), sender=sender)
    ) as client:
        contacted = _payload(
            await client.call_tool("contact_team_talents", {"request_id": request_id})
        )
        assert contacted["stato"] == "contattata"
        assert len(sender.sent) == 2
        addresses = {mail.to for mail in sender.sent}
        assert addresses == {"talento1@studio.it", "talento2@studio.it"}
        assert all(mail.subject == "Un progetto per te: sei disponibile?" for mail in sender.sent)

        again = await client.call_tool("contact_team_talents", {"request_id": request_id})
        assert again.is_error
        assert "già stati contattati" in again.content[0].text

        first_mail = next(mail for mail in sender.sent if mail.to == "talento1@studio.it")
        found = re.search(r"[?&]t=([A-Za-z0-9_-]+)&r=si", first_mail.text)
        assert found
        session2 = factory()
        TeamRequestService(session2, settings=_team_settings()).answer(found.group(1), "si")
        session2.close()

        rimanda = _payload(
            await client.call_tool(
                "contact_team_talents", {"request_id": request_id, "only_silent": True}
            )
        )
        assert rimanda["stato"] == "contattata"
        assert len(sender.sent) == 3
        assert sender.sent[2].to == "talento2@studio.it"
    _wipe_team(factory)


async def test_contact_team_talents_without_a_sender_is_the_no_sender_sentence(
    factory: sessionmaker[Session],
) -> None:
    session = factory()
    member = _talent_with_card(session)
    session.close()
    request_id = _team_request(factory, [member])

    async with Client(build_server(factory, lambda: IVAN, settings=_team_settings())) as client:
        refused = await client.call_tool("contact_team_talents", {"request_id": request_id})
        assert refused.is_error
        assert "L'invio delle email non è attivo" in refused.content[0].text
    _wipe_team(factory)


async def test_propose_team_proposes_as_the_admin_and_off_is_the_sentence(
    factory: sessionmaker[Session],
) -> None:
    session = factory()
    member = _talent_with_card(session)
    session.close()
    _seed_admin(factory)
    descrizione = (
        "Rifacciamo il gestionale degli ordini: un backend in Python con FastAPI, "
        "sei mesi, da remoto."
    )
    llm = RecordingCall([_proposal_response()])
    async with Client(
        build_server(factory, lambda: IVAN, settings=_team_settings(), llm=llm)
    ) as client:
        proposed = _payload(await client.call_tool("propose_team", {"descrizione": descrizione}))
        assert proposed["origine"] == "admin"
        assert proposed["team"][0]["freelancer_id"] == str(member)
        assert len(llm.requests) == 1

    async with Client(build_server(factory, lambda: IVAN, settings=_team_settings())) as client:
        off = await client.call_tool("propose_team", {"descrizione": descrizione})
        assert off.is_error
        assert "Il team builder è spento." in off.content[0].text
    _wipe_team(factory)


async def test_grant_and_revoke_talent_cloud_and_list_every_grant(
    factory: sessionmaker[Session],
) -> None:
    company_id = _seed_company(factory)
    _seed_admin(factory)
    sender = RecordingSender()
    async with Client(
        build_server(factory, lambda: IVAN, settings=_team_settings(), sender=sender)
    ) as client:
        granted = _payload(await client.call_tool("grant_talent_cloud", {"company_id": company_id}))
        assert granted["azienda"] == "ACME Srl"
        assert granted["mail_inviata"] is True
        assert len(sender.sent) == 1
        assert sender.sent[0].subject == "Il talent cloud di rebase è aperto per ACME Srl"

        again = _payload(await client.call_tool("grant_talent_cloud", {"company_id": company_id}))
        assert again["id"] == granted["id"] and again["mail_inviata"] is False
        assert len(sender.sent) == 1

        # A list-returning tool's structured content is `{"result": [...]}`, the shape
        # MCP wraps a bare array in.
        listed = _payload(await client.call_tool("list_talent_cloud_grants", {}))["result"]
        assert [g["id"] for g in listed] == [granted["id"]]

        revoked = _payload(
            await client.call_tool("revoke_talent_cloud", {"company_id": company_id})
        )
        assert revoked["revoked_at"] is not None

        again_revoke = await client.call_tool("revoke_talent_cloud", {"company_id": company_id})
        assert again_revoke.is_error

        missing = await client.call_tool(
            "grant_talent_cloud", {"company_id": "01a00000-0000-7000-8000-0000000000fe"}
        )
        assert missing.is_error
    _wipe_team(factory)
