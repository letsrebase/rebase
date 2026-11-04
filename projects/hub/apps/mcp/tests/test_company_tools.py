"""The company tools that file a request in the CRM: `create_company`, `link_company_to_pigro`."""

import json
from typing import Any
from uuid import UUID

from mcp import Client
from mcp_tool_support import IVAN, _payload, _seed_admin, _seed_company, _wipe
from sqlalchemy.orm import Session, sessionmaker

from rebase_mcp.server import build_server


def _crm_server(
    factory: sessionmaker[Session], fake: Any, token: str = "un-token-per-la-porta"
) -> Any:
    from rebase_core.config import Settings
    from rebase_core.crm_link import CrmLeadService

    settings = Settings(  # type: ignore[call-arg]
        _env_file=None, pigro_api_url="https://pigro.test", pigro_engagements_token=token
    )
    return build_server(
        factory,
        lambda: IVAN,
        settings=settings,
        crm_leads=lambda session: CrmLeadService(session, settings, fake, None),
    )


_COMPANY_ARGS = {
    "company_name": "Studio Rossi Srl",
    "contact_first_name": "Maria",
    "contact_last_name": "Verdi",
    "email": "maria@studiorossi.it",
    "phone": "+39 345 1234567",
    "requested_role": "Backend developer",
    "project": "Un backend developer per tre mesi.",
    "period_from": "2026-12-01",
    "duration": "3 mesi",
    "daily_budget": "500",
    "remote": "remote",
}


async def test_link_company_to_pigro_files_the_request_and_get_company_carries_the_state(
    factory: sessionmaker[Session],
) -> None:
    """«Riprova» for agents: the recorded door answers, the company comes back `linked`
    with the deal's address, and `get_company` and `list_companies` carry the same state."""
    from fakes_pigro import DEAL, DEAL_URL, TOKEN, RecordedPigro

    _wipe(factory)
    company_id = _seed_company(factory)
    _seed_admin(factory)
    fake = RecordedPigro(
        [
            (503, b'{"detail": "Spazio dei lead non configurato"}'),
            (
                201,
                json.dumps(
                    {
                        "customer_id": str(UUID(int=7)),
                        "person_id": None,
                        "deal_id": str(DEAL),
                        "deal_url": DEAL_URL,
                        "creato": True,
                    }
                ).encode(),
            ),
        ]
    )
    try:
        async with Client(_crm_server(factory, fake, TOKEN)) as client:
            before = _payload(await client.call_tool("get_company", {"company_id": company_id}))
            assert before["crm_status"] == "to_link"

            failed = _payload(
                await client.call_tool("link_company_to_pigro", {"company_id": company_id})
            )
            assert failed["crm_status"] == "error"
            assert failed["crm_situation"] == (
                "Il CRM non ha risposto: Spazio dei lead non configurato."
            )

            linked = _payload(
                await client.call_tool("link_company_to_pigro", {"company_id": company_id})
            )
            assert (linked["crm_status"], linked["crm_deal_url"]) == ("linked", DEAL_URL)
            assert linked["crm_situation"] is None

            listed = _payload(await client.call_tool("list_companies", {}))
            assert [r["crm_status"] for r in listed["items"]] == ["linked"]
        assert [c[1] for c in fake.calls] == [
            f"https://pigro.test/api/rebase/leads/{company_id}"
        ] * 2
        session = factory()
        try:
            from rebase_core.audit import AdminActionService

            newest = AdminActionService(session).timeline("company", UUID(company_id))[0]
            assert (newest.kind, newest.admin_id) == ("crm_link", IVAN.id)
        finally:
            session.close()
    finally:
        _wipe(factory)


async def test_link_company_to_pigro_links_another_deal_and_says_why_it_cannot(
    factory: sessionmaker[Session],
) -> None:
    from fakes_pigro import RecordedPigro

    _wipe(factory)
    company_id = _seed_company(factory)
    _seed_admin(factory)
    other = str(UUID(int=99))
    fake = RecordedPigro([(422, b'{"detail": "deal_id: non valido"}')])
    try:
        async with Client(_crm_server(factory, fake)) as client:
            refused = await client.call_tool(
                "link_company_to_pigro", {"company_id": company_id, "deal_id": other}
            )
        assert refused.is_error
        assert "Il CRM ha rifiutato la richiesta: deal_id: non valido." in refused.content[0].text
        assert json.loads(fake.calls[0][3])["deal_id"] == other
    finally:
        _wipe(factory)


async def test_link_company_to_pigro_without_a_token_or_a_request_is_a_sentence(
    factory: sessionmaker[Session],
) -> None:
    from fakes_pigro import RecordedPigro

    _wipe(factory)
    company_id = _seed_company(factory)
    try:
        async with Client(build_server(factory, lambda: IVAN)) as client:
            off = await client.call_tool("link_company_to_pigro", {"company_id": company_id})
        assert off.is_error and "REBASE_PIGRO_ENGAGEMENTS_TOKEN" in off.content[0].text
        async with Client(_crm_server(factory, RecordedPigro([(201, b"{}")]))) as client:
            missing = await client.call_tool(
                "link_company_to_pigro", {"company_id": str(UUID(int=5))}
            )
        assert missing.is_error and "non trovato" in missing.content[0].text
    finally:
        _wipe(factory)


async def test_create_company_opens_the_request_and_links_the_deal_the_crm_has(
    factory: sessionmaker[Session],
) -> None:
    from fakes_pigro import DEAL, DEAL_URL, TOKEN, RecordedPigro

    _wipe(factory)
    _seed_admin(factory)
    known = str(UUID(int=77))
    fake = RecordedPigro(
        [
            (
                200,
                json.dumps(
                    {
                        "customer_id": str(UUID(int=7)),
                        "person_id": None,
                        "deal_id": str(DEAL),
                        "deal_url": DEAL_URL,
                        "creato": False,
                    }
                ).encode(),
            )
        ]
    )
    try:
        async with Client(_crm_server(factory, fake, TOKEN)) as client:
            bad = await client.call_tool("create_company", {**_COMPANY_ARGS, "remote": "hybrid"})
            assert bad.is_error
            made = _payload(
                await client.call_tool(
                    "create_company",
                    {**_COMPANY_ARGS, "deal_id": f"https://crm.test/rebase/app/deal/{known}"},
                )
            )
        assert (made["company_name"], made["crm_status"], made["crm_deal_url"]) == (
            "Studio Rossi Srl",
            "linked",
            DEAL_URL,
        )
        assert json.loads(fake.calls[0][3])["deal_id"] == known
        assert len(fake.calls) == 1
        session = factory()
        try:
            from rebase_core.audit import AdminActionService

            newest = AdminActionService(session).timeline("company", UUID(made["id"]))
            assert {entry.kind for entry in newest} == {"company_created", "crm_link"}
        finally:
            session.close()
    finally:
        _wipe(factory)


async def test_create_company_without_the_crm_waits_as_to_link(
    factory: sessionmaker[Session],
) -> None:
    _wipe(factory)
    _seed_admin(factory)
    try:
        async with Client(build_server(factory, lambda: IVAN)) as client:
            made = _payload(await client.call_tool("create_company", _COMPANY_ARGS))
        assert made["crm_status"] == "to_link"
    finally:
        _wipe(factory)


_POINTS = [
    {"kind": "email", "value": "maria.verdi@gmail.com", "label": "personal"},
    {"kind": "phone", "value": "+39 02 7654321", "label": "billing", "note": "ufficio"},
    {"kind": "linkedin", "value": "linkedin.com/in/maria-verdi", "label": "work"},
]


def _read(point: dict[str, Any]) -> tuple[str, str, str, str | None, bool]:
    return (point["kind"], point["value"], point["label"], point["note"], point["is_primary"])


async def test_create_company_stores_the_contact_points_and_sends_them_to_the_crm(
    factory: sessionmaker[Session],
) -> None:
    """REB-948: the extra points go on the contact at creation, validated by the one service,
    and the CRM gets them with the request (the login address stays the door's `email`)."""
    from fakes_pigro import DEAL, DEAL_URL, TOKEN, RecordedPigro

    _wipe(factory)
    _seed_admin(factory)
    known = str(UUID(int=77))
    body = {
        "customer_id": str(UUID(int=7)),
        "person_id": None,
        "deal_id": str(DEAL),
        "deal_url": DEAL_URL,
        "creato": False,
    }
    fake = RecordedPigro([(200, json.dumps(body).encode())])
    try:
        async with Client(_crm_server(factory, fake, TOKEN)) as client:
            made = _payload(
                await client.call_tool(
                    "create_company",
                    {**_COMPANY_ARGS, "deal_id": known, "contact_points": _POINTS},
                )
            )
            again = _payload(await client.call_tool("get_company", {"company_id": made["id"]}))
        # The login address first, then the phone the request carried (primary), then the
        # extras in the order sent: an address never primary, the first profile primary.
        assert [_read(p) for p in made["contact_points"]] == [
            ("email", "maria@studiorossi.it", "work", None, True),
            ("phone", "+39 345 1234567", "work", None, True),
            ("email", "maria.verdi@gmail.com", "personal", None, False),
            ("phone", "+39 02 7654321", "billing", "ufficio", False),
            ("linkedin", "https://www.linkedin.com/in/maria-verdi", "work", None, True),
        ]
        assert again["contact_points"] == made["contact_points"]
        sent = json.loads(fake.calls[0][3])["contact"]
        assert sent["email"] == "maria@studiorossi.it"
        assert [
            (p["kind"], p["value"], p["label"], p["note"], p["is_primary"])
            for p in sent["contact_points"]
        ] == [
            ("phone", "+39 345 1234567", "work", None, True),
            ("email", "maria.verdi@gmail.com", "personal", None, False),
            ("phone", "+39 02 7654321", "billing", "ufficio", False),
            ("linkedin", "https://www.linkedin.com/in/maria-verdi", "work", None, True),
        ]
    finally:
        _wipe(factory)


async def test_create_company_adds_points_to_a_contact_already_on_file(
    factory: sessionmaker[Session],
) -> None:
    """A contact found by email keeps their own points: the new ones are added, a point they
    already have is left as it is, and a new one is never primary on its own."""
    _wipe(factory)
    _seed_admin(factory)
    try:
        async with Client(build_server(factory, lambda: IVAN)) as client:
            first = _payload(
                await client.call_tool(
                    "create_company", {**_COMPANY_ARGS, "contact_points": _POINTS[:2]}
                )
            )
            second = _payload(
                await client.call_tool(
                    "create_company",
                    {
                        **_COMPANY_ARGS,
                        "project": "Un secondo progetto.",
                        "contact_points": [
                            {
                                "kind": "phone",
                                "value": "+39 02 7654321",
                                "label": "other",
                                "note": "diverso",
                            },
                            {
                                "kind": "phone",
                                "value": "+39 333 0000000",
                                "label": "personal",
                                "is_primary": True,
                            },
                            _POINTS[2],
                        ],
                    },
                )
            )
        assert first["id"] != second["id"]
        assert [_read(p) for p in second["contact_points"]] == [
            ("email", "maria@studiorossi.it", "work", None, True),
            ("phone", "+39 345 1234567", "work", None, True),
            ("email", "maria.verdi@gmail.com", "personal", None, False),
            ("phone", "+39 02 7654321", "billing", "ufficio", False),
            ("phone", "+39 333 0000000", "personal", None, False),
            ("linkedin", "https://www.linkedin.com/in/maria-verdi", "work", None, True),
        ]
    finally:
        _wipe(factory)


async def test_create_company_refuses_a_point_that_is_another_persons_login_and_writes_nothing(
    factory: sessionmaker[Session],
) -> None:
    from rebase_core.models import Company, User

    _wipe(factory)
    _seed_admin(factory)
    _seed_company(factory)  # wile@acme.it is somebody's login address
    try:
        async with Client(build_server(factory, lambda: IVAN)) as client:
            refused = await client.call_tool(
                "create_company",
                {
                    **_COMPANY_ARGS,
                    "contact_points": [
                        {"kind": "email", "value": "Wile@acme.it", "label": "work"},
                    ],
                },
            )
            login = await client.call_tool(
                "create_company",
                {
                    **_COMPANY_ARGS,
                    "contact_points": [
                        {"kind": "email", "value": "maria@studiorossi.it", "label": "work"},
                    ],
                },
            )
            malformed = await client.call_tool(
                "create_company",
                {
                    **_COMPANY_ARGS,
                    "contact_points": [{"kind": "email", "value": "non-un-indirizzo"}],
                },
            )
        assert refused.is_error
        assert "wile@acme.it" in refused.content[0].text
        assert "un'altra persona" in refused.content[0].text
        assert login.is_error  # the contact's own login is not an extra point
        assert malformed.is_error
        session = factory()
        try:
            assert session.query(Company).count() == 1  # only the seeded one
            emails = {u.email for u in session.query(User)}
            assert "maria@studiorossi.it" not in emails
        finally:
            session.close()
    finally:
        _wipe(factory)


async def test_list_companies_q_finds_a_company_by_an_extra_email_a_phone_or_a_profile(
    factory: sessionmaker[Session],
) -> None:
    from rebase_core.contact_points import ContactPointInput, ContactPointService
    from rebase_core.models import User

    _wipe(factory)
    company_id = _seed_company(factory)
    session = factory()
    try:
        user = session.query(User).filter_by(email="wile@acme.it").one()
        ContactPointService(session).replace(
            user,
            [
                ContactPointInput(kind="email", value="coyote@desert.example", label="personal"),
                ContactPointInput(kind="phone", value="+39 388 871 3657", label="other"),
                ContactPointInput(kind="linkedin", value="linkedin.com/in/wile-e", label="work"),
            ],
        )
        session.commit()
    finally:
        session.close()
    try:
        async with Client(build_server(factory, lambda: IVAN)) as client:
            for term in (
                "coyote@desert",
                "3888713657",
                "+39 388 871 3657",
                "388-871-3657",
                "wile-e",
            ):
                found = _payload(await client.call_tool("list_companies", {"q": term}))
                assert [row["id"] for row in found["items"]] == [company_id], term
            none = _payload(await client.call_tool("list_companies", {"q": "3880000000"}))
            assert none["items"] == []
    finally:
        _wipe(factory)
