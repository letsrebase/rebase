"""The freelancer tools: read a candidate, move them, write a card from a signup, vetted."""

import json
from decimal import Decimal

from fakes_cards import PERSON, card_response
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
                first_name="Ada",
                last_name="Lovelace",
                email="ada@studio.it",
                daily_rate=Decimal("450"),
                job_title="Backend developer",
                remote="remote",
            ),
            PDF,
            "cv.pdf",
            "application/pdf",
        )
    finally:
        session.close()
    _seed_admin(factory)

    async with Client(build_server(factory, lambda: IVAN)) as client:
        listed = _payload(await client.call_tool("list_talents", {"fields": ["email"]}))
        assert listed["total"] == 1
        assert listed["items"][0]["email"] == "ada@studio.it"
        assert "cv_bytes" not in listed["items"][0]

        moved = _payload(
            await client.call_tool(
                "set_freelancer_status",
                {"freelancer_id": str(row.id), "status": "contacted"},
            )
        )
        assert moved["status"] == "contacted"
        audit = await client.call_tool("get_freelancer_audit", {"freelancer_id": str(row.id)})
        assert audit.structured_content is not None
        [entry] = audit.structured_content["result"]
        assert entry["kind"] == "status_changed"
        assert entry["payload"] == {"before": "new", "after": "contacted", "source": "mcp"}

        refused = await client.call_tool(
            "set_freelancer_status", {"freelancer_id": str(row.id), "status": "forse"}
        )
        assert refused.is_error
        assert "status" in refused.content[0].text

        missing = await client.call_tool("get_company", {"company_id": str(row.id)})
        assert missing.is_error

        tools = (await client.list_tools()).tools
        status_tool = next(t for t in tools if t.name == "set_freelancer_status")
        # The tool takes the state alone: a remark is a note of its own.
        assert "note" not in status_tool.input_schema["properties"]
        names = {tool.name for tool in tools}
        assert names == {
            "create_freelancer_from_signup",
            "list_talents",
            "get_talent",
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
            "list_duplicates",
            "merge_freelancers",
            "add_freelancer_note",
            "update_note",
            "delete_note",
            "list_companies",
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
            "list_campaigns",
            "get_campaign",
            "create_company",
            "link_company_to_pigro",
        }
    _wipe(factory)


async def test_a_card_is_written_from_a_signup_with_its_sources_in_the_thread(
    factory: sessionmaker[Session],
) -> None:
    _seed(factory, ["ada@studio.it"])
    _seed_admin(factory)
    async with Client(build_server(factory, lambda: IVAN)) as client:
        lead_rows = _payload(await client.call_tool("list_talents", {"status": "lead"}))
        signup_id = lead_rows["items"][0]["id"]
        created = _payload(
            await client.call_tool(
                "create_freelancer_from_signup",
                {
                    "signup_id": signup_id,
                    "first_name": "Ada",
                    "last_name": "Lovelace",
                    "job_title": "Backend developer",
                    "sources": ["https://www.linkedin.com/in/ada"],
                },
            )
        )
        assert created["email"] == "ada@studio.it"
        assert created["filled_by"] == "admin" and created["complete"] is False
        assert created["cv_filename"] is None and created["daily_rate"] is None
        assert (created["note"][0]["author"], created["note"][0]["source"]) == (
            "Ivan Sala",
            "mcp",
        )
        assert "https://www.linkedin.com/in/ada" in created["note"][0]["text"]
        # The card replaces the lead in the merged list, and the lead's own id now
        # answers the card: what «Talenti» shows for that person.
        listed = _payload(await client.call_tool("list_talents", {}))
        assert [item["id"] for item in listed["items"]] == [created["id"]]
        answered = _payload(await client.call_tool("get_talent", {"talent_id": signup_id}))
        assert answered["id"] == created["id"] and answered["email"] == "ada@studio.it"

        refused = await client.call_tool(
            "create_freelancer_from_signup",
            {"signup_id": signup_id, "first_name": "Ada", "last_name": "Lovelace", "sources": []},
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
        lead_rows = _payload(await client.call_tool("list_talents", {"status": "lead"}))
        signup_id = lead_rows["items"][0]["id"]
        draft = {
            "signup_id": signup_id,
            "first_name": "Ada",
            "last_name": "Lovelace",
            "sources": ["https://www.linkedin.com/in/ada"],
        }
        refused = await client.call_tool(
            "create_freelancer_from_signup", {**draft, "daily_rate": "tanto"}
        )
        assert refused.is_error and "daily_rate" in refused.content[0].text
        created = _payload(
            await client.call_tool(
                "create_freelancer_from_signup", {**draft, "daily_rate": "1.500"}
            )
        )
        # The draft answers the value it wrote, before the column's two decimals.
        assert Decimal(created["daily_rate"]) == Decimal("1500")
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
            text(
                "INSERT INTO user_contact_points (id, user_id, kind, value, label, is_primary) "
                "SELECT gen_random_uuid(), id, 'phone', '02 5550199', 'work', true "
                "FROM users WHERE email = 'ada@studio.it'"
            )
        )
        session.commit()
    finally:
        session.close()
    _seed_admin(factory)
    llm = RecordingCall([card_response(person=PERSON, employers=["Initech Labs"])])
    async with Client(build_server(factory, lambda: IVAN, card_llm=lambda: llm)) as client:
        before = _payload(
            await client.call_tool("get_freelancer", {"freelancer_id": freelancer_id})
        )
        assert before["cv_contacts"] is None and before["cv_differences"] == []
        assert before["phone"] == "02 5550199"

        await client.call_tool("regenerate_freelancer_card", {"freelancer_id": freelancer_id})
        read = _payload(await client.call_tool("get_freelancer", {"freelancer_id": freelancer_id}))
        assert read["cv_contacts"]["email"] == "ada.lovelace@example.org"
        assert read["cv_contacts"]["phone"] == "+39 333 123 4567"
        assert read["cv_contacts"]["companies"] == ["Initech Labs"]
        assert [d["field"] for d in read["cv_differences"]] == ["email", "phone"]

        others = [
            await client.call_tool("get_freelancer_card", {"freelancer_id": freelancer_id}),
            await client.call_tool("list_talents", {"fields": ["all"]}),
            await client.call_tool("get_talent", {"talent_id": freelancer_id}),
        ]
        for other in others:
            body = json.dumps(_payload(other))
            for secret in ("ada.lovelace@example.org", "333 123 4567", "Initech", "cv_contacts"):
                assert secret not in body, secret
    _wipe(factory)


async def test_set_freelancer_vetted_toggles_and_a_repeat_keeps_the_first_date(
    factory: sessionmaker[Session],
) -> None:
    freelancer_id = _seed_freelancer(factory)
    _seed_admin(factory)
    async with Client(build_server(factory, lambda: IVAN)) as client:
        before = _payload(await client.call_tool("list_talents", {"fields": ["all"]}))["items"][0]
        assert before["vetted_at"] is None and before["has_anonymous_card"] is False
        card = _payload(await client.call_tool("get_talent", {"talent_id": freelancer_id}))
        assert card["vetted_at"] is None and card["has_anonymous_card"] is False

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

        listed = _payload(await client.call_tool("list_talents", {"fields": ["all"]}))
        assert listed["items"][0]["vetted_at"] == first

        off = _payload(
            await client.call_tool(
                "set_freelancer_vetted", {"freelancer_id": freelancer_id, "vetted": False}
            )
        )
        assert off["vetted_at"] is None
    _wipe_team(factory)


async def test_an_admin_adds_an_extra_email_over_the_mcp_and_reads_it_back(
    factory: sessionmaker[Session],
) -> None:
    """REB-944: `override_freelancer` replaces the whole list of extra contact points, the
    login address comes first on every read without an `id`, and an address that is another
    person's is refused with its name."""
    from rebase_core.freelancer_schemas import FreelancerCreate
    from rebase_core.freelancers import FreelancerService

    _seed_admin(factory)
    ids: dict[str, str] = {}
    session = factory()
    try:
        for name, email in (("Ada", "ada@studio.it"), ("Grace", "grace@studio.it")):
            card, _ = FreelancerService(session).apply(
                FreelancerCreate(
                    first_name=name,
                    last_name="Lovelace",
                    email=email,
                    daily_rate=Decimal("450"),
                    job_title="Backend developer",
                    remote="remote",
                ),
                PDF,
                "cv.pdf",
                "application/pdf",
            )
            ids[name] = str(card.id)
    finally:
        session.close()
    ada, grace = ids["Ada"], ids["Grace"]
    async with Client(build_server(factory, lambda: IVAN)) as client:
        written = _payload(
            await client.call_tool(
                "override_freelancer",
                {
                    "freelancer_id": ada,
                    "contact_points": [
                        {
                            "kind": "email",
                            "value": "ada.casa@gmail.com",
                            "label": "personal",
                            "note": "solo per le fatture",
                        },
                        {"kind": "phone", "value": "+39 333 123 4567", "label": "work"},
                    ],
                },
            )
        )
        assert written["phone"] == "+39 333 123 4567"
        read = _payload(await client.call_tool("get_freelancer", {"freelancer_id": ada}))
        assert [
            (p["kind"], p["value"], p["label"], p["is_primary"]) for p in read["contact_points"]
        ] == [
            ("email", "ada@studio.it", "work", True),
            ("email", "ada.casa@gmail.com", "personal", False),
            ("phone", "+39 333 123 4567", "work", True),
        ]
        assert read["contact_points"][0]["id"] is None
        assert read["contact_points"][1]["note"] == "solo per le fatture"
        talent = _payload(await client.call_tool("get_talent", {"talent_id": ada}))
        assert [p["value"] for p in talent["contact_points"]][:2] == [
            "ada@studio.it",
            "ada.casa@gmail.com",
        ]

        taken = await client.call_tool(
            "override_freelancer",
            {
                "freelancer_id": grace,
                "contact_points": [
                    {"kind": "email", "value": "ADA.casa@gmail.com", "label": "other"}
                ],
            },
        )
        assert taken.is_error
        assert "ada.casa@gmail.com" in taken.content[0].text  # type: ignore[union-attr]
        assert "tra i recapiti di un'altra persona" in taken.content[0].text  # type: ignore[union-attr]

        # The list read back can be sent as it is: the login entry is the list's own first
        # line and is not stored.
        again = _payload(
            await client.call_tool(
                "override_freelancer",
                {"freelancer_id": ada, "contact_points": read["contact_points"]},
            )
        )
        assert [p["value"] for p in again["contact_points"]] == [
            "ada@studio.it",
            "ada.casa@gmail.com",
            "+39 333 123 4567",
        ]
        cleared = _payload(
            await client.call_tool(
                "override_freelancer", {"freelancer_id": ada, "contact_points": []}
            )
        )
        assert [p["value"] for p in cleared["contact_points"]] == ["ada@studio.it"]
    _wipe(factory)


async def test_a_card_from_a_signup_takes_the_public_contact_points_and_adds_on_a_rewrite(
    factory: sessionmaker[Session],
) -> None:
    """REB-948: the points found at the sources land on the person's list, the sign-up's
    address stays the primary email, and a second research adds without removing."""
    _seed(factory, ["ada@studio.it"])
    _seed_admin(factory)
    async with Client(build_server(factory, lambda: IVAN)) as client:
        signup_id = _payload(await client.call_tool("list_talents", {"status": "lead"}))["items"][
            0
        ]["id"]
        args = {
            "signup_id": signup_id,
            "first_name": "Ada",
            "last_name": "Lovelace",
            "sources": ["https://www.linkedin.com/in/ada"],
        }
        created = _payload(
            await client.call_tool(
                "create_freelancer_from_signup",
                {
                    **args,
                    "contact_points": [
                        {"kind": "email", "value": "ada@lovelace.example", "label": "work"},
                        {"kind": "phone", "value": "+44 20 7946 0958", "label": "work"},
                    ],
                },
            )
        )
        assert [
            (p["kind"], p["value"], p["is_primary"], p["id"] is None)
            for p in created["contact_points"]
        ] == [
            ("email", "ada@studio.it", True, True),
            ("email", "ada@lovelace.example", False, False),
            ("phone", "+44 20 7946 0958", True, False),
        ]
        rewritten = _payload(
            await client.call_tool(
                "create_freelancer_from_signup",
                {
                    **args,
                    "contact_points": [
                        {"kind": "phone", "value": "+44 20 7946 0958", "label": "other"},
                        {"kind": "linkedin", "value": "linkedin.com/in/ada", "label": "work"},
                    ],
                },
            )
        )
        assert [(p["kind"], p["value"], p["label"]) for p in rewritten["contact_points"]] == [
            ("email", "ada@studio.it", "work"),
            ("email", "ada@lovelace.example", "work"),
            ("phone", "+44 20 7946 0958", "work"),
            ("linkedin", "https://www.linkedin.com/in/ada", "work"),
        ]
        trail = _payload(
            await client.call_tool(
                "get_freelancer_audit", {"freelancer_id": created["id"], "limit": 10}
            )
        )["result"]
        # One `overridden` entry per research that added something, with the list before and
        # after (a revert puts the earlier one back): the first adds two, the second one.
        added = [
            [p["value"] for p in entry["payload"]["after"]["contact_points"]]
            for entry in reversed([e for e in trail if e["kind"] == "overridden"])
        ]
        assert len(added) == 2
        assert added[0] == ["ada@lovelace.example", "+44 20 7946 0958"]
        assert added[1][-1] == "https://www.linkedin.com/in/ada"
        notes = " ".join(n["text"] for n in rewritten["note"])
        assert "Recapiti aggiunti: email ada@lovelace.example, telefono +44 20 7946 0958" in notes
        assert "Recapiti aggiunti: LinkedIn https://www.linkedin.com/in/ada" in notes
    _wipe(factory)


async def test_a_card_from_a_signup_refuses_a_point_that_is_another_persons_login(
    factory: sessionmaker[Session],
) -> None:
    from rebase_core.models import Freelancer, User

    _seed(factory, ["ada@studio.it"])
    _seed_admin(factory)
    session = factory()
    session.add(User(email="grace@studio.it", first_name="Grace", last_name="Hopper"))
    session.commit()
    session.close()
    async with Client(build_server(factory, lambda: IVAN)) as client:
        ada = _payload(await client.call_tool("list_talents", {"status": "lead"}))["items"][0]["id"]
        refused = await client.call_tool(
            "create_freelancer_from_signup",
            {
                "signup_id": ada,
                "first_name": "Ada",
                "last_name": "Lovelace",
                "sources": ["https://www.linkedin.com/in/ada"],
                "contact_points": [
                    {"kind": "email", "value": "Grace@studio.it", "label": "work"},
                ],
            },
        )
        assert refused.is_error
        assert "grace@studio.it" in refused.content[0].text
        assert "un'altra persona" in refused.content[0].text
    session = factory()
    try:
        assert session.query(Freelancer).count() == 0  # nothing was written
    finally:
        session.close()
    _wipe(factory)
