"""The team requests, the proposals and the talent cloud over MCP."""

import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from fakes_cards import CARD, MODEL
from mcp import Client
from mcp_tool_support import IVAN, _payload, _seed_admin, _seed_company, _wipe_team
from sqlalchemy import update
from sqlalchemy.orm import Session, sessionmaker

from rebase_core.config import Settings
from rebase_core.mail import RecordingSender
from rebase_core.models import Freelancer, FreelancerCard, TeamProposal, User
from rebase_core.team_availability import TeamAvailabilityService
from rebase_core.team_requests import TeamRequestService
from rebase_core.team_schemas import TeamRequestCreate
from rebase_mcp.server import build_server


def _team_settings(**overrides: Any) -> Settings:
    return Settings(_env_file=None, **overrides)  # type: ignore[call-arg]


def _talent_with_card(session: Session, n: int = 1) -> UUID:
    """A live freelancer with an anonymous card already written (`CARD`/`MODEL`,
    `fakes_cards.py`), the way `TeamBuilder.catalogue_lines` and `.get`'s
    `cloud_visible` need one to keep a proposal's member in a read."""
    user = User(email=f"talento{n}@studio.it", first_name=f"Ada{n}", last_name=f"Lovelace{n}")
    session.add(user)
    session.flush()
    row = Freelancer(
        user_id=user.id,
        remote="remote",
        daily_rate=Decimal("450.00"),
        status="new",
        job_title="Backend developer",
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


def _proposal_row(session: Session, members: list[UUID], *, origin: str = "public") -> UUID:
    row = TeamProposal(
        description=(
            "Rifacciamo il gestionale degli ordini: un backend in Python con FastAPI, "
            "sei mesi, da remoto."
        ),
        summary="Un'azienda di logistica rifà il gestionale degli ordini, backend in Python.",
        location={"on_site": False, "place": None},
        team=[
            {
                "position": index,
                "freelancer_id": str(freelancer_id),
                "role": "Backend developer",
                "reasoning": "Nove anni di API in Python.",
                "week_days": 5,
            }
            for index, freelancer_id in enumerate(members, start=1)
        ],
        economics={"day": None, "month": None, "days_month": 22},
        model=MODEL,
        input_tokens=5200,
        output_tokens=640,
        cache_read_tokens=0,
        origin=origin,
        created_at=datetime.now(UTC) - timedelta(minutes=5),
    )
    session.add(row)
    session.commit()
    return row.id


def _team_request(
    factory: sessionmaker[Session], members: list[UUID], *, company: str = "Uno Srl"
) -> str:
    session = factory()
    try:
        proposal_id = _proposal_row(session, members)
        read, _mail = TeamRequestService(session, settings=_team_settings()).create(
            TeamRequestCreate(
                proposal_id=proposal_id,
                company=company,
                email="wile@acme.it",
                phone="+39 345 1234567",
            ),
            origin="public",
            user_id=None,
            company_id=None,
        )
        return str(read.id)
    finally:
        session.close()


async def test_team_request_tools_list_and_read_the_admins_page(
    factory: sessionmaker[Session],
) -> None:
    session = factory()
    member = _talent_with_card(session)
    session.close()
    ids = [
        _team_request(factory, [member], company=company)
        for company in ("Uno Srl", "Due Srl", "Tre Srl")
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
        assert (item["company"], item["origin"], item["status"]) == ("Uno Srl", "public", "new")
        assert (item["total_talents"], item["yes_talents"]) == (1, 0)

        only = _payload(await client.call_tool("list_team_requests", {"status": "closed"}))
        assert only["items"] == []
        bad = await client.call_tool("list_team_requests", {"status": "persa"})
        assert bad.is_error and "status" in bad.content[0].text

        detail = _payload(await client.call_tool("get_team_request", {"request_id": ids[0]}))
        assert detail["email"] == "wile@acme.it" and detail["phone"] == "+39 345 1234567"
        assert detail["proposal"]["team"][0]["freelancer_id"] == str(member)
        [talent] = detail["talents"]
        assert (talent["first_name"], talent["last_name"]) == ("Ada1", "Lovelace1")
        assert talent["daily_rate"] == "450.00"

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
    request_id = _team_request(factory, [member], company="Acme Srl")
    _seed_admin(factory)

    async with Client(build_server(factory, lambda: IVAN, settings=_team_settings())) as client:
        summary = "Un'azienda rifà il gestionale, sei mesi."
        edited = _payload(
            await client.call_tool(
                "set_team_request_summary", {"request_id": request_id, "summary": summary}
            )
        )
        assert edited["summary"] == summary

        refused = await client.call_tool(
            "set_team_request_summary",
            {"request_id": request_id, "summary": "ACME rifà il gestionale."},
        )
        assert refused.is_error
        assert "nomina l'azienda" in refused.content[0].text

        blank = await client.call_tool(
            "set_team_request_summary", {"request_id": request_id, "summary": "   "}
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
                {"request_id": request_id, "status": "contacted", "note": "Richiamare."},
            )
        )
        assert moved["status"] == "contacted" and moved["contacted_at"] is not None
        assert moved["note"] == "Richiamare."

        cleared = _payload(
            await client.call_tool(
                "set_team_request_status", {"request_id": request_id, "status": "closed"}
            )
        )
        assert cleared["status"] == "closed" and cleared["closed_at"] is not None
        # `note` omitted: the note from the previous call is untouched.
        assert cleared["note"] == "Richiamare."

        refused = await client.call_tool(
            "set_team_request_status", {"request_id": request_id, "status": "persa"}
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
            {"request_id": request_id, "status": "contacted", "note": "x" * 4001},
        )
        assert long.is_error
        assert long.content[0].text.endswith(": note: al massimo 4000 caratteri")
        nul = await client.call_tool(
            "set_team_request_status",
            {"request_id": request_id, "status": "closed", "note": "Richiamare\x00."},
        )
        assert nul.is_error
        assert nul.content[0].text.endswith(
            ": note: il testo contiene un carattere nullo (\\x00), non ammesso"
        )

        untouched = _payload(await client.call_tool("get_team_request", {"request_id": request_id}))
        assert untouched["status"] == "new" and untouched["note"] is None
        assert untouched["contacted_at"] is None and untouched["closed_at"] is None
    _wipe_team(factory)


async def test_contact_team_talents_mails_once_then_snooze_only_the_silent(
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
        assert contacted["status"] == "contacted"
        assert len(sender.sent) == 2
        addresses = {mail.to for mail in sender.sent}
        assert addresses == {"talento1@studio.it", "talento2@studio.it"}
        assert all(mail.subject == "Un progetto per te: sei disponibile?" for mail in sender.sent)

        again = await client.call_tool("contact_team_talents", {"request_id": request_id})
        assert again.is_error
        assert "già stati contattati" in again.content[0].text

        first_mail = next(mail for mail in sender.sent if mail.to == "talento1@studio.it")
        found = re.search(r"[?&]t=([A-Za-z0-9_-]+)&r=yes", first_mail.text)
        assert found
        session2 = factory()
        TeamAvailabilityService(session2, settings=_team_settings()).answer(found.group(1), "yes")
        session2.close()

        snooze = _payload(
            await client.call_tool(
                "contact_team_talents", {"request_id": request_id, "only_silent": True}
            )
        )
        assert snooze["status"] == "contacted"
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


async def test_list_team_proposals_reads_every_ask_with_its_request(
    factory: sessionmaker[Session],
) -> None:
    """0028: the asks newest first, the request filed on one, and an attempt Claude
    did not answer beside them; a word the filters do not take names the field."""
    session = factory()
    member = _talent_with_card(session)
    session.close()
    filed = _team_request(factory, [member], company="Uno Srl")
    session = factory()
    never_filed = _proposal_row(session, [member], origin="cloud")
    session.execute(
        update(TeamProposal)
        .where(TeamProposal.id == never_filed)
        .values(people=2, created_at=datetime.now(UTC) - timedelta(minutes=4))
    )
    attempt = TeamProposal(
        description="Una pipeline dati su Google Cloud, in sede a Milano, quattro mesi di lavoro.",
        summary="",
        location={"on_site": False, "place": None},
        team=[],
        economics={"day": None, "month": None, "days_month": 22},
        model="",
        input_tokens=0,
        output_tokens=0,
        cache_read_tokens=0,
        origin="public",
        error="llm_unavailable",
        created_at=datetime.now(UTC) - timedelta(minutes=3),
    )
    session.add(attempt)
    session.commit()
    attempt_id = str(attempt.id)
    session.close()

    async with Client(build_server(factory, lambda: IVAN, settings=_team_settings())) as client:
        page = _payload(await client.call_tool("list_team_proposals", {"limit": 2}))
        assert [item["id"] for item in page["items"]] == [attempt_id, str(never_filed)]
        failed, cloud = page["items"]
        assert (failed["error"], failed["members"], failed["request_id"]) == (
            "llm_unavailable",
            0,
            None,
        )
        assert (cloud["origin"], cloud["people"], cloud["members"], cloud["request_id"]) == (
            "cloud",
            2,
            1,
            None,
        )
        # REB-607: who the proposal held, by name and id, and nobody on an attempt.
        [held] = cloud["team"]
        assert (held["freelancer_id"], held["first_name"], held["last_name"], held["role"]) == (
            str(member),
            "Ada1",
            "Lovelace1",
            "Backend developer",
        )
        assert failed["team"] == []
        rest = _payload(
            await client.call_tool(
                "list_team_proposals", {"limit": 2, "cursor": page["next_cursor"]}
            )
        )
        [first] = rest["items"]
        assert first["request_id"] == filed and first["error"] is None
        assert rest["next_cursor"] is None

        only = _payload(await client.call_tool("list_team_proposals", {"outcome": "error"}))
        assert [item["id"] for item in only["items"]] == [attempt_id]
        bad = await client.call_tool("list_team_proposals", {"origin": "sito"})
        assert bad.is_error and "origin" in bad.content[0].text
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
        assert granted["company"] == "ACME Srl"
        assert granted["mail_sent"] is True
        assert len(sender.sent) == 1
        assert sender.sent[0].subject == "Il talent cloud di rebase è aperto per ACME Srl"

        again = _payload(await client.call_tool("grant_talent_cloud", {"company_id": company_id}))
        assert again["id"] == granted["id"] and again["mail_sent"] is False
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
