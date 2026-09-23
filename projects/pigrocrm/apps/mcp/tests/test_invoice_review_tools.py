"""REB-365's `review_invoice_import`: exists only behind `mcp_full_access`, like the
two writing tools it mirrors, but reads the register instead of writing it.

`test_mcp_invoice_ban.py` and `test_mcp_surface_coverage.py` own the structural
guarantee that this tool is absent by default and behind the same switch as
`import_issued_invoice`; this file only checks that the tool actually works once the
switch is on, and that it never writes.
"""

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
from pigrocrm.core.invoices.models import Invoice
from pigrocrm.core.storage import LocalFileStorage
from pigrocrm_mcp.server import build_server

TOOLS = {"review_invoice_import"}

FIXTURES = (
    Path(__file__).resolve().parents[3] / "packages" / "core" / "tests" / "fixtures" / "fatturapa"
)
CONSULENZA = "fpr12-consulenza-marzo.xml"
# Mirrors the fixture's own CedentePrestatore/CessionarioCommittente (see
# packages/core/tests/test_invoice_import_review.py for where these come from).
FORNITORE_PIVA = "01234567890"
FORNITORE_CF = "BNCCHR85M41H501Z"
CLIENTE_PIVA = "09876543210"

ADMIN = Actor(id=None, type="system", role="admin")


def _fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def _payload(result: Any) -> Any:
    """Mirrors `test_invoice_import_tools.py`'s own helper."""
    return result.structured_content or json.loads(result.content[0].text)


def _seed_emitter(session: Session) -> None:
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


def _server(session: Session, tmp_path: Path, *, full_access: bool) -> Any:
    return build_server(
        lambda: session,
        lambda: Actor(id=None, type="mcp", role="admin", full_access=full_access),
        LocalFileStorage(tmp_path),
        settings=Settings(_env_file=None, mcp_full_access=full_access),  # type: ignore[call-arg]
    )


@pytest.mark.parametrize("full_access", [False, True])
async def test_the_tool_exists_only_when_the_installation_opted_in(
    mcp_session: Session, tmp_path: Path, full_access: bool
) -> None:
    names = {
        tool.name
        for tool in await _server(mcp_session, tmp_path, full_access=full_access).list_tools()
    }
    assert (names >= TOOLS) is full_access


async def test_review_matches_the_customer_and_never_writes_and_is_idempotent(
    mcp_session: Session, tmp_path: Path, seeded_customer_id: str
) -> None:
    _seed_emitter(mcp_session)
    customer = mcp_session.get(Customer, UUID(seeded_customer_id))
    assert customer is not None
    customer.partita_iva = CLIENTE_PIVA
    mcp_session.flush()
    storage = LocalFileStorage(tmp_path / "documents")
    document_id = _seed_document(
        mcp_session, storage, _fixture(CONSULENZA), customer_id=seeded_customer_id
    )

    server = build_server(
        lambda: mcp_session,
        lambda: Actor(id=None, type="mcp", role="admin", full_access=True),
        storage,
        settings=Settings(_env_file=None, mcp_full_access=True),  # type: ignore[call-arg]
    )

    before = _invoice_count(mcp_session)
    async with Client(server) as client:
        first = _payload(
            await client.call_tool("review_invoice_import", {"document_ids": [document_id]})
        )
        second = _payload(
            await client.call_tool("review_invoice_import", {"document_ids": [document_id]})
        )

    assert first == second
    [row] = first["righe"]
    assert row["outcome"] == "ready"
    assert row["matched_customer_id"] == seeded_customer_id
    assert _invoice_count(mcp_session) == before


async def test_a_document_unrecognised_by_any_adapter_is_unclaimed(
    mcp_session: Session, tmp_path: Path, seeded_customer_id: str
) -> None:
    _seed_emitter(mcp_session)
    storage = LocalFileStorage(tmp_path / "documents")
    document_id = _seed_document(
        mcp_session, storage, b"garbage bytes", customer_id=seeded_customer_id
    )

    server = build_server(
        lambda: mcp_session,
        lambda: Actor(id=None, type="mcp", role="admin", full_access=True),
        storage,
        settings=Settings(_env_file=None, mcp_full_access=True),  # type: ignore[call-arg]
    )
    async with Client(server) as client:
        result = _payload(
            await client.call_tool("review_invoice_import", {"document_ids": [document_id]})
        )
    [row] = result["righe"]
    assert row["outcome"] == "unclaimed"
