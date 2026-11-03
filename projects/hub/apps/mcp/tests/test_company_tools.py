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
    "nome_azienda": "Studio Rossi Srl",
    "referente_nome": "Maria",
    "referente_cognome": "Verdi",
    "email": "maria@studiorossi.it",
    "telefono": "+39 345 1234567",
    "figura_richiesta": "Backend developer",
    "progetto": "Un backend developer per tre mesi.",
    "periodo_da": "2026-12-01",
    "durata": "3 mesi",
    "budget_giornaliero": "500",
    "remoto": "remoto",
}


async def test_link_company_to_pigro_files_the_request_and_get_company_carries_the_state(
    factory: sessionmaker[Session],
) -> None:
    """«Riprova» for agents: the recorded door answers, the company comes back `collegato`
    with the deal's address, and `get_company` and `list_aziende` carry the same state."""
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
            assert before["crm_stato"] == "da_collegare"

            failed = _payload(
                await client.call_tool("link_company_to_pigro", {"company_id": company_id})
            )
            assert failed["crm_stato"] == "errore"
            assert failed["crm_situazione"] == (
                "Il CRM non ha risposto: Spazio dei lead non configurato."
            )

            linked = _payload(
                await client.call_tool("link_company_to_pigro", {"company_id": company_id})
            )
            assert (linked["crm_stato"], linked["crm_deal_url"]) == ("collegato", DEAL_URL)
            assert linked["crm_situazione"] is None

            listed = _payload(await client.call_tool("list_aziende", {}))
            assert [r["crm_stato"] for r in listed["items"]] == ["collegato"]
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
            bad = await client.call_tool("create_company", {**_COMPANY_ARGS, "remoto": "ibrido"})
            assert bad.is_error
            made = _payload(
                await client.call_tool(
                    "create_company",
                    {**_COMPANY_ARGS, "deal_id": f"https://crm.test/rebase/app/deal/{known}"},
                )
            )
        assert (made["nome_azienda"], made["crm_stato"], made["crm_deal_url"]) == (
            "Studio Rossi Srl",
            "collegato",
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


async def test_create_company_without_the_crm_waits_as_da_collegare(
    factory: sessionmaker[Session],
) -> None:
    _wipe(factory)
    _seed_admin(factory)
    try:
        async with Client(build_server(factory, lambda: IVAN)) as client:
            made = _payload(await client.call_tool("create_company", _COMPANY_ARGS))
        assert made["crm_stato"] == "da_collegare"
    finally:
        _wipe(factory)
