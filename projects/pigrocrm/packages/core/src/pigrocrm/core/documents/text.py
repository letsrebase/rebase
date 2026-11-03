"""Bytes out as text: what an archived document says, readable without a download."""

from uuid import UUID

from pigrocrm.core.documents.context import ENTITY, DocumentContext
from pigrocrm.core.documents.schemas import DocumentTextRead
from pigrocrm.core.drive.reader import DOWNLOAD_MAX_BYTES as TEXT_SOURCE_MAX_BYTES
from pigrocrm.core.errors import Conflict
from pigrocrm.core.text import file_text

# `TEXT_SOURCE_MAX_BYTES` is how large a stored file may be for `extract_text` to parse it
# at all. `DIMENSIONE_MAX` (100 MB) is what the archive accepts -- a zip of photos, a video
# somebody attached to a deal -- and parsing one of those to answer "what is the codice
# destinatario" would spend a hundred megabytes of this process on a file with no text in
# it. It is Drive's own ceiling (`drive/reader.py::DOWNLOAD_MAX_BYTES`, the one definition,
# imported here) because it is the same question about the same kind of file;
# `settings.gmail_attachment_max_bytes` is its environment-overridable twin, and a test
# pins that its default equals it.
_TESTO_TROPPO_GRANDE = (
    "il documento supera {max_bytes} byte: scaricalo invece di leggerlo come testo"
)


class DocumentText:
    def __init__(self, context: DocumentContext) -> None:
        self.ctx = context

    def extract_text(
        self, document_id: UUID, numero: int | None, *, max_bytes: int | None = None
    ) -> DocumentTextRead:
        """The text of an archived document: a PDF, a `.docx`, a `.md`/`.txt`, an XML.

        The point of this method is that a fact only written inside a signed order form
        or a supplier's invoice -- a codice destinatario, an IBAN, the exact wording of
        a clause -- is a fact somebody has to be able to *read* without leaving the CRM,
        downloading the file and reading it by eye. `download` hands over bytes, which is
        the right answer for a browser and no answer at all for an agent.

        `extract_text` and not `read_text`, which is what `DriveReader` calls the same
        operation on a file of the titolare's Drive: that name is one of the bare-name
        bans in `test_mcp_invoice_ban.py`, and those bans are sound only while each name
        is unique in the codebase. A second `read_text` would turn a ban on an operation
        into a ban on a word, and would fail the build with a message about Drive over a
        document sitting in our own storage.

        The same extractor as Drive (`core/text.py`), on purpose: the bytes of a PDF do
        not know which door they came in by, and one parser of somebody else's malformed
        file is one place to fix it. Which means the same discipline holds here --
        nothing raises on a strange file, a type this does not read comes back as the
        empty string *with its mime*, and `troncato` says only whether something was
        cut, never that the file had no text to begin with.

        Two ceilings, like Drive's: the stored file is refused above
        `TEXT_SOURCE_MAX_BYTES`, and the text is cut at `max_bytes`, defaulting to
        `settings.document_text_max_bytes`.

        Reads nothing into the audit log: this is a read of content, like `download`,
        and an `activities` row per read would put a client's document title and the
        fact somebody read it into a timeline that exists to record changes.
        """
        document, version = self.ctx.resolve_version(document_id, numero)
        if version.dimensione > TEXT_SOURCE_MAX_BYTES:
            # Refused, not cut. Half a PDF is not a PDF: truncation is meaningful for
            # text and meaningless for the bytes a parser has to see whole.
            raise Conflict(
                ENTITY,
                _TESTO_TROPPO_GRANDE.format(max_bytes=TEXT_SOURCE_MAX_BYTES),
                document_id=str(document_id),
                numero=version.numero,
                dimensione=version.dimensione,
            )
        limit = self.ctx.settings.document_text_max_bytes if max_bytes is None else max_bytes
        estratto = file_text(
            self.ctx.storage.get(version.storage_key), mime=version.content_type, max_bytes=limit
        )
        return DocumentTextRead(
            document_id=document.id,
            numero=version.numero,
            titolo=document.titolo,
            testo=estratto.testo,
            mime=estratto.mime,
            troncato=estratto.troncato,
            provenienza=estratto.provenienza,
        )
