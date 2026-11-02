"""Reviewing and confirming FatturaPA files onto the register (REB-365, REB-366, REB-758).

The write of one invoice is `IssuedInvoiceImporter.import_issued`, the same call a
hand-declared import makes: never a second, independently maintained set of register
rules (design §5).
"""

import hashlib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from uuid import UUID

from pydantic import ValidationError as PydanticValidationError

from pigrocrm.core.actor import Actor
from pigrocrm.core.emitter.models import LegalEntity
from pigrocrm.core.errors import Conflict, NotFound, ValidationFailed
from pigrocrm.core.invoices.context import InvoiceContext
from pigrocrm.core.invoices.import_classification import classify_parsed_invoice
from pigrocrm.core.invoices.import_confirm import (
    ConfirmedInvoice,
    ConfirmedInvoiceRead,
    map_parsed_invoice_to_import,
    map_parsed_party_to_customer,
)
from pigrocrm.core.invoices.import_review import (
    ReviewedInvoiceRead,
    detect_adapter,
    match_customer,
    natural_key,
    register_lookup,
    review_content,
    review_invoices,
)
from pigrocrm.core.invoices.import_schemas import ParsedInvoice, ParsedInvoiceParty
from pigrocrm.core.invoices.import_upload import (
    UploadedInvoiceConfirmRead,
    UploadedInvoiceReviewRead,
    UploadedPartyDecision,
    expand_uploads,
    party_key,
)
from pigrocrm.core.invoices.issued_import import IssuedInvoiceImporter, check_import_customer
from pigrocrm.core.invoices.naming import numero_completo
from pigrocrm.core.invoices.register import holes
from pigrocrm.core.invoices.schemas import InvoiceRead

REVIEW_ACTION = "review_invoice_import"
CONFIRM_ACTION = "confirm_invoice_import"

# What a confirmation asks about the counterparty of an invoice: the customer id to
# attach, whether to create the counterparty when none matches, and whether to leave the
# invoice out altogether.
CustomerDecision = Callable[[ParsedInvoiceParty], tuple[UUID | None, bool, bool]]


class _Refused(Exception):
    """An invoice the register refused: its savepoint is rolled back, and the request
    carries on with the next one."""


class _GapTracker:
    """The undeclared holes of each register touched by one confirmation, read once.

    Each imported invoice reports the holes its register still has after it. Reloading
    the whole year's number set for every invoice made a batch quadratic in the register;
    here a (azienda, anno) register is loaded on first use and then follows the batch,
    adding each number as its invoice is written, which gives every row the same answer a
    fresh read would.
    """

    def __init__(self, ctx: InvoiceContext) -> None:
        self._ctx = ctx
        self._registers: dict[tuple[UUID, int], tuple[set[int], set[int]]] = {}

    def holes_after(self, azienda_id: UUID, anno: int, numero: int) -> list[int]:
        key = (azienda_id, anno)
        register = self._registers.get(key)
        if register is None:
            repo = self._ctx.repo
            register = (
                repo.numbers_present(azienda_id, anno),
                repo.declared_gaps(azienda_id, anno),
            )
            self._registers[key] = register
        present, declared = register
        present.add(numero)
        return holes(present, declared)


@dataclass
class _Batch:
    """What outlives one file of a confirmation request: `created_customers`, keyed by
    the azienda and the `party_key`, so a counterparty created for one invoice is the
    customer of every later invoice of it in the request, and the gap tracker."""

    gaps: _GapTracker
    created_customers: dict[tuple[UUID, str], UUID] = field(default_factory=dict)


@dataclass(frozen=True)
class _File:
    """One file's bytes and where they came from. `stored_as` is the document the bytes
    were read from, which a single-invoice file's row is pointed at; `None` means the
    bytes came in with the request, and `origin`, their file name, is recorded as the
    provenance of the document archived for them."""

    content: bytes
    stored_as: UUID | None
    origin: str | None
    single_invoice: bool

    @property
    def digest(self) -> str | None:
        return hashlib.sha256(self.content).hexdigest() if self.single_invoice else None


class InvoiceImporter:
    def __init__(self, ctx: InvoiceContext, issued: IssuedInvoiceImporter) -> None:
        self.ctx = ctx
        self.issued = issued

    def _importing_aziende(self) -> list[LegalEntity]:
        """The aziende a FatturaPA file can have been issued by (REB-619, spec §1.5):
        the active Italian ones. A space with none has nothing to classify against,
        and the refusal names the screen to go to."""
        aziende = self.ctx.emitter.repo.active_italian()
        if not aziende:
            raise NotFound("emitter_profile", "predefinita")
        return aziende

    # ---- review ---------------------------------------------------------------

    def review_import(
        self, document_ids: Sequence[UUID], actor: Actor
    ) -> list[ReviewedInvoiceRead]:
        """Read-only review of one or more already-archived documents (REB-365, design
        record §4, §7 item 3): for each `document_id`, reads the document's own stored
        bytes back -- never a caller-supplied copy -- and reports one row per invoice
        they parse into, tagged `ready`, `needs_customer_confirmation`,
        `already_present`, `conflict`, `incoming_skipped`, or `unclaimed`. **No database
        write of any kind**, so reviewing the same document twice returns the same
        verdict every time.
        """
        actor.require_admin(REVIEW_ACTION)
        ctx = self.ctx
        aziende = self._importing_aziende()
        rows: list[ReviewedInvoiceRead] = []
        for document_id in document_ids:
            content, _content_type, _filename = ctx.documents.download(document_id, None, actor)
            rows.extend(review_content(ctx.session, content, aziende, document_id))
        return rows

    def review_upload(
        self, files: Sequence[tuple[str, bytes]], actor: Actor
    ) -> list[UploadedInvoiceReviewRead]:
        """`review_import` for files that were never archived (REB-758): `files` are the
        `(name, bytes)` of what was uploaded, ZIPs still closed (`import_upload.
        expand_uploads` opens them, after the admin check), and the verdicts are the same
        ones from the same `review_invoices`, named by file and SHA-256 instead of a
        `document_id`. Read-only, admin only, and nothing is stored: see `import_upload`
        for why the page sends the files twice rather than staging them. A file that looks
        like a FatturaPA and does not parse is one `unclaimed` row with its `motivo`, not
        a refusal of the files sent with it."""
        actor.require_admin(REVIEW_ACTION)
        aziende = self._importing_aziende()
        rows: list[UploadedInvoiceReviewRead] = []
        for file in expand_uploads(files):
            for row in review_invoices(self.ctx.session, file.content, aziende):
                rows.append(
                    UploadedInvoiceReviewRead(
                        file=file.name,
                        sha256=file.sha256,
                        outcome=row.outcome,
                        invoice=row.invoice,
                        party_key=party_key(row.invoice.cliente) if row.invoice else None,
                        motivo=row.note,
                        azienda_id=row.azienda_id,
                        matched_customer_id=row.matched_customer_id,
                        mappature_giorni=row.mappature_giorni,
                    )
                )
        return rows

    # ---- confirmation ---------------------------------------------------------

    def confirm_import(
        self,
        document_id: UUID,
        actor: Actor,
        *,
        customer_id: UUID | None = None,
        create_customer: bool = False,
    ) -> list[ConfirmedInvoiceRead]:
        """Confirm one already-reviewed document onto the register (REB-366, design
        record §4-5, §7 item 4): **re-reads and re-parses the document's own stored
        bytes**, never trusts an earlier `review_invoice_import` call, re-classifies
        direction and duplication against the database's *current* state, and -- for
        every invoice that classifies `"ready"` -- maps it onto `InvoiceImport` and calls
        `import_issued` itself for the write, exactly as a hand-declared import would.

        Confirming an invoice already on the register at its own natural key is a
        no-op: `"already_present"` is reported, never a duplicate insert. A supplier's
        invoice (`"incoming_skipped"`), a number already on record under a different or
        no hash (`"conflict"`), a document no adapter recognises (`"unclaimed"`), and
        `import_issued`'s own register rules refusing with `Conflict`/`ValidationFailed`
        are reported as `"conflict"` and never written. A `lotto` batch keeps attempting
        every remaining invoice, and every invoice already committed earlier in the same
        call still comes back with its own row: one named outcome per invoice, never a
        silent drop.

        `customer_id` is the one human decision this adds: which `Customer` the invoice
        attaches to. When omitted, the current exact tax-id match
        (`import_review.match_customer`, re-run against the database's current state,
        never review time's) is used. A `customer_id` that is archived, or that is not on
        the azienda the invoice's issuer resolves to, is a `ValidationFailed` naming the
        counterparty, raised before any invoice is written (REB-783).

        `create_customer` (REB-367, design §7 item 5): when still unresolved and true,
        the matched party is inserted as a new `Customer` through
        `CustomerService._insert`, the non-committing half of `create`, inside the
        invoice's own transaction, so a refusal of the register write takes the customer
        with it. A `lotto` batch's invoices from the same brand-new counterparty share
        one freshly created row. `create_customer` false (the default) reports
        `"needs_customer_confirmation"` and writes nothing.

        **`xml_document_id`/`xml_hash_sha256`, for a single-invoice source document
        only** (design §5 item 3): the invoice row is pointed at the very `document_id`
        this call read the bytes from -- never a second `documents` row for the same
        content -- and its hash is computed from those same bytes. A `lotto` batch's
        invoices leave both `NULL`: a shared source document does not fit this project's
        document-ownership rules, so none of a batch's invoices takes ownership of it.
        """
        actor.require_admin(CONFIRM_ACTION)
        aziende = self._importing_aziende()
        content, _content_type, _filename = self.ctx.documents.download(document_id, None, actor)

        def decide(_party: ParsedInvoiceParty) -> tuple[UUID | None, bool, bool]:
            return customer_id, create_customer, False

        self._refuse_chosen_customers([content], aziende, decide)
        batch = _Batch(gaps=_GapTracker(self.ctx))
        return [
            ConfirmedInvoiceRead(
                document_id=document_id,
                outcome=row.outcome,
                azienda_id=row.azienda_id,
                fattura=row.fattura,
                buchi_non_dichiarati=row.buchi_non_dichiarati,
            )
            for row in self._confirm_content(
                content,
                aziende,
                actor,
                stored_as=document_id,
                origin=None,
                decide=decide,
                batch=batch,
            )
        ]

    def confirm_upload(
        self,
        files: Sequence[tuple[str, bytes]],
        decisions: Mapping[str, UploadedPartyDecision],
        actor: Actor,
    ) -> list[UploadedInvoiceConfirmRead]:
        """`confirm_import` for files that were never archived (REB-758). Each file is
        read again from its own bytes, never from an earlier `review_upload`, and is
        written by the one `_confirm_content` `confirm_import` runs, so the register
        rules, the one-transaction-per-invoice commit and the `conflict` outcome for a
        refused invoice are the same ones. `decisions` is keyed by the counterparty's
        `party_key`, and a customer created for one invoice is the customer of every
        later invoice of that counterparty in this request, whether or not the register
        could match it again by tax id. A `customer_id` that is archived, or that is not
        on the azienda the invoice's issuer resolves to, is a `ValidationFailed` naming
        the counterparty, raised before the first invoice of the request is written
        (REB-783).

        The one difference is where a single-invoice file's XML ends up. `confirm_import`
        points the invoice at the document it was handed; here there is none, so the
        file is archived as a `fattura_xml` document of the customer the invoice was
        written for, inside the invoice's own transaction. A `lotto` file archives
        nothing, for the reason `confirm_import` gives for leaving `xml_document_id`
        empty on a batch."""
        actor.require_admin(CONFIRM_ACTION)
        aziende = self._importing_aziende()

        def decide(party: ParsedInvoiceParty) -> tuple[UUID | None, bool, bool]:
            decision = decisions.get(party_key(party))
            if decision is None:
                return None, False, False
            return decision.customer_id, decision.create_customer, decision.skip

        uploaded = expand_uploads(files)
        self._refuse_chosen_customers([file.content for file in uploaded], aziende, decide)
        batch = _Batch(gaps=_GapTracker(self.ctx))
        rows: list[UploadedInvoiceConfirmRead] = []
        for file in uploaded:
            for row in self._confirm_content(
                file.content,
                aziende,
                actor,
                stored_as=None,
                origin=file.name,
                decide=decide,
                batch=batch,
            ):
                rows.append(
                    UploadedInvoiceConfirmRead(
                        file=file.name,
                        sha256=file.sha256,
                        outcome=row.outcome,
                        party_key=row.party_key,
                        motivo=row.note,
                        azienda_id=row.azienda_id,
                        fattura=row.fattura,
                        buchi_non_dichiarati=row.buchi_non_dichiarati,
                    )
                )
        return rows

    def _refuse_chosen_customers(
        self,
        contents: Sequence[bytes],
        aziende: Sequence[LegalEntity],
        decide: CustomerDecision,
    ) -> None:
        """`check_import_customer` for every invoice a confirmation is about to write,
        before the first of them is. Each invoice commits on its own, so a refusal found
        while writing would leave the earlier invoices of the same request on the
        register and give the caller a 422 that hides them; read first, the 422 means
        nothing was written. Reads exactly what `_confirm_content` will classify."""
        for content in contents:
            adapter = detect_adapter(content)
            if adapter is None:
                continue
            try:
                invoices = adapter.parse(content)
            except ValueError:
                continue
            for invoice in invoices:
                classification, azienda = classify_parsed_invoice(
                    invoice,
                    aziende,
                    existing_for=register_lookup(self.ctx.session, invoice),
                    content=content,
                )
                if classification != "ready" or azienda is None:
                    continue
                customer_id, _create, skip = decide(invoice.cliente)
                if customer_id is not None and not skip:
                    check_import_customer(
                        self.ctx, customer_id, azienda, invoice.cliente.ragione_sociale
                    )

    def _confirm_content(
        self,
        content: bytes,
        aziende: Sequence[LegalEntity],
        actor: Actor,
        *,
        stored_as: UUID | None,
        origin: str | None,
        decide: CustomerDecision,
        batch: _Batch,
    ) -> list[ConfirmedInvoice]:
        """The write both `confirm_import` and `confirm_upload` run, on one file's bytes
        (their docstrings carry the rules). `decide` answers, for an invoice's customer,
        the `customer_id` to attach, whether to create the counterparty when none
        matches, and whether to leave the invoice out altogether."""
        adapter = detect_adapter(content)
        if adapter is None:
            return [ConfirmedInvoice(outcome="unclaimed")]
        try:
            invoices = adapter.parse(content)
        except ValueError as exc:
            return [ConfirmedInvoice(outcome="unclaimed", note=str(exc))]
        file = _File(
            content=content, stored_as=stored_as, origin=origin, single_invoice=len(invoices) == 1
        )
        return [
            self._confirm_invoice(invoice, file, aziende, actor, decide, batch)
            for invoice in invoices
        ]

    def _confirm_invoice(
        self,
        invoice: ParsedInvoice,
        file: _File,
        aziende: Sequence[LegalEntity],
        actor: Actor,
        decide: CustomerDecision,
        batch: _Batch,
    ) -> ConfirmedInvoice:
        ctx = self.ctx
        party = party_key(invoice.cliente)
        classification, azienda = classify_parsed_invoice(
            invoice,
            aziende,
            existing_for=register_lookup(ctx.session, invoice),
            content=file.content,
        )
        if classification == "incoming_skipped" or azienda is None:
            return ConfirmedInvoice(outcome="incoming_skipped", party_key=party)
        key = natural_key(invoice)
        # `classify_parsed_invoice` routes a keyless invoice to "conflict": it compares
        # against a synthetic hashless row, and a hashless comparison is never "new". So
        # "already_present" and "ready" both carry a key, and this is the same answer.
        if classification == "conflict" or key is None:
            return ConfirmedInvoice(outcome="conflict", azienda_id=azienda.id, party_key=party)
        anno, numero = key
        if classification == "already_present":
            existing = ctx.repo.existing_by_number(azienda.id, anno, numero)
            return ConfirmedInvoice(
                outcome="already_present",
                azienda_id=azienda.id,
                fattura=ctx.get(existing.id) if existing is not None else None,
                party_key=party,
            )
        customer_id, create_customer, skip = decide(invoice.cliente)
        if skip:
            # The person left this customer out: whatever the register would match now (a
            # customer created by somebody else since the review, say), the invoice is not
            # written.
            return self._needs_customer(azienda, party)
        if customer_id is not None:
            check_import_customer(ctx, customer_id, azienda, invoice.cliente.ragione_sociale)
        else:
            customer_id = match_customer(ctx.session, invoice.cliente, azienda.id)
        cached_id = batch.created_customers.get((azienda.id, party))
        if customer_id is None and not create_customer:
            return self._needs_customer(azienda, party)

        new_customer_id: UUID | None = None
        try:
            # One SAVEPOINT per invoice: a refusal rolls back the customer inserted for it
            # and the XML archived for it, never an earlier invoice of the request (each
            # of those is committed) nor the caller's other pending work.
            with ctx.session.begin_nested():
                if customer_id is None:
                    customer_id = cached_id
                if customer_id is None:
                    # On the azienda the file landed on (§1.5), not the one the nation
                    # would propose: the customer was billed by this azienda already.
                    new_customer = ctx.customers._insert(
                        map_parsed_party_to_customer(invoice.cliente).model_copy(
                            update={"azienda_id": azienda.id}
                        ),
                        actor,
                    )
                    customer_id = new_customer_id = new_customer.id
                fattura = self._write(invoice, anno, numero, customer_id, azienda, file, actor)
        except _Refused:
            return ConfirmedInvoice(outcome="conflict", azienda_id=azienda.id, party_key=party)
        ctx.session.commit()
        # Cached only now that the invoice it was created for is committed: a rolled-back
        # insert must not leave an id behind for the next invoice of the batch to reuse.
        if new_customer_id is not None:
            batch.created_customers[(azienda.id, party)] = new_customer_id
        return ConfirmedInvoice(
            outcome="imported",
            azienda_id=azienda.id,
            fattura=fattura,
            buchi_non_dichiarati=batch.gaps.holes_after(azienda.id, anno, numero),
            party_key=party,
        )

    @staticmethod
    def _needs_customer(azienda: LegalEntity, party: str) -> ConfirmedInvoice:
        return ConfirmedInvoice(
            outcome="needs_customer_confirmation", azienda_id=azienda.id, party_key=party
        )

    def _write(
        self,
        invoice: ParsedInvoice,
        anno: int,
        numero: int,
        customer_id: UUID,
        azienda: LegalEntity,
        file: _File,
        actor: Actor,
    ) -> InvoiceRead:
        """The register write of one invoice, uncommitted. Raises `_Refused` for what the
        register or the document refuses, so the caller's savepoint undoes it."""
        try:
            # Inside the guard: `InvoiceImport` has bounds a parsed document can exceed
            # (200 lines, a width), and its refusal is this invoice's `conflict`, not a
            # failure of the request.
            data = map_parsed_invoice_to_import(
                invoice, anno=anno, numero=numero, customer_id=customer_id
            )
            xml_document_id = file.stored_as
            if file.single_invoice and file.stored_as is None:
                # `commit=False`: the document belongs to this invoice's transaction, so a
                # refusal rolls it back with the invoice and the customer instead of
                # leaving an XML nobody's register points at. The bytes `import_bytes`
                # already put in storage stay there, unreferenced: the accepted orphan its
                # own docstring describes.
                xml_document_id = self.ctx.documents.import_bytes(
                    customer_id=customer_id,
                    tipo="fattura_xml",
                    titolo=f"Fattura {numero_completo(anno, numero)} (XML)",
                    data=file.content,
                    content_type="application/xml",
                    actor=actor,
                    origine={"file": file.origin},
                    commit=False,
                    azienda_id=azienda.id,
                ).id
            return self.issued.import_issued(
                data,
                actor,
                azienda_id=azienda.id,
                xml_document_id=xml_document_id if file.single_invoice else None,
                xml_hash_sha256=file.digest,
                importata_da="fatturapa",
                commit=False,
            )
        except (Conflict, ValidationFailed, PydanticValidationError) as exc:
            raise _Refused from exc


__all__ = ["CONFIRM_ACTION", "REVIEW_ACTION", "InvoiceImporter"]
