"""`group_f24`, `list_f24_groups` and `ungroup_f24` over the MCP client (REB-1076): on a
`full_access` token's surface only, an administrator's alone, and a group answers its lines with the
code of each and the net to pay."""

import datetime as dt
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from mcp import Client
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor, Role
from pigrocrm.core.config import Settings
from pigrocrm.core.db import today_local
from pigrocrm.core.due_items.schemas import DueItemCreate
from pigrocrm.core.due_items.service import DueItemService
from pigrocrm.core.fiscal.models import FiscalProfile
from pigrocrm.core.fiscal.tax_items import slot_id
from pigrocrm.core.legal_entities.models import LegalEntity
from pigrocrm.core.storage import LocalFileStorage
from pigrocrm_mcp.server import build_server

TOOLS = ("group_f24", "list_f24_groups", "ungroup_f24")
DUE = today_local() + dt.timedelta(days=10)


def _server(
    session: Session, tmp_path: Path, *, full_access: bool = True, role: Role = "admin"
) -> Any:
    return build_server(
        lambda: session,
        lambda: Actor(id=uuid4(), type="mcp", role=role, full_access=full_access),
        LocalFileStorage(tmp_path),
        Settings(_env_file=None, mcp_full_access=full_access),  # type: ignore[call-arg]
    )


def _payload(result: Any) -> dict[str, Any]:
    return result.structured_content or json.loads(result.content[0].text)


def _items(session: Session) -> list[str]:
    """A balance and an advance of the substitute tax, and the credit that lowers them."""
    entity = LegalEntity(name="f24 mcp", legal_name="F24 mcp", country="IT")
    session.add(entity)
    session.flush()
    session.add(
        FiscalProfile(
            legal_entity_id=entity.id,
            pack_id="it-flat-rate",
            regime_code="RF19",
            default_vat_rate=0,
            profitability_coefficient=78,
            substitute_tax_rate=5,
        )
    )
    session.flush()
    service = DueItemService(session)
    ids: list[str] = []
    for kind, part, amount in (
        ("tax_balance", "balance", "100.00"),
        ("tax_advance", "first_advance", "40.00"),
    ):
        item = service.create(
            DueItemCreate(
                legal_entity_id=entity.id,
                direction="out",
                counterparty_type="tax_office",
                tax="substitute_tax",
                amount=amount,
                source_kind=kind,
                source_id=slot_id(kind, entity.id, "substitute_tax", 2026, part),  # type: ignore[arg-type]
                own_date=DUE,
            )
        )
        ids.append(str(item.id))
    iva = service.create(
        DueItemCreate(
            legal_entity_id=entity.id,
            direction="out",
            counterparty_type="tax_office",
            tax="iva",
            amount="25.00",
            source_kind="iva_settlement",
            source_id=uuid4(),
            own_date=DUE,
        )
    )
    session.commit()
    return [*ids, str(iva.id)]


async def test_the_tools_are_listed_only_to_a_full_access_token(
    mcp_session: Session, tmp_path: Path
) -> None:
    full = {tool.name for tool in await _server(mcp_session, tmp_path).list_tools()}
    plain = {
        tool.name for tool in await _server(mcp_session, tmp_path, full_access=False).list_tools()
    }
    assert set(TOOLS) <= full
    assert not set(TOOLS) & plain


async def test_a_token_without_full_access_cannot_call_them_by_name(
    mcp_session: Session, tmp_path: Path
) -> None:
    async with Client(_server(mcp_session, tmp_path, full_access=False)) as client:
        result = await client.call_tool("group_f24", {"due_item_ids": [str(uuid4())]})
    assert result.is_error
    assert "Unknown tool" in str(result.content)


async def test_an_administrator_groups_reads_and_ungroups_an_f24(
    mcp_session: Session, tmp_path: Path
) -> None:
    ids = _items(mcp_session)
    async with Client(_server(mcp_session, tmp_path)) as client:
        grouped = await client.call_tool("group_f24", {"due_item_ids": ids})
        assert not grouped.is_error, grouped
        group = _payload(grouped)
        # The codes of the pack on its two lines; the IVA line, with no code in the pack, says so.
        by_tax = {line["tax"]: line for line in group["lines"]}
        assert by_tax["substitute_tax"]["code"] in {"1790", "1792"}
        assert (by_tax["iva"]["code"], by_tax["iva"]["code_label"]) == (
            None,
            "codice da verificare",
        )
        assert (group["state"], group["total"]) == ("open", "165.00")

        listed = _payload(await client.call_tool("list_f24_groups", {}))
        assert [g["id"] for g in listed["items"]] == [group["id"]]

        dissolved = _payload(await client.call_tool("ungroup_f24", {"group_id": group["id"]}))
        assert dissolved["state"] == "dissolved"
        assert _payload(await client.call_tool("list_f24_groups", {}))["items"] == []


async def test_a_collaborator_with_full_access_is_refused(
    mcp_session: Session, tmp_path: Path
) -> None:
    ids = _items(mcp_session)
    async with Client(_server(mcp_session, tmp_path, role="collaborator")) as client:
        refused = await client.call_tool("group_f24", {"due_item_ids": ids})
        read = await client.call_tool("list_f24_groups", {})
    assert refused.is_error and read.is_error
