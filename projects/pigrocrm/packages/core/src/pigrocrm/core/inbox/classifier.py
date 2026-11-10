"""The classifier of the inbox (design 2026-11-09, incoming documents inbox, § 3 and § 3.5):
every item is read once, in this order.

1. **Structured and checkable is certain** (a FatturaPA, plain or `.p7m`; a statement file). The
   legal entity comes first (§ 3.4) and is certain only when exactly one of the space's matches;
   then the direction: the legal entity as the supplier is a **sales invoice**, which waits for a
   person in the issued-invoice review (`InboxService.issued_review` / `confirm_issued`: importing
   an invoice consumes a register number and is a person's act, never this classifier's); the
   legal entity as the buyer is a **supplier invoice** or a credit note, held with its parsed
   fields for the purchases module (C3), which does not exist yet; a statement file is tagged
   for C4. Nothing here writes a document, a purchase or an invoice.
2. **Free text is a proposal.** A PDF or an image is read by PigroCRM's own model when an
   administrator has switched it on (§ 3.2), and the answer becomes **a proposal and nothing
   else**: no `Document`, no purchase, no cost. With the model off, or when it cannot answer, the
   item stays unclassified (`new`, no kind) with a sentence saying why, and the PDF's extracted text
   is there for the user's own agent, which proposes through the MCP tool.
3. **Nothing recognised is `other`**, kept, with a sentence.

An item is read once: one that already has a kind or is not `new` is left as it is, and a
classifier run on it again does nothing. An item the model could not read (off, over the cap, an
outage) has no kind and is read again by the next run.
"""

import logging
import time
from collections.abc import Callable, Sequence
from datetime import timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.ai.gateway import ModelCapReached, ModelGateway, ModelOff
from pigrocrm.core.ai.reader import DocumentReader, Unreadable
from pigrocrm.core.ai.transport import IMAGE_TYPES, PDF_TYPE, Attachment, ModelUnavailable
from pigrocrm.core.errors import DomainError, ValidationFailed
from pigrocrm.core.inbox.access import visible_item, visible_to
from pigrocrm.core.inbox.models import (
    EXTRACTED_TEXT_MAX,
    MAX_READ_ATTEMPTS,
    READ_CLAIM_MINUTES,
    InboxItem,
)
from pigrocrm.core.inbox.p7m import P7mError, open_p7m
from pigrocrm.core.inbox.schemas import InboxItemPage, InboxItemRead, InboxUploadResult
from pigrocrm.core.inbox.service import attach_proposals
from pigrocrm.core.invoices import fatturapa_import
from pigrocrm.core.invoices.import_direction import match_legal_entity
from pigrocrm.core.invoices.import_schemas import ParsedInvoice, ParsedInvoiceParty
from pigrocrm.core.legal_entities.models import LegalEntity
from pigrocrm.core.legal_entities.repository import LegalEntityRepository
from pigrocrm.core.proposals.schemas import ProposalCreate
from pigrocrm.core.proposals.service import ProposalService
from pigrocrm.core.storage.base import DocumentStorage
from pigrocrm.core.text import file_text

logger = logging.getLogger(__name__)

CLASSIFY_ACTION = "classify_inbox_item"

REQUEST_DEADLINE_SECONDS = 120.0
"""How long into a request the classifier still sends a file to the model. A call takes at most
`CALL_TIMEOUT_SECONDS` (60) and a rate limit may add one wait of up to 30, so a request that
stops sending at 120 s answers within about 270 s, inside nginx's 300 s: the files it did not
reach stay unread for the next run."""

MAX_MODEL_FILE_BYTES = 20 * 1024 * 1024
"""The largest file sent to the model as it is (a PDF with no text, an image); the providers
refuse more than this and a refusal would still be a paid request."""

STATEMENT_TYPES = frozenset({"camt053", "csv", "xls", "xlsx"})
MODEL_FILE_TYPES = ("pdf", "png", "jpeg")
CHEAP_PER_RUN = 200
MEDIA_TYPES = {"png": "image/png", "jpeg": "image/jpeg", "pdf": PDF_TYPE}

SALES_REASON = (
    "Fattura emessa: attende la conferma di una persona nella revisione delle fatture emesse"
)
PURCHASE_REASON = "Fattura di acquisto: attende il modulo che la registra, che non c'e' ancora"
UNKNOWN_BUYER_REASON = (
    "Fattura di acquisto: il destinatario non e' un'azienda dello spazio, attende una persona"
)
MIXED_REASON = "La fattura mescola documenti emessi e ricevuti: attende una persona"
STATEMENT_REASON = "Estratto conto: lo legge il modulo cassa, che non c'e' ancora"
NOT_FATTURAPA_REASON = "XML che non e' una fattura elettronica: non viene letto"
OFF_REASON = (
    "Da leggere: il modello di PigroCRM e' spento. Il tuo agente puo' leggerlo e proporre "
    "cosa e', dal testo che trova qui"
)
OFF_IMAGE_REASON = (
    "Da leggere: il modello di PigroCRM e' spento, e una foto non ha testo che un agente possa "
    "leggere da qui"
)
NO_TEXT_REASON = (
    "Da leggere: il PDF non ha testo e il modello di PigroCRM e' spento, quindi il tuo agente "
    "non ha nulla da leggere da qui"
)
BUDGET_REASON = (
    "Da leggere: il modello legge pochi file per volta, e questo aspetta il prossimo giro"
)
INVALID_READING_REASON = "i campi letti dal modello non sono validi"
TOO_BIG_REASON = "Da leggere da una persona: il file e' troppo grande per il modello"
UNKNOWN_DOCUMENT_REASON = "Il modello non ha riconosciuto il documento"

_CREDIT_NOTES = frozenset({"note_credit"})


def _party(party: ParsedInvoiceParty) -> dict[str, Any]:
    return {"name": party.legal_name, "vat_number": party.vat_number, "tax_code": party.tax_code}


def _summary(invoice: ParsedInvoice) -> dict[str, Any]:
    return {
        "number": invoice.number,
        "issue_date": invoice.issue_date.isoformat(),
        "document_type": invoice.sdi_document_type,
        "currency": invoice.currency,
        "supplier": _party(invoice.supplier),
        "customer": _party(invoice.customer),
        "taxable_amount": str(invoice.taxable_amount),
        "vat_amount": str(invoice.vat_amount),
        "total": str(invoice.total),
    }


class InboxClassifier:
    def __init__(
        self,
        session: Session,
        storage: DocumentStorage,
        gateway: ModelGateway,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.clock = clock
        self.session = session
        self.storage = storage
        self.gateway = gateway
        self.reader = DocumentReader(gateway)

    # ---- entry points ---------------------------------------------------------------

    def classify(self, item_id: UUID, actor: Actor) -> InboxItemRead:
        """Reads one item once. Idempotent: an item that has a kind, or is not `new`, is
        returned as it is."""
        actor.require_write(CLASSIFY_ACTION)
        item = visible_item(self.session, item_id, actor)
        if item.state == "new" and item.kind is None and item.storage_key is not None:
            # A person asking for this one item gets it read however many times it was tried.
            self._guarded(item, actor, force=True)
        self.session.refresh(item)
        return attach_proposals(self.session, [InboxItemRead.model_validate(item)])[0]

    def classify_uploaded(
        self, uploaded: InboxUploadResult, actor: Actor, *, model_budget: int
    ) -> InboxUploadResult:
        """`classify_many` for the items an upload made, answered as the upload's result with
        what the classifier decided (kind, legal entity, the proposal)."""
        self.classify_many([i.id for i in uploaded.items], actor, model_budget=model_budget)
        reads = []
        for read in uploaded.items:
            item = self.session.get(InboxItem, read.id)
            assert item is not None
            self.session.refresh(item)
            reads.append(InboxItemRead.model_validate(item))
        return InboxUploadResult(items=attach_proposals(self.session, reads))

    def classify_pending(self, actor: Actor, *, limit: int) -> InboxItemPage:
        """The oldest items the actor sees that nobody has read yet and that a model could:
        what an upload left because it reads only so many files per request, or read while
        the model was off or down. Answers them as they are after this run."""
        actor.require_write(CLASSIFY_ACTION)
        clause = visible_to(actor)
        waiting = (
            InboxItem.state == "new",
            InboxItem.kind.is_(None),
            InboxItem.storage_key.is_not(None),
        )

        def pick(*extra: Any, cap: int) -> list[UUID]:
            stmt = select(InboxItem.id).where(*waiting, *extra)
            if clause is not None:
                stmt = stmt.where(clause)
            return list(
                self.session.execute(
                    stmt.order_by(
                        InboxItem.read_attempts, InboxItem.created_at, InboxItem.id
                    ).limit(cap)
                )
                .scalars()
                .all()
            )

        # What needs no model (a FatturaPA, a statement file uploaded before there was a
        # classifier) is classified whatever the budget; the model's files are read the least
        # tried first, and an item the model failed on `MAX_READ_ATTEMPTS` times is a person's
        # or an agent's, not another paid run's.
        ids = pick(InboxItem.file_type.not_in(MODEL_FILE_TYPES), cap=CHEAP_PER_RUN)
        ids += pick(
            InboxItem.file_type.in_(MODEL_FILE_TYPES),
            InboxItem.read_attempts < MAX_READ_ATTEMPTS,
            cap=limit,
        )
        self.classify_many(ids, actor, model_budget=limit)
        reads = []
        for item_id in ids:
            item = self.session.get(InboxItem, item_id)
            assert item is not None
            self.session.refresh(item)
            reads.append(InboxItemRead.model_validate(item))
        return InboxItemPage(
            items=attach_proposals(self.session, reads), next_cursor=None, total=len(reads)
        )

    def classify_many(self, item_ids: Sequence[UUID], actor: Actor, *, model_budget: int) -> None:
        """What an upload runs after its transaction: every certain item is classified, and at
        most `model_budget` items are sent to the model (a ZIP of two hundred receipts is not
        two hundred sequential calls inside one request); the rest stay unclassified and are
        read by a later run. A failure on one item does not stop the others, and never fails
        the upload that made them."""
        sent = 0
        started = self.clock()
        for item_id in item_ids:
            item = self.session.get(InboxItem, item_id)
            if item is None or item.state != "new" or item.kind is not None:
                continue
            over_budget = False
            if item.file_type in MODEL_FILE_TYPES:
                late = self.clock() - started >= REQUEST_DEADLINE_SECONDS
                if self.gateway.is_on() and (sent >= model_budget or late):
                    over_budget = True
                else:
                    sent += 1
            self._guarded(item, actor, over_budget=over_budget)

    def _guarded(
        self,
        item: InboxItem,
        actor: Actor,
        *,
        over_budget: bool = False,
        force: bool = False,
    ) -> None:
        """`_classify` with the item's reading claim released when it fails: an unexpected
        failure leaves the item unread, not claimed for the next ten minutes. Raises what it
        met only for the one item a person asked for."""
        try:
            self._classify(item, actor, over_budget=over_budget, force=force)
        except Exception as exc:
            self.session.rollback()
            self._release(item.id)
            logger.warning("inbox: reading %s failed: %s", item.id, type(exc).__name__)
            if force:
                raise

    def _release(self, item_id: UUID) -> None:
        self.session.execute(
            update(InboxItem).where(InboxItem.id == item_id).values(read_started_at=None)
        )
        self.session.commit()

    def _claim(self, item: InboxItem, *, force: bool) -> bool:
        """Takes the item for one paid reading: the claim is a committed timestamp, so a run
        that overlaps this one (an upload racing `POST /api/inbox/classify`) finds it taken
        and leaves it, and the file is paid for once. A claim older than `READ_CLAIM_MINUTES`
        is a run that died and is taken over."""
        stmt = (
            update(InboxItem)
            .where(
                InboxItem.id == item.id,
                InboxItem.state == "new",
                InboxItem.kind.is_(None),
                or_(
                    InboxItem.read_started_at.is_(None),
                    InboxItem.read_started_at < func.now() - timedelta(minutes=READ_CLAIM_MINUTES),
                ),
            )
            .values(read_started_at=func.now(), read_attempts=InboxItem.read_attempts + 1)
            .returning(InboxItem.id)
        )
        if not force:
            stmt = stmt.where(InboxItem.read_attempts < MAX_READ_ATTEMPTS)
        claimed = self.session.execute(stmt).first() is not None
        self.session.commit()
        if claimed:
            self.session.refresh(item)
        return claimed

    # ---- the dispatch ---------------------------------------------------------------

    def _classify(
        self, item: InboxItem, actor: Actor, *, over_budget: bool = False, force: bool = False
    ) -> None:
        assert item.storage_key is not None and item.file_type is not None
        content = self.storage.get(item.storage_key)
        file_type = item.file_type
        if file_type in ("xml", "p7m"):
            self._fatturapa(item, content)
        elif file_type in STATEMENT_TYPES:
            item.kind = "bank_statement"
            item.reason = STATEMENT_REASON
        elif file_type in ("pdf", "png", "jpeg"):
            self._free_text(item, content, actor, over_budget=over_budget, force=force)
            return  # `_free_text` commits where the model needs it
        self.session.commit()

    # ---- certain data ---------------------------------------------------------------

    def _legal_entities(self) -> list[LegalEntity]:
        return LegalEntityRepository(self.session).active_italian()

    def _fatturapa(self, item: InboxItem, content: bytes) -> None:
        document = content
        if item.file_type == "p7m":
            try:
                document = open_p7m(content)
            except P7mError as exc:  # upload already kept it as `other`; defensive
                item.kind, item.reason = "other", str(exc)
                return
        if not fatturapa_import.detect(document):
            item.kind, item.reason = "other", NOT_FATTURAPA_REASON
            return
        try:
            invoices = fatturapa_import.parse(document)
        except ValueError as exc:
            item.kind = "other"
            item.reason = f"Fattura elettronica non leggibile: {exc}"
            return
        item.parsed_fields = {"invoices": [_summary(invoice) for invoice in invoices]}

        legal_entities = self._legal_entities()
        try:
            suppliers = [match_legal_entity(i.supplier, legal_entities) for i in invoices]
            buyers = [match_legal_entity(i.customer, legal_entities) for i in invoices]
        except ValidationFailed as exc:
            # No legal entity has a tax id to compare with, or one file matches two: a person
            # fixes the data, and the file waits, certain of nothing.
            item.reason = exc.details.get("reason") or exc.message
            return
        sold = [s is not None for s in suppliers]
        if all(sold):
            owners = {s.id for s in suppliers if s is not None}
            if len(owners) == 1:
                item.kind = "sales_invoice"
                item.legal_entity_id = next(iter(owners))
                item.reason = SALES_REASON
            else:
                item.reason = MIXED_REASON
            return
        if any(sold):
            item.reason = MIXED_REASON
            return
        item.kind = (
            "credit_note"
            if all(i.sdi_document_type in _CREDIT_NOTES for i in invoices)
            else "supplier_invoice"
        )
        owners = {b.id for b in buyers if b is not None}
        if all(b is not None for b in buyers) and len(owners) == 1:
            item.legal_entity_id = next(iter(owners))
            item.reason = PURCHASE_REASON
        else:
            item.reason = UNKNOWN_BUYER_REASON

    # ---- free text ------------------------------------------------------------------

    def _free_text(
        self,
        item: InboxItem,
        content: bytes,
        actor: Actor,
        *,
        over_budget: bool,
        force: bool,
    ) -> None:
        assert item.file_type is not None
        media_type = MEDIA_TYPES[item.file_type]
        text: str | None = None
        if item.file_type == "pdf":
            # Postgres refuses a NUL in text, and a PDF's text can carry one.
            extracted = file_text(content, mime=PDF_TYPE, max_bytes=EXTRACTED_TEXT_MAX)
            text = extracted.text.replace("\x00", "").strip() or None
            item.extracted_text = text
        attachments: list[Attachment] = []
        if media_type in IMAGE_TYPES or (media_type == PDF_TYPE and text is None):
            if len(content) > MAX_MODEL_FILE_BYTES:
                item.reason = TOO_BIG_REASON
                self.session.commit()
                return
            attachments.append(Attachment(media_type, content))

        if over_budget:
            item.reason = BUDGET_REASON
            self.session.commit()
            return
        if not self.gateway.is_on():
            if item.file_type != "pdf":
                item.reason = OFF_IMAGE_REASON
            else:
                item.reason = OFF_REASON if text is not None else NO_TEXT_REASON
            self.session.commit()
            return
        self.session.commit()  # the gateway commits its own reservation: nothing may be pending
        if not self._claim(item, force=force):
            return  # another run is reading it, or it was tried too often for an automatic one

        try:
            read = self.reader.read(
                name=item.original_name,
                text=text,
                attachments=attachments,
                inbox_item_id=item.id,
            )
        except Unreadable as exc:
            self._unread(item, str(exc))
            return
        except DomainError as exc:  # the model off, over its cap, unavailable
            if isinstance(exc, ModelOff | ModelCapReached) or (
                isinstance(exc, ModelUnavailable) and not exc.maybe_billed
            ):
                # Nothing was paid and the model never read it: that is not a try.
                item.read_attempts = max(0, item.read_attempts - 1)
            self._unread(item, str(exc.details.get("reason") or exc.message))
            return
        if self._acted_on(item):
            return
        if read.kind == "other":
            item.kind = "other"
            item.reason = read.reason or UNKNOWN_DOCUMENT_REASON
            item.read_started_at = None
            self.session.commit()
            return
        item.read_started_at = None  # released by the commit of the proposal below
        try:
            ProposalService(self.session).create(
                ProposalCreate(
                    inbox_item_id=item.id,
                    target_type=read.kind,
                    proposed_fields=read.fields,
                    excerpt=read.excerpt or item.original_name,
                    excerpt_type=read.excerpt_type,
                    confidence=Decimal(read.confidence),
                    confidence_reason=read.confidence_reason,
                ),
                actor,
            )
        except (ValidationError, IntegrityError):
            # What the model wrote does not fit (a character a text column refuses, a field the
            # schema bounds): the sentence says so and carries none of it.
            self._unread_after_rollback(item.id, INVALID_READING_REASON)
        except DomainError as exc:
            self._unread_after_rollback(item.id, str(exc.details.get("reason") or exc.message))

    def _unread_after_rollback(self, item_id: UUID, sentence: str) -> None:
        self.session.rollback()
        item = self.session.get(InboxItem, item_id)
        if item is not None:
            self._unread(item, sentence)

    def _acted_on(self, item: InboxItem) -> bool:
        """Whether a person or an agent said something about the item while the model was reading
        it (discarded it, marked a copy, said what it is): what the model read then changes
        nothing, since the act is the later and the deliberate one (REB-985). The row is read
        again from the database, which the model's wait has left stale."""
        self.session.flush()  # a try not counted (read_attempts) is the caller's, and is kept
        self.session.refresh(item)
        return item.state != "new" or item.kind is not None

    def _unread(self, item: InboxItem, sentence: str) -> None:
        """The model could not give a reading: the item stays unclassified, with the sentence,
        and a later run (or a person, or an agent) reads it again. An item somebody acted on
        meanwhile is left as it is."""
        if self._acted_on(item):
            return
        item.reason = f"Lettura automatica non riuscita: {sentence}. Resta da leggere"
        item.read_started_at = None
        self.session.commit()
