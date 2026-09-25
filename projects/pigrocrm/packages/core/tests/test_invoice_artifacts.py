"""`produce_artifacts`: the PDF for every invoice type, and the XML alongside it once
a fattura has a number.

Task 13 shipped `produce_artifacts` with no test of its own anywhere in the repo. This
file exists because of two real defects found while building the REST surface on top
of it (task 14): it returned a single `InvoiceArtifact` where every caller needs the
whole result of one call, and it could not render a proforma's PDF at all -- it always
read the frozen, `issue`-only view (`_for_export`), which a proforma never populates.
"""

import hashlib
import subprocess
from collections.abc import Callable
from decimal import Decimal
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from pigrocrm.core.activities.service import ActivityService
from pigrocrm.core.actor import Actor
from pigrocrm.core.customers.models import Customer
from pigrocrm.core.documents.models import Document
from pigrocrm.core.documents.schemas import DocumentCreate, DocumentListQuery
from pigrocrm.core.documents.service import DocumentService
from pigrocrm.core.emitter.schemas import EmitterProfileUpsert
from pigrocrm.core.emitter.service import EmitterProfileService
from pigrocrm.core.errors import Conflict, NotFound, ValidationFailed
from pigrocrm.core.fiscal.schemas import FiscalProfileUpsert
from pigrocrm.core.fiscal.service import FiscalProfileService
from pigrocrm.core.invoices.models import Invoice
from pigrocrm.core.invoices.schemas import InvoiceCreate, InvoiceIssue, InvoiceLineIn
from pigrocrm.core.invoices.service import InvoiceService
from pigrocrm.core.storage.local import LocalFileStorage

ADMIN = Actor(id=None, type="system", role="admin")


@pytest.fixture
def storage(tmp_path) -> LocalFileStorage:  # type: ignore[no-untyped-def]
    return LocalFileStorage(tmp_path / "documents")


@pytest.fixture
def service(db_session: Session, storage: LocalFileStorage) -> InvoiceService:
    FiscalProfileService(db_session).upsert(FiscalProfileUpsert(codice_regime="RF19"), ADMIN)
    EmitterProfileService(db_session).upsert(
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
    return InvoiceService(db_session, storage)


@pytest.fixture
def customer_id(db_session: Session) -> UUID:
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
    db_session.add(customer)
    db_session.flush()
    return customer.id


def _issue(service: InvoiceService, customer_id: UUID) -> UUID:
    draft = service.create(
        InvoiceCreate(
            customer_id=customer_id,
            causale="Consulenza",
            righe=[InvoiceLineIn(descrizione="Consulenza", prezzo_unitario=Decimal("1000.00"))],
        ),
        ADMIN,
    )
    return service.issue(draft.id, InvoiceIssue(), ADMIN).id


def _confirmed_proforma(service: InvoiceService, customer_id: UUID) -> UUID:
    proforma = service.create(
        InvoiceCreate(
            customer_id=customer_id,
            tipo="proforma",
            righe=[InvoiceLineIn(descrizione="Consulenza", prezzo_unitario=Decimal("500.00"))],
        ),
        ADMIN,
    )
    service.confirm_proforma(proforma.id, ADMIN)
    return proforma.id


def test_a_confirmed_proforma_produces_only_a_pdf(
    service: InvoiceService, customer_id: UUID
) -> None:
    """A proforma is not a fiscal document (`export_xml` refuses one on the row's own
    state), but the PDF is a live view built from the current customer/emitter/fiscal
    data -- there is nothing frozen to read back, since only `issue` writes
    `snapshot`/`anno`/`numero`."""
    proforma_id = _confirmed_proforma(service, customer_id)
    artifacts = service.produce_artifacts(proforma_id, ADMIN)
    assert [a.kind for a in artifacts] == ["pdf"]
    assert artifacts[0].content_type == "application/pdf"


def test_an_issued_fattura_produces_both_the_pdf_and_the_xml(
    service: InvoiceService, customer_id: UUID
) -> None:
    invoice_id = _issue(service, customer_id)
    artifacts = service.produce_artifacts(invoice_id, ADMIN)
    assert [a.kind for a in artifacts] == ["pdf", "xml"]
    invoice = service.get(invoice_id, ADMIN)
    assert invoice.xml_hash_sha256 == artifacts[1].hash_sha256


def test_producing_artifacts_twice_is_idempotent_for_a_proforma(
    service: InvoiceService, db_session: Session, customer_id: UUID
) -> None:
    proforma_id = _confirmed_proforma(service, customer_id)
    first = service.produce_artifacts(proforma_id, ADMIN)
    second = service.produce_artifacts(proforma_id, ADMIN)
    assert first[0].hash_sha256 == second[0].hash_sha256
    versions = db_session.execute(
        text("SELECT count(*) FROM document_versions WHERE document_id = :id"),
        {"id": first[0].document_id},
    ).scalar_one()
    assert versions == 1


def test_producing_artifacts_twice_is_idempotent_for_an_issued_fattura(
    service: InvoiceService, db_session: Session, customer_id: UUID
) -> None:
    invoice_id = _issue(service, customer_id)
    first = service.produce_artifacts(invoice_id, ADMIN)
    second = service.produce_artifacts(invoice_id, ADMIN)
    assert [a.hash_sha256 for a in first] == [a.hash_sha256 for a in second]
    for artifact in first:
        versions = db_session.execute(
            text("SELECT count(*) FROM document_versions WHERE document_id = :id"),
            {"id": artifact.document_id},
        ).scalar_one()
        assert versions == 1


def _non_resident_customer(db_session: Session) -> UUID:
    customer = Customer(
        ragione_sociale="Example Ltd",
        partita_iva="GB123456789",
        indirizzo="1 Old Street",
        cap="EC1V 9HL",
        comune="London",
        provincia="",
        nazione="GB",
    )
    db_session.add(customer)
    db_session.flush()
    return customer.id


def test_the_pdf_for_a_non_resident_customer_carries_the_7_ter_reference(
    service: InvoiceService,
    db_session: Session,
    storage: LocalFileStorage,
    extract_pdf_text: Callable[[LocalFileStorage, Session, UUID], str],
) -> None:
    """ORB-32, on the document the customer reads. The footer used to print the
    profile's domestic declaration whatever the lines said; it now prints the
    declaration the lines actually carry, so the PDF and the XML agree."""
    invoice_id = _issue(service, _non_resident_customer(db_session))
    pdf, _xml = service.produce_artifacts(invoice_id, ADMIN)
    testo = extract_pdf_text(storage, db_session, pdf.document_id)
    assert "7-ter" in testo
    assert "DPR 633/1972" in testo
    assert "L. 190/2014" not in testo


def test_the_pdf_for_an_italian_customer_keeps_the_domestic_declaration(
    service: InvoiceService,
    customer_id: UUID,
    db_session: Session,
    storage: LocalFileStorage,
    extract_pdf_text: Callable[[LocalFileStorage, Session, UUID], str],
) -> None:
    invoice_id = _issue(service, customer_id)
    pdf, _xml = service.produce_artifacts(invoice_id, ADMIN)
    testo = extract_pdf_text(storage, db_session, pdf.document_id)
    assert "L. 190/2014" in testo
    assert "7-ter" not in testo


# --- discarding a proforma takes its PDF with it (ORB-41) ---------------------------


def test_discarding_a_proforma_archives_its_pdf_and_keeps_the_bytes(
    service: InvoiceService,
    db_session: Session,
    storage: LocalFileStorage,
    customer_id: UUID,
) -> None:
    """The soft delete of a proforma used to stop at `invoices.deleted_at` and leave
    `pdf_document_id` pointing at a live row, so `list_documents` kept showing
    "Proforma PROV-... (PDF)" and its download kept working while `get_invoice` on the
    owner answered not found (ORB-41). The document goes with its owner, in the same
    transaction, and as a soft delete: the row and the stored bytes stay, so a restore
    of the document is still a real restore."""
    proforma_id = _confirmed_proforma(service, customer_id)
    (pdf,) = service.produce_artifacts(proforma_id, ADMIN)
    documents = DocumentService(db_session, storage)
    listed = documents.list(DocumentListQuery(customer_id=customer_id), ADMIN).items
    assert pdf.document_id in [item.id for item in listed]

    service.soft_delete(proforma_id, ADMIN)

    listed = documents.list(DocumentListQuery(customer_id=customer_id), ADMIN).items
    assert pdf.document_id not in [item.id for item in listed]
    with pytest.raises(NotFound):
        documents.get(pdf.document_id, ADMIN)
    # Reversible: the row is archived, not gone, and the bytes are still where the
    # version says they are.
    row = db_session.get(Document, pdf.document_id)
    assert row is not None and row.deleted_at is not None
    version = documents.repo.version(row.id, row.versione_corrente)
    assert version is not None
    assert storage.get(version.storage_key)[:5] == b"%PDF-"
    # And the document's own timeline says why it went, the way a delete from the
    # documents surface would.
    kinds = [a.kind for a in ActivityService(db_session).timeline("document", pdf.document_id)]
    assert "deleted" in kinds


def test_a_proforma_consumed_between_the_read_and_the_write_is_a_conflict(
    service: InvoiceService,
    db_session: Session,
    storage: LocalFileStorage,
    customer_id: UUID,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The race `soft_delete`'s `IntegrityError` handler was written for (ORB-57): the
    pre-check reads `confermata`, an emission consumes the proforma while the delete is
    in flight, and `ck_invoices_no_delete_once_consumed` refuses the `UPDATE`. The
    handler could never run: `ActivityService.record` flushes, so the constraint fired
    inside `record`, before the `try`, and the caller got a raw `IntegrityError` on a
    session that still needed a rollback. The refusal has to be the domain `Conflict`
    the handler promises, the session has to come back usable, and nothing of the
    delete may survive, the PDF's archiving included, since that travels in the same
    transaction.

    Emulated on the test's own connection rather than from a second session: the
    `db_session` fixture holds every row inside one outer transaction that no other
    connection can see. The raw `UPDATE` slipped in after the repository's read leaves
    the row in exactly the state a committed emission would, with the service still
    holding the stale `confermata` it read. It is undone by the rollback the handler
    performs, so afterwards the proforma reads as it did before the attempt.
    """
    proforma_id = _confirmed_proforma(service, customer_id)
    (pdf,) = service.produce_artifacts(proforma_id, ADMIN)
    documents = DocumentService(db_session, storage)
    read = service.repo.get

    def read_then_lose_the_race(invoice_id: UUID) -> Invoice | None:
        invoice = read(invoice_id)
        # What `issue` does to a proforma, reduced to the one column the CHECK reads.
        db_session.execute(
            text("UPDATE invoices SET stato = 'consumata' WHERE id = :id"), {"id": invoice_id}
        )
        return invoice

    monkeypatch.setattr(service.repo, "get", read_then_lose_the_race)
    with pytest.raises(Conflict) as caught:
        service.soft_delete(proforma_id, ADMIN)
    assert "emesso nel frattempo" in caught.value.message
    monkeypatch.undo()

    # Usable session, whole rollback: neither the invoice nor its PDF is archived.
    proforma = db_session.get(Invoice, proforma_id)
    assert proforma is not None and proforma.deleted_at is None
    assert service.get(proforma_id, ADMIN).id == proforma_id
    assert documents.get(pdf.document_id, ADMIN).id == pdf.document_id
    listed = documents.list(DocumentListQuery(customer_id=customer_id), ADMIN).items
    assert pdf.document_id in [item.id for item in listed]


# --- the accrual period and the proforma's own date on the PDF (ORB-61, ORB-63) -------


def test_the_pdf_prints_the_accrual_period_when_the_document_has_one(
    service: InvoiceService,
    customer_id: UUID,
    db_session: Session,
    storage: LocalFileStorage,
    extract_pdf_text: Callable[[LocalFileStorage, Session, UUID], str],
) -> None:
    from datetime import date

    draft = service.create(
        InvoiceCreate(
            customer_id=customer_id,
            causale="Consulenza",
            competenza_da=date(2026, 8, 1),
            competenza_a=date(2026, 8, 31),
            righe=[InvoiceLineIn(descrizione="Consulenza", prezzo_unitario=Decimal("1000.00"))],
        ),
        ADMIN,
    )
    invoice_id = service.issue(draft.id, InvoiceIssue(), ADMIN).id
    pdf, _xml = service.produce_artifacts(invoice_id, ADMIN)
    testo = extract_pdf_text(storage, db_session, pdf.document_id)
    assert "Periodo di competenza: dal 01-08-2026 al 31-08-2026" in testo


def test_a_document_without_a_period_prints_no_period_line(
    service: InvoiceService,
    customer_id: UUID,
    db_session: Session,
    storage: LocalFileStorage,
    extract_pdf_text: Callable[[LocalFileStorage, Session, UUID], str],
) -> None:
    invoice_id = _issue(service, customer_id)
    pdf, _xml = service.produce_artifacts(invoice_id, ADMIN)
    assert "Periodo di competenza" not in extract_pdf_text(storage, db_session, pdf.document_id)


def test_the_proforma_pdf_prints_its_own_date_and_its_period_not_the_render_day(
    service: InvoiceService,
    customer_id: UUID,
    db_session: Session,
    storage: LocalFileStorage,
    extract_pdf_text: Callable[[LocalFileStorage, Session, UUID], str],
) -> None:
    """ORB-63: PROV-2026-0002 rendered on 2026-09-09 said 2026-09-09 and would have
    said tomorrow tomorrow. The date printed is the one stored on the row, which the
    sender chose, and a re-render after moving it prints the moved date."""
    from datetime import date

    from pigrocrm.core.invoices.schemas import InvoiceUpdate

    proforma = service.create(
        InvoiceCreate(
            customer_id=customer_id,
            tipo="proforma",
            data_emissione=date(2025, 12, 31),
            competenza_da=date(2025, 12, 1),
            competenza_a=date(2025, 12, 31),
            righe=[InvoiceLineIn(descrizione="Consulenza", prezzo_unitario=Decimal("500.00"))],
        ),
        ADMIN,
    )
    (pdf,) = service.produce_artifacts(proforma.id, ADMIN)
    testo = extract_pdf_text(storage, db_session, pdf.document_id)
    assert "Data: 31-12-2025" in testo
    assert "Periodo di competenza: dal 01-12-2025 al 31-12-2025" in testo

    # A proforma's PDF has no expected hash (it is not a fiscal identity), so the
    # re-render lands as version 2 of the same document; `download` serves the current
    # version, which is what the customer receives, so that is what is read back here.
    service.update(proforma.id, InvoiceUpdate(data_emissione=date(2026, 1, 2)), ADMIN)
    (again,) = service.produce_artifacts(proforma.id, ADMIN)
    assert again.document_id == pdf.document_id
    assert again.version_numero == 2
    data, _, _ = service.download(proforma.id, "pdf", ADMIN)
    testo = subprocess.run(
        ["pdftotext", "-layout", "-", "-"], input=data, capture_output=True, check=True
    ).stdout.decode("utf-8", errors="replace")
    assert "Data: 02-01-2026" in testo
    assert "Data: 31-12-2025" not in testo


# --- ORB-55: the address line follows the customer's country --------------------------


def _one_line(testo: str) -> str:
    """`pdftotext -layout` pads columns with runs of spaces; the assertions below are
    about words and their order, not about the padding."""
    return " ".join(testo.split())


def test_the_pdf_for_a_non_resident_customer_prints_the_address_without_empty_parentheses(
    service: InvoiceService,
    db_session: Session,
    storage: LocalFileStorage,
    extract_pdf_text: Callable[[LocalFileStorage, Session, UUID], str],
) -> None:
    """ORB-55. The template hard-coded `({{cliente.provincia}})`, so a London customer
    read `1 Old Street, EC1V 9HL London () GB` on the document. Outside Italy there is no
    province to print: the postcode as stored, the city, the country, and no parentheses."""
    invoice_id = _issue(service, _non_resident_customer(db_session))
    pdf, _xml = service.produce_artifacts(invoice_id, ADMIN)
    testo = _one_line(extract_pdf_text(storage, db_session, pdf.document_id))
    assert "1 Old Street, EC1V 9HL London GB" in testo
    assert "()" not in testo


def test_the_pdf_for_an_italian_customer_keeps_cap_comune_and_province(
    service: InvoiceService,
    customer_id: UUID,
    db_session: Session,
    storage: LocalFileStorage,
    extract_pdf_text: Callable[[LocalFileStorage, Session, UUID], str],
) -> None:
    """The other direction: an Italian address keeps the shape it always had, province
    in parentheses, so the fix for London did not move Rome."""
    invoice_id = _issue(service, customer_id)
    pdf, _xml = service.produce_artifacts(invoice_id, ADMIN)
    testo = _one_line(extract_pdf_text(storage, db_session, pdf.document_id))
    assert "Corso Italia 5, 00100 Roma (RM) IT" in testo


def test_the_proforma_pdf_for_a_non_resident_customer_prints_the_same_address_line(
    service: InvoiceService,
    db_session: Session,
    storage: LocalFileStorage,
    extract_pdf_text: Callable[[LocalFileStorage, Session, UUID], str],
) -> None:
    """The proforma template carried the same hard-coded parentheses, and a proforma
    never runs the export pre-check, so it is the document most likely to be printed
    for an incomplete record."""
    proforma_id = _confirmed_proforma(service, _non_resident_customer(db_session))
    (pdf,) = service.produce_artifacts(proforma_id, ADMIN)
    testo = _one_line(extract_pdf_text(storage, db_session, pdf.document_id))
    assert "1 Old Street, EC1V 9HL London GB" in testo
    assert "()" not in testo


# --- an invoice's PDF document takes only a PDF (REB-480) --------------------------

# An XML file in the XHTML namespace: the shape that rendered as a page of the app while
# the preview framed whatever the invoice's PDF document held (REB-463).
XHTML = (
    b'<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml">'
    b'<body><form><input name="password"/></form></body></html>'
)
UPLOADED_PDF = b"%PDF-1.7\nfinto\n"


@pytest.mark.parametrize("tipo", ["fattura", "proforma"])
def test_an_invoices_pdf_document_refuses_a_version_that_is_not_a_pdf(
    service: InvoiceService, customer_id: UUID, tipo: str
) -> None:
    """The guard sits in the version core, so the REST upload and every other caller of
    `add_version` meet it. The refusal writes nothing: the document keeps its version
    and the invoice keeps serving the PDF it had. A PDF still goes in."""
    invoice_id = (
        _issue(service, customer_id)
        if tipo == "fattura"
        else _confirmed_proforma(service, customer_id)
    )
    pdf = service.produce_artifacts(invoice_id, ADMIN)[0]

    with pytest.raises(ValidationFailed) as excinfo:
        service.documents.add_version(pdf.document_id, XHTML, "application/xml", ADMIN)
    assert excinfo.value.details["field"] == "content_type"
    assert excinfo.value.details["expected"] == "application/pdf"
    assert f"il PDF di una {tipo}:" in excinfo.value.details["reason"]
    assert service.documents.get(pdf.document_id, ADMIN).versione_corrente == pdf.version_numero
    content, content_type, _ = service.download(invoice_id, "pdf", ADMIN)
    assert content_type == "application/pdf"
    assert hashlib.sha256(content).hexdigest() == pdf.hash_sha256

    added = service.documents.add_version(pdf.document_id, UPLOADED_PDF, "application/pdf", ADMIN)
    assert added.numero == pdf.version_numero + 1
    assert service.download(invoice_id, "pdf", ADMIN)[0] == UPLOADED_PDF


def test_a_discarded_proformas_pdf_document_still_takes_only_a_pdf_once_restored(
    service: InvoiceService, customer_id: UUID
) -> None:
    """A soft-deleted invoice still names its PDF document, and that document can be
    restored on its own from the documents surface, so the rule follows it."""
    proforma_id = _confirmed_proforma(service, customer_id)
    (pdf,) = service.produce_artifacts(proforma_id, ADMIN)
    service.soft_delete(proforma_id, ADMIN)
    service.documents.restore(pdf.document_id, ADMIN)

    with pytest.raises(ValidationFailed):
        service.documents.add_version(pdf.document_id, XHTML, "application/xml", ADMIN)


def test_a_fattura_document_no_invoice_names_keeps_the_common_allowlist(
    service: InvoiceService, customer_id: UUID
) -> None:
    """The rule is the invoice's pointer, not `documents.tipo`: a `fattura` document
    nobody linked yet (an original waiting for `import_issued`) takes what any document
    takes. `_validate_original_pdf` refuses to link one whose current version is not a
    PDF, so it cannot become an invoice's PDF that way."""
    document = service.documents.create(
        DocumentCreate(customer_id=customer_id, tipo="fattura", titolo="Fattura fornitore"),
        ADMIN,
    )
    version = service.documents.add_version(document.id, XHTML, "application/xml", ADMIN)
    assert version.content_type == "application/xml"


def test_a_version_that_is_not_a_pdf_is_never_served_as_the_invoices_pdf(
    service: InvoiceService, customer_id: UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A version written before REB-480, when `add_version` took any allowed type here.
    The download answers it as a missing PDF, the 404 the web already words as «non è
    disponibile», and «Rigenera documenti» (`produce_artifacts`) stores a PDF over it."""
    invoice_id = _issue(service, customer_id)
    pdf = service.produce_artifacts(invoice_id, ADMIN)[0]
    with monkeypatch.context() as patched:
        # The guard lifted for this one call: the write as it could happen before.
        patched.setattr(DocumentService, "_check_invoice_pdf", lambda *_: None)
        service.documents.add_version(pdf.document_id, XHTML, "application/xml", ADMIN)

    with pytest.raises(NotFound):
        service.download(invoice_id, "pdf", ADMIN)

    repaired = service.produce_artifacts(invoice_id, ADMIN)[0]
    assert repaired.version_numero == pdf.version_numero + 2
    content, content_type, _ = service.download(invoice_id, "pdf", ADMIN)
    assert content_type == "application/pdf"
    assert hashlib.sha256(content).hexdigest() == pdf.hash_sha256
