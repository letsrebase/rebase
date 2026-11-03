"""Bytes in: versions of a document and bytes that arrived from somewhere else."""

import hashlib
from typing import Any
from uuid import UUID

from sqlalchemy.exc import IntegrityError

from pigrocrm.core.actor import Actor
from pigrocrm.core.documents.context import ENTITY, DocumentContext
from pigrocrm.core.documents.models import Document, DocumentVersion
from pigrocrm.core.documents.naming import slugify_folder
from pigrocrm.core.documents.schemas import (
    ALLOWED_CONTENT_TYPES,
    SIZE_MAX,
    DocumentCreate,
    DocumentRead,
    DocumentType,
    DocumentVersionRead,
)
from pigrocrm.core.errors import Conflict, ValidationFailed

# What a person reads for the stored `invoices.type` value.
_INVOICE_TYPE_LABELS = {"invoice": "fattura", "proforma": "proforma"}

# The leading bytes a file of each sniffable type starts with. A declared type is a
# claim by the uploader; these are what the bytes say. Plain text, Markdown and XML have
# no signature and are not sniffed. `.docx` and `.xlsx` are ZIP containers. A PDF may
# carry junk before its header, which the format tolerates within the first KiB.
# The action `add_version` refuses a non-writer under; the upload route checks it before
# reading the body, so it names it from here.
ADD_VERSION_ACTION = "add_document_version"
_PDF_HEADER_WINDOW = 1024
_SIGNATURES: dict[str, bytes] = {
    "application/pdf": b"%PDF-",
    "image/png": b"\x89PNG\r\n\x1a\n",
    "image/jpeg": b"\xff\xd8\xff",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": b"PK\x03\x04",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": b"PK\x03\x04",
}


def _matches_declared_type(data: bytes, content_type: str) -> bool:
    signature = _SIGNATURES.get(content_type)
    if signature is None:
        return True
    if content_type == "application/pdf":
        return signature in data[:_PDF_HEADER_WINDOW]
    return data.startswith(signature)


def _version_conflict(document: Document) -> Conflict:
    return Conflict(
        "document_version",
        "conflitto di concorrenza sul numero di versione, riprova",
        document_id=str(document.id),
    )


class DocumentUploads:
    def __init__(self, context: DocumentContext) -> None:
        self.ctx = context

    def next_number(self, document: Document) -> int:
        """The candidate version number for a new upload on `document`.

        Split out to a single, tiny method so `add_version`'s own ordering guarantee
        (below) has exactly one thing to be provably correct about, and so a test can
        force a collision deterministically without needing real threads against a
        single savepoint-backed test session.
        """
        return document.current_version + 1

    def add_version(
        self,
        document_id: UUID,
        data: bytes,
        content_type: str,
        actor: Actor,
        *,
        markdown_source: str | None = None,
        template_id: UUID | None = None,
        variables: dict[str, Any] | None = None,
        storage_prefix: str | None = None,
        commit: bool = True,
    ) -> DocumentVersionRead:
        """Every change makes a version; nothing is ever overwritten (spec 4.2).

        Ordering, deliberately: the `DocumentVersion` row is flushed -- which sends
        the `INSERT` and lets Postgres enforce `uq_document_versions_document_number`
        -- *before* a single byte reaches `storage.put`. Two callers racing to add a
        version to the same document both compute their candidate `number` from
        `document.current_version` before either has committed; under Postgres, the
        loser's `INSERT` either blocks until the winner's transaction resolves (real
        concurrency) or raises `IntegrityError` immediately against an
        already-committed row (the two-callers-in-one-process shape this project's
        own tests reproduce, see `next_number`). Either way, the loser's exception is
        caught and turned into `Conflict` *before* it has called `storage.put` at
        all -- so a losing racer can never overwrite the winning racer's bytes at the
        storage key both computed from the same `number`, and a failed flush never
        burns a version number: nothing was committed, so the same `number` is offered
        again on the next attempt.

        The reverse order (bytes first, row second) was rejected: on the race path it
        lets the loser's `storage.put` physically overwrite the winner's already-put
        bytes before the loser's own insert is rejected by the unique constraint --
        corrupting a version that a reader may already believe is final. Writing the
        row first means storage is only ever touched once the number is confirmed
        reserved (if only within this open transaction).

        The remaining failure mode -- `storage.put` itself raising after a successful
        flush -- is handled by rolling back before propagating: without that, the
        flushed `INSERT` would still be pending in the session, and an unrelated later
        commit on this same session could persist a version row pointing at bytes
        that were never written. Rolling back trades that for the harmless outcome:
        nothing committed, the number is not burned, the session stays usable.
        """
        actor.require_write(ADD_VERSION_ACTION)
        document = self.ctx.require(document_id)
        version = self.add_version_row(
            document,
            data,
            content_type,
            actor,
            markdown_source=markdown_source,
            template_id=template_id,
            variables=variables,
            storage_prefix=storage_prefix,
        )
        if commit:
            self.ctx.session.commit()
        return DocumentVersionRead.model_validate(version)

    def check_upload(self, data: bytes, content_type: str) -> None:
        """The facts about a candidate file that are true or false before anything is
        written: an allowed type, at least one byte, not over the ceiling, and bytes
        that begin the way the declared type says they do.

        Split out of `add_version_row` so `import_bytes` can run it *before* creating
        the `documents` row. Cheap enough to run twice, and it is run twice on that
        path deliberately: the version core keeps its own check so no future caller can
        reach `storage.put` without it.
        """
        if content_type not in ALLOWED_CONTENT_TYPES:
            raise ValidationFailed(
                ENTITY,
                "content_type",
                f"tipo di file non ammesso: {content_type}",
                expected=", ".join(sorted(ALLOWED_CONTENT_TYPES)),
            )
        if not data:
            raise ValidationFailed(ENTITY, "file", "il file e' vuoto", expected="almeno un byte")
        if len(data) > SIZE_MAX:
            raise ValidationFailed(
                ENTITY,
                "size",
                f"il file supera {SIZE_MAX} byte",
                expected=f"al massimo {SIZE_MAX} byte",
            )
        if not _matches_declared_type(data, content_type):
            raise ValidationFailed(
                ENTITY,
                "content_type",
                f"il contenuto del file non corrisponde al tipo dichiarato: {content_type}",
                expected=f"un file {ALLOWED_CONTENT_TYPES[content_type]} valido",
            )

    def check_invoice_pdf(self, document: Document, content_type: str) -> None:
        """A document that an invoice names as its PDF takes only a PDF (REB-480).

        `GET /api/invoices/{id}/pdf` serves the current version with that version's own
        type, and the invoice page frames it. Before this, anyone who may write could add
        an `application/xml` version to that document, and an XML file in the XHTML
        namespace then rendered as a page of the app. The web preview has refused a
        non-PDF since REB-463; this is the server's side of the same rule.

        Here, in the version core, rather than in the router: every version added to an
        existing document goes through `add_version_row` (the REST upload, `regenerate`,
        `InvoiceService`'s own render), and a guard on one door would leave the next
        caller a way around it. Keyed on the invoice's own pointer rather than on
        `documents.type`: a `invoice` document not yet linked to an invoice (an original
        PDF waiting for `import_issued`) keeps the common allowlist, and
        `_validate_original_pdf` already refuses to link one whose current version is not
        a PDF. A soft-deleted invoice still counts, because its document can be restored
        on its own. Every other document keeps `check_upload`'s allowlist unchanged.

        The row is locked before the link is read. `import_issued` reads an unlinked
        document's current version among its pure checks and writes the link only at its
        commit, and `_validate_original_pdf` takes the same lock before that read: an
        upload that arrives while an import holds the document waits, then sees the link
        and is refused; an import that arrives while an upload holds it waits, then sees
        the new version and refuses the document. Only a non-PDF upload locks: a PDF
        breaks no rule either way.
        """
        if content_type == "application/pdf":
            return
        self.ctx.repo.lock(document.id)
        type = self.ctx.repo.invoice_type_of_pdf(document.id)
        if type is not None:
            raise ValidationFailed(
                ENTITY,
                "content_type",
                f"questo documento e' il PDF di una {_INVOICE_TYPE_LABELS.get(type, type)}: "
                "una nuova versione deve essere "
                f"un PDF, non {content_type}",
                expected="application/pdf",
            )

    def add_version_row(
        self,
        document: Document,
        data: bytes,
        content_type: str,
        actor: Actor,
        *,
        markdown_source: str | None = None,
        template_id: UUID | None = None,
        variables: dict[str, Any] | None = None,
        storage_prefix: str | None = None,
    ) -> DocumentVersion:
        """`add_version` without the commit and on a `Document` already in hand -- see
        that method's docstring for the ordering this preserves, which is the whole
        substance of both.

        Takes the row rather than its id because `import_bytes` has just created it: a
        second `require` would be a redundant read, and (on a row flushed but not
        committed) a needlessly subtle one.
        """
        self.check_upload(data, content_type)
        self.check_invoice_pdf(document, content_type)

        number = self.next_number(document)
        version = DocumentVersion(
            document_id=document.id,
            number=number,
            markdown_source=markdown_source,
            template_id=template_id,
            variables=variables,
            storage_key=self.ctx.storage_key_for(
                document, number, content_type, prefix=storage_prefix
            ),
            content_type=content_type,
            size=len(data),
            hash_sha256=hashlib.sha256(data).hexdigest(),
            created_by=actor.id,
        )
        try:
            self.ctx.repo.add_version(version)
        except IntegrityError as exc:
            # The pre-check above (numero derived from the caller's own read of
            # versione_corrente) cannot cover two callers racing on the same
            # document: the unique constraint on (document_id, numero) is the real
            # authority. Storage was never touched -- see `add_version`.
            self.ctx.session.rollback()
            raise _version_conflict(document) from exc

        try:
            self.ctx.storage.put(version.storage_key, data, content_type)
        except Exception:
            # The row is flushed but not committed: rolling back here undoes that
            # insert too, so nothing is left pointing at bytes that were never
            # written, and the version number is free to be offered again.
            self.ctx.session.rollback()
            raise

        document.current_version = number
        self.ctx.activities.record(ENTITY, document.id, "version_added", actor, {"number": number})
        return version

    def import_bytes(
        self,
        *,
        customer_id: UUID | None = None,
        deal_id: UUID | None = None,
        contract_id: UUID | None = None,
        type: DocumentType,
        title: str,
        data: bytes,
        content_type: str,
        actor: Actor,
        origin: dict[str, Any],
        commit: bool = True,
        legal_entity_id: UUID | None = None,
    ) -> DocumentRead:
        """Bytes that arrived from somewhere else, filed as a document *and* its first
        version in one unit of work (slice 9 §3.5).

        The single entry point for imported bytes, and the reason it exists is the
        commit boundary. `create` commits and `add_version` commits, which is right for
        a person clicking upload -- the row they just made is theirs to see even if the
        upload then fails. It is wrong for an import: `InvoiceService.import_issued`
        fetches the original PDF from Drive during its *pure checks*, and every refusal
        after that point (a duplicate number, a declared gap, a register that is no
        longer chronological) must leave nothing behind. A document row committed on
        its own would outlive that refusal as an orphan PDF filed against a customer,
        with no invoice pointing at it and nothing to say why it is there.

        So the two halves compose through their non-committing cores with no commit in
        between, and `commit=False` hands the whole thing to the caller's transaction.
        `check_upload` runs *first*, before the `documents` row is flushed: a refusal
        raised from inside the version core would leave a flushed, uncommitted document
        in the caller's session for every later statement of that transaction to see --
        the exact failure `import_issued` orders its own checks to avoid.

        `origin` is recorded, sanitized, on `document.imported`: a PDF nobody in this
        CRM produced has to be able to answer "where did this come from?" years later,
        and "a file was uploaded" is not that answer. Its shape is the caller's --
        `{"drive_file_id": ..., "mime": ...}` for the Drive path -- because the CRM will
        learn other provenances and a fixed schema here would have to be migrated for
        each of them.

        **Exactly what `commit=False` promises, and what it does not.** It promises
        that this method issues no `commit`: on return, the `documents` row, its
        `document_versions` row and the three activity entries are *flushed* into the
        caller's transaction and nothing more, so the caller's own `commit` is what
        makes them exist and the caller's own `rollback` is what removes all of them
        together.

        It does **not** promise a clean session on failure, and the refusals split in
        two. `check_upload`, `check_owner` and a required custom field all raise
        *before this method has flushed anything of its own*, and none of them rolls
        back -- harmless for what this method wrote (nothing), and silent about what the
        caller wrote before calling. The two failures inside `add_version_row` -- an
        `IntegrityError` on the version's unique numero, a `storage.put` that raises --
        do roll the whole transaction back, because they must: a flushed version row
        must never be left pointing at bytes nobody wrote. So a caller that has already
        flushed rows of its own needs a `try/except ...: rollback; raise` of its own
        around this call to cover the first group -- which is exactly what
        `InvoiceService.import_issued` has, and why.

        And it promises nothing about storage: `storage.put` has already happened when
        this returns. Bytes written and then abandoned by a rollback are an accepted
        orphan, exactly as they already are in `add_version` -- the alternative is a
        two-phase delete that would itself have to be crash-safe, and an unreferenced
        object in storage costs disk, not correctness.
        """
        actor.require_write("import_document")
        self.check_upload(data, content_type)
        document = self.ctx.create_row(
            DocumentCreate(
                customer_id=customer_id,
                deal_id=deal_id,
                contract_id=contract_id,
                type=type,
                title=title,
            ),
            actor,
            # The record's own legal entity when the caller names it (an imported invoice's
            # original PDF is the invoice's, whatever legal entity its customer is in now).
            legal_entity_id=legal_entity_id,
        )
        self.add_version_row(document, data, content_type, actor)
        self.ctx.activities.record(
            ENTITY,
            document.id,
            "document.imported",
            actor,
            {"origin": origin, "title": document.title, "type": document.type},
        )
        if commit:
            self.ctx.session.commit()
        return DocumentRead.model_validate(document)

    def versions(self, document_id: UUID) -> list[DocumentVersionRead]:
        self.ctx.require(document_id)
        return [DocumentVersionRead.model_validate(v) for v in self.ctx.repo.versions(document_id)]

    def download(self, document_id: UUID, number: int | None) -> tuple[bytes, str, str]:
        """`(bytes, content_type, filename)`.

        The filename is built from the document's title, slugified: the title is user
        input and reaches a `Content-Disposition` header, where a quote or a newline
        would be header injection.
        """
        document, version = self.ctx.resolve_version(document_id, number)
        extension = ALLOWED_CONTENT_TYPES[version.content_type]
        filename = f"{slugify_folder(document.title)}-v{version.number}{extension}"
        return self.ctx.storage.get(version.storage_key), version.content_type, filename
