"""A match and the CRM: `link_match_to_pigro` and the `get_match_report` hours report."""

import json
from typing import Any
from uuid import UUID

from mcp import Client
from mcp_tool_support import IVAN, _payload
from sqlalchemy.orm import Session, sessionmaker

from rebase_mcp.server import build_server


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
        assert "REBASE_PIGRO_ENGAGEMENTS_TOKEN" in refused.content[0].text
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
