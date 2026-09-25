from uuid import UUID

from sqlalchemy import ColumnElement, desc, func, select
from sqlalchemy.orm import Session

from pigrocrm.core.dashboard.schemas import PendingOffer
from pigrocrm.core.db import (
    decode_cursor,
    escape_like,
    keyset_predicate,
    order_by,
    today_local,
)
from pigrocrm.core.deals.models import Deal
from pigrocrm.core.documents.models import Document, DocumentVersion
from pigrocrm.core.documents.schemas import DOCUMENT_SORTS, DocumentListQuery
from pigrocrm.core.invoices.models import Invoice
from pigrocrm.core.pipeline.models import PipelineStage


def _accepted_with_unwon_deal_predicate() -> tuple[ColumnElement[bool], ...]:
    """§6.2's first signal, as one predicate used by both the count and the list.

    Extracted rather than written twice because §7.2's guarantee -- "the card and its
    drill-through are the same query, not two calculations" -- is only true if the
    predicate is literally the same. Two hand-copied predicates agree until one is edited,
    and then the dashboard and the list disagree about the same rows with nothing to say
    which of them is right.

    `PipelineStage.tipo != "won"` and not `== "open"`: an accepted offer on a *lost* deal
    is the inconsistency too, and the two spellings differ only on that row.

    Returned as a tuple of clauses so the caller can splat it into `where(...)` alongside
    its own; the joins are the caller's, because a `COUNT` and a paginated `SELECT` build
    them differently. Both callers use the same **inner** joins, which is what makes an
    accepted offer filed against a customer rather than a deal absent from both sides with
    no special case written anywhere.
    """
    return (
        Document.deleted_at.is_(None),
        Document.tipo == "offerta",
        Document.stato == "accettata",
        Deal.deleted_at.is_(None),
        PipelineStage.tipo != "won",
    )


class DocumentRepository:
    """A repository never commits (project rule): every method here either reads or
    flushes, and the surrounding `DocumentService` method is always the one
    transaction."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, document_id: UUID, *, include_deleted: bool = False) -> Document | None:
        document = self.session.get(Document, document_id)
        if document is None:
            return None
        if document.deleted_at is not None and not include_deleted:
            return None
        return document

    def add(self, document: Document) -> Document:
        self.session.add(document)
        self.session.flush()
        return document

    def reassign_to_contract(self, document: Document, contract_id: UUID) -> Document:
        """Re-points a document's ownership at a contract, clearing whichever of
        `customer_id`/`deal_id` it held before -- the accept half of REB-358 §11's
        widened three-way ownership (`ck_documents_customer_xor_deal`), read by
        `ProposalService._accept_contratto` in the same transaction that creates the
        contract itself (spec §10's own "widen at accept time, not at archive time"
        ordering). Flushes -- and only flushes -- like every other method here: the
        surrounding service is the transaction."""
        document.customer_id = None
        document.deal_id = None
        document.contract_id = contract_id
        self.session.flush()
        return document

    def add_version(self, version: DocumentVersion) -> DocumentVersion:
        """Flushes -- and only flushes -- so the caller can observe (and, on the
        unique `(document_id, numero)` constraint, catch) the outcome before deciding
        whether to touch storage at all. See `DocumentService.add_version`."""
        self.session.add(version)
        self.session.flush()
        return version

    def version(self, document_id: UUID, numero: int) -> DocumentVersion | None:
        stmt = select(DocumentVersion).where(
            DocumentVersion.document_id == document_id, DocumentVersion.numero == numero
        )
        return self.session.execute(stmt).scalars().first()

    def versions(self, document_id: UUID) -> list[DocumentVersion]:
        stmt = (
            select(DocumentVersion)
            .where(DocumentVersion.document_id == document_id)
            .order_by(desc(DocumentVersion.numero))
        )
        return list(self.session.execute(stmt).scalars())

    def lock(self, document_id: UUID) -> Document | None:
        """The row, locked `FOR NO KEY UPDATE` until this transaction ends and read
        fresh, soft-deleted or not (REB-480).

        What serialises a version upload with `InvoiceService.import_issued` linking the
        same document as an invoice's PDF: each takes this lock before it reads what the
        other writes, so the second one waits for the first to commit and then reads its
        outcome. `NO KEY UPDATE` is the lock an `UPDATE` of `versione_corrente` takes
        anyway, and it leaves alone the `KEY SHARE` a foreign key from `invoices` takes.
        `populate_existing` because the session keeps objects across commits
        (`expire_on_commit=False`): a row loaded before the wait would otherwise answer
        with what it held then.
        """
        stmt = (
            select(Document)
            .where(Document.id == document_id)
            .with_for_update(key_share=True)
            .execution_options(populate_existing=True)
        )
        return self.session.execute(stmt).scalar_one_or_none()

    def invoice_tipo_of_pdf(self, document_id: UUID) -> str | None:
        """The `tipo` (`fattura` or `proforma`) of the invoice row that names this
        document as its PDF, a soft-deleted one included, or `None` when no invoice does.
        See `DocumentService._check_invoice_pdf` for why that is the test."""
        stmt = select(Invoice.tipo).where(Invoice.pdf_document_id == document_id).limit(1)
        return self.session.execute(stmt).scalar_one_or_none()

    def pending_offers(self, limit: int = 20) -> list[PendingOffer]:
        """Sent offers still awaiting an answer, oldest first, with their age in days.

        The age is computed in Python from `today_local()` rather than in SQL from
        `CURRENT_DATE`: `CURRENT_DATE` is the *server's* day, and every date in this
        product is a day in the emitter's zone (`db/clock.py`). On a UTC database at 00:30
        Rome time the two differ, and a dashboard showing "ferma da 0 giorni" for something
        sent yesterday is worse than showing nothing.
        """
        today = today_local()
        rows = self.session.execute(
            select(Document)
            .where(
                Document.deleted_at.is_(None),
                Document.tipo == "offerta",
                Document.stato == "inviata",
            )
            # Nulls last: an offer with no known start date is not the oldest one, and a
            # list meant to be worked from the top must not open with the least
            # informative row. Postgres already defaults an ASC sort to NULLS LAST, so
            # this is spelled out rather than relied upon -- the default flips with the
            # direction (DESC defaults to NULLS FIRST), and an ordering that changes
            # meaning when someone reverses it is an ordering nobody can reason about.
            .order_by(Document.stato_dal.asc().nulls_last(), Document.id.asc())
            .limit(limit)
        ).scalars()
        return [
            PendingOffer(
                document_id=str(row.id),
                titolo=row.titolo,
                deal_id=str(row.deal_id) if row.deal_id else None,
                customer_id=str(row.customer_id) if row.customer_id else None,
                stato_dal=row.stato_dal,
                giorni=(today - row.stato_dal).days if row.stato_dal is not None else None,
            )
            for row in rows
        ]

    def count_pending_offers(self) -> int:
        """The real total behind `pending_offers`'s truncated list, so a dashboard showing
        twenty of ninety says ninety."""
        return (
            self.session.scalar(
                select(func.count(Document.id)).where(
                    Document.deleted_at.is_(None),
                    Document.tipo == "offerta",
                    Document.stato == "inviata",
                )
            )
            or 0
        )

    def count_accepted_with_unwon_deal(self) -> int:
        """§6.2's first signal: accepted offers whose deal is not in a `won` stage.

        This is the case where automation A1 did **not** fire -- switched off, or declined
        with a recorded reason -- so it is the automation's permanent cross-check: if the
        automation goes quiet, this count speaks. It sits on the *commercial* dashboard
        because it needs no invoices, which is what lets it ship in the same sub-plan as
        the automation it verifies rather than one later (§17).

        A `COUNT` across a join, which §3 permits explicitly: it looks at two tables and
        produces no money figure. A `SUM` across one is how the same row gets counted
        twice, and on a margin nobody notices.
        """
        return (
            self.session.scalar(
                select(func.count(Document.id))
                .join(Deal, Deal.id == Document.deal_id)
                .join(PipelineStage, PipelineStage.id == Deal.pipeline_stage_id)
                .where(*_accepted_with_unwon_deal_predicate())
            )
            or 0
        )

    # `list` is defined LAST in this class on purpose: `def list(...)` rebinds `list` in
    # the class namespace, and Python 3.13 evaluates annotations eagerly, so a later
    # `-> list[...]` would raise `TypeError` at import time.
    def list(self, query: DocumentListQuery) -> list[Document]:
        stmt = select(Document).where(Document.deleted_at.is_(None))
        if query.customer_id:
            stmt = stmt.where(Document.customer_id == query.customer_id)
        if query.deal_id:
            stmt = stmt.where(Document.deal_id == query.deal_id)
        if query.contract_id:
            stmt = stmt.where(Document.contract_id == query.contract_id)
        if query.tipo:
            stmt = stmt.where(Document.tipo == query.tipo)
        if query.stato:
            stmt = stmt.where(Document.stato == query.stato)
        if query.search:
            # New in slice 6. One column, so no `or_`: spec §8.1 searches `titolo` and
            # nothing else on this table -- a document's body lives in storage, not in
            # a column, and its Markdown source is explicitly out of scope.
            #
            # `escape_like` neutralises "%"/"_"/"\" in the *user's* term before it is
            # wrapped in the "%...%" this method builds; `escape="\\"` states which
            # character it used rather than relying on ILIKE's default. Task A2
            # measured that the ESCAPE clause costs `ix_documents_titolo_trgm` nothing
            # -- the planner folds it into the same constant pattern -- so it stays
            # here exactly as in the other three repositories.
            like = f"%{escape_like(query.search.lower())}%"
            stmt = stmt.where(Document.titolo.ilike(like, escape="\\"))
        if query.solo_deal_non_vinto:
            # The drill-through of §6.2's signal card, sharing its predicate literally
            # rather than restating it -- see `_accepted_with_unwon_deal_predicate`.
            # An **additional** predicate on the statement built above, never a
            # replacement for it: `tipo` and `stato` supplied alongside it still narrow
            # the result, because a drill-through that silently dropped the caller's own
            # filters would be answering a different question from the one asked.
            # Inner joins, so an offer with no deal simply has no matching row -- no
            # special case needed, and none written. Both are many-to-one, so no document
            # is returned twice.
            stmt = stmt.join(Deal, Deal.id == Document.deal_id).join(
                PipelineStage, PipelineStage.id == Deal.pipeline_stage_id
            )
            stmt = stmt.where(*_accepted_with_unwon_deal_predicate())

        # Residuo R9 -- see `CustomerRepository.list` for the reasoning.
        spec = DOCUMENT_SORTS.resolve(query.sort)
        if query.cursor:
            value, row_id = decode_cursor(spec, query.cursor)
            stmt = stmt.where(keyset_predicate(spec, query.dir, value, row_id))

        return list(
            self.session.execute(
                stmt.order_by(*order_by(spec, query.dir)).limit(query.limit + 1)
            ).scalars()
        )
