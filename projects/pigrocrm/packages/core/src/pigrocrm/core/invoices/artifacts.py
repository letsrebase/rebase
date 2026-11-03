"""The PDF and the FatturaPA file of an invoice: producing, verifying, serving."""

import hashlib
from datetime import date
from typing import Literal
from uuid import UUID

from pigrocrm.core.actor import Actor
from pigrocrm.core.db.clock import local_date
from pigrocrm.core.documents.models import Document, DocumentVersion
from pigrocrm.core.documents.schemas import DocumentCreate
from pigrocrm.core.errors import Conflict, NotFound, ValidationFailed
from pigrocrm.core.fiscal.pack import PACK_NON_IT
from pigrocrm.core.invoices import pdf as invoice_pdf
from pigrocrm.core.invoices.constants import ENTITY
from pigrocrm.core.invoices.context import InvoiceContext
from pigrocrm.core.invoices.fatturapa.checks import (
    check_party_exportable,
    check_recipient_identity,
    check_recipient_routing,
)
from pigrocrm.core.invoices.fatturapa.exporter import InvoicePaExporter
from pigrocrm.core.invoices.fatturapa.normalise import normalise_fiscal_id
from pigrocrm.core.invoices.models import Invoice
from pigrocrm.core.invoices.naming import (
    invoice_storage_prefix,
    number_complete,
    proforma_storage_prefix,
    sdi_filename,
)
from pigrocrm.core.invoices.schemas import (
    ArtifactKind,
    InvoiceArtifact,
    InvoiceForExport,
    InvoiceLineRead,
    InvoiceSnapshot,
)
from pigrocrm.core.invoices.snapshot import build_snapshot
from pigrocrm.core.legal_entities.assets import LegalEntityAssets
from pigrocrm.core.text import PDF_MIME

XML_MIME = "application/xml"
# The `documents.type` of each artefact stream: `invoice`/`proforma` are the names
# `DocumentType` declares for *the* document of its kind, and `invoice_xml` is the one that
# needs qualifying because it is the second stream for the same invoice.
ArtifactType = Literal["invoice", "invoice_xml", "proforma"]


class InvoiceArtifacts:
    def __init__(self, ctx: InvoiceContext) -> None:
        self.ctx = ctx

    # ---- the frozen view ------------------------------------------------------

    def _frozen(self, invoice: Invoice) -> tuple[InvoiceSnapshot, int, int]:
        """The stored snapshot with the invoice's `(anno, numero)`.

        `InvoiceSnapshot.model_validate` on the stored JSONB is deliberate: the model is
        `extra="forbid"` and `versione` has no default, so a payload written by a
        different version of this code is a loud failure rather than one silently read
        with a field dropped. This is a fiscal document; guessing is the failure mode the
        version column exists to prevent.
        """
        if invoice.snapshot is None or invoice.year is None or invoice.number is None:
            raise Conflict(
                ENTITY,
                "un documento senza numero e senza congelamento non si esporta",
                status=invoice.status,
            )
        return InvoiceSnapshot.model_validate(invoice.snapshot), invoice.year, invoice.number

    def for_export(self, invoice: Invoice) -> InvoiceForExport:
        """The frozen view the exporter and the PDF read. Never the live profiles."""
        snapshot, year, number = self._frozen(invoice)
        if invoice.issue_date is None:  # pragma: no cover - the CHECKs make this unreachable
            raise Conflict(ENTITY, "manca la data di emissione", status=invoice.status)
        return self._export_view(
            invoice,
            snapshot,
            year,
            number,
            issue_date=invoice.issue_date,
            due_date=invoice.due_date,
        )

    def for_export_proforma(self, invoice: Invoice, actor: Actor) -> InvoiceForExport:
        """A live view for a proforma's PDF: a proforma never freezes (only `issue`
        writes `snapshot`/`year`/`number`), so there is nothing to read back. Built
        from the *current* customer/emitter/fiscal profile instead, and dated with the
        proforma's own `issue_date` (ORB-63), which `create` sets and which the
        sender may move while the document is a draft. The `created_at` fallback is
        for a row written before migration 0033 by a path that skipped its backfill:
        it is the civil date in Europe/Rome the PDF printed before the column was
        filled, never `today_local()`, so a re-render does not drift with the
        clock.

        `year`/`number` are placeholders that satisfy the schema's non-nullable bounds
        and never reach the document: the PDF prints `reference` instead of
        `number_complete` whenever it is given, and `produce_artifacts` always gives it
        for a proforma.
        """
        _, profile = self.ctx.regime(invoice.legal_entity_id)
        snapshot = build_snapshot(
            self.ctx, invoice.customer_id, profile, actor, invoice.legal_entity_id
        )
        return self._export_view(
            invoice,
            snapshot,
            invoice.created_at.year,
            1,
            issue_date=invoice.issue_date or local_date(invoice.created_at),
            due_date=None,
        )

    def _export_view(
        self,
        invoice: Invoice,
        snapshot: InvoiceSnapshot,
        year: int,
        number: int,
        *,
        issue_date: date,
        due_date: date | None,
    ) -> InvoiceForExport:
        return InvoiceForExport(
            year=year,
            number=number,
            issue_date=issue_date,
            due_date=due_date,
            sdi_document_type=invoice.sdi_document_type,
            currency=invoice.currency,
            taxable_amount=invoice.taxable_amount,
            vat_amount=invoice.vat_amount,
            stamp_duty=invoice.stamp_duty,
            total=invoice.total,
            memo=invoice.memo,
            accrual_from=invoice.accrual_from,
            accrual_to=invoice.accrual_to,
            purchase_order_number=invoice.purchase_order_number,
            purchase_order_date=invoice.purchase_order_date,
            purchase_order_tender_code=invoice.purchase_order_tender_code,
            purchase_order_project_code=invoice.purchase_order_project_code,
            snapshot=snapshot,
            lines=tuple(InvoiceLineRead.model_validate(r) for r in self.ctx.repo.lines(invoice.id)),
        )

    @staticmethod
    def frozen_abroad(invoice: Invoice) -> bool:
        """Whether the issued row's frozen profile is a foreign azienda's (REB-619):
        read off the stored JSON rather than the live profile, since the azienda's pack
        at issue time is the fact the document was built on, and off the row before
        `for_export` validates the whole snapshot, so the refusal is the specific one.
        A row with no snapshot yet is not frozen anywhere, abroad included."""
        snapshot = invoice.snapshot or {}
        tax = snapshot.get("tax") or {}
        return bool(tax.get("pack_id") == PACK_NON_IT)

    # ---- names ----------------------------------------------------------------

    @staticmethod
    def _xml_filename(snapshot: InvoiceSnapshot, year: int, number: int) -> str:
        """`IT{cf_o_piva}_{progressivo}.xml`, from the **frozen** emitter identity.

        Fiscal code first, then VAT number: the same order `IdTrasmittente` uses, and
        for the same reason -- the SdI accepts either, and this is what the working
        generator sent. Reading the snapshot rather than the live profile is what keeps
        the name stable after the issuer edits their own data.
        """
        legal_entity = snapshot.legal_entity
        id_tax = normalise_fiscal_id(legal_entity.tax_code) or normalise_fiscal_id(
            legal_entity.vat_number
        )
        if id_tax is None:
            raise ValidationFailed(
                "legal_entities",
                "tax_code",
                "il nome del file XML richiede un codice fiscale o una partita IVA "
                "validi dell'emittente",
                expected="11 cifre oppure 16 caratteri",
            )
        return sdi_filename(id_tax, year, number)

    @staticmethod
    def _pdf_identity(invoice: Invoice) -> tuple[ArtifactType, str, str]:
        """`(documents.tipo, title, download filename)` of the invoice's PDF. A proforma
        is named by its reference, a fattura by its number; the filename is a plain,
        slug-safe name, because nothing downstream validates it."""
        if invoice.type == "proforma":
            reference = invoice.reference or str(invoice.id)
            return "proforma", f"Proforma {reference} (PDF)", f"proforma-{reference.lower()}.pdf"
        if invoice.year is None or invoice.number is None:
            raise Conflict(ENTITY, "un documento senza numero non ha un PDF fiscale")
        return (
            "invoice",
            f"Fattura {number_complete(invoice.year, invoice.number)} (PDF)",
            f"fattura-{invoice.year}-{invoice.number}.pdf",
        )

    @staticmethod
    def _storage_prefix(invoice: Invoice) -> str:
        if invoice.type == "proforma":
            return proforma_storage_prefix(invoice.id)
        if invoice.year is None or invoice.number is None:  # pragma: no cover
            raise Conflict(ENTITY, "un documento senza numero non ha un prefisso fiscale")
        return invoice_storage_prefix(invoice.legal_entity_id, invoice.year, invoice.number)

    # ---- the documents rows ---------------------------------------------------

    def artifact_document(
        self, invoice: Invoice, type: ArtifactType, title: str, actor: Actor
    ) -> Document:
        """The `documents` row for one artefact stream, created on first use.

        Two rows per issued invoice, not one (spec 8.4): a `document_versions` chain is
        a linear history of *one* logical file with one `hash_sha256` used for
        deduplication and integrity, so mixing the PDF and the XML would make
        "version 3" ambiguous and the two hashes incomparable. Two streams, two hashes,
        two integrity checks -- and re-rendering the PDF never touches the XML.

        `documents.status` is left `NULL` for all three invoice artefact types: the
        authoritative state is `invoices.status`, and duplicating a state machine across
        two tables produces two truths.

        Creating it is only safe under the invoice's row lock (see
        `InvoiceContext.require_locked`): two unlocked callers would each create a
        `Document` and overwrite the pointer, orphaning one. The row is created without
        committing, so the lock is held until the caller has written the pointer (and the
        version, and the XML hash) and commits them together.
        """
        documents = self.ctx.documents
        existing_id = invoice.xml_document_id if type == "invoice_xml" else invoice.pdf_document_id
        if existing_id is not None:
            document = documents.repo.get(existing_id)
            if document is not None:
                return document
        # The invoice's own azienda, never the customer's current one: a customer moved
        # since the invoice was issued keeps the invoice where it was (spec §1.7), and so
        # must the artefacts rendered for it afterwards.
        created = documents.create(
            DocumentCreate(customer_id=invoice.customer_id, type=type, title=title),
            actor,
            legal_entity_id=invoice.legal_entity_id,
            commit=False,
        )
        document = documents.repo.get(created.id)
        if document is None:  # pragma: no cover - just created in this transaction
            raise NotFound("document", created.id)
        if type == "invoice_xml":
            invoice.xml_document_id = document.id
        else:
            invoice.pdf_document_id = document.id
        return document

    def _store_artifact(
        self,
        invoice: Invoice,
        *,
        kind: ArtifactKind,
        type: ArtifactType,
        title: str,
        content_type: str,
        filename: str,
        data: bytes,
        expected_hash: str | None,
        actor: Actor,
    ) -> InvoiceArtifact:
        """Write the bytes, or prove the bytes already there are the same bytes.

        Four outcomes, and they are spec 4's:

        * no previous hash -- the first successful production, the only moment with
          nothing to compare against. Write version 1 and record the hash;
        * the hash matches and the stored bytes still hash to it -- nothing to do.
          Return the existing version rather than writing an identical one, so a
          download does not grow the history;
        * the hash matches but the bytes are gone or corrupt, or stored under another
          type -- a **repair**: write a new version with identical content. Spec 4
          allows exactly this and calls it a repair, not a modification;
        * the hash differs -- an error to report, never a version to save.
        """
        ctx = self.ctx
        digest = hashlib.sha256(data).hexdigest()
        if expected_hash is not None and digest != expected_hash:
            raise Conflict(
                ENTITY,
                f"il {kind} rigenerato non coincide con quello originale: e' una "
                "divergenza da segnalare, non una nuova versione da salvare",
                expected=expected_hash,
                ottenuto=digest,
                field="xml_hash_sha256" if kind == "xml" else "hash_sha256",
            )

        document = self.artifact_document(invoice, type, title, actor)
        current = (
            ctx.documents.repo.version(document.id, document.current_version)
            if document.current_version
            else None
        )
        # The type too, not only the bytes (REB-480): the same bytes stored under another
        # type are what `download` refuses to serve, and reusing them would leave
        # «Rigenera documenti» unable to repair it. They get a new version instead.
        if (
            current is not None
            and current.hash_sha256 == digest
            and current.content_type == content_type
        ):
            try:
                stored_ok = (
                    hashlib.sha256(ctx.storage.get(current.storage_key)).hexdigest() == digest
                )
            except Exception:
                stored_ok = False
            if stored_ok:
                return InvoiceArtifact(
                    kind=kind,
                    document_id=document.id,
                    version_number=current.number,
                    filename=filename,
                    content_type=content_type,
                    hash_sha256=digest,
                )

        version = ctx.documents.add_version(
            document.id,
            data,
            content_type,
            actor,
            storage_prefix=self._storage_prefix(invoice),
            commit=False,
        )
        return InvoiceArtifact(
            kind=kind,
            document_id=document.id,
            version_number=version.number,
            filename=filename,
            content_type=content_type,
            hash_sha256=digest,
        )

    # ---- the XML --------------------------------------------------------------

    def export_xml(self, invoice_id: UUID, actor: Actor) -> InvoiceArtifact:
        """Produce -- or verify -- the FatturaPA file.

        Refuses a proforma and a draft on the basis of the row's own **state**, never a
        flag the caller passed: that is the second of the four independent mechanisms
        that stop a proforma from being mistaken for an invoice, and the only one that
        cannot be bypassed by a caller who believes otherwise.

        `xml_hash_sha256` is written by the **first** successful export -- the one
        moment with no previous value to compare against -- and from then on every
        export compares and does not rewrite. Until then the column is `NULL` and the
        export is freely repeatable, which is exactly what makes the out-of-transaction
        render of spec 3 harmless.

        An `cancelled` invoice still exports: it consumed a number and stays a document
        of record, and the file is the same frozen snapshot, compared against the stored
        hash, so nothing new is ever said about it. Refusing it would leave the register
        unauditable (`test_an_annulled_invoice_still_exports_its_xml`).

        A `"fatturapa"` row with a stored `xml_document_id` (design 2026-09-23 §5 item
        4/§7 item 6: a single-invoice source, never a `lotto` batch) has the original
        transmitted file already on record, so this hands that file back instead of
        refusing: `_serve_stored_xml` re-hashes the stored bytes against
        `invoice.xml_hash_sha256` and only ever returns an artefact pointing at the
        existing document/version. Every other imported row keeps refusing: there is no
        original file on record to serve.

        The row is locked first, so two concurrent exports cannot each create the XML
        `Document` and orphan one.
        """
        actor.require_write("export_invoice_xml")
        ctx = self.ctx
        invoice = ctx.require_locked(invoice_id)
        if invoice.type != "invoice":
            raise Conflict(
                ENTITY,
                "una proforma non produce un file FatturaPA: non e' un documento fiscale",
                type=invoice.type,
                status=invoice.status,
            )
        if invoice.status == "draft":
            raise Conflict(
                ENTITY,
                "una bozza non ha ancora un numero e non produce un file FatturaPA",
                status=invoice.status,
            )
        if self.frozen_abroad(invoice):
            raise Conflict(
                ENTITY,
                "questa azienda non emette fatture elettroniche: il documento e' il PDF",
                year=invoice.year,
                number=invoice.number,
            )
        if invoice.imported_from is not None:
            if invoice.imported_from == "fatturapa" and invoice.xml_document_id is not None:
                return self._serve_stored_xml(invoice)
            raise Conflict(
                ENTITY,
                "fattura importata: l'XML e' quello gia' trasmesso allo SdI dal sistema "
                "che l'ha emessa, questo CRM non ne produce un secondo",
                year=invoice.year,
                number=invoice.number,
            )

        export = self.for_export(invoice)
        # Re-checked here even though `issue` already checked: the snapshot could have
        # been edited out of band.
        check_party_exportable(export.snapshot.legal_entity, "legal_entities")
        check_party_exportable(export.snapshot.customer, "customer")
        check_recipient_routing(export.snapshot.customer)
        check_recipient_identity(export.snapshot.customer)

        data = InvoicePaExporter().to_bytes(export)
        artifact = self._store_artifact(
            invoice,
            kind="xml",
            type="invoice_xml",
            title=f"Fattura {number_complete(export.year, export.number)} (XML)",
            content_type=XML_MIME,
            filename=self._xml_filename(export.snapshot, export.year, export.number),
            data=data,
            expected_hash=invoice.xml_hash_sha256,
            actor=actor,
        )
        if invoice.xml_hash_sha256 is None:
            invoice.xml_hash_sha256 = artifact.hash_sha256
        ctx.session.commit()
        return artifact

    def _verified_xml(self, invoice: Invoice, version: DocumentVersion) -> bytes:
        """The stored XML, proven to still be the file the register recorded.

        `xml_hash_sha256` is written together with `xml_document_id` by both paths that
        set them (the first export, and the single-invoice FatturaPA import), so a row
        with one and not the other is a state no caller in this codebase produces.
        Refusing rather than serving unverified bytes keeps "hash-verified" true
        regardless of a future caller's mistake, instead of resting on that pairing as an
        unenforced convention.
        """
        if invoice.xml_hash_sha256 is None:
            raise Conflict(
                ENTITY,
                "manca l'hash registrato del file XML: non si serve un file che "
                "non si puo' verificare",
                year=invoice.year,
                number=invoice.number,
            )
        data = self.ctx.storage.get(version.storage_key)
        digest = hashlib.sha256(data).hexdigest()
        if digest != invoice.xml_hash_sha256:
            raise Conflict(
                ENTITY,
                "il file XML archiviato non coincide piu' con l'hash registrato: "
                "una divergenza da segnalare, non un file da servire",
                expected=invoice.xml_hash_sha256,
                ottenuto=digest,
                field="xml_hash_sha256",
            )
        return data

    def _current_version(self, document_id: UUID) -> tuple[Document, DocumentVersion] | None:
        repo = self.ctx.documents.repo
        document = repo.get(document_id)
        if document is None or not document.current_version:
            return None
        version = repo.version(document.id, document.current_version)
        return None if version is None else (document, version)

    def _serve_stored_xml(self, invoice: Invoice) -> InvoiceArtifact:
        """The original FatturaPA file a `"fatturapa"` single-invoice import already
        stored, served back rather than regenerated (design 2026-09-23 §5 item 4/§7
        item 6): the CRM never produced this XML, so there is no `InvoicePaExporter`
        call and no divergence-means-repair branch to offer, unlike `_store_artifact` --
        either the stored bytes still hash to what the register recorded when the row was
        confirmed, or this refuses rather than handing back a file it cannot vouch for.
        """
        if invoice.xml_document_id is None:  # pragma: no cover - callers check it first
            raise NotFound("invoice_artifact", f"{invoice.id}#xml")
        current = self._current_version(invoice.xml_document_id)
        if current is None:
            raise NotFound("invoice_artifact", f"{invoice.id}#xml")
        document, version = current
        self._verified_xml(invoice, version)
        snapshot, year, number = self._frozen(invoice)
        return InvoiceArtifact(
            kind="xml",
            document_id=document.id,
            version_number=version.number,
            filename=self._xml_filename(snapshot, year, number),
            content_type=version.content_type,
            hash_sha256=invoice.xml_hash_sha256 or "",
        )

    # ---- the PDF and the download ---------------------------------------------

    def produce_artifacts(self, invoice_id: UUID, actor: Actor) -> list[InvoiceArtifact]:
        """Render the PDF, and for an issued invoice the XML too.

        Called after `issue`'s commit, and callable again at any time: both artefacts
        regenerate deterministically from the frozen snapshot, so this is idempotent by
        construction rather than by a guard. That is the property that makes the
        post-commit render of spec 3 safe -- a crash between the commit and the render
        leaves an invoice that is fiscally complete and merely unprinted, and the next
        call finishes the job.

        Byte-identical on re-render, which is only true because `render_pdf` pins
        `--creation-timestamp 0`: Typst otherwise stamps wall-clock compile time into
        every PDF's `/CreationDate`.

        The PDF is produced for a proforma as well, from a live view built by
        `for_export_proforma` rather than the frozen `for_export` (a proforma never
        freezes); the XML is not, because a proforma is not a fiscal document.
        `export_xml` refuses one on the row's own state. Returns both artefacts
        (`[pdf]`, or `[pdf, xml]` once the fattura is issued) rather than only the
        last one produced, so a caller sees the whole result of one call.

        The row is locked for the write that files the PDF, so two concurrent calls
        cannot each create a `Document` and orphan one.
        """
        actor.require_write("produce_invoice_artifacts")
        ctx = self.ctx
        invoice = ctx.require(invoice_id)
        if invoice.imported_from is not None:
            raise Conflict(
                ENTITY,
                "fattura importata: il PDF e' l'originale caricato, non si rigenera",
                year=invoice.year,
                number=invoice.number,
            )
        export = (
            self.for_export_proforma(invoice, actor)
            if invoice.type == "proforma"
            else self.for_export(invoice)
        )
        reference = invoice.reference if invoice.type == "proforma" else None
        type, title, filename = self._pdf_identity(invoice)
        # The issuing azienda's logo (REB-627, spec §1.8), live like the document's.
        media = LegalEntityAssets(ctx.session, ctx.storage).media_for(invoice.legal_entity_id)
        # Everything the render needs is read: end the read transaction first, so the
        # compile (which may wait for a render slot) does not pin a pooled connection.
        ctx.session.commit()
        _, data = invoice_pdf.render_invoice_pdf(
            export, reference=reference, settings=ctx.settings, media=media
        )
        # The row is locked for the write: a second call waits for the first and then
        # finds its document, instead of creating a second one and overwriting the
        # pointer. Locked after the render and not before, so the lock is not held for a
        # Typst subprocess.
        invoice = ctx.require_locked(invoice_id)
        # No `expected_hash`: unlike the XML, the PDF's bytes are not a fiscal identity
        # the system promises never to change. A template correction should produce a new
        # version, not a divergence error.
        pdf_artifact = self._store_artifact(
            invoice,
            kind="pdf",
            type=type,
            title=title,
            content_type=PDF_MIME,
            filename=filename,
            data=data,
            expected_hash=None,
            actor=actor,
        )
        ctx.session.commit()
        artifacts = [pdf_artifact]
        # A foreign azienda's document is the PDF alone (REB-619): `export_xml` would
        # refuse it, and a refusal is not an artefact.
        if (
            invoice.type == "invoice"
            and invoice.status != "draft"
            and not self.frozen_abroad(invoice)
        ):
            artifacts.append(self.export_xml(invoice_id, actor))
        return artifacts

    def download(
        self, invoice_id: UUID, kind: ArtifactKind, actor: Actor
    ) -> tuple[bytes, str, str]:
        """`(bytes, content_type, filename)`.

        The download always goes through the API, which is the only place authorisation
        exists on either storage backend (slice 2 §5). The XML's name is the SdI
        convention, taken from the frozen snapshot; the PDF's is a plain, slug-safe name.
        The XML is served only if its bytes still hash to the recorded `xml_hash_sha256`,
        as `export_xml` does for an imported file.
        """
        ctx = self.ctx
        invoice = ctx.require(invoice_id)
        document_id = invoice.xml_document_id if kind == "xml" else invoice.pdf_document_id
        current = None if document_id is None else self._current_version(document_id)
        if current is None:
            raise NotFound("invoice_artifact", f"{invoice_id}#{kind}")
        _, version = current
        if kind == "xml":
            snapshot, year, number = self._frozen(invoice)
            data = self._verified_xml(invoice, version)
            return data, version.content_type, self._xml_filename(snapshot, year, number)
        if version.content_type != PDF_MIME:
            # The PDF is served as a PDF or not at all (REB-480). `add_version` refuses any
            # other type on this document now, so this is a version written before that:
            # answered as a missing PDF, which the web already words as «non è disponibile»
            # and, where it repairs it, points at «Rigenera documenti».
            raise NotFound("invoice_artifact", f"{invoice_id}#{kind}")
        _, _, filename = self._pdf_identity(invoice)
        return ctx.storage.get(version.storage_key), version.content_type, filename
