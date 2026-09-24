from datetime import timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy.orm import Session

from pigrocrm.core.customers.models import Customer
from pigrocrm.core.documents.models import Document, DocumentVersion
from pigrocrm.core.documents.schemas import ALLOWED_CONTENT_TYPES
from pigrocrm.core.errors import NotFound, ValidationFailed
from pigrocrm.core.gmail.attach import describe_attachments, resolve_attachments
from pigrocrm.core.storage.local import LocalFileStorage

MB = 1024 * 1024
PDF = b"%PDF-1.7\nfinto\n"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


class RefusingStorage:
    """A `DocumentStorage` whose every read is a test failure, and which remembers the
    keys it was asked for.

    The whole point of task B2-4 is *where* the size check happens: an attachment total
    refused only after the bytes have been pulled out of storage has already built the
    payload the limit exists to prevent. A storage that cannot be read makes that
    ordering observable -- if the check ever moves after the reads, these tests stop
    raising `ValidationFailed` and start raising something else.
    """

    def __init__(self) -> None:
        self.reads: list[str] = []

    def put(self, key: str, data: bytes, content_type: str) -> None:  # pragma: no cover
        raise AssertionError("resolve_attachments must never write")

    def get(self, key: str) -> bytes:
        self.reads.append(key)
        raise AssertionError("the size check must run before any byte is fetched")

    def delete(self, key: str) -> None:  # pragma: no cover
        raise AssertionError("resolve_attachments must never delete")

    def signed_url(self, key: str, ttl: timedelta) -> str | None:  # pragma: no cover
        return None


@pytest.fixture
def storage(tmp_path: Path) -> LocalFileStorage:
    return LocalFileStorage(tmp_path)


@pytest.fixture
def documento(db_session: Session) -> Document:
    customer = Customer(ragione_sociale=f"ACME {uuid4()}")
    db_session.add(customer)
    db_session.flush()
    document = Document(
        customer_id=customer.id,
        tipo="offerta",
        # Accented, punctuated and carrying a slash on purpose: the title is user input
        # and it becomes an attachment filename.
        titolo="Offerta Città / Q1 2026",
    )
    db_session.add(document)
    db_session.flush()
    return document


def make_version(
    session: Session,
    document: Document,
    *,
    numero: int,
    dimensione: int,
    content_type: str = "application/pdf",
) -> DocumentVersion:
    version = DocumentVersion(
        document_id=document.id,
        numero=numero,
        storage_key=f"acme/offerta-v{numero}-{uuid4().hex}.bin",
        content_type=content_type,
        dimensione=dimensione,
        hash_sha256="0" * 64,
    )
    session.add(version)
    session.flush()
    return version


@pytest.fixture
def offerta_version(db_session: Session, documento: Document) -> DocumentVersion:
    return make_version(db_session, documento, numero=1, dimensione=len(PDF))


def test_it_resolves_a_document_version_into_bytes_a_name_and_a_type(
    db_session: Session, storage: LocalFileStorage, offerta_version: DocumentVersion
) -> None:
    storage.put(offerta_version.storage_key, PDF, offerta_version.content_type)
    attachments = resolve_attachments(db_session, storage, [offerta_version.id], max_bytes=20 * MB)
    assert len(attachments) == 1
    assert attachments[0].content == PDF
    assert attachments[0].mime == "application/pdf"


def test_the_filename_is_the_slugified_title_with_the_extension_of_its_real_type(
    db_session: Session, storage: LocalFileStorage, documento: Document
) -> None:
    """A `.docx` named `.pdf` is a file the recipient's machine refuses to open, and a
    title reaching a MIME header raw is header injection with extra steps -- so the name
    is the slug, the version number and the extension the *recorded* type maps to, the
    same three pieces `DocumentService.download` already assembles."""
    version = make_version(db_session, documento, numero=4, dimensione=4, content_type=DOCX)
    storage.put(version.storage_key, b"PK\x03\x04", DOCX)

    attachments = resolve_attachments(db_session, storage, [version.id], max_bytes=20 * MB)

    assert attachments[0].filename == "offerta-citta-q1-2026-v4.docx"
    assert attachments[0].mime == DOCX


def test_the_mime_type_comes_from_the_allowlist_never_from_a_caller(
    db_session: Session, storage: LocalFileStorage, offerta_version: DocumentVersion
) -> None:
    """ALLOWED_CONTENT_TYPES in documents/schemas.py is the authority. Echoing a
    caller-supplied type is how a script gets mailed as a PDF."""
    storage.put(offerta_version.storage_key, PDF, offerta_version.content_type)
    attachments = resolve_attachments(db_session, storage, [offerta_version.id], max_bytes=20 * MB)
    assert attachments[0].mime in ALLOWED_CONTENT_TYPES


def test_a_recorded_type_outside_the_allowlist_is_refused_rather_than_guessed(
    db_session: Session, storage: LocalFileStorage, documento: Document
) -> None:
    """The allowlist is the authority in both directions: a row carrying a type it does
    not contain has no extension we can honestly give the file, and guessing one is how
    an executable arrives looking like a document."""
    version = make_version(
        db_session, documento, numero=7, dimensione=3, content_type="application/x-sh"
    )
    storage.put(version.storage_key, b"#!/", "application/pdf")

    with pytest.raises(ValidationFailed) as caught:
        resolve_attachments(db_session, storage, [version.id], max_bytes=20 * MB)
    assert caught.value.details["field"] == "attachment_version_ids"


def test_twenty_one_megabytes_are_refused_before_a_single_byte_is_fetched(
    db_session: Session, documento: Document
) -> None:
    """Spec 13, criterion 11. Gmail refuses above 25 MB and its refusal mid-upload is an
    incomprehensible message, so the limit is ours and the check is early.

    `RefusingStorage` is what makes "early" a fact rather than a claim: the version's
    bytes are unreachable, so a check that ran after the fetch could not produce this
    error at all."""
    version = make_version(db_session, documento, numero=1, dimensione=21 * MB)
    storage = RefusingStorage()

    with pytest.raises(ValidationFailed) as caught:
        resolve_attachments(db_session, storage, [version.id], max_bytes=20 * MB)

    assert storage.reads == []
    message = str(caught.value)
    # The error says how much it weighs and what the limit is. "Too big" is not an
    # actionable message.
    assert "21" in message and "20" in message
    assert caught.value.details["field"] == "attachment_version_ids"


def test_the_cap_is_on_the_total_not_on_each_file(db_session: Session, documento: Document) -> None:
    first = make_version(db_session, documento, numero=1, dimensione=12 * MB)
    second = make_version(db_session, documento, numero=2, dimensione=12 * MB)
    storage = RefusingStorage()

    with pytest.raises(ValidationFailed):
        resolve_attachments(db_session, storage, [first.id, second.id], max_bytes=20 * MB)
    assert storage.reads == []


def test_a_total_exactly_on_the_limit_is_accepted(
    db_session: Session, storage: LocalFileStorage, offerta_version: DocumentVersion
) -> None:
    """The limit is a maximum, not an exclusive bound: a message weighing exactly what
    the setting allows must go out, or the number in the error is a lie by one byte."""
    storage.put(offerta_version.storage_key, PDF, offerta_version.content_type)

    assert (
        resolve_attachments(db_session, storage, [offerta_version.id], max_bytes=len(PDF))[
            0
        ].content
        == PDF
    )

    with pytest.raises(ValidationFailed):
        resolve_attachments(db_session, storage, [offerta_version.id], max_bytes=len(PDF) - 1)


def test_the_bytes_are_weighed_again_when_the_recorded_size_disagrees_with_storage(
    db_session: Session, storage: LocalFileStorage, documento: Document
) -> None:
    """`dimensione` is metadata: no constraint ties it to what the backend actually
    holds, and a stale or wrong row would otherwise let an oversized payload straight
    through the cap it is the only guard for. The early check is the cheap one; this is
    the true one."""
    version = make_version(db_session, documento, numero=1, dimensione=1)
    storage.put(version.storage_key, b"x" * 200, version.content_type)

    with pytest.raises(ValidationFailed) as caught:
        resolve_attachments(db_session, storage, [version.id], max_bytes=100)
    assert caught.value.details["field"] == "attachment_version_ids"


def test_an_unknown_version_id_is_a_not_found(
    db_session: Session, storage: LocalFileStorage
) -> None:
    with pytest.raises(NotFound):
        resolve_attachments(db_session, storage, [uuid4()], max_bytes=20 * MB)


def test_a_version_whose_bytes_are_missing_from_storage_is_a_not_found(
    db_session: Session, storage: LocalFileStorage, offerta_version: DocumentVersion
) -> None:
    """A dangling storage key must not become an email with an empty attachment: the
    recipient would receive a 0-byte PDF and nobody would know why."""
    with pytest.raises(NotFound):
        resolve_attachments(db_session, storage, [offerta_version.id], max_bytes=20 * MB)


def test_an_empty_list_resolves_to_no_attachments_and_touches_nothing(db_session: Session) -> None:
    """The overwhelmingly common case: a mail with no attachments must not cost a query
    or a fetch, and must not be an error."""
    storage = RefusingStorage()
    assert resolve_attachments(db_session, storage, [], max_bytes=20 * MB) == ()
    assert storage.reads == []


def test_no_arbitrary_upload_path_exists() -> None:
    """Spec 6.4: attachments come only from document_version_id. A free upload in the
    composer would be a second route for bytes to enter the system, with a second
    authorisation to write."""
    import inspect

    from pigrocrm.core.gmail import attach

    signature = inspect.signature(attach.resolve_attachments)
    assert "version_ids" in signature.parameters
    assert not any(
        name in signature.parameters for name in ("content", "raw", "upload", "file", "bytes")
    )


def test_the_oversize_error_never_carries_the_title_or_the_storage_key(
    db_session: Session, documento: Document
) -> None:
    """A document title is a client's name more often than not, and a storage key is a
    path into the backend. Neither belongs in an error the composer renders, a log line
    or a test failure dump: the caller already knows which ids it passed."""
    version = make_version(db_session, documento, numero=1, dimensione=21 * MB)

    with pytest.raises(ValidationFailed) as caught:
        resolve_attachments(db_session, RefusingStorage(), [version.id], max_bytes=20 * MB)

    rendered = f"{caught.value!r} {caught.value.details}"
    assert "Citt" not in rendered
    assert version.storage_key not in rendered


def test_the_version_ids_are_uuids_and_the_order_is_the_caller_s(
    db_session: Session, storage: LocalFileStorage, documento: Document
) -> None:
    """Attachment order is visible to the recipient; it is the order the user chose in
    the composer, not whatever the database felt like returning."""
    first = make_version(db_session, documento, numero=1, dimensione=1)
    second = make_version(db_session, documento, numero=2, dimensione=1)
    storage.put(first.storage_key, b"1", first.content_type)
    storage.put(second.storage_key, b"2", second.content_type)

    ids: list[UUID] = [second.id, first.id]
    attachments = resolve_attachments(db_session, storage, ids, max_bytes=20 * MB)
    assert [a.content for a in attachments] == [b"2", b"1"]


# --- naming without reading (REB-415) -----------------------------------------------


def test_a_draft_names_each_attachment_exactly_as_the_send_will_and_reads_no_byte(
    db_session: Session, storage: LocalFileStorage, documento: Document
) -> None:
    """The Email tab shows a draft's attachments from `describe_attachments`, and the
    person presses Invia on what they read there. A name that differed from the one
    `resolve_attachments` puts in the MIME part would be a review of a file that is not
    the one leaving. Naming reads no byte, which is structural: it takes no storage, so a
    list of fifty drafts cannot pull fifty PDFs out of the backend to print their names."""
    pdf = make_version(db_session, documento, numero=2, dimensione=len(PDF))
    docx = make_version(db_session, documento, numero=3, dimensione=4, content_type=DOCX)
    storage.put(pdf.storage_key, PDF, pdf.content_type)
    storage.put(docx.storage_key, b"PK\x03\x04", DOCX)

    named = describe_attachments(db_session, [pdf.id, docx.id])
    sent = resolve_attachments(db_session, storage, [pdf.id, docx.id], max_bytes=20 * MB)

    assert [named[pdf.id].filename, named[docx.id].filename] == [a.filename for a in sent]
    assert named[pdf.id].filename == "offerta-citta-q1-2026-v2.pdf"
    assert named[pdf.id].dimensione == len(PDF)


def test_an_id_that_no_longer_resolves_is_named_as_missing_rather_than_dropped(
    db_session: Session,
) -> None:
    """A draft that silently lost an attachment on screen would be sent carrying one the
    person never saw, or refused for one they cannot find. So every id asked for gets an
    answer, and the missing one says it has no file."""
    missing = uuid4()

    named = describe_attachments(db_session, [missing])

    assert named[missing].filename is None
    assert named[missing].dimensione is None


def test_a_type_the_send_would_refuse_has_no_name_either(
    db_session: Session, documento: Document
) -> None:
    version = make_version(
        db_session, documento, numero=1, dimensione=4, content_type="application/x-msdownload"
    )

    assert describe_attachments(db_session, [version.id])[version.id].filename is None


def test_naming_nothing_asks_the_database_nothing(db_session: Session) -> None:
    assert describe_attachments(db_session, []) == {}
