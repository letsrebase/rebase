"""REB-366's `confirm_invoice_import`: exists only behind `mcp_full_access`, like the
two writing tools and the review tool it sits beside -- it is the one that actually
writes the register through `import_issued`.

`test_mcp_invoice_ban.py` and `test_mcp_surface_coverage.py` own the structural
guarantee that this tool is absent by default and behind the same switch as
`import_issued_invoice`; this file only checks that the tool actually converges onto
`import_issued`'s write once the switch is on.
"""

import hashlib
import json
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from mcp import Client
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.config import Settings
from pigrocrm.core.customers.models import Customer
from pigrocrm.core.documents.schemas import DocumentCreate
from pigrocrm.core.documents.service import DocumentService
from pigrocrm.core.emitter.repository import EmitterProfileRepository
from pigrocrm.core.emitter.schemas import EmitterProfileUpsert
from pigrocrm.core.emitter.service import EmitterProfileService
from pigrocrm.core.fiscal.repository import FiscalProfileRepository
from pigrocrm.core.fiscal.schemas import FiscalProfileUpsert
from pigrocrm.core.fiscal.service import FiscalProfileService
from pigrocrm.core.invoices.models import Invoice
from pigrocrm.core.storage import LocalFileStorage
from pigrocrm_mcp.server import build_server

TOOLS = {"confirm_invoice_import"}

FIXTURES = (
    Path(__file__).resolve().parents[3] / "packages" / "core" / "tests" / "fixtures" / "fatturapa"
)
CONSULENZA = "fpr12-consulenza-marzo.xml"
LOTTO = "fpr12-lotto-due-fatture.xml"
# Mirrors the fixtures' own CedentePrestatore/CessionarioCommittente (see
# packages/core/tests/test_invoice_import_confirm.py for where these come from).
FORNITORE_PIVA = "01234567890"
FORNITORE_CF = "BNCCHR85M41H501Z"
CLIENTE_PIVA = "09876543210"

ADMIN = Actor(id=None, type="system", role="admin")


def _fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def _payload(result: Any) -> Any:
    """Mirrors `test_invoice_import_tools.py`'s own helper."""
    return result.structured_content or json.loads(result.content[0].text)


def _seed_fiscal_and_emitter_profiles(session: Session) -> None:
    if FiscalProfileRepository(session).get() is None:
        FiscalProfileService(session).upsert(FiscalProfileUpsert(codice_regime="RF19"), ADMIN)
    if EmitterProfileRepository(session).get() is None:
        EmitterProfileService(session).upsert(
            EmitterProfileUpsert(
                ragione_sociale="Chiara Bianchi",
                partita_iva=FORNITORE_PIVA,
                codice_fiscale=FORNITORE_CF,
                indirizzo="Via delle Officine 10",
                cap="20121",
                comune="Milano",
                provincia="MI",
                nazione="IT",
            ),
            ADMIN,
        )


def _seed_document(
    session: Session, storage: LocalFileStorage, content: bytes, *, customer_id: str
) -> str:
    service = DocumentService(session, storage)
    document = service.create(
        DocumentCreate(customer_id=customer_id, titolo="Fattura ricevuta"), ADMIN
    )
    service.add_version(document.id, content, "application/xml", ADMIN)
    return str(document.id)


def _invoice_count(session: Session) -> int:
    return int(session.execute(select(func.count()).select_from(Invoice)).scalar_one())


def _server(session: Session, storage: LocalFileStorage, *, full_access: bool) -> Any:
    return build_server(
        lambda: session,
        lambda: Actor(id=None, type="mcp", role="admin", full_access=full_access),
        storage,
        settings=Settings(_env_file=None, mcp_full_access=full_access),  # type: ignore[call-arg]
    )


@pytest.mark.parametrize("full_access", [False, True])
async def test_the_tool_exists_only_when_the_installation_opted_in(
    mcp_session: Session, tmp_path: Path, full_access: bool
) -> None:
    storage = LocalFileStorage(tmp_path)
    names = {
        tool.name
        for tool in await _server(mcp_session, storage, full_access=full_access).list_tools()
    }
    assert (names >= TOOLS) is full_access


async def test_confirming_a_fresh_document_writes_and_a_second_confirm_is_a_no_op(
    mcp_session: Session, tmp_path: Path, seeded_customer_id: str
) -> None:
    _seed_fiscal_and_emitter_profiles(mcp_session)
    customer = mcp_session.get(Customer, UUID(seeded_customer_id))
    assert customer is not None
    customer.partita_iva = CLIENTE_PIVA
    mcp_session.flush()
    storage = LocalFileStorage(tmp_path / "documents")
    content = _fixture(CONSULENZA)
    document_id = _seed_document(mcp_session, storage, content, customer_id=seeded_customer_id)

    server = _server(mcp_session, storage, full_access=True)
    before = _invoice_count(mcp_session)
    async with Client(server) as client:
        first = _payload(
            await client.call_tool("confirm_invoice_import", {"document_id": document_id})
        )
        second = _payload(
            await client.call_tool("confirm_invoice_import", {"document_id": document_id})
        )

    [first_row] = first["righe"]
    assert first_row["outcome"] == "imported"
    assert first_row["fattura"]["numero"] == 6
    assert first_row["fattura"]["customer_id"] == seeded_customer_id
    assert first_row["fattura"]["xml_document_id"] == document_id
    assert first_row["fattura"]["xml_hash_sha256"] == hashlib.sha256(content).hexdigest()

    [second_row] = second["righe"]
    assert second_row["outcome"] == "already_present"
    assert second_row["fattura"]["id"] == first_row["fattura"]["id"]
    assert _invoice_count(mcp_session) == before + 1


async def test_a_batch_sourced_document_leaves_xml_document_id_null_on_every_invoice(
    mcp_session: Session, tmp_path: Path, seeded_customer_id: str
) -> None:
    _seed_fiscal_and_emitter_profiles(mcp_session)
    customer = mcp_session.get(Customer, UUID(seeded_customer_id))
    assert customer is not None
    customer.partita_iva = CLIENTE_PIVA
    mcp_session.flush()
    storage = LocalFileStorage(tmp_path / "documents")
    document_id = _seed_document(
        mcp_session, storage, _fixture(LOTTO), customer_id=seeded_customer_id
    )

    server = _server(mcp_session, storage, full_access=True)
    async with Client(server) as client:
        result = _payload(
            await client.call_tool("confirm_invoice_import", {"document_id": document_id})
        )

    righe = result["righe"]
    assert len(righe) == 2
    assert all(row["outcome"] == "imported" for row in righe)
    assert all(row["fattura"]["xml_document_id"] is None for row in righe)
    assert all(row["fattura"]["xml_hash_sha256"] is None for row in righe)


async def test_a_document_unrecognised_by_any_adapter_is_unclaimed_and_writes_nothing(
    mcp_session: Session, tmp_path: Path, seeded_customer_id: str
) -> None:
    _seed_fiscal_and_emitter_profiles(mcp_session)
    storage = LocalFileStorage(tmp_path / "documents")
    document_id = _seed_document(
        mcp_session, storage, b"garbage bytes", customer_id=seeded_customer_id
    )

    server = _server(mcp_session, storage, full_access=True)
    before = _invoice_count(mcp_session)
    async with Client(server) as client:
        result = _payload(
            await client.call_tool("confirm_invoice_import", {"document_id": document_id})
        )
    [row] = result["righe"]
    assert row["outcome"] == "unclaimed"
    assert row["fattura"] is None
    assert _invoice_count(mcp_session) == before


async def test_an_explicit_customer_id_resolves_needs_customer_confirmation(
    mcp_session: Session, tmp_path: Path, seeded_customer_id: str
) -> None:
    """`seeded_customer_id`'s own `partita_iva` is left `None` here, so the automatic
    match fails -- `customer_id` is the human decision that resolves it anyway."""
    _seed_fiscal_and_emitter_profiles(mcp_session)
    storage = LocalFileStorage(tmp_path / "documents")
    document_id = _seed_document(
        mcp_session, storage, _fixture(CONSULENZA), customer_id=seeded_customer_id
    )

    server = _server(mcp_session, storage, full_access=True)
    async with Client(server) as client:
        unresolved = _payload(
            await client.call_tool("confirm_invoice_import", {"document_id": document_id})
        )
        resolved = _payload(
            await client.call_tool(
                "confirm_invoice_import",
                {"document_id": document_id, "customer_id": seeded_customer_id},
            )
        )

    [unresolved_row] = unresolved["righe"]
    assert unresolved_row["outcome"] == "needs_customer_confirmation"
    assert unresolved_row["fattura"] is None

    [resolved_row] = resolved["righe"]
    assert resolved_row["outcome"] == "imported"
    assert resolved_row["fattura"]["customer_id"] == seeded_customer_id
