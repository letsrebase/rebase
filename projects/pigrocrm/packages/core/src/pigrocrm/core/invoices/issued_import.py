"""`import_issued`: register a fattura another system issued (slice 9 §3)."""

from datetime import date
from uuid import UUID

from sqlalchemy.exc import IntegrityError

from pigrocrm.core.actor import Actor
from pigrocrm.core.customers.models import Customer
from pigrocrm.core.db.clock import today_local
from pigrocrm.core.db.transactions import savepoint
from pigrocrm.core.errors import Conflict, ValidationFailed
from pigrocrm.core.fiscal.schemas import FiscalSnapshot
from pigrocrm.core.invoices.checks import (
    check_accrual,
    check_declared_totals,
    check_import_date,
    check_transmission_date,
)
from pigrocrm.core.invoices.constants import ENTITY
from pigrocrm.core.invoices.context import InvoiceContext
from pigrocrm.core.invoices.models import Invoice, InvoiceLine
from pigrocrm.core.invoices.naming import number_complete
from pigrocrm.core.invoices.original_pdf import FetchedPdf, OriginalPdfResolver
from pigrocrm.core.invoices.schemas import (
    CURRENCY,
    SDI_DOCUMENT_TYPE,
    SNAPSHOT_VERSION,
    InvoiceImport,
    InvoiceRead,
    InvoiceSnapshot,
)
from pigrocrm.core.invoices.snapshot import build_snapshot
from pigrocrm.core.legal_entities.models import LegalEntity
from pigrocrm.core.text import PDF_MIME

IMPORT_ACTION = "import_issued_invoice"


def check_import_customer(
    ctx: InvoiceContext, customer_id: UUID, legal_entity: LegalEntity, name: str
) -> None:
    """The customer an imported invoice is attached to (REB-783, REB-784) has to be one
    it can be written for: on the azienda whose register it lands on, and not archived.
    `name` is the counterparty the message names. The dialog lists only such customers,
    but the server is the contract: without this an invoice would land on one azienda's
    register for another's customer, a state `legal_entity_of_parent` derives away on every
    other path. It runs for the FatturaPA confirmation and for `import_issued` itself, so
    the hand-declared import is held to it too. An unknown id stays `NotFound`."""
    customer = ctx.session.get(Customer, customer_id)
    if customer is None:
        return
    if customer.deleted_at is not None:
        raise ValidationFailed(
            ENTITY,
            "customer_id",
            f"il cliente «{name}» è archiviato",
            expected="un cliente non archiviato",
        )
    if customer.legal_entity_id != legal_entity.id:
        raise ValidationFailed(
            ENTITY,
            "customer_id",
            f"il cliente «{name}» appartiene a un'altra "
            f"azienda: la fattura è scritta su «{legal_entity.legal_name}»",
            expected=f"un cliente dell'azienda {legal_entity.id}",
        )


class IssuedInvoiceImporter:
    def __init__(self, ctx: InvoiceContext, original_pdf: OriginalPdfResolver) -> None:
        self.ctx = ctx
        self.original_pdf = original_pdf

    def import_issued(
        self,
        data: InvoiceImport,
        actor: Actor,
        *,
        legal_entity_id: UUID | None = None,
        xml_document_id: UUID | None = None,
        xml_hash_sha256: str | None = None,
        imported_from: str | None = None,
        commit: bool = True,
    ) -> InvoiceRead:
        """Register a fattura that another system issued (slice 9 §3), on the register
        of `legal_entity_id`, the default azienda when `None` (REB-619).

        Same lock, same snapshot, same lines as `issue`; the two differences are the
        whole feature. The number is *declared*, so the counter follows it instead of
        producing it (§3.2 rule 3). And the totals are *declared*, so they are checked
        against the lines to the cent instead of recomputed (§3.3): the document the
        customer holds is the fact, and this method refuses to record a different one.

        Order: pure checks on the input, then `lock_counter(azienda_id, anno)` -- the only
        lock on the register, and the first row lock unless the original PDF is an
        existing document, whose own row `OriginalPdfResolver` locks (REB-480) -- then
        every check that reads the register (duplicates, neighbours, gaps, native
        numbers), then the write. Nothing is consumed on failure: the counter is only
        ever raised to a number that is being written in the same transaction.

        **One transaction, Drive included.** With `pdf_source.drive_file_id` the bytes
        of the original PDF are read among the pure checks and become a `documents` row
        only after the invoice has been flushed, through
        `DocumentService.import_bytes(commit=False)`, so a refusal at any point leaves
        neither an invoice nor an orphan PDF. The bytes already written to storage are an
        accepted orphan if the commit then fails, exactly as in `add_version`.

        **Nothing is flushed before every refusal has had its chance.** The snapshot is
        built and the original PDF resolved *above* the lock, because both can fail (a
        missing `legal_entities`, a PDF belonging to another customer, a Drive file
        outside the configured roots). The writes then run in one SAVEPOINT: whatever
        they raise, the refused row and its lines vanish and the caller's other pending
        work stays.

        `commit=False` leaves the commit to the caller, which owns the transaction the
        import is one step of (`InvoiceImporter` confirming a batch).

        **`xml_document_id`/`xml_hash_sha256`, gated by the caller (design §5 item 3).**
        Both default to `None`, the state a hand-declared import leaves them in.
        `InvoiceImporter` is the one caller that passes real values, and only for a
        single-invoice source document: the row is pointed at the very `document_id` the
        bytes were read from, never a second `documents` row for the same content.

        **`imported_from`, gated by the caller for the same reason (design §5 item 4/§7
        item 6).** `None` means "use `data`'s own declared value", and
        `InvoiceImport.imported_from` is fixed to `"external"`. `InvoiceImporter` passes
        `"fatturapa"` here, deliberately not through `data`, which is also the schema
        `POST /api/invoices/import` and `import_issued_invoice` deserialise straight from
        caller-supplied JSON: letting a caller declare `"fatturapa"` there would be an
        unbacked claim, since nothing on that door supplies or checks an XML document.
        """
        actor.require_admin(IMPORT_ACTION)
        ctx = self.ctx
        customer = ctx.check_owner(data.customer_id, data.deal_id)
        self._check_declared(data, today=today_local())
        # Read-only, above the lock: the assignment happens after the row exists. The
        # Drive branch reads bytes over HTTP here too: a network read is not a database
        # write, and "the file is outside the configured roots" or "it is not a PDF" are
        # facts about the caller's input.
        pdf_source = (
            self.original_pdf.resolve(data.customer_id, data.pdf_source, actor)
            if data.pdf_source is not None
            else None
        )
        # All of these can refuse -- `resolve` when the azienda does not exist, `regime`
        # when no fiscal profile is configured, `build_snapshot` when no emitter profile
        # is -- and none writes anything. The azienda is named by the caller, or the one
        # the customer's chain gives, as `create` does (REB-623).
        legal_entity = ctx.writing_legal_entity(
            legal_entity_id
            if legal_entity_id is not None
            else ctx.legal_entity_of_parent(customer, data.deal_id)
        )
        check_import_customer(ctx, data.customer_id, legal_entity, customer.legal_name)
        _, profile = ctx.regime(legal_entity.id)
        snapshot = build_snapshot(ctx, data.customer_id, profile, actor, legal_entity.id)
        counter = ctx.repo.lock_counter(legal_entity.id, data.year)
        self._check_register_rules(legal_entity.id, data)

        def concurrent_number(exc: IntegrityError) -> Conflict:
            return Conflict(
                ENTITY,
                "un altro processo ha scritto lo stesso numero: riprova e verifica il registro",
                year=data.year,
                number=data.number,
            )

        # From here on the transaction writes. Everything above was a read.
        with savepoint(ctx.session, concurrent_number):
            invoice = self._add_invoice(
                data,
                legal_entity.id,
                profile,
                snapshot,
                xml_document_id=xml_document_id,
                xml_hash_sha256=xml_hash_sha256,
                imported_from=imported_from,
            )
            self._add_lines(invoice, data)
            if pdf_source is not None:
                invoice.pdf_document_id = self._original_pdf_document(
                    invoice, data, pdf_source, actor
                )
            counter.last_number = max(counter.last_number, data.number)
            ctx.activities.record(
                ENTITY,
                invoice.id,
                "imported",
                actor,
                {
                    "year": data.year,
                    "number": data.number,
                    "total": str(data.total),
                    "imported_from": data.imported_from,
                },
            )
        if commit:
            ctx.session.commit()
        return ctx.get(invoice.id)

    def _check_declared(self, data: InvoiceImport, *, today: date) -> None:
        """The refusals that are facts about the input alone."""
        check_import_date(data.issue_date, today)
        check_accrual(data.accrual_from, data.accrual_to)
        check_declared_totals(data)
        if data.year != data.issue_date.year:
            raise ValidationFailed(
                ENTITY,
                "year",
                "l'anno del registro deve essere quello della data di emissione",
                expected=f"anno = {data.issue_date.year}",
            )
        if data.payment_status == "paid" and data.payment_date is None:
            raise ValidationFailed(
                ENTITY,
                "payment_date",
                "un incasso senza data non e' un incasso",
                expected="la data in cui il pagamento e' arrivato",
            )
        if data.payment_status != "paid" and data.payment_date is not None:
            raise ValidationFailed(
                ENTITY,
                "payment_date",
                "una data di incasso senza incasso non ha senso",
                expected="stato_pagamento = incassato, oppure nessuna data",
            )
        # The same two facts `mark_transmitted_externally` checks, and it has to be here:
        # the column is immutable once written and it is what `annul` reads to decide
        # whether a correction is still possible.
        if data.transmitted_externally_at is not None:
            check_transmission_date(data.transmitted_externally_at, data.issue_date, today)

    def _check_register_rules(self, legal_entity_id: UUID, data: InvoiceImport) -> None:
        """The rules that read the register, valid only under the counter lock."""
        repo = self.ctx.repo
        if data.number in repo.numbers_present(legal_entity_id, data.year):
            raise Conflict(
                ENTITY,
                "il registro porta gia' questo numero",
                year=data.year,
                number=data.number,
            )
        if data.number in repo.declared_gaps(legal_entity_id, data.year):
            raise Conflict(
                ENTITY,
                "questo numero e' dichiarato come buco del registro: togli la dichiarazione "
                "prima di importarlo",
                year=data.year,
                number=data.number,
            )
        first_native = repo.first_native_number(legal_entity_id, data.year)
        if first_native is not None and data.number > first_native:
            raise Conflict(
                ENTITY,
                "PigroCRM ha gia' emesso fatture in questo anno: si importa solo lo storico "
                "precedente alla prima emessa qui",
                year=data.year,
                number=data.number,
                before_nativa=first_native,
            )
        before, after = repo.neighbour_dates(legal_entity_id, data.year, data.number)
        if before is not None and data.issue_date < before:
            raise ValidationFailed(
                ENTITY,
                "issue_date",
                "il registro deve restare cronologico: il numero precedente porta la data "
                f"{before.isoformat()}",
                expected=f"una data dal {before.isoformat()} in poi",
            )
        if after is not None and data.issue_date > after:
            raise ValidationFailed(
                ENTITY,
                "issue_date",
                "il registro deve restare cronologico: il numero successivo porta la data "
                f"{after.isoformat()}",
                expected=f"una data fino al {after.isoformat()}",
            )

    def _add_invoice(
        self,
        data: InvoiceImport,
        legal_entity_id: UUID,
        profile: FiscalSnapshot,
        snapshot: InvoiceSnapshot,
        *,
        xml_document_id: UUID | None,
        xml_hash_sha256: str | None,
        imported_from: str | None,
    ) -> Invoice:
        ctx = self.ctx
        return ctx.repo.add(
            Invoice(
                customer_id=data.customer_id,
                deal_id=data.deal_id,
                legal_entity_id=legal_entity_id,
                type="invoice",
                status="issued",
                year=data.year,
                number=data.number,
                issue_date=data.issue_date,
                # The original's date when declared; otherwise the same terms a native
                # emission would apply (REB-326), never a second arithmetic.
                due_date=data.due_date
                or ctx.due_date_from_terms(data.issue_date, data.customer_id, profile.due_days),
                sdi_document_type=SDI_DOCUMENT_TYPE,
                currency=CURRENCY,
                memo=data.memo,
                accrual_from=data.accrual_from,
                accrual_to=data.accrual_to,
                taxable_amount=data.taxable_amount,
                vat_amount=data.vat_amount,
                stamp_duty=data.stamp_duty,
                total=data.total,
                payment_status=data.payment_status,
                payment_date=data.payment_date,
                transmitted_externally_at=data.transmitted_externally_at,
                internal_notes=data.internal_notes,
                imported_from=imported_from if imported_from is not None else data.imported_from,
                xml_document_id=xml_document_id,
                xml_hash_sha256=xml_hash_sha256,
                snapshot=snapshot.model_dump(mode="json"),
                snapshot_version=SNAPSHOT_VERSION,
                custom_fields={},
            )
        )

    def _add_lines(self, invoice: Invoice, data: InvoiceImport) -> None:
        for index, riga in enumerate(data.lines, start=1):
            self.ctx.repo.add_line(
                InvoiceLine(
                    invoice_id=invoice.id,
                    line_number=index,
                    description=riga.description,
                    quantity=riga.quantity,
                    unit_of_measure=riga.unit_of_measure,
                    unit_price=riga.unit_price,
                    total_price=riga.total_price,
                    vat_rate=riga.vat_rate,
                    vat_nature=riga.vat_nature,
                    legal_reference=riga.legal_reference,
                )
            )

    def _original_pdf_document(
        self, invoice: Invoice, data: InvoiceImport, pdf: UUID | FetchedPdf, actor: Actor
    ) -> UUID:
        """The `documents` row that is the invoice's original PDF."""
        documents = self.ctx.documents
        if isinstance(pdf, UUID):
            # The original must already sit with the invoice's azienda: a document of
            # another azienda (the customer's current one, after a move) named as this
            # invoice's PDF would be readable, and replaceable, by members who cannot see
            # the invoice. Here rather than among the pure checks, since the azienda is
            # resolved after them.
            original = documents.repo.get(pdf)
            if original is not None and original.legal_entity_id != invoice.legal_entity_id:
                raise ValidationFailed(
                    ENTITY,
                    "pdf_source",
                    "il documento appartiene a un'altra azienda",
                    expected=f"un documento dell'azienda {invoice.legal_entity_id}",
                )
            return pdf
        # `commit=False`: the `documents` row belongs to *this* transaction, so a failure
        # later in it leaves no orphan PDF filed against a customer.
        return documents.import_bytes(
            customer_id=invoice.customer_id,
            type="invoice",
            title=f"Fattura {number_complete(data.year, data.number)} (originale)",
            data=pdf.content,
            content_type=PDF_MIME,
            actor=actor,
            # The invoice's azienda, the one the file landed on (spec §1.5), and never the
            # customer's current one.
            legal_entity_id=invoice.legal_entity_id,
            # `DriveReader.read_bytes` answers with the bytes and the mime and no name, so
            # the id is the provenance: a name is neither unique nor stable.
            origin={"drive_file_id": pdf.drive_file_id, "mime": pdf.mime},
            commit=False,
        ).id
