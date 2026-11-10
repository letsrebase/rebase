"""`InvoiceService`: the one door to `invoices` and `invoice_lines`.

A facade over the collaborators that hold the logic, each in its own module and all
working on one `InvoiceContext` (one session, one transaction):

* `drafting.InvoiceDrafting`: create, update, lines, proforma, delete, payment state;
* `emission.InvoiceEmission`: `issue`, `annul`, `mark_transmitted_externally`;
* `register.RegisterGaps`: the numbers a register will never carry;
* `importing.InvoiceImporter` and `issued_import.IssuedInvoiceImporter`: reviewing and
  confirming FatturaPA files, and registering an invoice another system issued;
* `original_pdf.OriginalPdfResolver`: where an imported invoice's original PDF is;
* `artifacts.InvoiceArtifacts`: the PDF and the FatturaPA file.

The constructor and the public methods are the domain's API for the REST app, the MCP
server and the other cores (`work_units`, `analytics`, ...); nothing here decides.
"""

import datetime as dt
import logging
from collections.abc import Callable, Mapping, Sequence
from decimal import Decimal
from typing import NamedTuple
from uuid import UUID

from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.config import Settings
from pigrocrm.core.drive.reader import DriveReader
from pigrocrm.core.invoices.artifacts import InvoiceArtifacts
from pigrocrm.core.invoices.constants import ENTITY
from pigrocrm.core.invoices.context import InvoiceContext
from pigrocrm.core.invoices.drafting import InvoiceDrafting
from pigrocrm.core.invoices.emission import InvoiceEmission
from pigrocrm.core.invoices.import_confirm import ConfirmedInvoiceRead
from pigrocrm.core.invoices.import_review import ReviewedInvoiceRead
from pigrocrm.core.invoices.import_upload import (
    UploadedInvoiceConfirmRead,
    UploadedInvoiceReviewRead,
    UploadedPartyDecision,
)
from pigrocrm.core.invoices.importing import InvoiceImporter
from pigrocrm.core.invoices.issued_import import IssuedInvoiceImporter
from pigrocrm.core.invoices.original_pdf import OriginalPdfResolver
from pigrocrm.core.invoices.register import RegisterGaps
from pigrocrm.core.invoices.schemas import (
    ArtifactKind,
    InvoiceAnnul,
    InvoiceArtifact,
    InvoiceCreate,
    InvoiceImport,
    InvoiceIssue,
    InvoiceLineIn,
    InvoiceLineRead,
    InvoiceListQuery,
    InvoicePage,
    InvoiceRead,
    InvoiceTransmitted,
    InvoiceUpdate,
    PaymentState,
    RegisterGapRead,
    RegisterGapsDeclare,
)
from pigrocrm.core.storage.base import DocumentStorage

logger = logging.getLogger(__name__)


class UnpaidInvoiceRead(NamedTuple):
    """An invoice read with what is still owed on it and when it falls due."""

    invoice: InvoiceRead
    remaining: Decimal
    due_date: dt.date | None


class InvoiceService:
    def __init__(
        self,
        session: Session,
        storage: DocumentStorage,
        settings: Settings | None = None,
        *,
        drive_reader_factory: Callable[[Actor], DriveReader] | None = None,
    ) -> None:
        self.ctx = InvoiceContext(
            session, storage, settings, drive_reader_factory=drive_reader_factory
        )
        # The collaborators the callers (and the tests) reach through the facade.
        self.session = session
        self.storage = storage
        self.settings = self.ctx.settings
        self.repo = self.ctx.repo
        self.documents = self.ctx.documents
        self.fiscal = self.ctx.fiscal
        self.legal_entity = self.ctx.legal_entity
        self.organizations = self.ctx.organizations
        self.fields = self.ctx.fields
        self.activities = self.ctx.activities
        self.drafting = InvoiceDrafting(self.ctx)
        self.register = RegisterGaps(self.ctx)
        self.emission = InvoiceEmission(self.ctx, self.register)
        self.artifacts = InvoiceArtifacts(self.ctx)
        self.issued_import = IssuedInvoiceImporter(self.ctx, OriginalPdfResolver(self.ctx))
        self.importer = InvoiceImporter(self.ctx, self.issued_import)

    # ---- drafts and proformas -------------------------------------------------

    def create(
        self,
        data: InvoiceCreate,
        actor: Actor,
        *,
        legal_entity_id: UUID | None = None,
        commit: bool = True,
    ) -> InvoiceRead:
        return self.drafting.create(data, actor, legal_entity_id=legal_entity_id, commit=commit)

    def update(self, invoice_id: UUID, data: InvoiceUpdate, actor: Actor) -> InvoiceRead:
        return self.drafting.update(invoice_id, data, actor)

    def replace_lines(
        self, invoice_id: UUID, lines: list[InvoiceLineIn], actor: Actor
    ) -> InvoiceRead:
        return self.drafting.replace_lines(invoice_id, lines, actor)

    def confirm_proforma(self, invoice_id: UUID, actor: Actor) -> InvoiceRead:
        return self.drafting.confirm_proforma(invoice_id, actor)

    def soft_delete(self, invoice_id: UUID, actor: Actor) -> None:
        self.drafting.soft_delete(invoice_id, actor)

    def set_payment_state(self, invoice_id: UUID, data: PaymentState, actor: Actor) -> InvoiceRead:
        return self.drafting.set_payment_state(invoice_id, data, actor)

    # ---- emission -------------------------------------------------------------

    def issue(self, invoice_id: UUID, data: InvoiceIssue, actor: Actor) -> InvoiceRead:
        return self.emission.issue(invoice_id, data, actor)

    def issue_and_render(self, invoice_id: UUID, data: InvoiceIssue, actor: Actor) -> InvoiceRead:
        """`issue`, then the artefacts in the second transaction `issue` leaves to its
        caller, then the row as it stands.

        Once `issue` has committed the number is consumed and the invoice is a fiscal
        fact, so the answer is the issued row whatever the render does (REB-143): an
        error here would tell the person (or the agent, which would then issue it again)
        that the emission failed. A render that raises -- anything, a Typst crash or a
        storage backend that refused the bytes as much as a `DomainError` -- is logged
        and rolled back, which clears what it left half-flushed so the read below starts
        on a clean session. `pdf_document_id`/`xml_document_id` on the answer say which
        file exists, and `produce_artifacts` is the retry. The rollback is safe because
        the emission it follows is already committed."""
        issued = self.issue(invoice_id, data, actor)
        try:
            self.produce_artifacts(issued.id, actor)
        except Exception:
            logger.exception("invoice %s issued, its PDF/XML were not produced", issued.id)
            self.session.rollback()
        return self.get(issued.id, actor)

    def annul(self, invoice_id: UUID, data: InvoiceAnnul, actor: Actor) -> InvoiceRead:
        return self.emission.annul(invoice_id, data, actor)

    def mark_transmitted_externally(
        self, invoice_id: UUID, data: InvoiceTransmitted, actor: Actor
    ) -> InvoiceRead:
        return self.emission.mark_transmitted_externally(invoice_id, data, actor)

    # ---- the register ---------------------------------------------------------

    def declare_gaps(
        self,
        year: int,
        data: RegisterGapsDeclare,
        actor: Actor,
        legal_entity_id: UUID | None = None,
    ) -> list[RegisterGapRead]:
        return self.register.declare_gaps(year, data, actor, legal_entity_id)

    def register_gaps(
        self, year: int, actor: Actor, legal_entity_id: UUID | None = None
    ) -> list[RegisterGapRead]:
        return self.register.register_gaps(year, actor, legal_entity_id)

    def undeclared_gaps(self, year: int, legal_entity_id: UUID | None = None) -> list[int]:
        return self.register.undeclared_gaps(year, legal_entity_id)

    # ---- import ---------------------------------------------------------------

    def import_issued(
        self,
        data: InvoiceImport,
        actor: Actor,
        *,
        legal_entity_id: UUID | None = None,
        xml_document_id: UUID | None = None,
        xml_hash_sha256: str | None = None,
        imported_from: str | None = None,
    ) -> InvoiceRead:
        return self.issued_import.import_issued(
            data,
            actor,
            legal_entity_id=legal_entity_id,
            xml_document_id=xml_document_id,
            xml_hash_sha256=xml_hash_sha256,
            imported_from=imported_from,
        )

    def review_import(
        self, document_ids: Sequence[UUID], actor: Actor
    ) -> list[ReviewedInvoiceRead]:
        return self.importer.review_import(document_ids, actor)

    def confirm_import(
        self,
        document_id: UUID,
        actor: Actor,
        *,
        customer_id: UUID | None = None,
        create_organization: bool = False,
    ) -> list[ConfirmedInvoiceRead]:
        return self.importer.confirm_import(
            document_id, actor, customer_id=customer_id, create_organization=create_organization
        )

    def review_upload(
        self, files: Sequence[tuple[str, bytes]], actor: Actor
    ) -> list[UploadedInvoiceReviewRead]:
        return self.importer.review_upload(files, actor)

    def confirm_upload(
        self,
        files: Sequence[tuple[str, bytes]],
        decisions: Mapping[str, UploadedPartyDecision],
        actor: Actor,
    ) -> list[UploadedInvoiceConfirmRead]:
        return self.importer.confirm_upload(files, decisions, actor)

    # ---- artefacts ------------------------------------------------------------

    def export_xml(self, invoice_id: UUID, actor: Actor) -> InvoiceArtifact:
        return self.artifacts.export_xml(invoice_id, actor)

    def produce_artifacts(self, invoice_id: UUID, actor: Actor) -> list[InvoiceArtifact]:
        return self.artifacts.produce_artifacts(invoice_id, actor)

    def download(
        self, invoice_id: UUID, kind: ArtifactKind, actor: Actor
    ) -> tuple[bytes, str, str]:
        return self.artifacts.download(invoice_id, kind, actor)

    # ---- reads ----------------------------------------------------------------

    def get(self, invoice_id: UUID, actor: Actor) -> InvoiceRead:
        return self.ctx.get(invoice_id)

    def lines(self, invoice_id: UUID, actor: Actor) -> list[InvoiceLineRead]:
        self.ctx.require(invoice_id)
        return [InvoiceLineRead.model_validate(r) for r in self.ctx.repo.lines(invoice_id)]

    def unpaid_for_customer(
        self, customer_id: UUID, actor: Actor, *, limit: int
    ) -> list[UnpaidInvoiceRead]:
        """One customer's issued invoices with something left to collect, oldest deadline
        first: the rows behind the receivables total, for one customer, each with what is
        still owed on it and the date it falls due."""
        rows = self.ctx.repo.unpaid_for_customer(customer_id, limit)
        reads = self.ctx.reads([row.invoice for row in rows])
        return [
            UnpaidInvoiceRead(invoice, row.remaining, row.due_date)
            for invoice, row in zip(reads, rows, strict=True)
        ]

    # `list` must stay the last method defined in this class -- an unconditional
    # project rule (`test_module_imports.py`). `lines` and `unpaid_for_customer` above
    # return `list[...]`, so they must be defined before this point or their annotations
    # resolve `list` to this method and fail at import on Python 3.13.
    def list(self, query: InvoiceListQuery, actor: Actor) -> InvoicePage:
        rows = self.ctx.repo.list(query)
        has_more = len(rows) > query.limit
        items = rows[: query.limit]
        return InvoicePage(
            items=self.ctx.reads(items),
            next_cursor=items[-1].id if has_more and items else None,
        )


__all__ = ["ENTITY", "InvoiceService", "UnpaidInvoiceRead"]
