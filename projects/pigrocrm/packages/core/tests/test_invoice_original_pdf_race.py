"""An import links a document as an invoice's PDF while a version that is not a PDF is
uploaded onto that same document (REB-480).

`DocumentService._check_invoice_pdf` refuses a non-PDF version on a document an invoice
names as its PDF, and to do that it reads the link. `import_issued` writes the link only
at its commit, after `_validate_original_pdf` has read the document's current version
among its pure checks. With nothing between them both reads pass: the upload sees no
link, the import sees a PDF, and the invoice ends up naming a document whose current
version is XML. Greptile found it on PR #420. Both paths now lock the document's row
before they read, so whichever comes second waits for the first to commit.

A committed fixture and two real connections, for the reason `test_invoice_issue_race.py`
gives: the transactional `db_session` shares one transaction between both sides, and the
interleaving under test could not happen there.
"""

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import date
from decimal import Decimal
from pathlib import Path
from threading import Event
from uuid import UUID

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session, sessionmaker

from pigrocrm.core.actor import Actor
from pigrocrm.core.customers.models import Customer
from pigrocrm.core.db import session_factory
from pigrocrm.core.documents.schemas import DocumentCreate
from pigrocrm.core.documents.service import DocumentService
from pigrocrm.core.emitter.schemas import EmitterProfileUpsert
from pigrocrm.core.emitter.service import EmitterProfileService
from pigrocrm.core.errors import ValidationFailed
from pigrocrm.core.fiscal.schemas import FiscalProfileUpsert
from pigrocrm.core.fiscal.service import FiscalProfileService
from pigrocrm.core.invoices.schemas import InvoiceImport, InvoiceLineImport, PdfSorgente
from pigrocrm.core.invoices.service import InvoiceService
from pigrocrm.core.storage.local import LocalFileStorage

ADMIN = Actor(id=None, type="user", role="admin")
ORIGINAL_PDF = b"%PDF-1.4 originale"
XHTML = b'<html xmlns="http://www.w3.org/1999/xhtml"><body>ciao</body></html>'


def _configure(session: Session) -> UUID:
    FiscalProfileService(session).upsert(FiscalProfileUpsert(codice_regime="RF19"), ADMIN)
    EmitterProfileService(session).upsert(
        EmitterProfileUpsert(
            ragione_sociale="Studio Rossi",
            partita_iva="01234567890",
            codice_fiscale="HMCRFT00A01H501K",
            indirizzo="Via Vittorio Veneto 12",
            cap="20124",
            comune="Milano",
            provincia="MI",
            nazione="IT",
            email="mario@example.com",
        ),
        ADMIN,
    )
    customer = Customer(
        ragione_sociale="Acme S.r.l.",
        partita_iva="12345678901",
        codice_sdi="ABCDEFG",
        indirizzo="Corso Italia 5",
        cap="00100",
        comune="Roma",
        provincia="RM",
        nazione="IT",
    )
    session.add(customer)
    session.flush()
    return customer.id


def _import_payload(customer_id: UUID, document_id: UUID) -> InvoiceImport:
    return InvoiceImport(
        anno=2026,
        numero=7,
        data_emissione=date(2026, 5, 5),
        customer_id=customer_id,
        causale="Consulenza",
        righe=[
            InvoiceLineImport(
                descrizione="Consulenza",
                quantita=Decimal("1"),
                prezzo_unitario=Decimal("100"),
                prezzo_totale=Decimal("100.00"),
                aliquota_iva=Decimal("0"),
                natura="N2.2",
            )
        ],
        imponibile=Decimal("100.00"),
        imposta=Decimal("0.00"),
        bollo=Decimal("0.00"),
        totale=Decimal("100.00"),
        pdf_sorgente=PdfSorgente(document_id=document_id),
    )


def _cleanup(factory: sessionmaker[Session], customer_id: UUID | None) -> None:
    """Unwind everything this test committed, the year's counter included (see
    `test_invoice_issue_race._cleanup` for why the counter is the part that bites).
    Dependency order, since nothing is rolling back: the invoice's lines and the invoice
    before the document it points at, the versions before their document, the activities
    of all of them, then the profiles and the customer."""
    if customer_id is None:
        return
    with factory() as cleaner:
        params = {"customer": customer_id}
        cleaner.execute(
            text(
                "DELETE FROM activities WHERE entity_id IN ("
                " SELECT id FROM invoices WHERE customer_id = :customer"
                " UNION SELECT id FROM documents WHERE customer_id = :customer)"
            ),
            params,
        )
        cleaner.execute(
            text(
                "DELETE FROM invoice_lines WHERE invoice_id IN "
                "(SELECT id FROM invoices WHERE customer_id = :customer)"
            ),
            params,
        )
        cleaner.execute(text("DELETE FROM invoices WHERE customer_id = :customer"), params)
        cleaner.execute(
            text(
                "DELETE FROM document_versions WHERE document_id IN "
                "(SELECT id FROM documents WHERE customer_id = :customer)"
            ),
            params,
        )
        cleaner.execute(text("DELETE FROM documents WHERE customer_id = :customer"), params)
        cleaner.execute(text("DELETE FROM invoice_counters"))
        cleaner.execute(text("DELETE FROM fiscal_profile"))
        cleaner.execute(text("DELETE FROM emitter_profile"))
        cleaner.execute(text("DELETE FROM customers WHERE id = :customer"), params)
        cleaner.commit()


def test_an_xml_upload_during_an_import_of_the_same_pdf_waits_and_is_refused(
    db_engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The import has read the document's current version as a PDF and not committed
    the link yet; the upload arrives in that window. It must not finish before the
    import does, and once the import commits it must be refused, leaving the invoice's
    PDF a PDF.

    `_validate_original_pdf` is wrapped to hold the import there, right after its read,
    until the upload has had two seconds to get through. Without the lock the upload
    needs a few milliseconds and comes back with a new version: the `not done` assertion
    fails first, and the refusal assertion after it."""
    factory = session_factory(db_engine)
    storage = LocalFileStorage(tmp_path / "documents")
    customer_id: UUID | None = None
    try:
        with factory() as setup:
            customer_id = _configure(setup)
            documents = DocumentService(setup, storage)
            document = documents.create(
                DocumentCreate(customer_id=customer_id, tipo="fattura", titolo="Originale"),
                ADMIN,
            )
            documents.add_version(document.id, ORIGINAL_PDF, "application/pdf", ADMIN)
            setup.commit()

        validated = Event()
        release = Event()
        original: Callable[..., UUID] = InvoiceService._validate_original_pdf

        def held_after_the_read(self: InvoiceService, customer: UUID, document_id: UUID) -> UUID:
            result = original(self, customer, document_id)
            validated.set()
            release.wait(timeout=30)
            return result

        monkeypatch.setattr(InvoiceService, "_validate_original_pdf", held_after_the_read)

        def import_it() -> UUID:
            with factory() as session:
                assert customer_id is not None
                read = InvoiceService(session, storage).import_issued(
                    _import_payload(customer_id, document.id), ADMIN
                )
                return read.id

        def upload_xml() -> str:
            with factory() as session:
                try:
                    DocumentService(session, storage).add_version(
                        document.id, XHTML, "application/xml", ADMIN
                    )
                except ValidationFailed:
                    return "refused"
                return "added"

        with ThreadPoolExecutor(max_workers=2) as pool:
            importing = pool.submit(import_it)
            assert validated.wait(timeout=30), "the import never reached its read"
            uploading = pool.submit(upload_xml)
            done, _ = wait([uploading], timeout=2)
            upload_waited = not done
            release.set()
            invoice_id = importing.result(timeout=60)
            outcome = uploading.result(timeout=60)

        assert upload_waited, "the upload went through while the import held the document"
        assert outcome == "refused"
        with factory() as reader:
            content, content_type, _ = InvoiceService(reader, storage).download(
                invoice_id, "pdf", ADMIN
            )
        assert (content, content_type) == (ORIGINAL_PDF, "application/pdf")
    finally:
        _cleanup(factory, customer_id)
