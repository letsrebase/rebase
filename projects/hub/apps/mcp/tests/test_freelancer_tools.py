"""The freelancer tools: read a candidate, move them, write a card from a signup, vetted."""

import json
from decimal import Decimal

from fakes_cards import PERSONA, card_response
from mcp import Client
from mcp_tool_support import (
    IVAN,
    PDF,
    _payload,
    _seed,
    _seed_admin,
    _seed_freelancer,
    _seed_freelancer_with_text_cv,
    _wipe,
    _wipe_team,
)
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from rebase_core.llm import RecordingCall
from rebase_mcp.server import build_server


async def test_the_admin_tools_read_and_move_a_candidate_without_the_cv(
    factory: sessionmaker[Session],
) -> None:
    from decimal import Decimal

    from rebase_core.freelancer_schemas import FreelancerCreate
    from rebase_core.freelancers import FreelancerService

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
    _seed_admin(factory)

    async with Client(build_server(factory, lambda: IVAN)) as client:
        listed = _payload(await client.call_tool("list_talenti", {"campi": ["email"]}))
        assert listed["totale"] == 1
        assert listed["items"][0]["email"] == "ada@studio.it"
        assert "cv_bytes" not in listed["items"][0]

        moved = _payload(
            await client.call_tool(
                "set_freelancer_status",
                {"freelancer_id": str(row.id), "stato": "contattato"},
            )
        )
        assert moved["stato"] == "contattato"
        audit = await client.call_tool("get_freelancer_audit", {"freelancer_id": str(row.id)})
        assert audit.structured_content is not None
        [entry] = audit.structured_content["result"]
        assert entry["kind"] == "status_changed"
        assert entry["payload"] == {"before": "nuovo", "after": "contattato", "fonte": "mcp"}

        refused = await client.call_tool(
            "set_freelancer_status", {"freelancer_id": str(row.id), "stato": "forse"}
        )
        assert refused.is_error
        assert "stato" in refused.content[0].text

        missing = await client.call_tool("get_company", {"company_id": str(row.id)})
        assert missing.is_error

        tools = (await client.list_tools()).tools
        status_tool = next(t for t in tools if t.name == "set_freelancer_status")
        # The tool takes the state alone: a remark is a note of its own.
        assert "note" not in status_tool.input_schema["properties"]
        names = {tool.name for tool in tools}
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
            "list_duplicati",
            "merge_freelancers",
            "add_freelancer_note",
            "update_note",
            "delete_note",
            "list_aziende",
            "get_company",
            "set_company_status",
            "override_company",
            "delete_company",
            "restore_company",
            "get_company_audit",
            "revert_company_action",
            "add_company_note",
            "list_team_requests",
            "list_team_proposals",
            "list_ai_config",
            "get_team_request",
            "set_team_request_summary",
            "contact_team_talents",
            "set_team_request_status",
            "grant_talent_cloud",
            "revoke_talent_cloud",
            "list_talent_cloud_grants",
            "guide_stats",
            "login_stats",
            "list_campagne",
            "get_campagna",
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
            "create_company",
            "link_company_to_pigro",
            "get_match_report",
            "set_freelancer_tax_data",
        }
    _wipe(factory)


async def test_a_card_is_written_from_a_signup_with_its_sources_in_the_thread(
    factory: sessionmaker[Session],
) -> None:
    _seed(factory, ["ada@studio.it"])
    _seed_admin(factory)
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
        assert (created["note"][0]["autore"], created["note"][0]["fonte"]) == (
            "Ivan Sala",
            "mcp",
        )
        assert "https://www.linkedin.com/in/ada" in created["note"][0]["testo"]
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
    _seed_admin(factory)
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


async def test_get_freelancer_carries_the_cvs_contacts_and_no_other_tool_does(
    factory: sessionmaker[Session],
) -> None:
    """REB-855: what the CV says of the person is an admin's, on `get_freelancer` and
    nowhere else: the anonymous card, the list and the talent row stay without it."""
    freelancer_id = _seed_freelancer_with_text_cv(
        factory, "Ada Lovelace, backend developer a Torino da nove anni: Python, FastAPI, AWS."
    )
    session = factory()
    try:
        session.execute(
            text("UPDATE users SET telefono = '02 5550199' WHERE email = 'ada@studio.it'")
        )
        session.commit()
    finally:
        session.close()
    _seed_admin(factory)
    llm = RecordingCall([card_response(persona=PERSONA, employers=["Initech Labs"])])
    async with Client(build_server(factory, lambda: IVAN, card_llm=lambda: llm)) as client:
        before = _payload(
            await client.call_tool("get_freelancer", {"freelancer_id": freelancer_id})
        )
        assert before["contatti_cv"] is None and before["differenze_cv"] == []
        assert before["telefono"] == "02 5550199"

        await client.call_tool("regenerate_freelancer_card", {"freelancer_id": freelancer_id})
        read = _payload(await client.call_tool("get_freelancer", {"freelancer_id": freelancer_id}))
        assert read["contatti_cv"]["email"] == "ada.lovelace@example.org"
        assert read["contatti_cv"]["telefono"] == "+39 333 123 4567"
        assert read["contatti_cv"]["aziende"] == ["Initech Labs"]
        assert [d["campo"] for d in read["differenze_cv"]] == ["email", "telefono"]

        others = [
            await client.call_tool("get_freelancer_card", {"freelancer_id": freelancer_id}),
            await client.call_tool("list_talenti", {"campi": ["tutti"]}),
            await client.call_tool("get_talento", {"talento_id": freelancer_id}),
        ]
        for other in others:
            body = json.dumps(_payload(other))
            for secret in ("ada.lovelace@example.org", "333 123 4567", "Initech", "contatti_cv"):
                assert secret not in body, secret
    _wipe(factory)


async def test_set_freelancer_vetted_toggles_and_a_repeat_keeps_the_first_date(
    factory: sessionmaker[Session],
) -> None:
    freelancer_id = _seed_freelancer(factory)
    _seed_admin(factory)
    async with Client(build_server(factory, lambda: IVAN)) as client:
        before = _payload(await client.call_tool("list_talenti", {"campi": ["tutti"]}))["items"][0]
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

        listed = _payload(await client.call_tool("list_talenti", {"campi": ["tutti"]}))
        assert listed["items"][0]["vetted_at"] == first

        off = _payload(
            await client.call_tool(
                "set_freelancer_vetted", {"freelancer_id": freelancer_id, "vetted": False}
            )
        )
        assert off["vetted_at"] is None
    _wipe_team(factory)
