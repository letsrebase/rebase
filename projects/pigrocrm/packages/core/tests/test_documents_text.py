"""`DocumentService.extract_text`: the data inside an archived file, made readable.

The case this exists for is concrete. A codice destinatario is printed on a signed
order form and written nowhere else in the CRM; before this, recovering it meant
downloading the PDF and reading it by eye, which an agent cannot do and a person should
not have to. So what these tests hold to is not "the extractor works" -- `test_text.py`
owns that -- but the three properties a *caller* depends on: the right version is read,
a file that cannot be read says so instead of pretending to be empty, and the answer
always carries the sentence that says whose words those are.
"""

from pathlib import Path

import pytest
from sqlalchemy.orm import Session
from test_text import minimal_pdf, minimal_pdf_pages

from pigrocrm.core.actor import Actor
from pigrocrm.core.config import Settings
from pigrocrm.core.customers.models import Customer
from pigrocrm.core.documents.models import DocumentVersion
from pigrocrm.core.documents.schemas import DocumentCreate, DocumentRead
from pigrocrm.core.documents.service import DocumentService
from pigrocrm.core.documents.text import TEXT_SOURCE_MAX_BYTES
from pigrocrm.core.errors import Conflict, NotFound
from pigrocrm.core.storage import LocalFileStorage
from pigrocrm.core.text import PROVENANCE, TEXT_TRUNCATION_MARKER

ADMIN = Actor(id=None, type="system", role="admin")


@pytest.fixture
def service(db_session: Session, tmp_path: Path) -> DocumentService:
    return DocumentService(
        db_session,
        LocalFileStorage(tmp_path),
        Settings(_env_file=None),  # type: ignore[call-arg]
    )


@pytest.fixture
def customer(db_session: Session) -> Customer:
    row = Customer(legal_name="ACME S.r.l.")
    db_session.add(row)
    db_session.flush()
    return row


def _document(service: DocumentService, customer: Customer) -> DocumentRead:
    return service.create(
        DocumentCreate(customer_id=customer.id, type="document", title="Modulo d'ordine"), ADMIN
    )


def test_reads_the_text_of_the_current_version(
    service: DocumentService, customer: Customer
) -> None:
    document = _document(service, customer)
    service.add_version(
        document.id, minimal_pdf(["Codice destinatario: ABCDEFG"]), "application/pdf", ADMIN
    )

    read = service.extract_text(document.id, None, ADMIN)

    assert "ABCDEFG" in read.text
    assert read.number == 1
    assert read.mime == "application/pdf"
    assert read.truncated is False


def test_without_to_number_it_is_the_latest_version_and_not_the_first(
    service: DocumentService, customer: Customer
) -> None:
    """The default is `current_version`, which is what an agent holding only a
    document id means by "the document"."""
    document = _document(service, customer)
    service.add_version(document.id, minimal_pdf(["prima stesura"]), "application/pdf", ADMIN)
    service.add_version(document.id, minimal_pdf(["seconda stesura"]), "application/pdf", ADMIN)

    read = service.extract_text(document.id, None, ADMIN)

    assert read.number == 2
    assert "seconda" in read.text


def test_an_explicit_number_reads_that_version(
    service: DocumentService, customer: Customer
) -> None:
    document = _document(service, customer)
    service.add_version(document.id, minimal_pdf(["prima stesura"]), "application/pdf", ADMIN)
    service.add_version(document.id, minimal_pdf(["seconda stesura"]), "application/pdf", ADMIN)

    read = service.extract_text(document.id, 1, ADMIN)

    assert read.number == 1
    assert "before" in read.text


def test_a_version_nobody_uploaded_is_not_found(
    service: DocumentService, customer: Customer
) -> None:
    document = _document(service, customer)
    service.add_version(document.id, minimal_pdf(["unica"]), "application/pdf", ADMIN)

    with pytest.raises(NotFound):
        service.extract_text(document.id, 7, ADMIN)


def test_a_document_without_versions_is_not_found(
    service: DocumentService, customer: Customer
) -> None:
    """`current_version` is 0 on a document nobody has uploaded to, and the honest
    answer is the same as for a version number that does not exist: there is no file."""
    document = _document(service, customer)

    with pytest.raises(NotFound):
        service.extract_text(document.id, None, ADMIN)


def test_an_unknown_document_is_not_found(service: DocumentService) -> None:
    from uuid import uuid4

    with pytest.raises(NotFound):
        service.extract_text(uuid4(), None, ADMIN)


def test_the_xml_of_an_invoice_is_read_as_text(
    service: DocumentService, customer: Customer
) -> None:
    """The FatturaPA file holds, in plain characters, the fields the PDF only prints."""
    document = _document(service, customer)
    xml = (
        b"<FatturaElettronica><CodiceDestinatario>ABCDEFG</CodiceDestinatario></FatturaElettronica>"
    )
    service.add_version(document.id, xml, "application/xml", ADMIN)

    read = service.extract_text(document.id, None, ADMIN)

    assert "<CodiceDestinatario>ABCDEFG</CodiceDestinatario>" in read.text


def test_a_type_with_no_text_answers_empty_with_its_mime(
    service: DocumentService, customer: Customer
) -> None:
    """Not an exception, and not a bare empty string either: the mime is how a caller
    says *which* file it could not read."""
    document = _document(service, customer)
    service.add_version(document.id, b"\x89PNG\r\n\x1a\nfinto", "image/png", ADMIN)

    read = service.extract_text(document.id, None, ADMIN)

    assert read.text == ""
    assert read.mime == "image/png"
    # Nothing was cut -- there was nothing to cut. See `FileText.truncated`.
    assert read.truncated is False


def test_a_scan_without_a_text_layer_is_not_reported_as_truncated(
    service: DocumentService, customer: Customer
) -> None:
    document = _document(service, customer)
    service.add_version(document.id, minimal_pdf([]), "application/pdf", ADMIN)

    read = service.extract_text(document.id, None, ADMIN)

    assert read.text == ""
    assert read.truncated is False


def test_text_longer_than_the_ceiling_is_cut_and_says_so(
    service: DocumentService, customer: Customer
) -> None:
    document = _document(service, customer)
    service.add_version(document.id, b"a" * 500, "text/plain", ADMIN)

    read = service.extract_text(document.id, None, ADMIN, max_bytes=100)

    assert read.truncated is True
    assert read.text.endswith(TEXT_TRUNCATION_MARKER)


def test_the_ceiling_comes_from_the_settings_when_the_caller_gives_none(
    db_session: Session, tmp_path: Path, customer: Customer
) -> None:
    service = DocumentService(
        db_session,
        LocalFileStorage(tmp_path),
        Settings(_env_file=None, document_text_max_bytes=10),  # type: ignore[call-arg]
    )
    document = _document(service, customer)
    service.add_version(document.id, b"a" * 500, "text/plain", ADMIN)

    assert service.extract_text(document.id, None, ADMIN).truncated is True


def test_a_file_too_large_to_parse_is_refused_rather_than_cut(
    service: DocumentService, customer: Customer, db_session: Session
) -> None:
    """Half a PDF is not a PDF: the bytes ceiling refuses, where the text ceiling cuts.

    The size is written onto the stored row rather than uploaded, because uploading
    twenty megabytes to assert a comparison would be twenty megabytes spent on
    arithmetic. What is under test is the guard, and the guard reads `size`.
    """
    document = _document(service, customer)
    service.add_version(document.id, minimal_pdf(["breve"]), "application/pdf", ADMIN)
    version = db_session.query(DocumentVersion).filter_by(document_id=document.id).one()
    version.size = TEXT_SOURCE_MAX_BYTES + 1
    db_session.flush()

    with pytest.raises(Conflict) as excinfo:
        service.extract_text(document.id, None, ADMIN)

    assert str(TEXT_SOURCE_MAX_BYTES) in str(excinfo.value)


def test_a_pdf_beyond_the_page_ceiling_reports_that_it_stopped(
    service: DocumentService, customer: Customer
) -> None:
    """`truncated` covers the page ceiling too, which leaves nothing in the text to
    notice -- see `_extracted`. Six hundred pages, one word each: far under any byte
    ceiling, and still short of the document."""
    document = _document(service, customer)
    service.add_version(
        document.id, minimal_pdf_pages([[f"p{n}"] for n in range(600)]), "application/pdf", ADMIN
    )

    read = service.extract_text(document.id, None, ADMIN)

    assert read.truncated is True


def test_every_answer_carries_its_provenance(service: DocumentService, customer: Customer) -> None:
    """The one field that is not about the file: whoever reads `text` is reading words
    written outside this system, and has to be told so in the same payload."""
    document = _document(service, customer)
    service.add_version(document.id, minimal_pdf(["qualsiasi"]), "application/pdf", ADMIN)

    read = service.extract_text(document.id, None, ADMIN)

    assert read.provenance == PROVENANCE
    assert read.title == "Modulo d'ordine"


def test_the_gmail_attachment_ceiling_defaults_to_the_text_source_ceiling() -> None:
    """One ceiling for "a file this size is not worth parsing", defined once in
    `drive/reader.py` and read by documents; the setting is its environment-overridable
    twin and must start from the same number."""
    from pigrocrm.core.config import Settings
    from pigrocrm.core.drive.reader import DOWNLOAD_MAX_BYTES

    assert Settings(jwt_secret="x" * 32).gmail_attachment_max_bytes == DOWNLOAD_MAX_BYTES
    assert TEXT_SOURCE_MAX_BYTES == DOWNLOAD_MAX_BYTES
