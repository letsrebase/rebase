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
from pigrocrm.core.customers.models import Customer
from pigrocrm.core.emitter.repository import AziendaRepository
from pigrocrm.core.emitter.schemas import AziendaUpsert
from pigrocrm.core.emitter.service import AziendaService
from pigrocrm.core.fiscal.repository import FiscalProfileRepository
from pigrocrm.core.fiscal.schemas import FiscalProfileUpsert
from pigrocrm.core.fiscal.service import FiscalProfileService
from pigrocrm.core.storage import LocalFileStorage
from pigrocrm_mcp.server import build_server

TOOLS = {"import_issued_invoice", "declare_invoice_register_gaps"}


def _payload(result: Any) -> Any:
    """Mirrors `test_gmail_tools.py`'s helper: a dict result arrives as
    `structured_content` directly, a list result arrives wrapped as `{"result": [...]}`.
    """
    import json

    return result.structured_content or json.loads(result.content[0].text)


def _seed_fiscal_and_emitter_profiles(session: Session) -> None:
    """The two profiles `import_issued` reads before it will write anything -- copied
    from `packages/core/tests/conftest.py`'s `_invoice_service` because the MCP test
    root has its own `conftest.py` and does not see the core suite's.
    """
    admin = Actor(id=None, type="system", role="admin")
    # The azienda first, always (REB-615: a bare default is already seeded and this
    # fills in its identity), then its fiscal profile when it has none.
    AziendaService(session).upsert_default(
        AziendaUpsert(
            ragione_sociale="Studio Rossi di Mario Rossi",
            partita_iva="01234567890",
            codice_fiscale="HMCRFT00A01H501K",
            indirizzo="Via Vittorio Veneto 12",
            cap="20124",
            comune="Milano",
            provincia="MI",
            nazione="IT",
            email="mario@example.com",
        ),
        admin,
    )
    azienda = AziendaRepository(session).default()
    assert azienda is not None
    if FiscalProfileRepository(session).get(azienda.id) is None:
        FiscalProfileService(session).upsert(FiscalProfileUpsert(codice_regime="RF19"), admin)


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
    _seed_fiscal_and_emitter_profiles(mcp_session)
    customer = Customer(
        ragione_sociale="Acme S.r.l.",
        partita_iva="12345678901",
        codice_sdi="ABCDEFG",
        indirizzo="Via Roma 1",
        cap="20154",
        comune="Milano",
        provincia="MI",
        nazione="IT",
    )
    mcp_session.add(customer)
    mcp_session.flush()

    def _dati(numero: int, data_emissione: str) -> dict[str, Any]:
        return {
            "anno": 2026,
            "numero": numero,
            "data_emissione": data_emissione,
            "customer_id": str(customer.id),
            "righe": [
                {
                    "descrizione": "900142/0526/Consulenza AI CTO progetto Aurora",
                    "quantita": "20",
                    "prezzo_unitario": "300",
                    "prezzo_totale": "6000.00",
                    "aliquota_iva": "0",
                    "natura": "N2.2",
                }
            ],
            "imponibile": "6000.00",
            "imposta": "0.00",
            # `bollo` is declared beside the total and never added to it: the identity is
            # `imponibile + imposta == totale`, the same one `sum_totals` stores for a
            # natively issued invoice.
            "bollo": "2.00",
            "totale": "6000.00",
        }

    async with Client(_server(mcp_session, tmp_path, full_access=True)) as client:
        # Numero 1 leaves nothing undeclared: it is where a year's register starts, and
        # the scan runs from 1 -- importing a 7 first would report the six holes below it.
        first = await client.call_tool("import_issued_invoice", {"dati": _dati(1, "2026-06-01")})
        assert _payload(first)["buchi_non_dichiarati"] == []

        result = await client.call_tool("import_issued_invoice", {"dati": _dati(3, "2026-06-05")})
        payload = _payload(result)
        assert payload["fattura"]["numero"] == 3
        assert payload["fattura"]["importata_da"] == "esterno"
        # 2 was never imported and never declared: it is a gap left by the previous
        # numbering until an operator accounts for it.
        assert payload["buchi_non_dichiarati"] == [2]

        gaps = await client.call_tool(
            "declare_invoice_register_gaps",
            {
                "anno": 2026,
                "buchi": [{"numero": 2, "motivo": "annullata nel gestionale precedente"}],
            },
        )
        gap_rows = _payload(gaps)
        rows = (
            gap_rows["result"] if isinstance(gap_rows, dict) and "result" in gap_rows else gap_rows
        )
        assert rows[0]["numero"] == 2

        # And the gap really is declared now: nothing left undeclared for the year.
        after = await client.call_tool("import_issued_invoice", {"dati": _dati(4, "2026-06-06")})
        assert _payload(after)["buchi_non_dichiarati"] == []


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
                "anno": 2027,
                "buchi": [{"numero": 3, "motivo": "annullata nel gestionale precedente"}],
            },
        )

    async with Client(_server(mcp_session, tmp_path, full_access=False)) as client:
        result = await client.call_tool("list_invoice_register_gaps", {"anno": 2027})
        rows = _payload(result)
        rows = rows["result"] if isinstance(rows, dict) and "result" in rows else rows
        assert rows[0]["numero"] == 3
        assert rows[0]["motivo"] == "annullata nel gestionale precedente"
