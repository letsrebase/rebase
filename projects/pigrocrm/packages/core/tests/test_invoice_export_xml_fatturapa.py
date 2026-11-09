"""`export_xml` serving a `"fatturapa"` row's own original file back (REB-368,
design 2026-09-23 §5 item 4/§7 item 6), exercised through a real
`InvoiceService.confirm_import` call and a real FPR12 fixture -- never a hand-built
snapshot -- so `_for_export`/`_xml_filename` run on exactly the row the register
would actually hold.
"""

import hashlib
from pathlib import Path
from uuid import UUID

import pytest
from fakes.customer_fixtures import make_customer_with_address
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.documents.schemas import DocumentCreate
from pigrocrm.core.documents.service import DocumentService
from pigrocrm.core.errors import Conflict
from pigrocrm.core.fiscal.models import FiscalProfile
from pigrocrm.core.invoices.models import Invoice
from pigrocrm.core.invoices.service import InvoiceService
from pigrocrm.core.legal_entities.repository import LegalEntityRepository
from pigrocrm.core.organizations.models import Organization
from pigrocrm.core.storage import LocalFileStorage

FIXTURES = Path(__file__).parent / "fixtures" / "fatturapa"
CONSULTING = "fpr-12-march-consulting.xml"
LOTTO = "fpr-12-batch-due-invoices.xml"
ADMIN = Actor(id=None, type="user", role="admin")

SUPPLIER_VAT_NUMBER = "01234567890"
SUPPLIER_CF = "BNCCHR85M41H501Z"
CUSTOMER_VAT_NUMBER = "09876543210"


def _fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def _svc(session: Session, storage: LocalFileStorage) -> InvoiceService:
    from pigrocrm.core.actor import Actor
    from pigrocrm.core.legal_entities.schemas import LegalEntityUpsert
    from pigrocrm.core.legal_entities.service import LegalEntityService

    LegalEntityService(session).upsert_default(
        LegalEntityUpsert(
            legal_name="Chiara Bianchi",
            vat_number=SUPPLIER_VAT_NUMBER,
            tax_code=SUPPLIER_CF,
            country="IT",
        ),
        Actor(id=None, type="system", role="admin"),
    )
    legal_entity = LegalEntityRepository(session).default()
    assert legal_entity is not None
    session.add(FiscalProfile(regime_code="RF19", legal_entity_id=legal_entity.id))
    session.flush()
    return InvoiceService(session, storage)


def _document(
    session: Session,
    storage: LocalFileStorage,
    content: bytes,
    *,
    owner: Organization | None = None,
) -> UUID:
    owner = owner if owner is not None else make_customer_with_address(session)
    service = DocumentService(session, storage)
    document = service.create(DocumentCreate(customer_id=owner.id, title="Fattura ricevuta"), ADMIN)
    service.add_version(document.id, content, "application/xml", ADMIN)
    return document.id


def _confirm_single(
    service: InvoiceService, document_id: UUID, *, customer_id: UUID | None = None
) -> Invoice:
    """Confirms a single-invoice document through the real service call and returns
    the resulting row, already `"fatturapa"` -- `confirm_import` sets it natively."""
    [row] = service.confirm_import(document_id, ADMIN, customer_id=customer_id)
    assert row.outcome == "imported" and row.invoice is not None
    assert row.invoice.imported_from == "fatturapa"
    invoice = service.repo.get(row.invoice.id)
    assert invoice is not None
    return invoice


def test_export_xml_serves_the_original_file_for_a_single_invoice_fatturapa_row(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    service = _svc(db_session, local_storage)
    content = _fixture(CONSULTING)
    customer = make_customer_with_address(db_session, vat_number=CUSTOMER_VAT_NUMBER)
    document_id = _document(db_session, local_storage, content, owner=customer)
    invoice = _confirm_single(service, document_id, customer_id=customer.id)
    expected_digest = hashlib.sha256(content).hexdigest()
    assert invoice.xml_document_id == document_id
    assert invoice.xml_hash_sha256 == expected_digest

    artifact = service.export_xml(invoice.id, ADMIN)

    assert artifact.kind == "xml"
    assert artifact.document_id == document_id
    assert artifact.version_number == 1
    assert artifact.hash_sha256 == expected_digest

    # No reconstruction happened: still exactly one version on that document.
    document = service.documents.repo.get(document_id)
    assert document is not None
    assert document.current_version == 1

    data, content_type, _ = service.download(invoice.id, "xml", ADMIN)
    assert data == content
    assert content_type == "application/xml"


def test_export_xml_is_idempotent_and_never_writes_a_second_version(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    service = _svc(db_session, local_storage)
    content = _fixture(CONSULTING)
    customer = make_customer_with_address(db_session, vat_number=CUSTOMER_VAT_NUMBER)
    document_id = _document(db_session, local_storage, content, owner=customer)
    invoice = _confirm_single(service, document_id, customer_id=customer.id)

    first = service.export_xml(invoice.id, ADMIN)
    second = service.export_xml(invoice.id, ADMIN)

    assert second == first
    document = service.documents.repo.get(document_id)
    assert document is not None
    assert document.current_version == 1


def test_export_xml_refuses_a_batch_sourced_fatturapa_row_exactly_as_before(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    """A `lotto` batch never gets `xml_document_id` (design §5 item 3), so a
    `"fatturapa"` row out of one -- `confirm_import` sets `imported_from` the same
    way regardless of batch size -- still has nothing on file to serve back."""
    service = _svc(db_session, local_storage)
    content = _fixture(LOTTO)
    customer = make_customer_with_address(db_session, vat_number=CUSTOMER_VAT_NUMBER)
    document_id = _document(db_session, local_storage, content, owner=customer)
    rows = service.confirm_import(document_id, ADMIN, customer_id=customer.id)
    assert len(rows) == 2
    invoice_ids = [row.invoice.id for row in rows if row.invoice is not None]
    for row in rows:
        assert row.invoice is not None
        assert row.invoice.imported_from == "fatturapa"
        assert row.invoice.xml_document_id is None

    for invoice_id in invoice_ids:
        with pytest.raises(Conflict) as caught:
            service.export_xml(invoice_id, ADMIN)
        assert "non ne produce un secondo" in caught.value.message


def test_export_xml_still_refuses_an_external_row_exactly_as_before(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    """The hand-declared `"external"` path is untouched by the `"fatturapa"` branch:
    it never has an `xml_document_id` to serve, and keeps refusing unconditionally."""
    from datetime import date
    from decimal import Decimal

    service = _svc(db_session, local_storage)
    customer = make_customer_with_address(db_session)
    row = Invoice(
        customer_id=customer.id,
        type="invoice",
        status="issued",
        year=2026,
        number=1,
        issue_date=date(2026, 3, 15),
        imported_from="external",
        taxable_amount=Decimal("100.00"),
        vat_amount=Decimal("0.00"),
        stamp_duty=Decimal("0.00"),
        total=Decimal("100.00"),
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
    handing back a file it cannot vouch for -- there is no `InvoicePaExporter` to
    regenerate from and repair with, unlike the native path's own `_store_artifact`."""
    service = _svc(db_session, local_storage)
    content = _fixture(CONSULTING)
    customer = make_customer_with_address(db_session, vat_number=CUSTOMER_VAT_NUMBER)
    document_id = _document(db_session, local_storage, content, owner=customer)
    invoice = _confirm_single(service, document_id, customer_id=customer.id)

    document = service.documents.repo.get(document_id)
    assert document is not None
    version = service.documents.repo.version(document.id, document.current_version)
    assert version is not None
    local_storage.put(version.storage_key, b"<tampered/>", "application/xml")

    with pytest.raises(Conflict) as caught:
        service.export_xml(invoice.id, ADMIN)
    assert caught.value.details["field"] == "xml_hash_sha256"
