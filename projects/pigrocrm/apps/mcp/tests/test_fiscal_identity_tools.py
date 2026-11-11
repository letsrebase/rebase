"""`update_fiscal_profile` and `update_legal_entity` on the default surface (ORB-188).

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
from pigrocrm.core.errors import NotFound
from pigrocrm.core.fiscal.service import FiscalProfileService
from pigrocrm.core.legal_entities.service import LegalEntityService
from pigrocrm.core.storage import LocalFileStorage
from pigrocrm_mcp.server import build_server

ADMIN = Actor(id=None, type="mcp", role="admin")
COLLABORATOR = Actor(id=None, type="mcp", role="collaborator")

PROFILE = {
    "regime_code": "RF19",
    "profitability_coefficient": "78",
    "substitute_tax_rate": "5",
    "social_security_rate": "26.07",
    "payment_method": "MP05",
    "due_days": 30,
    "iban": "IT60X0542811101000000123456",
}
LEGAL_ENTITY = {
    "legal_name": "Studio Verdi",
    "vat_number": "01234567890",
    "address": "Via Roma 1",
    "postal_code": "20100",
    "city": "Milano",
    "province": "MI",
    "certified_email": "studio@pec.example",
    "sdi_code": "ABCDEFG",
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
    assert {"update_fiscal_profile", "update_legal_entity"} <= names
    # And the reads they pair with, so the docstrings' «leggi prima» is possible.
    assert {"describe_fiscal_profile", "describe_legal_entity"} <= names
    # And the creation milestone 5 of spec 2026-10-03 opened (§9, REB-631), on the same
    # surface as the two writes: setting a space up is not a fiscal act.
    assert "create_legal_entity" in names


async def test_an_admin_agent_sets_the_fiscal_profile_and_the_read_reflects_it(
    mcp_session: Session, tmp_path: Path
) -> None:
    server = _server(mcp_session, ADMIN, tmp_path)
    async with Client(server) as client:
        written = await client.call_tool("update_fiscal_profile", {"data": PROFILE})
        assert not written.is_error, written.content[0].text
        assert written.structured_content["regime_code"] == "RF19"
        assert written.structured_content["iban"] == PROFILE["iban"]
        read = await client.call_tool("describe_fiscal_profile", {})
        assert read.structured_content["due_days"] == 30
        assert read.structured_content["social_security_rate"] == "26.07"
        # A total replacement: a second write without the IBAN leaves none.
        again = await client.call_tool(
            "update_fiscal_profile", {"data": {"regime_code": "RF19", "due_days": 60}}
        )
        assert not again.is_error, again.content[0].text
        assert again.structured_content["iban"] is None
        assert again.structured_content["due_days"] == 60


async def test_an_admin_agent_sets_the_legal_entity_and_to_malformed_value_names_its_field(
    mcp_session: Session, tmp_path: Path
) -> None:
    server = _server(mcp_session, ADMIN, tmp_path)
    async with Client(server) as client:
        written = await client.call_tool("update_legal_entity", {"data": LEGAL_ENTITY})
        assert not written.is_error, written.content[0].text
        assert written.structured_content["legal_name"] == "Studio Verdi"
        assert written.structured_content["sdi_code"] == "ABCDEFG"
        read = await client.call_tool("describe_legal_entity", {})
        assert read.structured_content["vat_number"] == "01234567890"
        assert read.structured_content["city"] == "Milano"
        # The service's own shape checks answer through the tool, field first.
        refused = await client.call_tool(
            "update_legal_entity", {"data": {**LEGAL_ENTITY, "vat_number": "123"}}
        )
        assert refused.is_error
        assert "vat_number" in refused.content[0].text
        assert "11 cifre" in refused.content[0].text
    assert LegalEntityService(mcp_session).get(ADMIN).vat_number == "01234567890"


async def test_the_legal_entity_read_can_be_handed_back_to_the_write_unchanged(
    mcp_session: Session, tmp_path: Path
) -> None:
    """The round trip the write's docstring prescribes -- "read first, send back
    the object read with the changes" -- has to validate: `LegalEntityUpsert` forbids
    extra keys, so the read must not carry `id` or the timestamps."""
    server = _server(mcp_session, ADMIN, tmp_path)
    async with Client(server) as client:
        await client.call_tool("update_legal_entity", {"data": LEGAL_ENTITY})
        read = await client.call_tool("describe_legal_entity", {})
        assert not {"id", "created_at", "updated_at"} & set(read.structured_content)
        back = await client.call_tool(
            "update_legal_entity", {"data": {**read.structured_content, "city": "Roma"}}
        )
        assert not back.is_error, back.content[0].text
        assert back.structured_content["city"] == "Roma"
        assert back.structured_content["sdi_code"] == "ABCDEFG"
        assert back.structured_content["country"] == "IT"


async def test_the_legal_entity_read_says_where_the_address_is_and_hands_back_without_it(
    mcp_session: Session, tmp_path: Path
) -> None:
    """REB-1069: `place` is derived from the city and the province, so it is on the read and
    never on the write: the object read goes back whole, and fixing the city is what moves it."""
    server = _server(mcp_session, ADMIN, tmp_path)
    async with Client(server) as client:
        await client.call_tool("update_legal_entity", {"data": {**LEGAL_ENTITY, "city": "Milano"}})
        read = await client.call_tool("describe_legal_entity", {})
        place = read.structured_content["place"]
        assert (place["status"], place["municipality_code"], place["region_name"]) == (
            "matched",
            "015146",
            "Lombardia",
        )
        back = await client.call_tool(
            "update_legal_entity", {"data": {**read.structured_content, "city": "Milan"}}
        )
        assert not back.is_error, back.content[0].text
        assert back.structured_content["place"]["status"] == "unknown"
        assert back.structured_content["place"]["region_name"] == "Lombardia"


async def test_to_collaborator_agent_is_refused_both_writes_and_nothing_changes(
    mcp_session: Session, tmp_path: Path
) -> None:
    """The gate that remains is the role, carried by the token's owner: the tools are
    listed for everyone (a surface is one thing) and refuse a non-admin at the service."""
    server = _server(mcp_session, COLLABORATOR, tmp_path)
    async with Client(server) as client:
        fiscal = await client.call_tool("update_fiscal_profile", {"data": PROFILE})
        assert fiscal.is_error
        assert "admin" in fiscal.content[0].text
        emitter = await client.call_tool("update_legal_entity", {"data": LEGAL_ENTITY})
        assert emitter.is_error
        assert "admin" in emitter.content[0].text
    # Nothing was written: the default azienda every space starts with still carries
    # only its name, and no fiscal profile exists.
    with pytest.raises(NotFound):
        FiscalProfileService(mcp_session).describe(ADMIN)
    legal_entity = LegalEntityService(mcp_session).get(ADMIN)
    assert legal_entity.legal_name == "Spazio di prova"
    assert legal_entity.vat_number is None


async def test_an_explicit_legal_entity_id_that_is_not_an_id_is_refused_not_the_default(
    mcp_session: Session, tmp_path: Path
) -> None:
    """Only an omitted `legal_entity_id` means the default: an empty string or a value that
    is not a UUID must not write the default's row in silence."""
    server = _server(mcp_session, ADMIN, tmp_path)
    async with Client(server) as client:
        for bad in ("", "non-un-id"):
            result = await client.call_tool(
                "update_legal_entity", {"data": LEGAL_ENTITY, "legal_entity_id": bad}
            )
            assert result.is_error, bad
            assert "legal_entity_id" in result.content[0].text
    assert LegalEntityService(mcp_session).get(ADMIN).vat_number is None


async def test_an_admin_agent_opens_to_second_legal_entity_with_its_profile(
    mcp_session: Session, tmp_path: Path
) -> None:
    """REB-631, spec §7: one call, the row and its profile; the new azienda is listed
    after the default and its profile reads back under its id, while a profile the
    fiscal rules refuse makes the whole call fail with the field named."""
    server = _server(mcp_session, ADMIN, tmp_path)
    async with Client(server) as client:
        await client.call_tool("update_legal_entity", {"data": LEGAL_ENTITY})
        created = await client.call_tool(
            "create_legal_entity",
            {
                "data": {"name": "rebase ltd", "legal_name": "Rebase Ltd", "country": "GB"},
                "tax_profile": {"pack_id": "non-it", "default_vat_rate": "20.00"},
            },
        )
        assert not created.is_error, created.content[0].text
        fresh = created.structured_content
        assert (fresh["name"], fresh["is_default"], fresh["active"]) == ("rebase ltd", False, True)
        listed = await client.call_tool("list_legal_entities", {})
        rows = listed.structured_content
        if isinstance(rows, dict):
            # A list result reaches the wire wrapped under `result`.
            rows = rows["result"]
        assert [a["name"] for a in rows] == ["Studio Verdi", "rebase ltd"]
        profile = await client.call_tool(
            "describe_fiscal_profile", {"legal_entity_id": fresh["id"]}
        )
        assert profile.structured_content["pack_id"] == "non-it"
        refused = await client.call_tool(
            "create_legal_entity",
            {
                "data": {"name": "terza", "legal_name": "Terza Srl"},
                "tax_profile": {
                    "pack_id": "non-it",
                    "default_vat_rate": "20.00",
                    "apply_stamp_duty": True,
                },
            },
        )
        assert refused.is_error
        assert "apply_stamp_duty" in refused.content[0].text
    assert len(LegalEntityService(mcp_session).list(ADMIN)) == 2


async def test_to_collaborator_agent_cannot_open_an_legal_entity(
    mcp_session: Session, tmp_path: Path
) -> None:
    server = _server(mcp_session, COLLABORATOR, tmp_path)
    async with Client(server) as client:
        refused = await client.call_tool(
            "create_legal_entity",
            {
                "data": {"name": "x", "legal_name": "X Ltd", "country": "GB"},
                "tax_profile": {"pack_id": "non-it", "default_vat_rate": "20.00"},
            },
        )
    assert refused.is_error
    assert "admin" in refused.content[0].text
