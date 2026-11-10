"""The original PDF of an imported fattura: where it is, and whether it may be the invoice's.

Everything here is read-only: a refusal has to come before the first row of the import is
flushed, so nothing is written (the one lock taken, on an existing document's row, is
documented on `_validate_document`).
"""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select

from pigrocrm.core.actor import Actor
from pigrocrm.core.drive.reader import ALREADY_AUTHORIZED, DriveReader, drive_reader_for
from pigrocrm.core.errors import Conflict, NotFound, ValidationFailed
from pigrocrm.core.invoices.constants import ENTITY
from pigrocrm.core.invoices.context import InvoiceContext
from pigrocrm.core.invoices.models import Invoice
from pigrocrm.core.invoices.schemas import PdfSource
from pigrocrm.core.text import PDF_MIME

# The original of an invoice has to be a `PDF_MIME` and nothing else is accepted from Drive:
# the point of §3.5 is the document the customer received, and a `.docx` or a scan-shaped
# `image/jpeg` is not that document even when it looks like it.
# The name a Drive refusal uses when the grant is missing a scope ("<feature> non è
# disponibile: manca l'autorizzazione ..."), so it reads as a feature that is off rather
# than as a broken credential.
DRIVE_FEATURE = "import della fattura"


@dataclass(frozen=True)
class FetchedPdf:
    """The original PDF already read from Drive, waiting for a row to belong to.

    `import_issued` reads Drive among its *pure* checks -- a network read is not a
    database write, and a file outside the roots or a `.txt` in place of the invoice has
    to be refused before the counter is locked. But the `documents` row it becomes cannot
    be written until the invoice exists, so what crosses that boundary is this: the
    bytes, and the id they came from for the provenance record.
    """

    content: bytes
    drive_file_id: str
    mime: str


class OriginalPdfResolver:
    def __init__(self, ctx: InvoiceContext) -> None:
        self.ctx = ctx

    def resolve(self, customer_id: UUID, source: PdfSource, actor: Actor) -> UUID | FetchedPdf:
        """Where the original PDF is, resolved to something the write phase can use, and
        every refusal that belongs to the caller's input spent here. Writes nothing.

        Two answers, because there are two provenances. A `documents` row is already a
        row: what comes back is its id, and `import_issued` assigns it in one line. A
        Drive file is not a row yet and cannot become one before the invoice exists (the
        title names the invoice's own number), so what comes back is `FetchedPdf`.

        Reading Drive above the counter lock is deliberate: it is a network read, not a
        database write, and the two things that can be wrong with a `drive_file_id` (a
        file outside the configured roots, a file that is not a PDF) are facts about the
        caller's input. Doing it after the lock would hold a row lock for the duration of
        an HTTP call to Google, and would refuse *after* an `Invoice` had been flushed.

        `NotFound("drive_file")` becomes `Conflict`, not `NotFound`: nothing about the
        *invoice* is missing, and `DriveReader` answers in those same words for a file
        that does not exist, one in an unconfigured corner of the owner's Drive and
        one in a stranger's Drive -- a distinction this method must not redraw.

        **The reader's other refusals pass through untouched, deliberately.** A file over
        the download ceiling, a folder id where a file id was meant, a native Google file
        with no bytes and a Google failure mid-read each already arrive as a `Conflict`
        under the entity `drive_file`, carrying a sentence written for the person who
        typed the id. Re-wrapping them under `invoice` would claim the problem is with the
        fattura when it is with the file.
        """
        if source.drive_file_id is not None:
            return self._read_from_drive(source.drive_file_id, actor)
        if source.document_id is None:  # pragma: no cover - the schema refuses this first
            raise ValidationFailed(
                ENTITY,
                "pdf_source",
                "indica da dove prendere il PDF originale",
                expected="esattamente uno fra document_id e drive_file_id",
            )
        return self._validate_document(customer_id, source.document_id)

    def _drive_reader(self, actor: Actor) -> DriveReader:
        """This actor's reader on their own connected Drive, or the injected one.

        `drive_reader_for` is the gate as well as the constructor: it refuses before any
        HTTP call when Drive is not connected, when the grant was revoked or when
        `drive.readonly` was never granted, and it takes the roots from the account row
        so a reader cannot be pointed at a folder the owner did not configure.
        """
        ctx = self.ctx
        if ctx.drive_reader_factory is not None:
            return ctx.drive_reader_factory(actor)
        return drive_reader_for(
            ctx.session,
            actor,
            ctx.settings,
            feature=DRIVE_FEATURE,
            # The agent ban for this read is `import_issued_invoice`, and `import_issued`
            # has already applied it under that name before this is reached. Spelled
            # rather than left to a default, so the exemption is visible where it is
            # taken (see `ALREADY_AUTHORIZED`).
            action=ALREADY_AUTHORIZED,
        )

    def _read_from_drive(self, drive_file_id: str, actor: Actor) -> FetchedPdf:
        """The bytes of the original, with every refusal that is a fact about the file.

        Empty bytes are checked *here* and not left to `DocumentUploads.check_upload`:
        that check runs inside `import_bytes`, after the `Invoice` has been flushed,
        whereas "the file on Drive has no bytes" belongs among the pure checks with the
        mime. A zero-byte PDF is not exotic (a sync that died half way) and Drive serves
        it without complaint.

        **The mime is decided on the metadata, before a byte is downloaded**, as
        `import_drive_file` does: a 20 MB spreadsheet named by mistake must not be pulled
        through this process only to be refused on a fact one `files.get` already knew.
        Not re-checked after the download, because it would be the same value.

        A *folder* is deliberately not refused here and left to `read_bytes`: the reader
        has the better sentence for it and refuses it before any download too.
        """
        reader = self._drive_reader(actor)
        try:
            entry = reader.describe(drive_file_id)
            if not entry.folder and entry.mime != PDF_MIME:
                raise ValidationFailed(
                    ENTITY,
                    "pdf_source.drive_file_id",
                    f"il file su Drive non è un PDF ma un {entry.mime}: l'originale di "
                    "una fattura è il PDF che il cliente ha ricevuto",
                    expected=PDF_MIME,
                )
            content, mime = reader.read_bytes(drive_file_id)
        except NotFound as exc:
            raise Conflict(
                ENTITY,
                "il file non è leggibile dalle cartelle Drive configurate: controlla "
                "l'id, oppure aggiungi la cartella che lo contiene in Impostazioni → Drive",
                drive_file_id=drive_file_id,
            ) from exc
        if not content:
            raise ValidationFailed(
                ENTITY,
                "pdf_source.drive_file_id",
                "il file su Drive è vuoto",
                expected="almeno un byte",
            )
        return FetchedPdf(content=content, drive_file_id=drive_file_id, mime=mime)

    def _validate_document(self, customer_id: UUID, document_id: UUID) -> UUID:
        """Check that this `documents` row may become the invoice's PDF, and return its
        id. Writes nothing; it only locks the row it reads.

        The PDF the customer actually received, never rendered: a PDF produced today
        with today's layout would not be that document (§3.5).

        Takes `customer_id` rather than an `Invoice` because four of the five refusals
        below are facts about the caller's input, so they belong among `import_issued`'s
        pure checks, above the counter lock and before a single row is flushed.

        The document's row is locked before it is read (REB-480), and stays locked until
        the import commits the link. A version upload takes the same lock before it checks
        whether an invoice names the document, so a non-PDF version cannot land between
        this read and that commit. It is the one row lock above `lock_counter`, and the
        order cannot deadlock: every other writer of an unlinked document's row (an
        upload, an edit) never takes the counter, and another import of the same document
        takes this lock first as well.
        """
        documents = self.ctx.documents
        document = documents.repo.lock(document_id)
        if document is None or document.deleted_at is not None:
            raise NotFound("document", document_id)
        if document.type != "invoice":
            raise ValidationFailed(
                ENTITY,
                "pdf_source",
                "il documento non e' di tipo fattura",
                expected="un documento con tipo 'fattura'",
            )
        if document.customer_id != customer_id:
            raise ValidationFailed(
                ENTITY,
                "pdf_source",
                "il documento appartiene a un altro cliente",
                expected=f"un documento del cliente {customer_id}",
            )
        if not document.current_version:
            raise ValidationFailed(
                ENTITY,
                "pdf_source",
                "il documento non ha ancora un file caricato",
                expected="un documento con almeno una versione PDF",
            )
        current = documents.repo.version(document.id, document.current_version)
        if current is None or current.content_type != PDF_MIME:
            raise ValidationFailed(
                ENTITY,
                "pdf_source",
                "la versione corrente del documento non e' un PDF",
                expected=PDF_MIME,
            )
        taken = (
            self.ctx.session.execute(
                select(Invoice.id).where(Invoice.pdf_document_id == document_id)
            )
            .scalars()
            .first()
        )
        if taken is not None:
            raise Conflict(
                ENTITY,
                "questo PDF e' gia' collegato a un'altra fattura",
                document_id=str(document_id),
            )
        return document.id
