import hashlib
import re
import unicodedata
from datetime import UTC, datetime
from typing import Any, get_args
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from pigrocrm.core.activities.service import ActivityService
from pigrocrm.core.actor import Actor
from pigrocrm.core.automations.runner import AutomationRunner
from pigrocrm.core.clock import oggi_in_italia
from pigrocrm.core.config import Settings, get_settings
from pigrocrm.core.contracts.models import Contract
from pigrocrm.core.customers.models import Customer
from pigrocrm.core.customers.schemas import CustomerRead
from pigrocrm.core.db import encode_cursor, today_local
from pigrocrm.core.deals.models import Deal
from pigrocrm.core.documents.models import Document, DocumentVersion
from pigrocrm.core.documents.repository import DocumentRepository
from pigrocrm.core.documents.schemas import (
    ALLOWED_CONTENT_TYPES,
    DIMENSIONE_MAX,
    DOCUMENT_SORTS,
    DocumentCreate,
    DocumentFromTemplate,
    DocumentListQuery,
    DocumentPage,
    DocumentRead,
    DocumentTextRead,
    DocumentTipo,
    DocumentUpdate,
    DocumentVersionRead,
    OfferState,
)
from pigrocrm.core.emitter.service import EmitterProfileService
from pigrocrm.core.errors import Conflict, NotFound, ValidationFailed
from pigrocrm.core.fields.schemas import EntityType
from pigrocrm.core.fields.service import FieldDefinitionService
from pigrocrm.core.fields.validator import validate_custom_fields
from pigrocrm.core.render.pdf import build_header, render_pdf
from pigrocrm.core.schemas import reject_cleared_columns, supplied_changes
from pigrocrm.core.storage.base import DocumentStorage
from pigrocrm.core.templates.models import Template
from pigrocrm.core.templates.renderer import DeclaredVariable, render_template
from pigrocrm.core.templates.service import TemplateService
from pigrocrm.core.text import file_text

ENTITY: EntityType = "document"

# The template types a `documents` row can legitimately be created from. Derived from
# `DocumentTipo` itself, never retyped, so a value added there is covered here by
# construction -- see `_require_template` for why the two vocabularies stopped being
# the same Literal.
DOCUMENT_TIPI: frozenset[str] = frozenset(get_args(DocumentTipo))
_SLUG_STRIP = re.compile(r"[^a-z0-9]+")
# How large a stored file may be for `extract_text` to parse it at all. `DIMENSIONE_MAX`
# (100 MB) is what the archive accepts -- a zip of photos, a video somebody attached to
# a deal -- and parsing one of those to answer "what is the codice destinatario" would
# spend a hundred megabytes of this process on a file with no text in it. Same 20 MB as
# `drive/reader.py::DOWNLOAD_MAX_BYTES` and `settings.gmail_attachment_max_bytes`,
# because it is the same question about the same kind of file.
TEXT_SOURCE_MAX_BYTES = 20_971_520
_TESTO_TROPPO_GRANDE = (
    "il documento supera {max_bytes} byte: scaricalo invece di leggerlo come testo"
)
_CUSTOMER_ID_FRAGMENT = 8

# The offer lifecycle, as a table rather than a chain of `if`s: the UI reads it to
# decide which buttons to show, and the MCP tool reads it to tell an agent what it may
# do next. `accettata` and `rifiutata` are terminal -- an offer that was answered is a
# fact, and editing that fact is a new offer, not a state change.
OFFER_TRANSITIONS: dict[str, frozenset[str]] = {
    "bozza": frozenset({"inviata"}),
    "inviata": frozenset({"accettata", "rifiutata", "bozza"}),
    "accettata": frozenset(),
    "rifiutata": frozenset(),
}


def slugify_folder(raw: str) -> str:
    """A customer name (or a document title) as a storage-key/filename segment.

    Mirrors `fields/schemas.slugify_key` in spirit -- NFKD, drop the combining marks,
    collapse the rest -- but joins with "-" rather than "_", because these segments
    are read by a human browsing Google Drive, which is the whole point of storing a
    slug at all.
    """
    decomposed = unicodedata.normalize("NFKD", raw)
    transliterated = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return _SLUG_STRIP.sub("-", transliterated.strip().lower()).strip("-") or "senza-nome"


class DocumentService:
    def __init__(
        self, session: Session, storage: DocumentStorage, settings: Settings | None = None
    ) -> None:
        self.session = session
        self.storage = storage
        self.settings = settings or get_settings()
        self.repo = DocumentRepository(session)
        self.fields = FieldDefinitionService(session)
        self.activities = ActivityService(session)
        self.templates = TemplateService(session)
        self.emitter = EmitterProfileService(session)

    # ---- owner resolution ---------------------------------------------------

    def _check_owner(
        self,
        customer_id: UUID | None,
        deal_id: UUID | None,
        contract_id: UUID | None = None,
    ) -> None:
        """Exactly one owner, and it must exist.

        The database check constraint (`ck_documents_customer_xor_deal`) is the
        second line under concurrency; this is the first, and it is what turns a
        syntactically valid but unknown UUID into this project's own `NotFound`
        instead of a raw `ForeignKeyViolation` reaching the caller from `flush()`.

        `contract_id` defaults to `None` so every existing caller that only knows
        about `customer_id`/`deal_id` (`create_from_template`) keeps working
        unchanged -- REB-358 widened the *rule* from two owners to three without
        widening every call site's own schema.
        """
        owners = (customer_id, deal_id, contract_id)
        if sum(owner is not None for owner in owners) != 1:
            raise ValidationFailed(
                ENTITY,
                "customer_id",
                "un documento appartiene a un cliente, a un deal o a un contratto: "
                "mai a più di uno, mai a nessuno",
                expected="esattamente uno fra customer_id, deal_id e contract_id",
            )
        if customer_id is not None and self.session.get(Customer, customer_id) is None:
            raise NotFound("customer", customer_id)
        if deal_id is not None and self.session.get(Deal, deal_id) is None:
            raise NotFound("deal", deal_id)
        if contract_id is not None and self.session.get(Contract, contract_id) is None:
            raise NotFound("contract", contract_id)

    def _customer_of(self, document: Document) -> Customer | None:
        if document.customer_id is not None:
            return self.session.get(Customer, document.customer_id)
        if document.deal_id is not None:
            deal = self.session.get(Deal, document.deal_id)
            return self.session.get(Customer, deal.customer_id) if deal else None
        if document.contract_id is not None:
            contract = self.session.get(Contract, document.contract_id)
            return self.session.get(Customer, contract.customer_id) if contract else None
        return None

    def storage_key_for(
        self, document: Document, numero: int, content_type: str, *, prefix: str | None = None
    ) -> str:
        """`{cliente-slug}-{id[:8]}/{document_id}/v{numero}{ext}`, or
        `{prefix}/v{numero}{ext}` when a caller supplies its own prefix.

        The customer-id fragment is what keeps the folder stable when a customer is
        renamed -- the slug alone would send the next version into a different folder
        and orphan the earlier ones. The slug is what makes the folder legible to a
        human browsing Drive. Built entirely from validated internal pieces (a
        slugified name, a UUID's own hex digits, an integer, an extension drawn from
        `ALLOWED_CONTENT_TYPES`, never the caller's raw string) and still passed
        through `storage.put`'s own `validate_storage_key` gate before anything
        touches a filesystem -- this method's job is to produce a *sensible* key, not
        to be the security boundary itself.

        The `prefix` override exists for the fiscal artefacts of slice 3, which need
        `fatture/{anno}/{numero}/` and `proforma/{id}/` rather than a customer folder:
        the bytes live under a prefix that keeps a file identifiable when it is pulled
        out of its context, which is one of the four independent mechanisms that stop a
        proforma from being read as an invoice. Keyword-only and defaulting to `None`,
        so every existing caller is unaffected; the value still passes through
        `storage.put`'s own `validate_storage_key` gate, which is what actually refuses
        an unsafe key -- this method's job is to produce a sensible one.
        """
        if prefix is not None:
            return f"{prefix}/v{numero}{ALLOWED_CONTENT_TYPES[content_type]}"
        customer = self._customer_of(document)
        folder = (
            f"{slugify_folder(customer.ragione_sociale)}-{str(customer.id)[:_CUSTOMER_ID_FRAGMENT]}"
            if customer
            else "senza-cliente"
        )
        return f"{folder}/{document.id}/v{numero}{ALLOWED_CONTENT_TYPES[content_type]}"

    # ---- custom fields ------------------------------------------------------

    def _validated_custom(self, values: dict[str, Any]) -> dict[str, Any]:
        return validate_custom_fields(ENTITY, self.fields.specs_for(ENTITY), values)

    def _update_custom_fields(self, document: Document, provided: dict[str, Any]) -> dict[str, Any]:
        """Validates only the keys the caller is touching, never the merge with what
        is stored. Identical in shape to `CustomerService._update_custom_fields`; see
        that method's docstring for the archiving contract this preserves."""
        active_by_key = {spec.key: spec for spec in self.fields.specs_for(ENTITY)}
        to_remove: set[str] = set()
        for key, value in provided.items():
            if value is not None:
                continue
            spec = active_by_key.get(key)
            if spec is not None and spec.required:
                raise ValidationFailed(
                    ENTITY, key, "campo obbligatorio", expected="un valore non vuoto"
                )
            to_remove.add(key)
        to_set = {key: value for key, value in provided.items() if value is not None}
        touched = [spec for spec in active_by_key.values() if spec.key in to_set]
        merged = {k: v for k, v in document.custom_fields.items() if k not in to_remove}
        merged.update(validate_custom_fields(ENTITY, touched, to_set))
        return merged

    # ---- documents ------------------------------------------------------------

    def _create_row(self, data: DocumentCreate, actor: Actor) -> Document:
        """Everything `create` does except the commit, so `import_bytes` can compose it
        with `_add_version_row` inside one transaction.

        Not named `*_in_transaction`: that suffix is reserved by spec §9.3 for methods
        that skip authorisation and record nothing, callable only from
        `core/automations/` (see `tests/test_in_transaction_callers.py`). This one
        checks the actor and records exactly as before -- the only thing it leaves to
        its caller is *when* the transaction ends.
        """
        actor.require_write("create_document")
        payload = data.model_dump()
        self._check_owner(payload["customer_id"], payload["deal_id"], payload["contract_id"])
        # Only an offer has a state; everything else keeps NULL. A new offer starts as
        # a draft rather than stateless, so a Kanban-style state picker always has a
        # value to show.
        is_offer = payload["tipo"] == "offerta"
        payload["stato"] = (payload.get("stato") or "bozza") if is_offer else None
        payload["custom_fields"] = self._validated_custom(payload.get("custom_fields") or {})

        document = self.repo.add(Document(**payload))
        self.activities.record(
            ENTITY,
            document.id,
            "created",
            actor,
            {"titolo": document.titolo, "tipo": document.tipo},
        )
        return document

    def create(self, data: DocumentCreate, actor: Actor) -> DocumentRead:
        document = self._create_row(data, actor)
        self.session.commit()
        return DocumentRead.model_validate(document)

    def update(self, document_id: UUID, data: DocumentUpdate, actor: Actor) -> DocumentRead:
        actor.require_write("update_document")
        document = self._require(document_id)
        changes = supplied_changes(data, exclude={"custom_fields"})
        reject_cleared_columns(ENTITY, Document, changes)
        if data.custom_fields is not None:
            changes["custom_fields"] = self._update_custom_fields(document, data.custom_fields)
        for key, value in changes.items():
            setattr(document, key, value)
        self.activities.record(ENTITY, document.id, "updated", actor, {"changed": sorted(changes)})
        self.session.commit()
        return DocumentRead.model_validate(document)

    def get(self, document_id: UUID, actor: Actor) -> DocumentRead:
        return DocumentRead.model_validate(self._require(document_id))

    def soft_delete(self, document_id: UUID, actor: Actor) -> None:
        """Sets `deleted_at`. The stored bytes are left alone -- soft delete never
        touches storage. `delete` on the storage backends is a separate, deliberate
        act (the one that had to be fixed so it could not leave a document retrievable
        after deletion); a restore that came back without the file would not be a real
        restore, so nothing here calls it."""
        actor.require_write("delete_document")
        document = self._require(document_id)
        document.deleted_at = datetime.now(UTC)
        self.activities.record(ENTITY, document.id, "deleted", actor)
        self.session.commit()

    def restore(self, document_id: UUID, actor: Actor) -> DocumentRead:
        actor.require_write("restore_document")
        document = self.repo.get(document_id, include_deleted=True)
        if document is None:
            raise NotFound(ENTITY, document_id)
        was_deleted = document.deleted_at is not None
        document.deleted_at = None
        if was_deleted:
            self.activities.record(ENTITY, document.id, "restored", actor)
        self.session.commit()
        return DocumentRead.model_validate(document)

    # ---- versions ---------------------------------------------------------------

    def _next_numero(self, document: Document) -> int:
        """The candidate version number for a new upload on `document`.

        Split out to a single, tiny method so `add_version`'s own ordering guarantee
        (below) has exactly one thing to be provably correct about, and so a test can
        force a collision deterministically -- the same technique
        `test_upsert_converts_a_true_insert_race_into_a_clean_conflict` in
        `test_emitter.py` uses on `EmitterProfileRepository.get` -- without needing
        real threads against a single savepoint-backed test session.
        """
        return document.versione_corrente + 1

    def add_version(
        self,
        document_id: UUID,
        data: bytes,
        content_type: str,
        actor: Actor,
        *,
        sorgente_markdown: str | None = None,
        template_id: UUID | None = None,
        variabili: dict[str, Any] | None = None,
        storage_prefix: str | None = None,
    ) -> DocumentVersionRead:
        """Every change makes a version; nothing is ever overwritten (spec 4.2).

        Ordering, deliberately: the `DocumentVersion` row is flushed -- which sends
        the `INSERT` and lets Postgres enforce `uq_document_versions_document_numero`
        -- *before* a single byte reaches `storage.put`. Two callers racing to add a
        version to the same document both compute their candidate `numero` from
        `document.versione_corrente` before either has committed; under Postgres, the
        loser's `INSERT` either blocks until the winner's transaction resolves (real
        concurrency) or raises `IntegrityError` immediately against an
        already-committed row (the two-callers-in-one-process shape this project's
        own tests reproduce, see `_next_numero`). Either way, the loser's exception is
        caught and turned into `Conflict` *before* it has called `storage.put` at
        all -- so a losing racer can never overwrite the winning racer's bytes at the
        storage key both computed from the same `numero`, and a failed flush never
        burns a version number: nothing was committed, so the same `numero` is offered
        again on the next attempt.

        The reverse order (bytes first, row second -- storage.put before the insert)
        was rejected: it is exactly as safe on the *happy* path, but on the race path
        it lets the loser's `storage.put` execute and physically overwrite the
        winner's already-put bytes before the loser's own insert is rejected by the
        unique constraint -- corrupting a version that a reader may already believe is
        final. Writing the row first means storage is only ever touched once the
        number is confirmed reserved (if only within this open transaction).

        The remaining failure mode -- `storage.put` itself raising after a successful
        flush -- is handled by rolling back before propagating: without that, the
        flushed `INSERT` would still be pending in the session, and an unrelated later
        commit on this same session could persist a version row pointing at bytes
        that were never written. Rolling back trades that for the harmless outcome:
        nothing committed, the number is not burned, the session stays usable.
        """
        actor.require_write("add_document_version")
        document = self._require(document_id)
        version = self._add_version_row(
            document,
            data,
            content_type,
            actor,
            sorgente_markdown=sorgente_markdown,
            template_id=template_id,
            variabili=variabili,
            storage_prefix=storage_prefix,
        )
        self.session.commit()
        return DocumentVersionRead.model_validate(version)

    def _check_upload(self, data: bytes, content_type: str) -> None:
        """The three facts about a candidate file that are true or false before anything
        is written: an allowed type, at least one byte, not over the ceiling.

        Split out of `_add_version_row` so `import_bytes` can run it *before* creating
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
        if len(data) > DIMENSIONE_MAX:
            raise ValidationFailed(
                ENTITY,
                "dimensione",
                f"il file supera {DIMENSIONE_MAX} byte",
                expected=f"al massimo {DIMENSIONE_MAX} byte",
            )

    def _check_invoice_pdf(self, document: Document, content_type: str) -> None:
        """A document that an invoice names as its PDF takes only a PDF (REB-480).

        `GET /api/invoices/{id}/pdf` serves the current version with that version's own
        type, and the invoice page frames it. Before this, anyone who may write could add
        an `application/xml` version to that document, and an XML file in the XHTML
        namespace then rendered as a page of the app. The web preview has refused a
        non-PDF since REB-463; this is the server's side of the same rule.

        Here, in the version core, rather than in the router: every version added to an
        existing document goes through `_add_version_row` (the REST upload, `regenerate`,
        `InvoiceService`'s own render), and a guard on one door would leave the next
        caller a way around it. Keyed on the invoice's own pointer rather than on
        `documents.tipo`: a `fattura` document not yet linked to an invoice (an original
        PDF waiting for `import_issued`) keeps the common allowlist, and
        `_validate_original_pdf` already refuses to link one whose current version is not
        a PDF. A soft-deleted invoice still counts, because its document can be restored
        on its own. Every other document keeps `_check_upload`'s allowlist unchanged.

        What this does not close: `import_issued` reads an unlinked document's current
        version in its pure checks and writes the link later, in its own transaction. A
        non-PDF upload to that same document in between sees no link yet and lands.
        `InvoiceService.download` then answers 404 for it rather than serving it, and a
        PDF version uploaded over it repairs it.
        """
        if content_type == "application/pdf":
            return
        tipo = self.repo.invoice_tipo_of_pdf(document.id)
        if tipo is not None:
            raise ValidationFailed(
                ENTITY,
                "content_type",
                f"questo documento e' il PDF di una {tipo}: una nuova versione deve essere "
                f"un PDF, non {content_type}",
                expected="application/pdf",
            )

    def _add_version_row(
        self,
        document: Document,
        data: bytes,
        content_type: str,
        actor: Actor,
        *,
        sorgente_markdown: str | None = None,
        template_id: UUID | None = None,
        variabili: dict[str, Any] | None = None,
        storage_prefix: str | None = None,
    ) -> DocumentVersion:
        """`add_version` without the commit and on a `Document` already in hand -- see
        that method's docstring for the ordering this preserves, which is the whole
        substance of both.

        Takes the row rather than its id because `import_bytes` has just created it: a
        second `_require` would be a redundant read, and (on a row flushed but not
        committed) a needlessly subtle one.
        """
        self._check_upload(data, content_type)
        self._check_invoice_pdf(document, content_type)

        numero = self._next_numero(document)
        version = DocumentVersion(
            document_id=document.id,
            numero=numero,
            sorgente_markdown=sorgente_markdown,
            template_id=template_id,
            variabili=variabili,
            storage_key=self.storage_key_for(document, numero, content_type, prefix=storage_prefix),
            content_type=content_type,
            dimensione=len(data),
            hash_sha256=hashlib.sha256(data).hexdigest(),
            creato_da=actor.id,
        )
        try:
            self.repo.add_version(version)
        except IntegrityError as exc:
            # The pre-check above (numero derived from the caller's own read of
            # versione_corrente) cannot cover two callers racing on the same
            # document: the unique constraint on (document_id, numero) is the real
            # authority. Storage was never touched -- see the docstring above.
            self.session.rollback()
            raise Conflict(
                "document_version",
                "conflitto di concorrenza sul numero di versione, riprova",
                document_id=str(document.id),
            ) from exc

        try:
            self.storage.put(version.storage_key, data, content_type)
        except Exception:
            # The row is flushed but not committed: rolling back here undoes that
            # insert too, so nothing is left pointing at bytes that were never
            # written, and the version number is free to be offered again.
            self.session.rollback()
            raise

        document.versione_corrente = numero
        self.activities.record(ENTITY, document.id, "version_added", actor, {"numero": numero})
        return version

    def import_bytes(
        self,
        *,
        customer_id: UUID | None = None,
        deal_id: UUID | None = None,
        tipo: DocumentTipo,
        titolo: str,
        data: bytes,
        content_type: str,
        actor: Actor,
        origine: dict[str, Any],
        commit: bool = True,
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
        `_check_upload` runs *first*, before the `documents` row is flushed: a refusal
        raised from inside the version core would leave a flushed, uncommitted document
        in the caller's session for every later statement of that transaction to see --
        the exact failure `import_issued` orders its own checks to avoid.

        `origine` is recorded, sanitized, on `document.importato`: a PDF nobody in this
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
        two. `_check_upload`, `_check_owner` and a required custom field all raise
        *before this method has flushed anything of its own*, and none of them rolls
        back -- harmless for what this method wrote (nothing), and silent about what the
        caller wrote before calling. The two failures inside `_add_version_row` -- an
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
        self._check_upload(data, content_type)
        document = self._create_row(
            DocumentCreate(customer_id=customer_id, deal_id=deal_id, tipo=tipo, titolo=titolo),
            actor,
        )
        self._add_version_row(document, data, content_type, actor)
        self.activities.record(
            ENTITY,
            document.id,
            "document.importato",
            actor,
            {"origine": origine, "titolo": document.titolo, "tipo": document.tipo},
        )
        if commit:
            self.session.commit()
        return DocumentRead.model_validate(document)

    def versions(self, document_id: UUID, actor: Actor) -> list[DocumentVersionRead]:
        self._require(document_id)
        return [DocumentVersionRead.model_validate(v) for v in self.repo.versions(document_id)]

    def download(
        self, document_id: UUID, numero: int | None, actor: Actor
    ) -> tuple[bytes, str, str]:
        """`(bytes, content_type, filename)`.

        The filename is built from the document's title, slugified: the title is user
        input and reaches a `Content-Disposition` header, where a quote or a newline
        would be header injection.
        """
        document, version = self._resolve_version(document_id, numero)
        extension = ALLOWED_CONTENT_TYPES[version.content_type]
        filename = f"{slugify_folder(document.titolo)}-v{version.numero}{extension}"
        return self.storage.get(version.storage_key), version.content_type, filename

    def _resolve_version(
        self, document_id: UUID, numero: int | None
    ) -> tuple[Document, DocumentVersion]:
        """The document and the version a caller asked for -- `None` meaning "the
        current one".

        Shared by `download` and `extract_text` rather than written twice: the two differ
        only in what they do with the bytes, and a second copy of "no `numero` means
        `versione_corrente`" is a second place for that default to drift. A document
        with no version at all (`versione_corrente is None`) and a `numero` nobody ever
        uploaded answer the same `NotFound`, because to the caller they are the same
        fact: that version is not there.
        """
        document = self._require(document_id)
        wanted = numero if numero is not None else document.versione_corrente
        version = self.repo.version(document_id, wanted) if wanted else None
        if version is None:
            raise NotFound("document_version", f"{document_id}#{wanted}")
        return document, version

    def extract_text(
        self, document_id: UUID, numero: int | None, actor: Actor, *, max_bytes: int | None = None
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
        `TEXT_SOURCE_MAX_BYTES` (a 100 MB video in the archive is not a document to
        extract, and `DIMENSIONE_MAX` lets one exist), and the text is cut at
        `max_bytes`, defaulting to `settings.document_text_max_bytes`.

        Reads nothing into the audit log: this is a read of content, like `download`,
        and an `activities` row per read would put a client's document title and the
        fact somebody read it into a timeline that exists to record changes.
        """
        document, version = self._resolve_version(document_id, numero)
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
        limit = self.settings.document_text_max_bytes if max_bytes is None else max_bytes
        estratto = file_text(
            self.storage.get(version.storage_key), mime=version.content_type, max_bytes=limit
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

    # ---- template-driven documents ------------------------------------------

    def _require_template(self, template_id: UUID) -> Template:
        """`TemplateService` exposes no `_require` of its own -- only `get`, which
        returns a `TemplateRead` (a detached copy with `variabili_dichiarate` already
        turned into `TemplateVariable` objects). This needs the live ORM row instead:
        `declared_variables` reads `template.variabili_dichiarate` as the raw list of
        dicts JSONB actually stores, and `template.corpo_markdown`/`template.tipo`/
        `template.nome` feed straight into rendering. Going through
        `self.templates.repo.get` directly is what `TemplateService.declared_variables`
        itself documents as the expected call shape for a `Template` already in hand.
        """
        template = self.templates.repo.get(template_id)
        if template is None:
            raise NotFound("template", template_id)
        if template.tipo not in DOCUMENT_TIPI:
            # `TemplateTipo` and `DocumentTipo` used to be the same Literal, so
            # `Document.tipo = template.tipo` a few methods down was a copy that could
            # not go wrong. Slice 5 widened the template side with `email` and
            # `sollecito` -- neither of which is a thing a `documents` row can be --
            # and `DocumentRead.tipo` is a plain `str`, so without this check the copy
            # would succeed silently and put a document of type "sollecito" in the
            # customer's document list. The conversion between the two vocabularies is
            # explicit here rather than implicit in the overlap.
            raise ValidationFailed(
                ENTITY,
                "template_id",
                f"il template '{template.nome}' è di tipo '{template.tipo}', "
                "che non è un tipo di documento",
                expected=f"un template fra: {', '.join(sorted(DOCUMENT_TIPI))}",
            )
        return template

    def _template_scope(
        self, document: Document, variabili: dict[str, Any], actor: Actor
    ) -> dict[str, Any]:
        """What a template can read: the caller's variables, plus `cliente` and
        `emittente` from the record itself.

        The caller's own keys go in first and the record's go in second, so a caller
        cannot shadow `cliente` or `emittente` with values of their own -- an offer
        must state the customer the document is filed under, not the one whoever
        pressed the button typed.
        """
        customer = self._customer_of(document)
        scope: dict[str, Any] = dict(variabili)
        scope["emittente"] = self.emitter.as_template_values(actor)["emittente"]
        scope["cliente"] = (
            CustomerRead.model_validate(customer).model_dump(mode="json") if customer else {}
        )
        # `oggi_in_italia()`, never a bare `date.today()`: see `clock.py`. `{{oggi}}` is
        # the date a template prints on the document itself -- the date on an offer or a
        # contract. On a host that is not running in Europe/Rome (the API image runs in
        # UTC) every render between midnight and 01:00 CET dates the document to the
        # previous day, and on the night of 31 December to the previous *year*. The
        # error is not transient: the rendered PDF and the scope that produced it are
        # frozen into an immutable version, and `regenerate` faithfully reproduces the
        # wrong date from that snapshot, so nothing later corrects it.
        scope.setdefault("oggi", oggi_in_italia().isoformat())
        return scope

    def _render_to_pdf(
        self, corpo: str, scope: dict[str, Any], declared: tuple[DeclaredVariable, ...]
    ) -> tuple[str, bytes]:
        markdown = render_template(corpo, scope, declared)
        header = build_header(scope["emittente"])
        return markdown, render_pdf(markdown, header_typst=header, settings=self.settings)

    def create_from_template(self, data: DocumentFromTemplate, actor: Actor) -> DocumentRead:
        """The call behind "Claude, prepara una nuova offerta usando il template
        Consulenza CTO" (spec 7) and behind the UI's Genera button. One transaction:
        the document, its first version and the timeline entry commit together, and a
        render failure leaves nothing behind."""
        actor.require_write("create_document_from_template")
        template = self._require_template(data.template_id)
        self._check_owner(data.customer_id, data.deal_id)

        document = self.repo.add(
            Document(
                customer_id=data.customer_id,
                deal_id=data.deal_id,
                tipo=template.tipo,
                titolo=data.titolo,
                stato="bozza" if template.tipo == "offerta" else None,
                custom_fields=self._validated_custom(data.custom_fields or {}),
            )
        )
        try:
            scope = self._template_scope(document, data.variabili, actor)
            markdown, pdf = self._render_to_pdf(
                template.corpo_markdown, scope, self.templates.declared_variables(template)
            )
        except Exception:
            # "a render failure leaves nothing behind": the `Document` row above was
            # only flushed, never committed, so a missing required variable or a
            # broken Markdown/Typst compile must undo it too -- otherwise it sits in
            # the still-open transaction, invisible to another connection but already
            # visible to this same session's own later reads (a plain `SELECT`, not a
            # `COMMIT`, is what a flushed row needs to be seen by).
            self.session.rollback()
            raise

        key = self.storage_key_for(document, 1, "application/pdf")
        version = DocumentVersion(
            document_id=document.id,
            numero=1,
            sorgente_markdown=markdown,
            template_id=template.id,
            variabili=data.variabili,
            storage_key=key,
            content_type="application/pdf",
            dimensione=len(pdf),
            hash_sha256=hashlib.sha256(pdf).hexdigest(),
            creato_da=actor.id,
        )
        # Same ordering `add_version` establishes and the same reason: the version
        # row is flushed -- and Postgres's own `uq_document_versions_document_numero`
        # given a chance to reject it -- *before* a byte reaches `storage.put`, so a
        # losing racer's bytes can never physically overwrite a winner's. The brief's
        # own sample called `storage.put` first; that ordering was rejected once
        # already for `add_version` and is not reintroduced here.
        try:
            self.repo.add_version(version)
        except IntegrityError as exc:
            self.session.rollback()
            raise Conflict(
                "document_version",
                "conflitto di concorrenza sul numero di versione, riprova",
                document_id=str(document.id),
            ) from exc

        try:
            self.storage.put(key, pdf, "application/pdf")
        except Exception:
            # The row is flushed but not committed: rolling back here undoes that
            # insert (and the document's own flushed insert) too, so nothing is left
            # pointing at bytes that were never written.
            self.session.rollback()
            raise

        document.versione_corrente = 1
        self.activities.record(
            ENTITY, document.id, "created_from_template", actor, {"template": template.nome}
        )
        self.session.commit()
        return DocumentRead.model_validate(document)

    def regenerate(self, document_id: UUID, numero: int, actor: Actor) -> DocumentVersionRead:
        """Rebuild an old version as a new one.

        Reads the version's own `template_id` and `variabili` -- which is exactly why
        both are stored (spec 4.2) -- so a six-month-old offer regenerates identically
        without the person who wrote it being in the room.
        """
        actor.require_write("regenerate_document")
        document = self._require(document_id)
        source = self.repo.version(document_id, numero)
        if source is None:
            raise NotFound("document_version", f"{document_id}#{numero}")
        if source.template_id is None or source.variabili is None:
            raise ValidationFailed(
                ENTITY,
                "numero",
                "questa versione non e' stata generata da un template e non si rigenera",
                expected="una versione creata da un template",
            )
        template = self._require_template(source.template_id)
        scope = self._template_scope(document, source.variabili, actor)
        markdown, pdf = self._render_to_pdf(
            template.corpo_markdown, scope, self.templates.declared_variables(template)
        )
        return self.add_version(
            document_id,
            pdf,
            "application/pdf",
            actor,
            sorgente_markdown=markdown,
            template_id=template.id,
            variabili=source.variabili,
        )

    def set_offer_state(self, document_id: UUID, stato: OfferState, actor: Actor) -> DocumentRead:
        """The only writer of `documents.stato`.

        Deliberately not a field on `DocumentUpdate`, and it stays that way now that
        A14 is closed and a `null` on an Update schema really does clear a column: the
        original reason was that it could not, but the better one is that
        `documents.stato` is `NULL` for everything that is not an offer, so "clear it"
        is not a state a caller should be able to ask for on an offer at all. A
        dedicated method taking a required, non-nullable literal cannot express it.
        """
        actor.require_write("set_offer_state")
        document = self._require(document_id)
        if document.tipo != "offerta" or document.stato is None:
            raise ValidationFailed(
                ENTITY,
                "stato",
                "solo un documento di tipo offerta ha uno stato",
                expected="un documento di tipo offerta",
            )
        allowed = OFFER_TRANSITIONS[document.stato]
        if stato not in allowed:
            raise Conflict(
                ENTITY,
                f"da '{document.stato}' non si puo' passare a '{stato}'",
                stato_attuale=document.stato,
                transizioni_ammesse=sorted(allowed),
            )
        previous, document.stato = document.stato, stato
        # The day this state began. `today_local()` and never `date.today()`: see
        # `db/clock.py`. An offer whose state was set at 00:30 CET on 1 January would
        # otherwise be reported as having been in that state since the previous year.
        document.stato_dal = today_local()

        # Slice 6 §9.3. The order of these four steps is fixed and is not cosmetic:
        #
        #   1. the mutation above,
        #   2. the runner -- which mutates the deal and records its own activity,
        #   3. this document's own activity,
        #   4. the commit.
        #
        # `ActivityService.record`'s docstring requires it to be the last thing that
        # touches the session before the caller's commit, and forbids following it with a
        # call into another service that commits on its own behalf. The runner sits before
        # it and never commits, so both halves of that contract hold -- and the automation
        # is atomic with its trigger: either the offer is accepted and the deal is moved,
        # or neither is true. `test_automation_atomicity.py` proves both directions,
        # including that a concurrent reader never sees one half without the other.
        #
        # Called explicitly, not through a hook: an implicit hook on a state change is a
        # mechanism whose call sites cannot be found by reading the code.
        AutomationRunner(self.session).on_offer_state_changed(document, previous, actor)

        self.activities.record(
            ENTITY, document.id, "state_changed", actor, {"da": previous, "a": stato}
        )
        self.session.commit()
        return DocumentRead.model_validate(document)

    def _require(self, document_id: UUID) -> Document:
        document = self.repo.get(document_id)
        if document is None:
            raise NotFound(ENTITY, document_id)
        return document

    # `list` must stay the last method defined in this class -- an unconditional
    # project rule (`test_module_imports.py`). Defining a method named `list` rebinds
    # that name in the *class* namespace, so any later method whose own return
    # annotation is a bare `list[...]` would resolve `list` to this method instead of
    # the builtin and fail at import time on Python 3.13.
    def list(self, query: DocumentListQuery, actor: Actor) -> DocumentPage:
        rows = self.repo.list(query)
        has_more = len(rows) > query.limit
        items = rows[: query.limit]
        # See `CustomerService.list` for why the cursor carries the sort value as well
        # as the id.
        spec = DOCUMENT_SORTS.resolve(query.sort)
        next_cursor = (
            encode_cursor(spec, getattr(items[-1], spec.key), items[-1].id)
            if has_more and items
            else None
        )
        return DocumentPage(
            items=[DocumentRead.model_validate(d) for d in items],
            next_cursor=next_cursor,
        )
