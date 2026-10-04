"""`update_fiscal_profile` and `update_azienda` on the default surface (ORB-188).

Until this card the fiscal profile's write existed only behind `mcp_full_access` and the
emitter had no tool under any switch, so the assistant a person had just connected could
not do the first step «Get started» asks for. Both rows are one rewritable row each, an
issued invoice keeps its own copy of both, and the services ask `require_admin`: that
gate, and not the switch, is what these tests pin. Built servers, `Settings(_env_file=
None)`, never the ambient `.env` (this repository's own has the switch **on**).
"""

from pathlib import Path
from typing import Any

import pytest
from mcp import Client
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.config import Settings
from pigrocrm.core.emitter.service import AziendaService
from pigrocrm.core.errors import NotFound
from pigrocrm.core.fiscal.service import FiscalProfileService
from pigrocrm.core.storage import LocalFileStorage
from pigrocrm_mcp.server import build_server

ADMIN = Actor(id=None, type="mcp", role="admin")
COLLABORATORE = Actor(id=None, type="mcp", role="collaboratore")

PROFILO = {
    "codice_regime": "RF19",
    "coefficiente_redditivita": "78",
    "aliquota_imposta_sostitutiva": "5",
    "aliquota_inps": "26.07",
    "modalita_pagamento": "MP05",
    "giorni_scadenza": 30,
    "iban": "IT60X0542811101000000123456",
}
EMITTENTE = {
    "ragione_sociale": "Studio Verdi",
    "partita_iva": "01234567890",
    "indirizzo": "Via Roma 1",
    "cap": "20100",
    "comune": "Milano",
    "provincia": "MI",
    "pec": "studio@pec.example",
    "codice_sdi": "ABCDEFG",
}


def _server(session: Session, actor: Actor, tmp_path: Path) -> Any:
    return build_server(
        lambda: session,
        lambda: actor,
        LocalFileStorage(str(tmp_path)),
        Settings(_env_file=None),  # type: ignore[call-arg]
    )


async def test_both_writes_exist_on_an_installation_that_never_opened_the_switch(
    mcp_session: Session, tmp_path: Path
) -> None:
    server = _server(mcp_session, ADMIN, tmp_path)
    names = {tool.name for tool in await server.list_tools()}
    assert {"update_fiscal_profile", "update_azienda"} <= names
    # And the reads they pair with, so the docstrings' «leggi prima» is possible.
    assert {"describe_fiscal_profile", "describe_azienda"} <= names
    # And the creation milestone 5 of spec 2026-10-03 opened (§9, REB-631), on the same
    # surface as the two writes: setting a space up is not a fiscal act.
    assert "create_azienda" in names


async def test_an_admin_agent_sets_the_fiscal_profile_and_the_read_reflects_it(
    mcp_session: Session, tmp_path: Path
) -> None:
    server = _server(mcp_session, ADMIN, tmp_path)
    async with Client(server) as client:
        written = await client.call_tool("update_fiscal_profile", {"dati": PROFILO})
        assert not written.is_error, written.content[0].text
        assert written.structured_content["codice_regime"] == "RF19"
        assert written.structured_content["iban"] == PROFILO["iban"]
        read = await client.call_tool("describe_fiscal_profile", {})
        assert read.structured_content["giorni_scadenza"] == 30
        assert read.structured_content["aliquota_inps"] == "26.07"
        # A total replacement: a second write without the IBAN leaves none.
        again = await client.call_tool(
            "update_fiscal_profile", {"dati": {"codice_regime": "RF19", "giorni_scadenza": 60}}
        )
        assert not again.is_error, again.content[0].text
        assert again.structured_content["iban"] is None
        assert again.structured_content["giorni_scadenza"] == 60


async def test_an_admin_agent_sets_the_emitter_and_a_malformed_value_names_its_field(
    mcp_session: Session, tmp_path: Path
) -> None:
    server = _server(mcp_session, ADMIN, tmp_path)
    async with Client(server) as client:
        written = await client.call_tool("update_azienda", {"dati": EMITTENTE})
        assert not written.is_error, written.content[0].text
        assert written.structured_content["ragione_sociale"] == "Studio Verdi"
        assert written.structured_content["codice_sdi"] == "ABCDEFG"
        read = await client.call_tool("describe_azienda", {})
        assert read.structured_content["partita_iva"] == "01234567890"
        assert read.structured_content["comune"] == "Milano"
        # The service's own shape checks answer through the tool, field first.
        refused = await client.call_tool(
            "update_azienda", {"dati": {**EMITTENTE, "partita_iva": "123"}}
        )
        assert refused.is_error
        assert "partita_iva" in refused.content[0].text
        assert "11 cifre" in refused.content[0].text
    assert AziendaService(mcp_session).get(ADMIN).partita_iva == "01234567890"


async def test_the_emitter_read_can_be_handed_back_to_the_write_unchanged(
    mcp_session: Session, tmp_path: Path
) -> None:
    """The round trip the write's docstring prescribes -- «leggi prima, rimanda indietro
    l'oggetto letto con le modifiche» -- has to validate: `AziendaUpsert` forbids
    extra keys, so the read must not carry `id` or the timestamps."""
    server = _server(mcp_session, ADMIN, tmp_path)
    async with Client(server) as client:
        await client.call_tool("update_azienda", {"dati": EMITTENTE})
        read = await client.call_tool("describe_azienda", {})
        assert not {"id", "created_at", "updated_at"} & set(read.structured_content)
        back = await client.call_tool(
            "update_azienda", {"dati": {**read.structured_content, "comune": "Roma"}}
        )
        assert not back.is_error, back.content[0].text
        assert back.structured_content["comune"] == "Roma"
        assert back.structured_content["codice_sdi"] == "ABCDEFG"
        assert back.structured_content["nazione"] == "IT"


async def test_a_collaboratore_agent_is_refused_both_writes_and_nothing_changes(
    mcp_session: Session, tmp_path: Path
) -> None:
    """The gate that remains is the role, carried by the token's owner: the tools are
    listed for everyone (a surface is one thing) and refuse a non-admin at the service."""
    server = _server(mcp_session, COLLABORATORE, tmp_path)
    async with Client(server) as client:
        fiscal = await client.call_tool("update_fiscal_profile", {"dati": PROFILO})
        assert fiscal.is_error
        assert "admin" in fiscal.content[0].text
        emitter = await client.call_tool("update_azienda", {"dati": EMITTENTE})
        assert emitter.is_error
        assert "admin" in emitter.content[0].text
    # Nothing was written: the default azienda every space starts with still carries
    # only its name, and no fiscal profile exists.
    with pytest.raises(NotFound):
        FiscalProfileService(mcp_session).describe(ADMIN)
    azienda = AziendaService(mcp_session).get(ADMIN)
    assert azienda.ragione_sociale == "Spazio di prova"
    assert azienda.partita_iva is None


async def test_an_explicit_azienda_id_that_is_not_an_id_is_refused_not_the_default(
    mcp_session: Session, tmp_path: Path
) -> None:
    """Only an omitted `azienda_id` means the default: an empty string or a value that
    is not a UUID must not write the default's row in silence."""
    server = _server(mcp_session, ADMIN, tmp_path)
    async with Client(server) as client:
        for bad in ("", "non-un-id"):
            result = await client.call_tool(
                "update_azienda", {"dati": EMITTENTE, "azienda_id": bad}
            )
            assert result.is_error, bad
            assert "azienda_id" in result.content[0].text
    assert AziendaService(mcp_session).get(ADMIN).partita_iva is None


async def test_an_admin_agent_opens_a_second_azienda_with_its_profile(
    mcp_session: Session, tmp_path: Path
) -> None:
    """REB-631, spec §7: one call, the row and its profile; the new azienda is listed
    after the default and its profile reads back under its id, while a profile the
    fiscal rules refuse makes the whole call fail with the field named."""
    server = _server(mcp_session, ADMIN, tmp_path)
    async with Client(server) as client:
        await client.call_tool("update_azienda", {"dati": EMITTENTE})
        created = await client.call_tool(
            "create_azienda",
            {
                "dati": {"nome": "rebase ltd", "ragione_sociale": "Rebase Ltd", "nazione": "GB"},
                "profilo_fiscale": {"pack_id": "non-it", "aliquota_iva_default": "20.00"},
            },
        )
        assert not created.is_error, created.content[0].text
        nuova = created.structured_content
        assert (nuova["nome"], nuova["predefinita"], nuova["attiva"]) == ("rebase ltd", False, True)
        listed = await client.call_tool("list_aziende", {})
        rows = listed.structured_content
        if isinstance(rows, dict):
            # A list result reaches the wire wrapped under `result`.
            rows = rows["result"]
        assert [a["nome"] for a in rows] == ["Studio Verdi", "rebase ltd"]
        profile = await client.call_tool("describe_fiscal_profile", {"azienda_id": nuova["id"]})
        assert profile.structured_content["pack_id"] == "non-it"
        refused = await client.call_tool(
            "create_azienda",
            {
                "dati": {"nome": "terza", "ragione_sociale": "Terza Srl"},
                "profilo_fiscale": {
                    "pack_id": "non-it",
                    "aliquota_iva_default": "20.00",
                    "applica_bollo": True,
                },
            },
        )
        assert refused.is_error
        assert "applica_bollo" in refused.content[0].text
    assert len(AziendaService(mcp_session).list(ADMIN)) == 2


async def test_a_collaboratore_agent_cannot_open_an_azienda(
    mcp_session: Session, tmp_path: Path
) -> None:
    server = _server(mcp_session, COLLABORATORE, tmp_path)
    async with Client(server) as client:
        refused = await client.call_tool(
            "create_azienda",
            {
                "dati": {"nome": "x", "ragione_sociale": "X Ltd", "nazione": "GB"},
                "profilo_fiscale": {"pack_id": "non-it", "aliquota_iva_default": "20.00"},
            },
        )
    assert refused.is_error
    assert "admin" in refused.content[0].text
