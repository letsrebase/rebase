"""`export_xml` serving a `"fatturapa"` row's own original file back (REB-368,
design 2026-09-23 §5 item 4/§7 item 6), exercised through a real
`InvoiceService.confirm_import` call and a real FPR12 fixture -- never a hand-built
snapshot -- so `_for_export`/`_xml_filename` run on exactly the row the register
would actually hold.
"""

import hashlib
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.customers.models import Customer
from pigrocrm.core.documents.schemas import DocumentCreate
from pigrocrm.core.documents.service import DocumentService
from pigrocrm.core.emitter.models import EmitterProfile
from pigrocrm.core.errors import Conflict
from pigrocrm.core.fiscal.models import FiscalProfile
from pigrocrm.core.invoices.models import Invoice
from pigrocrm.core.invoices.service import InvoiceService
from pigrocrm.core.storage import LocalFileStorage

FIXTURES = Path(__file__).parent / "fixtures" / "fatturapa"
CONSULENZA = "fpr12-consulenza-marzo.xml"
LOTTO = "fpr12-lotto-due-fatture.xml"
ADMIN = Actor(id=None, type="user", role="admin")

FORNITORE_PIVA = "01234567890"
FORNITORE_CF = "BNCCHR85M41H501Z"
CLIENTE_PIVA = "09876543210"


def _fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def _svc(session: Session, storage: LocalFileStorage) -> InvoiceService:
    session.add(FiscalProfile(codice_regime="RF19"))
    session.add(
        EmitterProfile(
            ragione_sociale="Chiara Bianchi",
            partita_iva=FORNITORE_PIVA,
            codice_fiscale=FORNITORE_CF,
            nazione="IT",
        )
    )
    session.flush()
    return InvoiceService(session, storage)


def _customer(session: Session, **overrides: object) -> Customer:
    base: dict[str, object] = {
        "ragione_sociale": f"Cliente {uuid4()}",
        "indirizzo": "Piazza dei Modelli 4",
        "cap": "00187",
        "comune": "Roma",
        "provincia": "RM",
        "nazione": "IT",
    }
    base.update(overrides)
    customer = Customer(**base)
    session.add(customer)
    session.flush()
    return customer


def _document(
    session: Session, storage: LocalFileStorage, content: bytes, *, owner: Customer | None = None
) -> UUID:
    owner = owner if owner is not None else _customer(session)
    service = DocumentService(session, storage)
    document = service.create(
        DocumentCreate(customer_id=owner.id, titolo="Fattura ricevuta"), ADMIN
    )
    service.add_version(document.id, content, "application/xml", ADMIN)
    return document.id


def _confirm_single(
    service: InvoiceService, document_id: UUID, *, customer_id: UUID | None = None
) -> Invoice:
    """Confirms a single-invoice document through the real service call and returns
    the resulting row, already `"fatturapa"` -- `confirm_import` sets it natively."""
    [row] = service.confirm_import(document_id, ADMIN, customer_id=customer_id)
    assert row.outcome == "imported" and row.fattura is not None
    assert row.fattura.importata_da == "fatturapa"
    invoice = service.repo.get(row.fattura.id)
    assert invoice is not None
    return invoice


def test_export_xml_serves_the_original_file_for_a_single_invoice_fatturapa_row(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    service = _svc(db_session, local_storage)
    content = _fixture(CONSULENZA)
    customer = _customer(db_session, partita_iva=CLIENTE_PIVA)
    document_id = _document(db_session, local_storage, content, owner=customer)
    invoice = _confirm_single(service, document_id, customer_id=customer.id)
    expected_digest = hashlib.sha256(content).hexdigest()
    assert invoice.xml_document_id == document_id
    assert invoice.xml_hash_sha256 == expected_digest

    artifact = service.export_xml(invoice.id, ADMIN)

    assert artifact.kind == "xml"
    assert artifact.document_id == document_id
    assert artifact.version_numero == 1
    assert artifact.hash_sha256 == expected_digest

    # No reconstruction happened: still exactly one version on that document.
    document = service.documents.repo.get(document_id)
    assert document is not None
    assert document.versione_corrente == 1

    data, content_type, _ = service.download(invoice.id, "xml", ADMIN)
    assert data == content
    assert content_type == "application/xml"


def test_export_xml_is_idempotent_and_never_writes_a_second_version(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    service = _svc(db_session, local_storage)
    content = _fixture(CONSULENZA)
    customer = _customer(db_session, partita_iva=CLIENTE_PIVA)
    document_id = _document(db_session, local_storage, content, owner=customer)
    invoice = _confirm_single(service, document_id, customer_id=customer.id)

    first = service.export_xml(invoice.id, ADMIN)
    second = service.export_xml(invoice.id, ADMIN)

    assert second == first
    document = service.documents.repo.get(document_id)
    assert document is not None
    assert document.versione_corrente == 1


def test_export_xml_refuses_a_batch_sourced_fatturapa_row_exactly_as_before(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    """A `lotto` batch never gets `xml_document_id` (design §5 item 3), so a
    `"fatturapa"` row out of one -- `confirm_import` sets `importata_da` the same
    way regardless of batch size -- still has nothing on file to serve back."""
    service = _svc(db_session, local_storage)
    content = _fixture(LOTTO)
    customer = _customer(db_session, partita_iva=CLIENTE_PIVA)
    document_id = _document(db_session, local_storage, content, owner=customer)
    rows = service.confirm_import(document_id, ADMIN, customer_id=customer.id)
    assert len(rows) == 2
    invoice_ids = [row.fattura.id for row in rows if row.fattura is not None]
    for row in rows:
        assert row.fattura is not None
        assert row.fattura.importata_da == "fatturapa"
        assert row.fattura.xml_document_id is None

    for invoice_id in invoice_ids:
        with pytest.raises(Conflict) as caught:
            service.export_xml(invoice_id, ADMIN)
        assert "non ne produce un secondo" in caught.value.message


def test_export_xml_still_refuses_an_esterno_row_exactly_as_before(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    """The hand-declared `"esterno"` path is untouched by the `"fatturapa"` branch:
    it never has an `xml_document_id` to serve, and keeps refusing unconditionally."""
    from datetime import date
    from decimal import Decimal

    service = _svc(db_session, local_storage)
    customer = _customer(db_session)
    row = Invoice(
        customer_id=customer.id,
        tipo="fattura",
        stato="emessa",
        anno=2026,
        numero=1,
        data_emissione=date(2026, 3, 15),
        importata_da="esterno",
        imponibile=Decimal("100.00"),
        imposta=Decimal("0.00"),
        bollo=Decimal("0.00"),
        totale=Decimal("100.00"),
    )
    db_session.add(row)
    db_session.flush()

    with pytest.raises(Conflict) as caught:
        service.export_xml(row.id, ADMIN)
    assert "non ne produce un secondo" in caught.value.message


def test_export_xml_refuses_a_fatturapa_row_whose_stored_file_no_longer_matches_its_hash(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    """Hash-verified, genuinely: if the document's own storage bytes have drifted
    from what the register recorded at confirm time, this refuses rather than
    handing back a file it cannot vouch for -- there is no `FatturaPAExporter` to
    regenerate from and repair with, unlike the native path's own `_store_artifact`."""
    service = _svc(db_session, local_storage)
    content = _fixture(CONSULENZA)
    customer = _customer(db_session, partita_iva=CLIENTE_PIVA)
    document_id = _document(db_session, local_storage, content, owner=customer)
    invoice = _confirm_single(service, document_id, customer_id=customer.id)

    document = service.documents.repo.get(document_id)
    assert document is not None
    version = service.documents.repo.version(document.id, document.versione_corrente)
    assert version is not None
    local_storage.put(version.storage_key, b"<tampered/>", "application/xml")

    with pytest.raises(Conflict) as caught:
        service.export_xml(invoice.id, ADMIN)
    assert caught.value.details["campo"] == "xml_hash_sha256"
