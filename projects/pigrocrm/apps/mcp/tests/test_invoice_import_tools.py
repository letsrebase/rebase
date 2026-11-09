"""The two import tools exist only behind `mcp_full_access` and write the register.

`import_issued_invoice` and `declare_invoice_register_gaps` are slice 9's addition to
the nineteen-strong ban list: registering a numbered, already-issued row straight into
the fiscal register, and naming the numbers it will never carry. Both change what the
register says about the past, so both live behind the same switch as `issue_invoice`
and the rest of `privileged.py` -- see `test_mcp_invoice_ban.py` and
`test_mcp_surface_coverage.py` for the structural guarantee; this file only checks that
the two tools actually work once the switch is on.
"""

from pathlib import Path
from typing import Any

import pytest
from mcp import Client
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.config import Settings
from pigrocrm.core.fiscal.repository import FiscalProfileRepository
from pigrocrm.core.fiscal.schemas import FiscalProfileUpsert
from pigrocrm.core.fiscal.service import FiscalProfileService
from pigrocrm.core.legal_entities.repository import LegalEntityRepository
from pigrocrm.core.legal_entities.schemas import LegalEntityUpsert
from pigrocrm.core.legal_entities.service import LegalEntityService
from pigrocrm.core.organizations.models import Organization
from pigrocrm.core.storage import LocalFileStorage
from pigrocrm_mcp.server import build_server

TOOLS = {"import_issued_invoice", "declare_invoice_register_gaps"}


def _payload(result: Any) -> Any:
    """Mirrors `test_gmail_tools.py`'s helper: a dict result arrives as
    `structured_content` directly, a list result arrives wrapped as `{"result": [...]}`.
    """
    import json

    return result.structured_content or json.loads(result.content[0].text)


def _seed_fiscal_and_legal_entity_profiles(session: Session) -> None:
    """The two profiles `import_issued` reads before it will write anything -- copied
    from `packages/core/tests/conftest.py`'s `_invoice_service` because the MCP test
    root has its own `conftest.py` and does not see the core suite's.
    """
    admin = Actor(id=None, type="system", role="admin")
    # The azienda first, always (REB-615: a bare default is already seeded and this
    # fills in its identity), then its fiscal profile when it has none.
    LegalEntityService(session).upsert_default(
        LegalEntityUpsert(
            legal_name="Studio Rossi di Mario Rossi",
            vat_number="01234567890",
            tax_code="HMCRFT00A01H501K",
            address="Via Vittorio Veneto 12",
            postal_code="20124",
            city="Milano",
            province="MI",
            country="IT",
            email="mario@example.com",
        ),
        admin,
    )
    legal_entity = LegalEntityRepository(session).default()
    assert legal_entity is not None
    if FiscalProfileRepository(session).get(legal_entity.id) is None:
        FiscalProfileService(session).upsert(FiscalProfileUpsert(regime_code="RF19"), admin)


def _server(session: Session, tmp_path: Path, *, full_access: bool) -> Any:
    return build_server(
        lambda: session,
        lambda: Actor(id=None, type="mcp", role="admin", full_access=full_access),
        LocalFileStorage(tmp_path),
        settings=Settings(_env_file=None, mcp_full_access=full_access),  # type: ignore[call-arg]
    )


@pytest.mark.parametrize("full_access", [False, True])
async def test_the_tools_exist_only_when_the_installation_opted_in(
    mcp_session: Session, tmp_path: Path, full_access: bool
) -> None:
    names = {
        tool.name
        for tool in await _server(mcp_session, tmp_path, full_access=full_access).list_tools()
    }
    assert (names >= TOOLS) is full_access


async def test_import_registers_the_invoice_and_names_the_gaps(
    mcp_session: Session, tmp_path: Path
) -> None:
    _seed_fiscal_and_legal_entity_profiles(mcp_session)
    customer = Organization(
        legal_name="Acme S.r.l.",
        vat_number="12345678901",
        sdi_code="ABCDEFG",
        address="Via Roma 1",
        postal_code="20154",
        city="Milano",
        province="MI",
        country="IT",
    )
    mcp_session.add(customer)
    mcp_session.flush()

    def _data(number: int, issue_date: str) -> dict[str, Any]:
        return {
            "year": 2026,
            "number": number,
            "issue_date": issue_date,
            "customer_id": str(customer.id),
            "lines": [
                {
                    "description": "900142/0526/Consulenza AI CTO progetto Aurora",
                    "quantity": "20",
                    "unit_price": "300",
                    "total_price": "6000.00",
                    "vat_rate": "0",
                    "vat_nature": "N2.2",
                }
            ],
            "taxable_amount": "6000.00",
            "vat_amount": "0.00",
            # `stamp_duty` is declared beside the total and never added to it: the identity is
            # `imponibile + imposta == totale`, the same one `sum_totals` stores for a
            # natively issued invoice.
            "stamp_duty": "2.00",
            "total": "6000.00",
        }

    async with Client(_server(mcp_session, tmp_path, full_access=True)) as client:
        # Numero 1 leaves nothing undeclared: it is where a year's register starts, and
        # the scan runs from 1 -- importing a 7 first would report the six holes below it.
        first = await client.call_tool("import_issued_invoice", {"data": _data(1, "2026-06-01")})
        assert _payload(first)["undeclared_gaps"] == []

        result = await client.call_tool("import_issued_invoice", {"data": _data(3, "2026-06-05")})
        payload = _payload(result)
        assert payload["invoice"]["number"] == 3
        assert payload["invoice"]["imported_from"] == "external"
        # 2 was never imported and never declared: it is a gap left by the previous
        # numbering until an operator accounts for it.
        assert payload["undeclared_gaps"] == [2]

        gaps = await client.call_tool(
            "declare_invoice_register_gaps",
            {
                "year": 2026,
                "gaps": [{"number": 2, "reason": "annullata nel gestionale precedente"}],
            },
        )
        gap_rows = _payload(gaps)
        rows = (
            gap_rows["result"] if isinstance(gap_rows, dict) and "result" in gap_rows else gap_rows
        )
        assert rows[0]["number"] == 2

        # And the gap really is declared now: nothing left undeclared for the year.
        after = await client.call_tool("import_issued_invoice", {"data": _data(4, "2026-06-06")})
        assert _payload(after)["undeclared_gaps"] == []


async def test_list_invoice_register_gaps_reads_what_was_declared(
    mcp_session: Session, tmp_path: Path
) -> None:
    """`list_invoice_register_gaps` is a plain read: it stays on the surface of a
    default installation, unlike the two tools above -- it writes nothing and reads a
    table the REST API's `GET /api/invoices/register/{anno}/gaps` already exposes."""
    names = {
        tool.name for tool in await _server(mcp_session, tmp_path, full_access=False).list_tools()
    }
    assert "list_invoice_register_gaps" in names
    assert not {"import_issued_invoice", "declare_invoice_register_gaps"} <= names

    async with Client(_server(mcp_session, tmp_path, full_access=True)) as client:
        await client.call_tool(
            "declare_invoice_register_gaps",
            {
                "year": 2027,
                "gaps": [{"number": 3, "reason": "annullata nel gestionale precedente"}],
            },
        )

    async with Client(_server(mcp_session, tmp_path, full_access=False)) as client:
        result = await client.call_tool("list_invoice_register_gaps", {"year": 2027})
        rows = _payload(result)
        rows = rows["result"] if isinstance(rows, dict) and "result" in rows else rows
        assert rows[0]["number"] == 3
        assert rows[0]["reason"] == "annullata nel gestionale precedente"


async def test_the_import_and_the_gaps_take_the_legal_entity_they_are_for(
    mcp_session: Session, tmp_path: Path
) -> None:
    """REB-620, spec §7: `legal_entity_id` on the three register tools; omitted is the
    default azienda's register, a value that is not an id is refused in words."""
    from pigrocrm.core.legal_entities.models import LegalEntity

    _seed_fiscal_and_legal_entity_profiles(mcp_session)
    second = LegalEntity(
        name="rebase",
        legal_name="Rebase S.r.l.",
        vat_number="09876543210",
        address="Via Po 1",
        postal_code="10100",
        city="Torino",
        province="TO",
        country="IT",
    )
    customer = Organization(
        legal_name="Acme S.r.l.",
        vat_number="12345678901",
        sdi_code="ABCDEFG",
        address="Via Roma 1",
        postal_code="20154",
        city="Milano",
        province="MI",
        country="IT",
    )
    mcp_session.add_all([second, customer])
    mcp_session.flush()
    customer.legal_entity_id = second.id
    mcp_session.flush()
    FiscalProfileService(mcp_session).upsert(
        FiscalProfileUpsert(regime_code="RF19"),
        Actor(id=None, type="system", role="admin"),
        legal_entity_id=second.id,
    )
    data = {
        "year": 2026,
        "number": 3,
        "issue_date": "2026-06-05",
        "customer_id": str(customer.id),
        "lines": [
            {
                "description": "Consulenza",
                "quantity": "1",
                "unit_price": "100.00",
                "total_price": "100.00",
                "vat_rate": "0",
                "vat_nature": "N2.2",
            }
        ],
        "taxable_amount": "100.00",
        "vat_amount": "0.00",
        "stamp_duty": "2.00",
        "total": "100.00",
    }
    async with Client(_server(mcp_session, tmp_path, full_access=True)) as client:
        imported = await client.call_tool(
            "import_issued_invoice", {"data": data, "legal_entity_id": str(second.id)}
        )
        payload = _payload(imported)
        assert payload["invoice"]["legal_entity_id"] == str(second.id)
        assert payload["undeclared_gaps"] == [1, 2]
        await client.call_tool(
            "declare_invoice_register_gaps",
            {
                "year": 2026,
                "gaps": [{"number": 1, "reason": "mai emessa"}],
                "legal_entity_id": str(second.id),
            },
        )
        on_second = _payload(
            await client.call_tool(
                "list_invoice_register_gaps", {"year": 2026, "legal_entity_id": str(second.id)}
            )
        )
        rows = on_second["result"] if isinstance(on_second, dict) else on_second
        assert [r["number"] for r in rows] == [1]
        on_default = _payload(await client.call_tool("list_invoice_register_gaps", {"year": 2026}))
        rows = on_default["result"] if isinstance(on_default, dict) else on_default
        assert rows == []
        refused = await client.call_tool(
            "list_invoice_register_gaps", {"year": 2026, "legal_entity_id": "x"}
        )
        assert refused.is_error
        assert "legal_entity_id" in refused.content[0].text
