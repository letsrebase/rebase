"""The only writer of `invoices` and `invoice_lines`.

This task covers everything that happens before a number exists. A draft and a
proforma are ordinary mutable rows; the number, the freezing and the artefacts belong
to `issue`, `annul` and the artefact methods added by the following tasks.
"""

import hashlib
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from pigrocrm.core.activities.service import ActivityService
from pigrocrm.core.actor import Actor
from pigrocrm.core.clock import ITALY_TZ, oggi_in_italia
from pigrocrm.core.config import Settings, get_settings
from pigrocrm.core.customers.models import Customer
from pigrocrm.core.customers.service import CustomerService
from pigrocrm.core.deals.models import Deal
from pigrocrm.core.documents.models import Document
from pigrocrm.core.documents.schemas import DocumentCreate
from pigrocrm.core.documents.service import ENTITY as DOCUMENT_ENTITY
from pigrocrm.core.documents.service import DocumentService
from pigrocrm.core.drive.reader import ALREADY_AUTHORIZED, DriveReader, drive_reader_for
from pigrocrm.core.emitter.service import EmitterProfileService
from pigrocrm.core.errors import Conflict, ImmutableField, NotFound, ValidationFailed
from pigrocrm.core.fields.schemas import EntityType
from pigrocrm.core.fields.service import FieldDefinitionService
from pigrocrm.core.fields.validator import validate_custom_fields
from pigrocrm.core.fiscal.regime import RegimeStrategy, resolve_regime
from pigrocrm.core.fiscal.schemas import FiscalSnapshot
from pigrocrm.core.fiscal.service import FiscalProfileService
from pigrocrm.core.invoices import pdf as invoice_pdf
from pigrocrm.core.invoices.fatturapa import (
    FatturaPAExporter,
    check_document_text_exportable,
    check_party_exportable,
    check_recipient_identity,
    check_recipient_routing,
    normalise_fiscal_id,
)
from pigrocrm.core.invoices.import_classification import classify_parsed_invoice
from pigrocrm.core.invoices.import_confirm import (
    ConfirmedInvoiceRead,
    map_parsed_invoice_to_import,
    map_parsed_party_to_customer,
)
from pigrocrm.core.invoices.import_review import (
    ReviewedInvoiceRead,
    detect_adapter,
    existing_for,
    match_customer,
    natural_key,
    review_content,
)
from pigrocrm.core.invoices.import_schemas import ParsedInvoiceParty
from pigrocrm.core.invoices.models import Invoice, InvoiceLine, InvoiceRegisterGap
from pigrocrm.core.invoices.naming import (
    invoice_storage_prefix,
    numero_completo,
    proforma_riferimento,
    proforma_storage_prefix,
    sdi_filename,
)
from pigrocrm.core.invoices.repository import InvoiceRepository
from pigrocrm.core.invoices.scadenza import scadenza_da_termini
from pigrocrm.core.invoices.schemas import (
    ANNO_MAX,
    ANNO_MIN,
    DIVISA,
    SNAPSHOT_VERSIONE,
    TIPO_DOCUMENTO,
    ArtifactKind,
    InvoiceAnnul,
    InvoiceArtifact,
    InvoiceCreate,
    InvoiceForExport,
    InvoiceImport,
    InvoiceIssue,
    InvoiceLineIn,
    InvoiceLineRead,
    InvoiceListQuery,
    InvoicePage,
    InvoiceRead,
    InvoiceSnapshot,
    InvoiceTransmitted,
    InvoiceUpdate,
    PartySnapshot,
    PaymentState,
    PdfSorgente,
    RegisterGapRead,
    RegisterGapsDeclare,
)
from pigrocrm.core.invoices.totals import (
    MONEY_MAX_EXCLUSIVE,
    ComputedLine,
    build_riepilogo,
    line_total,
    overflows_money_column,
    round_money,
    sum_totals,
)
from pigrocrm.core.schemas import reject_cleared_columns, supplied_changes
from pigrocrm.core.storage.base import DocumentStorage

ENTITY: EntityType = "invoice"
ZERO = Decimal("0.00")
IMPORT_ACTION = "import_issued_invoice"
GAPS_ACTION = "declare_invoice_register_gaps"
REVIEW_ACTION = "review_invoice_import"
CONFIRM_ACTION = "confirm_invoice_import"
# How many undeclared numbers `issue`'s refusal spells out. The whole list always stays
# in `details["numeri"]`, machine-readable; the *sentence* is read by a person, and a
# register whose lowest imported number is high can leave hundreds of them.
GAPS_SHOWN_IN_MESSAGE = 20

# The mime the original of a fattura has to be, and the only one `_resolve_original_pdf`
# accepts from Drive: the point of §3.5 is the document the customer received, and a
# `.docx` or a scan-shaped `image/jpeg` is not that document even when it looks like it.
PDF_MIME = "application/pdf"
# The name a Drive refusal uses when the grant is missing a scope ("<feature> non è
# disponibile: manca l'autorizzazione ..."), so it reads as a feature that is off rather
# than as a broken credential.
DRIVE_FEATURE = "import della fattura"

# What stays writable once a `fattura` has left the `bozza` state (spec 4). Everything
# else on the row is frozen, and an attempt raises `ImmutableField` naming the field.
# `stato_pagamento`/`data_incasso` are absent because they have their own method, which
# is what makes their agreement checkable; `stato` is absent for the same reason.
MUTABLE_AFTER_ISSUE: frozenset[str] = frozenset({"note_interne", "custom_fields"})


@dataclass(frozen=True)
class _FetchedPdf:
    """The original PDF already read from Drive, waiting for a row to belong to.

    `import_issued` reads Drive among its *pure* checks -- a network read is not a
    database write, and a file outside the roots or a `.txt` in place of the invoice
    has to be refused before the counter is locked. But the `documents` row it becomes
    cannot be written until the invoice exists, so what crosses that boundary is this:
    the bytes, and the id they came from for the provenance record.
    """

    contenuto: bytes
    drive_file_id: str
    mime: str


class InvoiceService:
    def __init__(
        self,
        session: Session,
        storage: DocumentStorage,
        settings: Settings | None = None,
        *,
        drive_reader_factory: Callable[[Actor], DriveReader] | None = None,
    ) -> None:
        self.session = session
        self.storage = storage
        self.settings = settings or get_settings()
        self.repo = InvoiceRepository(session)
        self.fields = FieldDefinitionService(session)
        self.activities = ActivityService(session)
        self.fiscal = FiscalProfileService(session)
        self.emitter = EmitterProfileService(session)
        self.documents = DocumentService(session, storage, self.settings)
        # REB-367 (design §7 item 5): the same session as everything else here, so a
        # customer `confirm_import` creates for a brand-new counterparty lands in the
        # exact transaction the invoice write is about to commit, never a customer
        # committed on its own moments before a register failure.
        self.customers = CustomerService(session)
        # The one seam of §3.5: `None` in production, where the reader is composed from
        # the actor's own stored Drive account by `drive_reader_for`. A test that wants
        # an in-memory Drive injects the factory instead of seeding an account row and a
        # sealed refresh token -- and the tests that *are* about the credential path
        # leave this `None` and monkeypatch the HTTP transport, so neither wiring is
        # asserted only through the other.
        self._drive_reader_factory = drive_reader_factory

    # ---- shared helpers -------------------------------------------------------

    def _check_owner(self, customer_id: UUID, deal_id: UUID | None) -> Customer:
        """A syntactically valid but unknown UUID becomes this project's own
        `NotFound` instead of a raw `ForeignKeyViolation` reaching the caller from
        `flush()`. The nullable `deal_id` is checked too whenever a value is supplied
        -- skipping a nullable FK is the defect `deals.owner_id` shipped with.

        Returns the customer, because the regime needs its country to pick the
        `Natura` of every line (ORB-32) and the row has just been read anyway."""
        customer = self.session.get(Customer, customer_id)
        if customer is None:
            raise NotFound("customer", customer_id)
        if deal_id is None:
            return customer
        deal = self.session.get(Deal, deal_id)
        if deal is None:
            raise NotFound("deal", deal_id)
        if deal.customer_id != customer_id:
            raise ValidationFailed(
                ENTITY,
                "deal_id",
                "il deal appartiene a un altro cliente",
                expected=f"un deal del cliente {customer_id}",
            )
        return customer

    def _regime(self) -> tuple[RegimeStrategy, FiscalSnapshot]:
        """The strategy and the parameters, read together so a caller cannot pair a
        profile with the wrong strategy. Raises `NotFound("fiscal_profile", ...)` when
        nothing is configured, which tells the user which screen to go to."""
        profile = self.fiscal.snapshot()
        return resolve_regime(profile.codice_regime), profile

    def _computed_lines(
        self, righe: Sequence[InvoiceLineIn], profile: FiscalSnapshot, *, nazione_cliente: str
    ) -> tuple[ComputedLine, ...]:
        """Caller input plus the regime's answer, renumbered from 1.

        Renumbering here is what maintains contiguity: the unique constraint on
        `(invoice_id, numero_linea)` and the `>= 1` check are the database's half, and
        no single-row `CHECK` can see the other rows.

        `nazione_cliente` is the customer's country as it is *now*: the `Natura` is
        decided when the lines are computed and copied unchanged by `issue`, so a
        proforma whose customer changed country is repaired by replacing its lines.
        """
        strategy = resolve_regime(profile.codice_regime)
        computed: list[ComputedLine] = []
        for index, riga in enumerate(righe, start=1):
            aliquota, natura, riferimento = strategy.resolve_line_vat(
                riga.aliquota_iva, profile, nazione_cliente=nazione_cliente
            )
            prezzo_totale = line_total(
                quantita=riga.quantita,
                prezzo_unitario=riga.prezzo_unitario,
                sconto_percentuale=riga.sconto_percentuale,
                sconto_importo=riga.sconto_importo,
            )
            # The factors mirror `Numeric(12, 6)` in the schema; their product goes to
            # `invoice_lines.prezzo_totale`, which is `Numeric(12, 2)`, and no Pydantic
            # bound on two factors can express a bound on their product. Refused here,
            # before anything is flushed: Postgres answers an overflow with
            # `NumericValueOutOfRange`, which SQLAlchemy raises as `DataError` and not
            # `IntegrityError`, so no handler catches it -- an unhandled 500 with the
            # caller's transaction already aborted. The line is named because an invoice
            # may carry `MAX_LINES` of them.
            if overflows_money_column(prezzo_totale):
                raise ValidationFailed(
                    ENTITY,
                    "righe",
                    f"l'importo della riga {index} non e' rappresentabile: quantita per "
                    "prezzo unitario, al netto degli sconti, supera il massimo",
                    expected=f"un importo con valore assoluto inferiore a {MONEY_MAX_EXCLUSIVE}",
                )
            computed.append(
                ComputedLine(
                    numero_linea=index,
                    descrizione=riga.descrizione,
                    quantita=riga.quantita,
                    unita_misura=riga.unita_misura,
                    prezzo_unitario=riga.prezzo_unitario,
                    sconto_percentuale=riga.sconto_percentuale,
                    sconto_importo=riga.sconto_importo,
                    prezzo_totale=prezzo_totale,
                    aliquota_iva=aliquota,
                    natura=natura,
                    riferimento_normativo=riferimento,
                )
            )
        return tuple(computed)

    def _apply_totals(
        self, invoice: Invoice, computed: Sequence[ComputedLine], profile: FiscalSnapshot
    ) -> None:
        """Compute and **store**. Never recomputed by a client: a total computed in
        the browser is the structural defect inherited from the previous system,
        and on an invoice it costs more."""
        strategy = resolve_regime(profile.codice_regime)
        riepilogo = build_riepilogo(computed)
        imponibile, imposta, totale = sum_totals(riepilogo)
        # The stamp duty is stored but does not enter the total: `DatiBollo` declares
        # that the issuer settled it virtually (spec 7.2).
        bollo = strategy.bollo(riepilogo, profile)

        # Summation reaches the same overflow with no oversized line anywhere:
        # `InvoiceCreate.righe` admits `MAX_LINES` of them, and two hundred lines of
        # fifty million each are two hundred perfectly ordinary amounts whose sum is not.
        # Checked before a single attribute is assigned, so a refused invoice leaves no
        # unstorable value sitting on a mapped object for the next autoflush to find.
        for field, value in (
            ("imponibile", imponibile),
            ("imposta", imposta),
            ("totale", totale),
            ("bollo", bollo),
        ):
            if overflows_money_column(value):
                raise ValidationFailed(
                    ENTITY,
                    field,
                    f"la somma delle righe non e' rappresentabile: {field} supera il massimo",
                    expected=f"un importo con valore assoluto inferiore a {MONEY_MAX_EXCLUSIVE}",
                )

        invoice.imponibile = imponibile
        invoice.imposta = imposta
        invoice.totale = totale
        invoice.bollo = bollo

    def _persist_lines(self, invoice: Invoice, computed: Sequence[ComputedLine]) -> None:
        self.repo.clear_lines(invoice.id)
        for riga in computed:
            self.repo.add_line(
                InvoiceLine(
                    invoice_id=invoice.id,
                    numero_linea=riga.numero_linea,
                    descrizione=riga.descrizione,
                    quantita=riga.quantita,
                    unita_misura=riga.unita_misura,
                    prezzo_unitario=riga.prezzo_unitario,
                    sconto_percentuale=riga.sconto_percentuale,
                    sconto_importo=riga.sconto_importo,
                    prezzo_totale=riga.prezzo_totale,
                    aliquota_iva=riga.aliquota_iva,
                    natura=riga.natura,
                    riferimento_normativo=riga.riferimento_normativo,
                )
            )

    def _validated_custom(self, values: dict[str, Any]) -> dict[str, Any]:
        return validate_custom_fields(ENTITY, self.fields.specs_for(ENTITY), values)

    def _update_custom_fields(self, invoice: Invoice, provided: dict[str, Any]) -> dict[str, Any]:
        """Validates only the keys the caller is touching, never the merge with what is
        stored -- identical in shape to `CustomerService._update_custom_fields`, and for
        the same reason: re-validating the merge would let archiving a field block every
        future custom-field update on rows that still hold it."""
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
        merged = {k: v for k, v in invoice.custom_fields.items() if k not in to_remove}
        merged.update(validate_custom_fields(ENTITY, touched, to_set))
        return merged

    def _is_editable(self, invoice: Invoice) -> bool:
        """A `fattura` is editable only as a `bozza`; a `proforma` is editable until it
        is `consumata`. That asymmetry is the point of a proforma (spec 5): agree the
        amount, correct it as many times as needed, without touching the register."""
        if invoice.tipo == "proforma":
            return invoice.stato in ("bozza", "confermata")
        return invoice.stato == "bozza"

    def _require_editable(self, invoice: Invoice, field: str) -> None:
        if not self._is_editable(invoice):
            raise ImmutableField(
                ENTITY,
                field,
                f"una fattura in stato '{invoice.stato}' e' un documento fiscale: "
                "si corregge con un annullamento e una nuova emissione, non con una modifica",
            )

    @staticmethod
    def _check_competenza(da: date | None, a: date | None) -> None:
        """The accrual period as it will be stored: both ends or neither, in order.

        Checked on the *merged* row by `update`, not on the request, because completing
        a period whose other end is already stored is a one-field change. The two table
        CHECKs (`ck_invoices_competenza_together`, `ck_invoices_competenza_ordered`)
        refuse the same shapes on every other write path; this is what turns a
        violation into a `ValidationFailed` naming the field instead of a poisoned
        session.
        """
        if (da is None) != (a is None):
            missing = "competenza_a" if a is None else "competenza_da"
            raise ValidationFailed(
                ENTITY,
                missing,
                "un periodo di competenza ha un inizio e una fine: indicali entrambi "
                "oppure nessuno dei due",
                expected="competenza_da e competenza_a insieme",
            )
        if da is not None and a is not None and a < da:
            raise ValidationFailed(
                ENTITY,
                "competenza_a",
                "il periodo di competenza finisce prima di cominciare",
                expected=f"una data dal {da.isoformat()} in poi",
            )

    # ---- writes ---------------------------------------------------------------

    def create(self, data: InvoiceCreate, actor: Actor) -> InvoiceRead:
        """A draft or a proforma. Neither has a number, which is why a failed creation
        cannot burn one -- not as a matter of care, but because there is nothing to
        burn until `issue` runs."""
        actor.require_write("create_invoice")
        customer = self._check_owner(data.customer_id, data.deal_id)
        _, profile = self._regime()
        self._check_competenza(data.competenza_da, data.competenza_a)
        if data.tipo != "proforma" and data.data_emissione is not None:
            raise ValidationFailed(
                ENTITY,
                "data_emissione",
                "una bozza di fattura non ha data: la prende all'emissione, "
                "quando entra nel registro",
                expected="nessuna data, oppure tipo = 'proforma'",
            )

        invoice = Invoice(
            customer_id=data.customer_id,
            deal_id=data.deal_id,
            tipo=data.tipo,
            stato="bozza",
            tipo_documento=TIPO_DOCUMENTO,
            divisa=DIVISA,
            causale=data.causale,
            competenza_da=data.competenza_da,
            competenza_a=data.competenza_a,
            note_interne=data.note_interne,
            imponibile=ZERO,
            imposta=ZERO,
            bollo=ZERO,
            totale=ZERO,
            stato_pagamento="da_incassare",
            custom_fields=self._validated_custom(data.custom_fields or {}),
        )
        if data.tipo == "proforma":
            # `oggi_in_italia()`, not `date.today()`: see `clock.py`'s module docstring
            # for why a bare `date.today()` on a host that is not running in
            # Europe/Rome (this project's own `Dockerfile.api` pins no `TZ`, so its
            # base image defaults to UTC) reproduces the previous system's UTC-instant defect
            # through the standard library's default rather than an explicit
            # conversion. Low-stakes here specifically -- `riferimento` is a
            # non-fiscal, internal-only identifier, not a register entry -- but there
            # is no reason to let the wrong clock answer the question at all.
            invoice.riferimento = proforma_riferimento(
                oggi_in_italia().year, self.repo.next_proforma_sequence()
            )
            # A proforma's own document date (ORB-63): the sender's, stored here so the
            # PDF stops printing the render day. Not a register date -- none of
            # `_check_issue_date`'s limits apply, and the fattura born from this row
            # takes its own date at `issue` -- which is why any date is accepted.
            invoice.data_emissione = data.data_emissione or oggi_in_italia()
        computed = self._computed_lines(data.righe, profile, nazione_cliente=customer.nazione)
        self._apply_totals(invoice, computed, profile)
        self.repo.add(invoice)
        self._persist_lines(invoice, computed)
        self.activities.record(
            ENTITY,
            invoice.id,
            "created",
            actor,
            {"tipo": invoice.tipo, "righe": len(computed), "totale": str(invoice.totale)},
        )
        self.session.commit()
        return self._read(invoice)

    def update(self, invoice_id: UUID, data: InvoiceUpdate, actor: Actor) -> InvoiceRead:
        actor.require_write("update_invoice")
        invoice = self._require(invoice_id)
        changes = supplied_changes(data, exclude={"custom_fields"})
        reject_cleared_columns(ENTITY, Invoice, changes)
        frozen = [key for key in changes if key not in MUTABLE_AFTER_ISSUE]
        if frozen and not self._is_editable(invoice):
            raise ImmutableField(
                ENTITY,
                sorted(frozen)[0],
                f"campo congelato su un documento in stato '{invoice.stato}'",
            )
        if "data_emissione" in changes:
            # Editable only on a proforma, and only ever to another date: a fattura's
            # date is the register's and `issue` assigns it (spec 6.2), and a proforma
            # without a date would put the render day back on its PDF (ORB-63).
            if invoice.tipo != "proforma":
                raise ValidationFailed(
                    ENTITY,
                    "data_emissione",
                    "la data di una fattura la assegna l'emissione, non una modifica",
                    expected="nessuna data su una bozza di fattura",
                )
            if changes["data_emissione"] is None:
                raise ValidationFailed(
                    ENTITY,
                    "data_emissione",
                    "una proforma porta sempre una data: si sposta, non si toglie",
                    expected="una data",
                )
        if "competenza_da" in changes or "competenza_a" in changes:
            self._check_competenza(
                changes.get("competenza_da", invoice.competenza_da),
                changes.get("competenza_a", invoice.competenza_a),
            )
        if data.custom_fields is not None:
            changes["custom_fields"] = self._update_custom_fields(invoice, data.custom_fields)
        for key, value in changes.items():
            setattr(invoice, key, value)
        self.activities.record(ENTITY, invoice.id, "updated", actor, {"changed": sorted(changes)})
        self.session.commit()
        return self._read(invoice)

    def replace_lines(
        self, invoice_id: UUID, righe: list[InvoiceLineIn], actor: Actor
    ) -> InvoiceRead:
        """The whole list, never a partial patch.

        The reason from spec 11 that still stands: it is the natural shape of a line
        editor, and a line's totals are derived from the whole list, so patching one
        line in place would leave the invoice's totals to be reconciled separately. The
        second reason it was written for -- that while `exclude_none=True` was the
        update contract no optional numeric or date column could be cleared at all
        (A14) -- was retired by task 4B-1, which closed that defect rather than
        continuing to sidestep it.
        """
        actor.require_write("replace_invoice_lines")
        invoice = self._require(invoice_id)
        self._require_editable(invoice, "righe")
        customer = self._check_owner(invoice.customer_id, None)
        _, profile = self._regime()
        computed = self._computed_lines(righe, profile, nazione_cliente=customer.nazione)
        self._apply_totals(invoice, computed, profile)
        self._persist_lines(invoice, computed)
        self.activities.record(
            ENTITY,
            invoice.id,
            "lines_replaced",
            actor,
            {"righe": len(computed), "totale": str(invoice.totale)},
        )
        self.session.commit()
        return self._read(invoice)

    def confirm_proforma(self, invoice_id: UUID, actor: Actor) -> InvoiceRead:
        """`bozza` -> `confermata` on a proforma: the amount is agreed and the document
        is ready to be sent, still without touching the register."""
        actor.require_write("confirm_proforma")
        invoice = self._require(invoice_id)
        if invoice.tipo != "proforma":
            raise Conflict(
                ENTITY, "solo una proforma si conferma", tipo=invoice.tipo, stato=invoice.stato
            )
        if invoice.stato != "bozza":
            raise Conflict(
                ENTITY,
                f"da '{invoice.stato}' non si puo' passare a 'confermata'",
                stato_attuale=invoice.stato,
            )
        if not self.repo.lines(invoice.id):
            raise ValidationFailed(
                ENTITY,
                "righe",
                "una proforma senza righe non si conferma",
                expected="almeno una riga",
            )
        invoice.stato = "confermata"
        self.activities.record(ENTITY, invoice.id, "confirmed", actor)
        self.session.commit()
        return self._read(invoice)

    def soft_delete(self, invoice_id: UUID, actor: Actor) -> None:
        """Only what never consumed a number, and never a `consumata` proforma.

        Checked here **and** by `ck_invoices_no_delete_once_consumed`, which is what
        makes the rule true for a psql session too. Without the gap-free register the
        numbering guarantee of spec 3 would be worth nothing: a number that can be
        deleted is a gap with extra steps.

        The artefact rows go with the invoice (ORB-41). A proforma's PDF is filed among
        the customer's documents by `produce_artifacts`, and `DocumentRepository.list`
        filters on `documents.deleted_at` alone, so until this archived it too the
        PDF stayed listed and downloadable while `get` on its owner answered not found.
        Done here at repository level rather than through `DocumentService.soft_delete`
        for two reasons: that method commits on its own, and the two rows must fall
        in one transaction or a crash in between leaves exactly the orphan this fixes;
        and the permission that governs is the invoice's, already checked above. A soft
        delete like the invoice's own: the row and the bytes stay, so a restore of the
        document is still a real restore. The table CHECK above guarantees this never
        reaches a fiscal artefact, since a numbered invoice cannot get this far.
        """
        actor.require_write("delete_invoice")
        invoice = self._require(invoice_id)
        if invoice.numero is not None or invoice.stato == "consumata":
            raise Conflict(
                ENTITY,
                "un documento che ha consumato un numero non si elimina: "
                "si annulla, conservando il numero",
                stato=invoice.stato,
                numero=invoice.numero,
            )
        now = datetime.now(UTC)
        invoice.deleted_at = now
        try:
            # The UPDATE reaches PostgreSQL here, on purpose, and not at whichever later
            # statement happens to autoflush: `DocumentRepository.get` and
            # `ActivityService.record` both flush, and a CHECK violation raised there
            # was outside this handler, so the race below surfaced as a raw
            # `IntegrityError` on a session that still needed a rollback (ORB-57).
            self.session.flush()
        except IntegrityError as exc:
            # The pre-check above cannot cover a row that was issued concurrently: the
            # CHECK is the real authority, and the rollback is mandatory or the
            # caller's session is unusable on its next statement.
            self.session.rollback()
            raise Conflict(
                ENTITY, "il documento e' stato emesso nel frattempo e non si elimina piu'"
            ) from exc
        # From here on the row is ours until the commit: the flush took its lock, so an
        # emission that arrives now waits, and then fails its own CHECK on a deleted row.
        for document_id in (invoice.pdf_document_id, invoice.xml_document_id):
            if document_id is None:
                continue
            document = self.documents.repo.get(document_id)
            if document is None:
                # Already archived, by hand or by an earlier attempt: nothing to redo.
                continue
            document.deleted_at = now
            self.activities.record(DOCUMENT_ENTITY, document.id, "deleted", actor)
        self.activities.record(ENTITY, invoice.id, "deleted", actor)
        self.session.commit()

    def set_payment_state(self, invoice_id: UUID, data: PaymentState, actor: Actor) -> InvoiceRead:
        """Collection is a subsequent fact, not part of the document (spec 4), so this
        is the one invoice write a collaborator -- and an agent -- may perform."""
        actor.require_write("set_payment_state")
        invoice = self._require(invoice_id)
        if invoice.stato != "emessa":
            raise Conflict(
                ENTITY,
                "solo una fattura emessa ha un incasso da registrare",
                stato=invoice.stato,
            )
        if data.stato_pagamento == "incassato" and data.data_incasso is None:
            raise ValidationFailed(
                ENTITY,
                "data_incasso",
                "un incasso senza data non e' un incasso",
                expected="la data in cui il pagamento e' arrivato",
            )
        invoice.stato_pagamento = data.stato_pagamento
        # Cleared rather than left dangling: the table's own
        # `ck_invoices_incasso_requires_state` would refuse the inconsistent pair
        # anyway, and a stale date on an uncollected invoice is a lie either way.
        invoice.data_incasso = data.data_incasso if data.stato_pagamento == "incassato" else None
        self.activities.record(
            ENTITY,
            invoice.id,
            "payment_state_changed",
            actor,
            {"stato_pagamento": invoice.stato_pagamento},
        )
        self.session.commit()
        return self._read(invoice)

    def _party_from_customer(self, customer: Customer) -> PartySnapshot:
        return PartySnapshot(
            ragione_sociale=customer.ragione_sociale,
            partita_iva=customer.partita_iva,
            codice_fiscale=customer.codice_fiscale,
            codice_sdi=customer.codice_sdi,
            pec=customer.pec,
            indirizzo=customer.indirizzo or "",
            cap=customer.cap or "",
            comune=customer.comune or "",
            provincia=customer.provincia or "",
            nazione=customer.nazione,
            email=customer.email,
            telefono=customer.telefono,
            sito_web=customer.sito_web,
        )

    def _party_from_emitter(self, actor: Actor) -> PartySnapshot:
        """The issuer's identity from `emitter_profile` (slice 2).

        `emitter_profile.regime_fiscale` is deliberately not read here: it is a
        human-readable caption for the PDF header, `String(200)` of free text, and the
        machine value the SdI validates is `fiscal_profile.codice_regime`. Two columns,
        two jobs; conflating them is how a caption ends up inside `RegimeFiscale`.
        """
        profile = self.emitter.get(actor)
        return PartySnapshot(
            ragione_sociale=profile.ragione_sociale,
            partita_iva=profile.partita_iva,
            codice_fiscale=profile.codice_fiscale,
            codice_sdi=profile.codice_sdi,
            pec=profile.pec,
            indirizzo=profile.indirizzo or "",
            cap=profile.cap or "",
            comune=profile.comune or "",
            provincia=profile.provincia or "",
            nazione=profile.nazione,
            email=profile.email,
            telefono=profile.telefono,
            sito_web=profile.sito_web,
        )

    def _build_snapshot(
        self, customer_id: UUID, profile: FiscalSnapshot, actor: Actor
    ) -> InvoiceSnapshot:
        """The frozen identities, from an id rather than from a row.

        `customer_id` and not an `Invoice` on purpose: `_party_from_emitter` raises
        `NotFound("emitter_profile")` whenever the issuer's own profile is missing --
        the exact state a fresh installation is in before §2.3 is done -- and
        `import_issued` has to be able to hit that refusal *before* it flushes anything.
        Taking the flushed row as the argument made that impossible by construction.
        `issue` and `_for_export_proforma` pass `invoice.customer_id` and are unchanged
        in behaviour.
        """
        customer = self.session.get(Customer, customer_id)
        if customer is None:  # pragma: no cover - the FK makes this unreachable
            raise NotFound("customer", customer_id)
        return InvoiceSnapshot(
            versione=SNAPSHOT_VERSIONE,
            emittente=self._party_from_emitter(actor),
            cliente=self._party_from_customer(customer),
            fiscale=profile,
        )

    def _scadenza_dai_termini(
        self, data_emissione: date, customer_id: UUID, giorni_profilo: int
    ) -> date:
        """The due date the customer's terms give from `data_emissione` (REB-326). The
        customer's days when it has them, the profile's otherwise; the end-of-month slide
        is the customer's alone. One arithmetic, `scadenza_da_termini`, for every caller."""
        giorni, fine_mese = self.repo.payment_terms({customer_id}).get(customer_id, (None, False))
        return scadenza_da_termini(
            data_emissione, giorni_profilo if giorni is None else giorni, fine_mese=fine_mese
        )

    def _check_issue_date(self, data_emissione: date, anno_corrente: int) -> None:
        """Two limits, both from spec 6.2.

        `data_emissione` is a `date` in the issuer's own calendar, never the UTC
        projection of an instant: `toISOString()` on 31 December at 23:30 CET yields
        1 January, which puts an immutable document in the wrong fiscal year. That is
        the defect this whole method exists around, and the fix is `oggi_in_italia()`
        -- not a bare `date.today()`, which would only be safe if this process were
        guaranteed to run with Italy's own timezone, and it is not (see `clock.py`).
        """
        oggi = oggi_in_italia()
        if data_emissione > oggi:
            raise ValidationFailed(
                ENTITY,
                "data_emissione",
                "una fattura non si emette con data futura",
                expected=f"una data non successiva a {oggi.isoformat()}",
            )
        if data_emissione < date(anno_corrente, 1, 1):
            raise ValidationFailed(
                ENTITY,
                "data_emissione",
                "un anno chiuso e' chiuso: non si inserisce nel registro di un anno "
                "precedente dopo che ne e' iniziato uno nuovo",
                expected=f"una data dal {anno_corrente}-01-01 in poi",
            )

    def _check_issuable(self, source: Invoice, from_proforma: bool) -> None:
        """Whether this row may still become a fiscal document.

        Extracted because `issue` asks it twice — cheaply before taking the year's
        counter lock, and authoritatively after — and two copies of a refusal message
        drift. See `issue`'s docstring for why once is not enough.
        """
        if from_proforma:
            if source.stato != "confermata":
                raise Conflict(
                    ENTITY,
                    "solo una proforma confermata si converte in fattura",
                    stato_attuale=source.stato,
                    stato_richiesto="confermata",
                )
        elif source.stato != "bozza":
            raise Conflict(
                ENTITY,
                f"una fattura in stato '{source.stato}' e' gia' stata emessa: "
                "una correzione e' un annullamento e una nuova fattura",
                stato_attuale=source.stato,
            )

    def issue(self, invoice_id: UUID, data: InvoiceIssue, actor: Actor) -> InvoiceRead:
        """Consume a register number. **One transaction, in this exact order.**

        `invoice_id` names either a `bozza` **fattura**, issued in place, or a
        `confermata` **proforma**, in which case a *new* `emessa` row is created with
        the proforma's lines copied and `origine_proforma_id` pointing back at it, and
        the proforma is marked `consumata` (spec 5). One method, because emitting from
        scratch and from a proforma share the lock, the validations, the freezing and
        the numbering, and splitting them would mean two paths to keep aligned on
        exactly the part that must not diverge.

        Ordering, and why each step is where it is:

        1. resolve the source row and the issue date, and check the date against
           "not in the future, not before 1 January of the current year". No lock yet:
           these are pure checks on the caller's own input;
        2. `lock_counter(anno)` -- the **first** row lock this transaction takes;
        3. every fiscal validation, the totals, and the chronological-monotonicity
           check. All of it after the lock, so "the date of the previous number" is a
           safe thing to read, and all of it *before* the counter is touched, so a
           refusal never even reaches the increment;
        4. increment the counter, write the row with `(anno, numero)`, write the
           frozen `snapshot`, write the activity;
        5. `COMMIT`.

        **The number does not exist before the commit.** If any step fails, the
        rollback returns `ultimo_numero` to its previous value and nothing was
        consumed -- the property a `SEQUENCE` does not have.

        The PDF and the XML are produced **after** this commit, in a second
        transaction, from the snapshot. Holding a row lock for the duration of a Typst
        subprocess would serialise every emission on PDF compile time, and an invoice
        is a legal fact independent of its printout: if the render fails, the invoice
        exists with its number and its artefacts regenerate deterministically. That is
        the one documented exception to "one service method = one transaction", and
        spec 3 mandates it.

        Two concurrent `issue()` calls on the same `invoice_id` are **in** scope, and
        this is the one method in the class where that matters. The others --
        `update`, `replace_lines`, `confirm_proforma`, `soft_delete` -- also read via
        `_require` without a lock, and a race there is an ordinary lost update: the
        second write wins and the row is consistent. Here the loser would consume a
        register number and then overwrite the winner's number on the very same row,
        leaving the first number owned by no invoice. `uq_invoices_anno_numero` cannot
        see it, because the row simply carries a different number. That is a permanent
        gap in the register -- the single property this whole design exists to
        guarantee, and the reason it is a locked counter row rather than a `SEQUENCE`.

        The cure needs no new lock and no lock-order decision. The state is checked
        twice: once before the counter lock, to refuse an obviously-doomed request
        without serialising on it, and once *after*, which is the authoritative one.
        `lock_counter` blocks until the other emission's transaction ends, so by the
        time this transaction holds that lock the competing commit is visible, and the
        re-read sees `emessa`. The order stays counter-then-row throughout, so the
        deadlock argument in `lock_counter`'s own docstring is unchanged.
        """
        actor.require_admin("issue_invoice")
        source = self._require(invoice_id)
        data_emissione = data.data_emissione or oggi_in_italia()
        anno = data_emissione.year
        self._check_issue_date(data_emissione, oggi_in_italia().year)

        from_proforma = source.tipo == "proforma"
        self._check_issuable(source, from_proforma)

        # Step 2. From here on, every other emission for this year waits.
        counter = self.repo.lock_counter(anno)

        # An imported register can arrive with numbers out of order -- the whole point
        # of slice 9 is that the history is not imported number-by-number in sequence.
        # A gap in it is *silent* until someone names it (`declare_gaps`), and native
        # issuing must not resume on top of a silent gap: doing so would make the next
        # native number look like it continues a register that in fact has an
        # unexplained hole underneath it. Checked here, inside the same lock that
        # protects the increment below, so a concurrent import cannot close the gap
        # and let this call through on a stale read.
        buchi = self.undeclared_gaps(anno)
        if buchi:
            # Rendered short, reported whole: `details["numeri"]` carries every number,
            # and the message names the first `GAPS_SHOWN_IN_MESSAGE` plus a count. An
            # import that starts at a high number can leave hundreds of holes, and a
            # refusal a person cannot read is a refusal that does not explain itself.
            mostrati = ", ".join(str(n) for n in buchi[:GAPS_SHOWN_IN_MESSAGE])
            if len(buchi) > GAPS_SHOWN_IN_MESSAGE:
                mostrati += f", ... ({len(buchi)} in tutto)"
            raise Conflict(
                ENTITY,
                f"il registro importato ha buchi non dichiarati ai numeri {mostrati}: "
                "dichiarali (o importali) prima di riprendere a emettere",
                anno=anno,
                numeri=buchi,
            )

        # And only now is the state answer trustworthy. The check above ran against a
        # read taken before any lock: a competing `issue()` on this same row could have
        # been between its own check and its own commit at that moment. Acquiring the
        # counter lock is what orders the two transactions -- it is released only at the
        # other one's commit -- so re-reading here sees whatever that emission actually
        # did. Without this, both calls pass the check, both take a number, and the
        # second overwrites the first: one number consumed and carried by nobody.
        self.session.refresh(source)
        self._check_issuable(source, from_proforma)

        # Step 3. Validations and totals, after the lock and before the increment.
        _, profile = self._regime()
        snapshot = self._build_snapshot(source.customer_id, profile, actor)
        check_party_exportable(snapshot.emittente, "emitter_profile")
        check_party_exportable(snapshot.cliente, "customer")
        check_recipient_routing(snapshot.cliente)
        check_recipient_identity(snapshot.cliente)

        righe = self.repo.lines(source.id)
        if not righe:
            raise ValidationFailed(
                ENTITY,
                "righe",
                "una fattura senza righe non si emette",
                expected="almeno una riga",
            )
        # The stored lines were computed when they were written, against the customer
        # and the profile of that moment, and `issue` copies them rather than recomputing
        # them because the amounts the customer agreed to must not move here. The
        # `Natura` pair is different: it is a statement about the customer's country and
        # the issuer's regime as they are *now*, and a draft or a proforma written before
        # the customer's country was corrected (ORB-32) would otherwise consume a register
        # number with the wrong one. So the regime is asked again and any disagreement
        # refuses, here, before the counter is touched; `replace_lines` is the remedy and
        # the message says so. `import_issued` is exempt by construction: it never reaches
        # this method, and its lines are declared, not computed.
        strategy = resolve_regime(profile.codice_regime)
        for r in righe:
            _, natura_attesa, riferimento_atteso = strategy.resolve_line_vat(
                r.aliquota_iva, profile, nazione_cliente=snapshot.cliente.nazione
            )
            if (r.natura, r.riferimento_normativo) != (natura_attesa, riferimento_atteso):
                raise ValidationFailed(
                    ENTITY,
                    "righe",
                    f"la riga {r.numero_linea} porta natura {r.natura} e un riferimento "
                    "normativo che non corrispondono al cliente e al profilo fiscale "
                    "attuali: sostituisci le righe prima di emettere",
                    expected=f"natura {natura_attesa} con il riferimento {riferimento_atteso!r}",
                )
        # The document's own text, by the writer's rules, before the counter moves. The
        # parties have had this since ORB-56; the causale and the lines did not, and an
        # em dash in a causale spent a number whose XML was then refused on every export
        # (ORB-140). What the check accepts here is what `export_xml` will serialise.
        check_document_text_exportable(source.causale, righe)

        computed = tuple(
            ComputedLine(
                numero_linea=r.numero_linea,
                descrizione=r.descrizione,
                quantita=r.quantita,
                unita_misura=r.unita_misura,
                prezzo_unitario=r.prezzo_unitario,
                sconto_percentuale=r.sconto_percentuale,
                sconto_importo=r.sconto_importo,
                prezzo_totale=r.prezzo_totale,
                aliquota_iva=r.aliquota_iva,
                natura=r.natura,
                riferimento_normativo=r.riferimento_normativo,
            )
            for r in righe
        )
        riepilogo = build_riepilogo(computed)
        imponibile, imposta, totale = sum_totals(riepilogo)
        if totale <= ZERO:
            raise ValidationFailed(
                ENTITY,
                "totale",
                "una TD01 a zero o negativa non e' una fattura",
                expected="un totale maggiore di zero",
            )

        previous = self.repo.last_issued_date(anno)
        if previous is not None and data_emissione < previous:
            raise ValidationFailed(
                ENTITY,
                "data_emissione",
                "il registro deve restare cronologicamente monotono rispetto al numero: "
                f"l'ultima fattura del {anno} porta la data {previous.isoformat()}",
                expected=f"una data dal {previous.isoformat()} in poi",
            )

        # Step 4. Increment, write, freeze.
        counter.ultimo_numero += 1
        numero = counter.ultimo_numero

        target = source
        if from_proforma:
            target = self.repo.add(
                Invoice(
                    customer_id=source.customer_id,
                    deal_id=source.deal_id,
                    tipo="fattura",
                    stato="bozza",
                    tipo_documento=TIPO_DOCUMENTO,
                    divisa=DIVISA,
                    causale=source.causale,
                    # The period is a fact about the work and travels with it. The
                    # date does not: the proforma's `data_emissione` is the sender's
                    # (ORB-63), and the fattura takes the register's below.
                    competenza_da=source.competenza_da,
                    competenza_a=source.competenza_a,
                    note_interne=source.note_interne,
                    imponibile=ZERO,
                    imposta=ZERO,
                    bollo=ZERO,
                    totale=ZERO,
                    stato_pagamento="da_incassare",
                    origine_proforma_id=source.id,
                    custom_fields=dict(source.custom_fields),
                )
            )
            self._persist_lines(target, computed)
            source.stato = "consumata"

        target.stato = "emessa"
        target.anno = anno
        target.numero = numero
        target.data_emissione = data_emissione
        # A date written on the draft (or on the proforma this fattura is born from) is
        # what the person meant; otherwise the customer's own terms, the profile's days
        # as the fallback (REB-326). Read here, at emission, and never again: a term
        # changed next month does not move an invoice already in the register.
        target.data_scadenza = source.data_scadenza or self._scadenza_dai_termini(
            data_emissione, source.customer_id, profile.giorni_scadenza
        )
        target.imponibile = imponibile
        target.imposta = imposta
        target.totale = totale
        target.bollo = strategy.bollo(riepilogo, profile)
        target.snapshot = snapshot.model_dump(mode="json")
        target.snapshot_versione = SNAPSHOT_VERSIONE

        self.activities.record(
            ENTITY,
            target.id,
            "issued",
            actor,
            {
                "anno": anno,
                "numero": numero,
                "totale": str(target.totale),
                "origine_proforma_id": str(source.id) if from_proforma else None,
            },
        )
        try:
            self.session.commit()
        except IntegrityError as exc:
            # The partial unique index `uq_invoices_anno_numero` is the net under the
            # row lock, not the mechanism (spec 3). Reaching it means something wrote
            # a number without taking the lock -- an importer, a direct INSERT, a
            # second service -- and this is what makes that failure observable instead
            # of a silent duplicate. The rollback is mandatory or the caller's session
            # is unusable on its next statement.
            self.session.rollback()
            raise Conflict(
                ENTITY,
                "un altro processo ha scritto lo stesso numero senza passare dal "
                "contatore: riprova e verifica il registro",
                anno=anno,
                numero=numero,
            ) from exc

        # Spec 3 asks for the render to happen **after** this commit, in a second
        # transaction, and it is right: holding the counter's row lock for the duration
        # of a Typst subprocess would serialise every emission on PDF compile time, and
        # an invoice is a legal fact independent of its printout.
        #
        # But the second transaction belongs to the **caller**, not to this method. When
        # `issue` called `produce_artifacts` itself, `issue` stopped being one
        # transaction: the artefact commit survived the test fixture's rollback, so an
        # issued invoice leaked across tests and the next first-invoice-of-2026 collided
        # on `uq_invoices_anno_numero`. Eight tests failed that way, and three more on
        # the `documents` row pinning a customer that teardown then could not remove.
        #
        # A leak that only shows up as someone else's failing test is the cheap version
        # of the same defect in production, where the transaction boundary would be
        # equally invisible and the consequence a partially-committed emission. So
        # `issue` returns here, one method and one transaction, and the adapters call
        # `produce_artifacts` next -- which is idempotent and regenerates from the
        # snapshot, so a crash between the two leaves an invoice that is fiscally
        # complete and merely unprinted.
        return self._read(target)

    def review_import(
        self, document_ids: Sequence[UUID], actor: Actor
    ) -> list[ReviewedInvoiceRead]:
        """Read-only review of one or more already-archived documents (REB-365,
        design record §4, §7 item 3): for each `document_id`, reads the document's
        own stored bytes back -- never a caller-supplied copy -- and reports one
        row per invoice they parse into, tagged `ready`, `needs_customer_
        confirmation`, `already_present`, `conflict`, `incoming_skipped`, or
        `unclaimed`. **No database write of any kind**: `review_content` only
        reads, and this method never calls `self.session.commit()`.

        Reviewing the same document twice runs the same reads against the same
        rows and returns the same verdict every time (the issue's own "Done
        when"): nothing here is a lock, a counter or a flush, so there is nothing
        for a second call to have changed.
        """
        actor.require_admin(REVIEW_ACTION)
        emitter = self.emitter.repo.get()
        if emitter is None:
            raise NotFound("emitter_profile", "singleton")
        rows: list[ReviewedInvoiceRead] = []
        for document_id in document_ids:
            content, _content_type, _filename = self.documents.download(document_id, None, actor)
            rows.extend(review_content(self.session, content, emitter, document_id))
        return rows

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
        direction and duplication against the database's *current* state, and --
        for every invoice that classifies `"ready"` -- maps it onto `InvoiceImport`
        and calls `import_issued` itself for the write, exactly as a hand-declared
        import would. Never a second, independently-maintained set of register
        rules (design §5): the write is the same call, in the same one transaction
        `import_issued` already commits.

        Confirming an invoice already on the register at its own natural key is a
        no-op: `"already_present"` is reported, never a duplicate insert. A
        supplier's invoice (`"incoming_skipped"`), a number already on record under
        a different or no hash (`"conflict"`), a document no adapter recognises
        (`"unclaimed"`), and `import_issued`'s own four further register rules
        (declared gaps, the first-native-number ceiling, chronological order both
        ways) refusing with `Conflict`/`ValidationFailed` are all reported as
        `"conflict"` and never written, mirroring `review_invoice_import`'s own
        vocabulary for the first three and never a silent drop for the last: a
        `lotto` batch keeps attempting every remaining invoice, and every invoice
        already committed earlier in the same call still comes back with its own
        row, exactly the guarantee `import_classification.py`'s own docstring
        states ("never a silent drop, always one named outcome per invoice").

        `customer_id` is the one human decision this issue's scope adds: which
        `Customer` this invoice attaches to. When omitted, the current exact
        tax-id match (`import_review.match_customer`, re-run against the
        database's current state, never review time's) is used.

        `create_customer` is REB-367's own addition (design §7 item 5): when
        still unresolved after both of the above, and `create_customer` is
        true, the matched party (`invoice.cliente`) is inserted as a new
        `Customer` -- through `CustomerService._insert`, the non-committing
        half of `create` -- inside this same transaction, never a premature
        commit that could leave a customer on file with no invoice if the
        register write fails a moment later. A `lotto` batch's several
        invoices from the same brand-new counterparty share one freshly
        created row, keyed on the parsed party itself (`ParsedInvoiceParty` is
        frozen and compares by value, so the second invoice in the batch finds
        the row the first one just created rather than inserting a second).
        `create_customer` false (the default) leaves today's behaviour
        unchanged: the row reports `"needs_customer_confirmation"` and nothing
        is written.

        **`xml_document_id`/`xml_hash_sha256`, for a single-invoice source
        document only** (design §5 item 3): when the document parses into
        exactly one invoice, the invoice row is pointed at the very `document_id`
        this call already read the bytes from -- never a second `documents` row
        for the same content -- and its hash is computed from those same bytes.
        A `lotto` batch's invoices leave both `NULL`, exactly like today's
        `esterno` path: a shared source document does not fit this project's
        document-ownership rules (design §5 item 3), so none of a batch's
        invoices takes ownership of it.
        """
        actor.require_admin(CONFIRM_ACTION)
        emitter = self.emitter.repo.get()
        if emitter is None:
            raise NotFound("emitter_profile", "singleton")
        content, _content_type, _filename = self.documents.download(document_id, None, actor)
        adapter = detect_adapter(content)
        if adapter is None:
            return [ConfirmedInvoiceRead(document_id=document_id, outcome="unclaimed")]

        invoices = adapter.parse(content)
        single_invoice_document = len(invoices) == 1
        digest = hashlib.sha256(content).hexdigest() if single_invoice_document else None

        rows: list[ConfirmedInvoiceRead] = []
        # Keyed on the parsed party itself, not a tax id string: `ParsedInvoiceParty`
        # is frozen and hashes/compares by value, and every invoice from the same
        # counterparty in one document parses to an identical party object, so this
        # is exactly "the same new counterparty seen again in this batch" with no
        # normalisation of its own to get wrong.
        created_customers: dict[ParsedInvoiceParty, UUID] = {}
        for invoice in invoices:
            classification = classify_parsed_invoice(
                invoice, emitter, existing=existing_for(self.session, invoice), content=content
            )
            if classification == "incoming_skipped":
                rows.append(
                    ConfirmedInvoiceRead(document_id=document_id, outcome="incoming_skipped")
                )
                continue
            if classification == "conflict":
                rows.append(ConfirmedInvoiceRead(document_id=document_id, outcome="conflict"))
                continue
            # `existing_for` derives its own comparison row from the same `natural_key`
            # this re-derives: a `None` key always routes here through "conflict" above
            # (a keyless invoice compares against a synthetic hashless row, and a
            # hashless comparison is never "new" -- `import_dedup.check_invoice_
            # duplicate`), so "already_present"/"ready" both guarantee one.
            key = natural_key(invoice)
            assert key is not None, "already_present/ready both require a derivable natural key"
            anno, numero = key
            if classification == "already_present":
                existing = self.repo.existing_by_number(anno, numero)
                fattura = self.get(existing.id, actor) if existing is not None else None
                rows.append(
                    ConfirmedInvoiceRead(
                        document_id=document_id, outcome="already_present", fattura=fattura
                    )
                )
                continue
            resolved_customer_id = customer_id
            if resolved_customer_id is None:
                resolved_customer_id = match_customer(self.session, invoice.cliente)
            # Not cached in `created_customers` yet: `self.session.rollback()` below
            # would revert this insert in the database while leaving a Python-level
            # cache entry pointing at an id that no longer exists, so a later invoice
            # in the same batch from the same counterparty would reuse a dangling id
            # and fail with an uncaught `NotFound` instead of its own outcome. Cached
            # only once `import_issued` for *this* invoice has actually committed.
            new_customer_id: UUID | None = None
            if resolved_customer_id is None and create_customer:
                resolved_customer_id = created_customers.get(invoice.cliente)
                if resolved_customer_id is None:
                    new_customer = self.customers._insert(
                        map_parsed_party_to_customer(invoice.cliente), actor
                    )
                    resolved_customer_id = new_customer.id
                    new_customer_id = new_customer.id
            if resolved_customer_id is None:
                rows.append(
                    ConfirmedInvoiceRead(
                        document_id=document_id, outcome="needs_customer_confirmation"
                    )
                )
                continue
            data = map_parsed_invoice_to_import(
                invoice, anno=anno, numero=numero, customer_id=resolved_customer_id
            )
            try:
                fattura = self.import_issued(
                    data,
                    actor,
                    xml_document_id=document_id if single_invoice_document else None,
                    xml_hash_sha256=digest if single_invoice_document else None,
                    importata_da="fatturapa",
                )
            except (Conflict, ValidationFailed):
                # `import_issued` itself already rolled back on the paths that flushed
                # anything (its own `except IntegrityError`/`except Exception` around the
                # commit); the four register-rule checks above its lock raise before any
                # flush, so this is a defensive no-op there and the real guard on the
                # committing paths -- either way the session must still answer the next
                # invoice's own queries and writes in this same batch. The customer this
                # iteration may have just inserted rolls back with it, and was never
                # cached above, so the next invoice tries its own fresh insert instead of
                # reusing a now-dangling id.
                self.session.rollback()
                rows.append(ConfirmedInvoiceRead(document_id=document_id, outcome="conflict"))
                continue
            if new_customer_id is not None:
                created_customers[invoice.cliente] = new_customer_id
            rows.append(
                ConfirmedInvoiceRead(
                    document_id=document_id,
                    outcome="imported",
                    fattura=fattura,
                    buchi_non_dichiarati=self.undeclared_gaps(anno),
                )
            )
        return rows

    def import_issued(
        self,
        data: InvoiceImport,
        actor: Actor,
        *,
        xml_document_id: UUID | None = None,
        xml_hash_sha256: str | None = None,
        importata_da: str | None = None,
    ) -> InvoiceRead:
        """Register a fattura that another system issued (slice 9 §3).

        Same lock, same snapshot, same lines as `issue`; the two differences are the
        whole feature. The number is *declared*, so the counter follows it instead of
        producing it (§3.2 rule 3). And the totals are *declared*, so they are checked
        against the lines to the cent instead of recomputed (§3.3): the document the
        customer holds is the fact, and this method refuses to record a different one.

        Order: pure checks on the input, then `lock_counter(anno)` -- the first and only
        row lock -- then every check that reads the register (duplicates, neighbours,
        gaps, native numbers), then the write. Nothing is consumed on failure: the
        counter is only ever raised to a number that is being written in the same
        transaction.

        **One transaction, Drive included.** With `pdf_sorgente.drive_file_id` the bytes
        of the original PDF are read among the pure checks (see `_resolve_original_pdf`)
        and become a `documents` row only after the invoice has been flushed, through
        `DocumentService.import_bytes(commit=False)` -- so the single `commit` below is
        still the only one, and a refusal at any point leaves neither an invoice nor an
        orphan PDF. The bytes already written to storage by that call are an accepted
        orphan if the commit then fails, exactly as they are in `add_version`.

        **Nothing is flushed before every refusal has had its chance.** The snapshot is
        built and the original PDF is resolved *above* the lock, because both can fail
        -- a missing `emitter_profile`, a PDF belonging to another customer, a Drive file
        outside the configured roots -- and both used to run after `repo.add`/`add_line`
        had already flushed an `Invoice` and its lines into the caller's transaction. A
        `ValidationFailed` raised at that point left the refused row sitting in the
        session for every later statement to see: the next query in the same transaction
        found a fattura the caller had been told did not exist. Only `IntegrityError` was
        rolled back, and a missing emitter profile is not an `IntegrityError`. So the
        reads happen first, the writes happen last, and the commit is wrapped in a
        rollback for anything that still escapes.

        **`xml_document_id`/`xml_hash_sha256`, gated by the caller (design §5 item
        3).** Both default to `None`, exactly the state a hand-declared import
        leaves them in today. `InvoiceService.confirm_import` is the one caller
        that ever passes real values, and only for a single-invoice source
        document: the newly-parsed path *has* the original transmitted bytes in
        hand, already stored under `xml_document_id`, so pointing the row at that
        same `document_id` instead of archiving a second copy is additive, never
        a change to what a hand-declared import (`xml_document_id` staying
        `None`) already guarantees.

        **`importata_da`, also gated by the caller, and for the same reason
        (design §5 item 4/§7 item 6).** `None` (the default) means "use `data`'s
        own declared value" -- `InvoiceImport.importata_da` is fixed to
        `Literal["esterno"]`, so a hand-declared import always writes exactly
        that. `InvoiceService.confirm_import` is the one caller that ever
        passes `"fatturapa"` here, deliberately *not* through `data` itself:
        `InvoiceImport` is also the schema `POST /api/invoices/import` and
        `import_issued_invoice` deserialise straight from caller-supplied
        JSON, and letting a caller declare `"fatturapa"` there would be an
        unbacked claim -- nothing on that door ever supplies or checks an
        `xml_document_id`. Keeping `importata_da` a keyword-only override here,
        exactly like `xml_document_id`/`xml_hash_sha256`, is what makes
        `"fatturapa"` reachable only from a caller that has actually parsed a
        document. No `CHECK` constraint ties any of the three to each other
        (confirmed by reading `models.py`), so this is additive, never a change
        to what a hand-declared import already guarantees.
        """
        actor.require_admin(IMPORT_ACTION)
        self._check_owner(data.customer_id, data.deal_id)
        self._check_import_date(data.data_emissione)
        self._check_competenza(data.competenza_da, data.competenza_a)
        self._check_declared_totals(data)
        if data.anno != data.data_emissione.year:
            raise ValidationFailed(
                ENTITY,
                "anno",
                "l'anno del registro deve essere quello della data di emissione",
                expected=f"anno = {data.data_emissione.year}",
            )
        if data.stato_pagamento == "incassato" and data.data_incasso is None:
            raise ValidationFailed(
                ENTITY,
                "data_incasso",
                "un incasso senza data non e' un incasso",
                expected="la data in cui il pagamento e' arrivato",
            )
        if data.stato_pagamento != "incassato" and data.data_incasso is not None:
            raise ValidationFailed(
                ENTITY,
                "data_incasso",
                "una data di incasso senza incasso non ha senso",
                expected="stato_pagamento = incassato, oppure nessuna data",
            )
        # The same two facts `mark_transmitted_externally` checks, and it has to be here:
        # the column is immutable once written and it is what `annul` reads to decide
        # whether a correction is still possible. An import that wrote it unchecked could
        # seed a delivery dated tomorrow, or before the invoice it delivers.
        if data.trasmessa_esternamente_il is not None:
            self._check_transmission_date(data.trasmessa_esternamente_il, data.data_emissione)
        # Read-only, above the lock: the assignment happens after the row exists. The
        # Drive branch reads bytes over HTTP here too -- a network read is not a database
        # write, and "the file is outside the configured roots" or "it is not a PDF" are
        # facts about the caller's input, which is what this stretch of the method is for.
        pdf_sorgente = (
            self._resolve_original_pdf(data.customer_id, data.pdf_sorgente, actor)
            if data.pdf_sorgente is not None
            else None
        )
        # Both of these can refuse -- `_regime` when no fiscal profile is configured,
        # `_build_snapshot` when no emitter profile is -- and neither writes anything.
        _, profile = self._regime()
        snapshot = self._build_snapshot(data.customer_id, profile, actor)
        counter = self.repo.lock_counter(data.anno)
        if data.numero in self.repo.numbers_present(data.anno):
            raise Conflict(
                ENTITY,
                "il registro porta gia' questo numero",
                anno=data.anno,
                numero=data.numero,
            )
        if data.numero in self.repo.declared_gaps(data.anno):
            raise Conflict(
                ENTITY,
                "questo numero e' dichiarato come buco del registro: togli la dichiarazione "
                "prima di importarlo",
                anno=data.anno,
                numero=data.numero,
            )
        first_native = self.repo.first_native_number(data.anno)
        if first_native is not None and data.numero > first_native:
            raise Conflict(
                ENTITY,
                "PigroCRM ha gia' emesso fatture in questo anno: si importa solo lo storico "
                "precedente alla prima emessa qui",
                anno=data.anno,
                numero=data.numero,
                prima_nativa=first_native,
            )
        before, after = self.repo.neighbour_dates(data.anno, data.numero)
        if before is not None and data.data_emissione < before:
            raise ValidationFailed(
                ENTITY,
                "data_emissione",
                "il registro deve restare cronologico: il numero precedente porta la data "
                f"{before.isoformat()}",
                expected=f"una data dal {before.isoformat()} in poi",
            )
        if after is not None and data.data_emissione > after:
            raise ValidationFailed(
                ENTITY,
                "data_emissione",
                "il registro deve restare cronologico: il numero successivo porta la data "
                f"{after.isoformat()}",
                expected=f"una data fino al {after.isoformat()}",
            )
        # From here on the transaction writes. Everything above was a read.
        invoice = self.repo.add(
            Invoice(
                customer_id=data.customer_id,
                deal_id=data.deal_id,
                tipo="fattura",
                stato="emessa",
                anno=data.anno,
                numero=data.numero,
                data_emissione=data.data_emissione,
                # The original's date when declared; otherwise the same terms a native
                # emission would apply (REB-326), never a second arithmetic.
                data_scadenza=data.data_scadenza
                or self._scadenza_dai_termini(
                    data.data_emissione, data.customer_id, profile.giorni_scadenza
                ),
                tipo_documento=TIPO_DOCUMENTO,
                divisa=DIVISA,
                causale=data.causale,
                competenza_da=data.competenza_da,
                competenza_a=data.competenza_a,
                imponibile=data.imponibile,
                imposta=data.imposta,
                bollo=data.bollo,
                totale=data.totale,
                stato_pagamento=data.stato_pagamento,
                data_incasso=data.data_incasso,
                trasmessa_esternamente_il=data.trasmessa_esternamente_il,
                note_interne=data.note_interne,
                importata_da=importata_da if importata_da is not None else data.importata_da,
                xml_document_id=xml_document_id,
                xml_hash_sha256=xml_hash_sha256,
                snapshot=snapshot.model_dump(mode="json"),
                snapshot_versione=SNAPSHOT_VERSIONE,
                custom_fields={},
            )
        )
        for index, riga in enumerate(data.righe, start=1):
            self.repo.add_line(
                InvoiceLine(
                    invoice_id=invoice.id,
                    numero_linea=index,
                    descrizione=riga.descrizione,
                    quantita=riga.quantita,
                    unita_misura=riga.unita_misura,
                    prezzo_unitario=riga.prezzo_unitario,
                    prezzo_totale=riga.prezzo_totale,
                    aliquota_iva=riga.aliquota_iva,
                    natura=riga.natura,
                    riferimento_normativo=riga.riferimento_normativo,
                )
            )
        if isinstance(pdf_sorgente, UUID):
            invoice.pdf_document_id = pdf_sorgente
        elif pdf_sorgente is not None:
            # `commit=False`: the `documents` row belongs to *this* transaction. With the
            # ordinary committing `create` it would survive a failure of the commit below
            # as an orphan PDF filed against a customer, with no invoice pointing at it.
            #
            # And this is the one write in the method that can still *refuse*, which is
            # why it carries its own rollback rather than relying on the one around the
            # commit. The `Invoice` and its lines are already flushed here, so a refusal
            # from inside `import_bytes` -- a required custom field on `document`, an
            # `IntegrityError` on the version's unique numero, a storage backend that is
            # down -- would otherwise leave the refused fattura sitting in the caller's
            # session, and the next query in the same transaction would find a fattura
            # the caller had just been told did not exist. Empty bytes and a wrong mime
            # cannot reach here: they are refused among the pure checks above, before
            # anything was flushed at all. This guard is for everything else.
            try:
                invoice.pdf_document_id = self.documents.import_bytes(
                    customer_id=data.customer_id,
                    tipo="fattura",
                    titolo=f"Fattura {numero_completo(data.anno, data.numero)} (originale)",
                    data=pdf_sorgente.contenuto,
                    content_type=PDF_MIME,
                    actor=actor,
                    # `DriveReader.read_bytes` answers with the bytes and the mime and no
                    # name, so the id is the provenance -- which is the part that
                    # identifies the file on Drive anyway, a name being neither unique
                    # nor stable.
                    origine={
                        "drive_file_id": pdf_sorgente.drive_file_id,
                        "mime": pdf_sorgente.mime,
                    },
                    commit=False,
                ).id
            except Exception:
                # Re-raised unchanged: there is nothing useful to translate a required
                # custom field or a dead storage backend into. The rollback is the
                # mandatory part -- without it the flushed `INSERT`s stay pending.
                self.session.rollback()
                raise
        counter.ultimo_numero = max(counter.ultimo_numero, data.numero)
        self.activities.record(
            ENTITY,
            invoice.id,
            "imported",
            actor,
            {
                "anno": data.anno,
                "numero": data.numero,
                "totale": str(data.totale),
                "importata_da": data.importata_da,
            },
        )
        try:
            self.session.commit()
        except IntegrityError as exc:
            self.session.rollback()
            raise Conflict(
                ENTITY,
                "un altro processo ha scritto lo stesso numero: riprova e verifica il registro",
                anno=data.anno,
                numero=data.numero,
            ) from exc
        except Exception:
            # Belt and braces for everything that is not a duplicate number: a `DataError`
            # from a value Postgres cannot store, a connection dropped mid-commit. The
            # error is re-raised unchanged -- there is nothing useful to translate it into
            # -- but the rollback is mandatory, or the caller's session raises on its next
            # statement with the original failure nowhere in sight.
            self.session.rollback()
            raise
        return self.get(invoice.id, actor)

    def _check_import_date(self, data_emissione: date) -> None:
        """Only "not in the future" (§3.2 rule 5). `_check_issue_date` also refuses a
        closed year, and a closed year is exactly what an import fills."""
        oggi = oggi_in_italia()
        if data_emissione > oggi:
            raise ValidationFailed(
                ENTITY,
                "data_emissione",
                "una fattura non si importa con data futura",
                expected=f"una data non successiva a {oggi.isoformat()}",
            )

    def _check_declared_totals(self, data: InvoiceImport) -> None:
        """`imponibile == Σ prezzo_totale` and `imponibile + imposta == totale`, to the
        cent (§3.3).

        **The stamp duty is not in the identity.** `sum_totals` (slice 3, `totals.py`)
        stores `totale = imponibile + imposta` and keeps `bollo` alongside, because
        `DatiBollo/BolloVirtuale` declares that the *issuer* settled the stamp virtually:
        charging it to the customer would need a line of its own with `Natura N1`, which
        is out of scope. So an imported invoice is checked against the same identity a
        natively issued one satisfies -- anything else would make the two halves of the
        same table disagree -- and the previous system's register is the confirming
        fact: its «Totale» column always equals «Imp. Reddito». `bollo` is still
        checked (non-negative) and still stored; it is simply never added.
        """
        somma_righe = round_money(sum((r.prezzo_totale for r in data.righe), Decimal("0")))
        if somma_righe != round_money(data.imponibile):
            raise ValidationFailed(
                ENTITY,
                "imponibile",
                f"l'imponibile dichiarato ({data.imponibile}) non e' la somma delle righe "
                f"({somma_righe})",
                expected="imponibile uguale alla somma dei prezzi totali di riga",
            )
        if data.bollo < ZERO:
            raise ValidationFailed(
                ENTITY,
                "bollo",
                f"il bollo dichiarato ({data.bollo}) e' negativo",
                expected="un bollo maggiore o uguale a zero",
            )
        atteso = round_money(data.imponibile + data.imposta)
        if atteso != round_money(data.totale):
            raise ValidationFailed(
                ENTITY,
                "totale",
                f"il totale dichiarato ({data.totale}) non e' imponibile + imposta "
                f"({atteso}): il bollo si dichiara a parte e non entra nel totale",
                expected="totale uguale a imponibile + imposta",
            )
        if data.totale <= ZERO:
            raise ValidationFailed(
                ENTITY,
                "totale",
                "una TD01 a zero o negativa non e' una fattura",
                expected="un totale maggiore di zero",
            )

    def _drive_reader(self, actor: Actor) -> DriveReader:
        """This actor's reader on their own connected Drive, or the injected one.

        `drive_reader_for` is the gate as well as the constructor: it refuses before any
        HTTP call when Drive is not connected, when the grant was revoked or when
        `drive.readonly` was never granted, and it takes the roots from the account row
        so a reader cannot be pointed at a folder the titolare did not configure.
        """
        if self._drive_reader_factory is not None:
            return self._drive_reader_factory(actor)
        return drive_reader_for(
            self.session,
            actor,
            self.settings,
            feature=DRIVE_FEATURE,
            # The agent ban for this read is `import_issued_invoice`, and `import_issued`
            # has already applied it under that name -- before this method is reached and
            # before the counter is touched. Naming it again here would check the same
            # ban twice; naming anything else would check the wrong one. Spelled rather
            # than left to a default, so the exemption is visible where it is taken (see
            # `ALREADY_AUTHORIZED`).
            action=ALREADY_AUTHORIZED,
        )

    def _resolve_original_pdf(
        self, customer_id: UUID, sorgente: PdfSorgente, actor: Actor
    ) -> "UUID | _FetchedPdf":
        """Where the original PDF is, resolved to something the write phase can use, and
        every refusal that belongs to the caller's input spent here. Writes nothing.

        Two answers, because there are two provenances. A `documents` row is already a
        row: what comes back is its id, and `import_issued` assigns it in one line. A
        Drive file is not a row yet and cannot become one before the invoice exists (the
        title names the invoice's own number), so what comes back is `_FetchedPdf` -- the
        bytes, read *here*, among the pure checks.

        Reading Drive above the counter lock is deliberate. It is a network read, not a
        database write, and the two things that can be wrong with a `drive_file_id` -- a
        file outside the configured roots, a file that is not a PDF -- are facts about
        the caller's input, exactly like the four refusals of the `document_id` branch
        below. Doing it after the lock would hold a row lock for the duration of an HTTP
        call to Google, and would refuse *after* an `Invoice` had been flushed into the
        caller's transaction.

        `NotFound("drive_file")` becomes `Conflict`, not `NotFound`: nothing about the
        *invoice* is missing, and `DriveReader` answers in those same words for a file
        that does not exist, one in an unconfigured corner of the titolare's Drive and
        one in a stranger's Drive -- a distinction it refuses to draw, and that this
        method must not redraw by translating one of them differently.

        **The reader's other refusals pass through untouched, deliberately.** A file over
        the 20 MB download ceiling, a folder id where a file id was meant, a native
        Google file with no bytes and a Google failure mid-read each already arrive as a
        `Conflict` under the entity `drive_file`, carrying a sentence written for the
        person who typed the id ("il file supera N byte: aprilo su Drive invece di
        importarlo"). The last of the four is true because `DriveReader._guarded`
        re-stamps it: the transport's own entity is `document_blob`, the storage's
        subject, and this sentence was false for that case until the reader took the
        entity over (9C final review, F3). Re-wrapping them
        under `invoice` would either lose that sentence or repeat it, and it would claim
        the problem is with the fattura when the problem is with the file: `drive_file`
        is the truthful subject, and it is the same entity `drive_reader_for`'s own
        refusals use for the account (`google_drive_account`). Only the `NotFound` above
        is translated, and what makes it different is its *class*, not its entity -- a
        `NotFound` reaching an import reads as "the thing you asked to import is not
        there", which is exactly the existence answer the reader refuses to give.
        """
        if sorgente.drive_file_id is not None:
            return self._read_original_pdf_from_drive(sorgente.drive_file_id, actor)
        if sorgente.document_id is None:  # pragma: no cover - the schema refuses this first
            # `PdfSorgente`'s own validator already guarantees exactly one of the two,
            # so this is the second line and not the first -- written as a refusal
            # rather than an `assert`, which `python -O` deletes, and phrased for a
            # caller that reached the service through some future path of its own.
            raise ValidationFailed(
                ENTITY,
                "pdf_sorgente",
                "indica da dove prendere il PDF originale",
                expected="esattamente uno fra document_id e drive_file_id",
            )
        return self._validate_original_pdf(customer_id, sorgente.document_id)

    def _read_original_pdf_from_drive(self, drive_file_id: str, actor: Actor) -> _FetchedPdf:
        """The bytes of the original, with every refusal that is a fact about the file.

        Empty bytes are checked *here* and not left to `DocumentService._check_upload`,
        which would refuse them just as surely: that check runs inside `import_bytes`,
        which `import_issued` calls after the `Invoice` has been flushed, whereas "the
        file on Drive has no bytes" is a fact about the caller's `drive_file_id` and
        belongs among the pure checks with the mime. A zero-byte PDF is not exotic --
        a sync that died half way, a placeholder somebody made and never filled -- and
        Drive serves it without complaint.

        **The mime is decided on the metadata, before a byte is downloaded**, which is
        the same ordering `import_drive_file` uses and for the same reason. The check
        used to run on what came *back*: a 20 MB spreadsheet somebody named by mistake
        was pulled through this process and buffered whole in memory (`DriveTransport`
        reads a response in one `read()`) only to be refused on a fact one `files.get`
        already knew -- the titolare's bandwidth and Drive quota spent to reach a
        decidable "no". The reader's declared-size ceiling bounds that; it does not
        remove it, and every file under the ceiling paid in full.

        Not re-checked after the download, because it would be the same value: for
        anything that is not a native Google file `read_bytes` reports the metadata's own
        mime, and a native Google file is refused by this check before its export runs.

        A *folder* is deliberately not refused here and left to `read_bytes`, again as
        `import_drive_file` does: the reader has the better sentence for it («una cartella
        non ha byte da leggere: elencane i figli»), refuses it before any download too,
        and answering it here would replace advice with a content type.
        """
        reader = self._drive_reader(actor)
        try:
            entry = reader.describe(drive_file_id)
            if not entry.cartella and entry.mime != PDF_MIME:
                raise ValidationFailed(
                    ENTITY,
                    "pdf_sorgente.drive_file_id",
                    f"il file su Drive non è un PDF ma un {entry.mime}: l'originale di "
                    "una fattura è il PDF che il cliente ha ricevuto",
                    expected=PDF_MIME,
                )
            contenuto, mime = reader.read_bytes(drive_file_id)
        except NotFound as exc:
            raise Conflict(
                ENTITY,
                "il file non è leggibile dalle cartelle Drive configurate: controlla "
                "l'id, oppure aggiungi la cartella che lo contiene in Impostazioni → Drive",
                drive_file_id=drive_file_id,
            ) from exc
        if not contenuto:
            raise ValidationFailed(
                ENTITY,
                "pdf_sorgente.drive_file_id",
                "il file su Drive è vuoto",
                expected="almeno un byte",
            )
        return _FetchedPdf(contenuto=contenuto, drive_file_id=drive_file_id, mime=mime)

    def _validate_original_pdf(self, customer_id: UUID, document_id: UUID) -> UUID:
        """Check that this `documents` row may become the invoice's PDF, and return its
        id. Reads only.

        The PDF the customer actually received, never rendered: a PDF produced today
        with today's layout would not be that document (§3.5).

        Split from the assignment on purpose, and taking `customer_id` rather than an
        `Invoice` for the same reason `_build_snapshot` does: four of the five refusals
        below are facts about the caller's input, so they belong among `import_issued`'s
        pure checks, above the counter lock and before a single row is flushed. The
        assignment -- one line, `invoice.pdf_document_id = ...` -- happens after the row
        exists.
        """
        document = self.documents.repo.get(document_id)
        if document is None or document.deleted_at is not None:
            raise NotFound("document", document_id)
        if document.tipo != "fattura":
            raise ValidationFailed(
                ENTITY,
                "pdf_sorgente",
                "il documento non e' di tipo fattura",
                expected="un documento con tipo 'fattura'",
            )
        if document.customer_id != customer_id:
            raise ValidationFailed(
                ENTITY,
                "pdf_sorgente",
                "il documento appartiene a un altro cliente",
                expected=f"un documento del cliente {customer_id}",
            )
        if not document.versione_corrente:
            raise ValidationFailed(
                ENTITY,
                "pdf_sorgente",
                "il documento non ha ancora un file caricato",
                expected="un documento con almeno una versione PDF",
            )
        current = self.documents.repo.version(document.id, document.versione_corrente)
        if current is None or current.content_type != PDF_MIME:
            raise ValidationFailed(
                ENTITY,
                "pdf_sorgente",
                "la versione corrente del documento non e' un PDF",
                expected=PDF_MIME,
            )
        taken = (
            self.session.execute(select(Invoice.id).where(Invoice.pdf_document_id == document_id))
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

    def _check_register_year(self, anno: int) -> None:
        """Bound `anno` before it reaches a lock or a query.

        Every other `anno` in this domain is bounded the same way
        (`InvoiceImport.anno`, `InvoiceListQuery.anno`, both `Field(ge=ANNO_MIN,
        le=ANNO_MAX)`), and these three methods are the one place a bare `int` from a
        caller reaches `lock_counter`'s raw `INSERT INTO invoice_counters` or a
        register query directly. An out-of-range value would otherwise surface as a
        Postgres `integer out of range` `DataError` -- not something a caller can act
        on -- or, for a smaller-but-still-nonsense year, silently create junk
        counter/gap rows for a year nothing else in the system will ever ask about.
        """
        if not ANNO_MIN <= anno <= ANNO_MAX:
            raise ValidationFailed(
                ENTITY,
                "anno",
                "anno fuori dal registro",
                expected=f"un anno fra {ANNO_MIN} e {ANNO_MAX}",
            )

    def declare_gaps(
        self, anno: int, data: RegisterGapsDeclare, actor: Actor
    ) -> list[RegisterGapRead]:
        """Name the numbers the register will never carry, and why (spec 9 §3.2 rule 4).

        A declared gap is the honest alternative to two dishonest ones: inventing a row
        to fill it, or leaving it silent so that it looks like a lost invoice. It is
        refused for a number that *is* an invoice, and the import refuses a number that
        is a declared gap: the two sets never overlap.

        `lock_counter(anno)` first, for the same reason `import_issued` takes it before
        reading `numbers_present`/`declared_gaps`: without it, a gap declared here and
        an import of the same number could each read the register before the other's
        write, and both would go through.

        **The whole batch is validated before the first `add_gap`.** `data.buchi` carries
        no uniqueness rule of its own, so `seen` refuses a same-batch duplicate, and
        `present`/`already` refuse a number that is an invoice or is already declared --
        all of it in one pass over the list, with nothing written yet. Validating inside
        the writing loop meant a refusal on the fourth element left the first three
        flushed in the caller's transaction: rows the caller was told had not been
        created, visible to every later statement of the same transaction and committed
        by whatever committed next.
        The writing loop and the commit are still wrapped, because a *concurrent*
        declaration of the same number can reach the unique index underneath
        `uq_invoice_register_gaps_anno_numero` after this transaction's own read of
        `already`; and the rollback is mandatory either way, or the caller's session is
        unusable on its next statement (see `issue` and `import_issued`, which wrap their
        own commits for exactly this reason).
        """
        actor.require_admin(GAPS_ACTION)
        self._check_register_year(anno)
        self.repo.lock_counter(anno)
        present = self.repo.numbers_present(anno)
        already = self.repo.declared_gaps(anno)
        seen: set[int] = set()
        for buco in data.buchi:
            if buco.numero in present:
                raise Conflict(
                    ENTITY,
                    "questo numero e' una fattura del registro, non un buco",
                    anno=anno,
                    numero=buco.numero,
                )
            if buco.numero in already or buco.numero in seen:
                raise Conflict(ENTITY, "buco gia' dichiarato", anno=anno, numero=buco.numero)
            seen.add(buco.numero)
        try:
            for buco in data.buchi:
                self.repo.add_gap(
                    InvoiceRegisterGap(
                        anno=anno,
                        numero=buco.numero,
                        motivo=buco.motivo,
                        dichiarato_da=actor.id,
                    )
                )
                # The register has no row of its own to hang a timeline entry on, so
                # the entity id is derived deterministically from the year rather than
                # left unrecorded: `ActivityService.record` accepts any `entity_type`
                # string (it is not constrained to `EntityType`), and `uuid5` gives the
                # same id every time this year's register is touched again.
                self.activities.record(
                    "invoice_register",
                    uuid5(NAMESPACE_URL, f"pigrocrm:invoice_register:{anno}"),
                    "gap_declared",
                    actor,
                    {"anno": anno, "numero": buco.numero, "motivo": buco.motivo},
                )
            self.session.commit()
        except IntegrityError as exc:
            self.session.rollback()
            raise Conflict(
                ENTITY, "un altro processo ha dichiarato lo stesso buco: riprova", anno=anno
            ) from exc
        except Exception:
            # Same belt and braces as `import_issued`: whatever is not a duplicate gap is
            # re-raised unchanged, but never with the transaction left aborted.
            self.session.rollback()
            raise
        return self.register_gaps(anno, actor)

    def register_gaps(self, anno: int, actor: Actor) -> list[RegisterGapRead]:
        self._check_register_year(anno)
        return [RegisterGapRead.model_validate(g) for g in self.repo.gaps(anno)]

    def undeclared_gaps(self, anno: int) -> list[int]:
        """Numbers from 1 to the highest in the register that are neither an invoice nor
        a declared gap. Empty is the only state in which native issuing may resume (spec
        9 §3.2 rule 4): an import can arrive in any order, so a hole is expected until
        the operator has looked at every one of them and either imported or declared it.

        **From 1, not from the lowest number imported.** A year's register always starts
        at 1 -- that is what makes it a register -- so a missing 1 is exactly as much a
        hole as a missing 8, and bounding the scan below by `min(present)` made the one
        hole an importer is most likely to leave the one hole nobody is told about.
        The previous system's register of 2026 is the case in point: its lowest
        existing invoice is the
        2, and 1 has to be *declared* (it was never issued), not silently assumed away.
        The upper bound stays exclusive at `max(present)`: numbers above the highest one
        present are simply the future, and the counter hands them out next.
        """
        self._check_register_year(anno)
        present = self.repo.numbers_present(anno)
        if not present:
            return []
        declared = self.repo.declared_gaps(anno)
        top = max(present)
        return [n for n in range(1, top) if n not in present and n not in declared]

    def annul(self, invoice_id: UUID, data: InvoiceAnnul, actor: Actor) -> InvoiceRead:
        """Strike the page through; keep the number.

        The number **stays consumed** and the row stays readable: that is what
        preserves the gap-free property of spec 3, which would otherwise be worth
        nothing -- a number that can disappear is a gap with extra steps.

        Refused once the file has been handed to the intermediary. From that point the
        correction requires a credit note (`TD04`), which this slice does not produce
        for a structural reason rather than as a deferral: a credit note corrects an
        invoice **already accepted by the SdI**, and nothing here is transmitted. So
        the application says where the correction has to happen, instead of offering a
        button that pretends to solve it.
        """
        actor.require_admin("annul_invoice")
        invoice = self._require(invoice_id)
        if invoice.stato != "emessa":
            raise Conflict(
                ENTITY,
                "si annulla solo una fattura emessa: una bozza si elimina, "
                "una fattura gia' annullata non si annulla due volte",
                stato_attuale=invoice.stato,
            )
        if invoice.trasmessa_esternamente_il is not None:
            raise Conflict(
                ENTITY,
                "la fattura e' stata consegnata all'intermediario il "
                f"{invoice.trasmessa_esternamente_il.isoformat()}: da questo punto la "
                "correzione richiede una nota di credito, che PigroCRM non emette. "
                "Va fatta dal tuo intermediario o dal portale dell'Agenzia delle Entrate.",
                trasmessa_esternamente_il=invoice.trasmessa_esternamente_il.isoformat(),
            )
        invoice.stato = "annullata"
        # `oggi_in_italia()`, never a bare `date.today()`: see `clock.py`. The date
        # printed on an annulment record is subject to the same "issuer's own
        # calendar" requirement as `data_emissione`.
        invoice.annullata_il = oggi_in_italia()
        invoice.motivo_annullamento = data.motivo
        self.activities.record(
            ENTITY,
            invoice.id,
            "annulled",
            actor,
            {"numero": invoice.numero, "anno": invoice.anno, "motivo": data.motivo},
        )
        self.session.commit()
        return self._read(invoice)

    def _check_transmission_date(self, quando: date, data_emissione: date | None) -> None:
        """The two facts a delivery date has to satisfy: not in the future, not before the
        invoice it delivers.

        Shared by `mark_transmitted_externally` and `import_issued` because the column is
        the same column and it means the same thing. `import_issued` used to write
        `trasmessa_esternamente_il` exactly as given, which left a hole with real
        consequences: the value is immutable once written (see
        `mark_transmitted_externally`) and it is what `annul` reads to decide whether a
        correction is still possible at all, so an import could permanently record a
        delivery dated tomorrow, or dated before the emission, with no way to correct it.
        """
        oggi = oggi_in_italia()
        if quando > oggi:
            raise ValidationFailed(
                ENTITY,
                "trasmessa_esternamente_il",
                "una consegna non si registra con data futura",
                expected=f"una data non successiva a {oggi.isoformat()}",
            )
        if data_emissione is not None and quando < data_emissione:
            raise ValidationFailed(
                ENTITY,
                "trasmessa_esternamente_il",
                f"la consegna non puo' precedere l'emissione ({data_emissione.isoformat()})",
                expected=f"una data dal {data_emissione.isoformat()} in poi",
            )

    def mark_transmitted_externally(
        self, invoice_id: UUID, data: InvoiceTransmitted, actor: Actor
    ) -> InvoiceRead:
        """Record that the XML has been handed to the intermediary. Settable **once**.

        This is the column that makes annulment safe rather than optimistic: without
        it the system could not distinguish an invoice that never left -- annullable --
        from one already deposited with the Agenzia delle Entrate, and would treat the
        two the same. Freezing it after the first write is what stops that distinction
        from being editable away.
        """
        actor.require_admin("mark_transmitted_externally")
        invoice = self._require(invoice_id)
        if invoice.stato != "emessa":
            raise Conflict(
                ENTITY,
                "solo una fattura emessa si consegna a un intermediario",
                stato_attuale=invoice.stato,
            )
        if invoice.trasmessa_esternamente_il is not None:
            raise ImmutableField(
                ENTITY,
                "trasmessa_esternamente_il",
                "la consegna si registra una volta sola: e' il fatto su cui si decide "
                "se un annullamento e' ancora possibile",
            )
        self._check_transmission_date(data.data, invoice.data_emissione)
        invoice.trasmessa_esternamente_il = data.data
        self.activities.record(
            ENTITY, invoice.id, "transmitted_externally", actor, {"data": data.data.isoformat()}
        )
        self.session.commit()
        return self._read(invoice)

    def _for_export(self, invoice: Invoice) -> InvoiceForExport:
        """The frozen view the exporter and the PDF read. Never the live profiles.

        `InvoiceSnapshot.model_validate` on the stored JSONB is deliberate: the model
        is `extra="forbid"` and `versione` has no default, so a payload written by a
        different version of this code is a loud failure rather than one silently read
        with a field dropped. This is a fiscal document; guessing is the failure mode
        the version column exists to prevent.
        """
        if invoice.snapshot is None or invoice.anno is None or invoice.numero is None:
            raise Conflict(
                ENTITY,
                "un documento senza numero e senza congelamento non si esporta",
                stato=invoice.stato,
            )
        if invoice.data_emissione is None:  # pragma: no cover - the CHECKs make this unreachable
            raise Conflict(ENTITY, "manca la data di emissione", stato=invoice.stato)
        return InvoiceForExport(
            anno=invoice.anno,
            numero=invoice.numero,
            data_emissione=invoice.data_emissione,
            data_scadenza=invoice.data_scadenza,
            tipo_documento=invoice.tipo_documento,
            divisa=invoice.divisa,
            imponibile=invoice.imponibile,
            imposta=invoice.imposta,
            bollo=invoice.bollo,
            totale=invoice.totale,
            causale=invoice.causale,
            competenza_da=invoice.competenza_da,
            competenza_a=invoice.competenza_a,
            snapshot=InvoiceSnapshot.model_validate(invoice.snapshot),
            righe=tuple(InvoiceLineRead.model_validate(r) for r in self.repo.lines(invoice.id)),
        )

    def _for_export_proforma(self, invoice: Invoice, actor: Actor) -> InvoiceForExport:
        """A live view for a proforma's PDF: a proforma never freezes (only `issue`
        writes `snapshot`/`anno`/`numero`), so there is nothing to read back. Built
        from the *current* customer/emitter/fiscal profile instead, and dated with the
        proforma's own `data_emissione` (ORB-63), which `create` sets and which the
        sender may move while the document is a draft. The `created_at` fallback is
        for a row written before migration 0033 by a path that skipped its backfill:
        it is the civil date in Europe/Rome the PDF printed before the column was
        filled, never `oggi_in_italia()`, so a re-render does not drift with the
        clock. `anno`/`numero` are placeholders that satisfy the schema's
        non-nullable bounds; `build_scope` never reads them here because
        `riferimento is not None` skips the `numero_completo` branch entirely.
        """
        _, profile = self._regime()
        snapshot = self._build_snapshot(invoice.customer_id, profile, actor)
        return InvoiceForExport(
            anno=invoice.created_at.year,
            numero=1,
            data_emissione=invoice.data_emissione or invoice.created_at.astimezone(ITALY_TZ).date(),
            data_scadenza=None,
            tipo_documento=invoice.tipo_documento,
            divisa=invoice.divisa,
            imponibile=invoice.imponibile,
            imposta=invoice.imposta,
            bollo=invoice.bollo,
            totale=invoice.totale,
            causale=invoice.causale,
            competenza_da=invoice.competenza_da,
            competenza_a=invoice.competenza_a,
            snapshot=snapshot,
            righe=tuple(InvoiceLineRead.model_validate(r) for r in self.repo.lines(invoice.id)),
        )

    def _artifact_document(
        self, invoice: Invoice, tipo: str, titolo: str, actor: Actor
    ) -> Document:
        """The `documents` row for one artefact stream, created on first use.

        Two rows per issued invoice, not one (spec 8.4): a `document_versions` chain is
        a linear history of *one* logical file with one `hash_sha256` used for
        deduplication and integrity, so mixing the PDF and the XML would make
        "version 3" ambiguous and the two hashes incomparable. Two streams, two hashes,
        two integrity checks -- and re-rendering the PDF never touches the XML.

        `documents.stato` is left `NULL` for all three invoice artefact types: the
        authoritative state is `invoices.stato`, and duplicating a state machine across
        two tables produces two truths.
        """
        existing_id = invoice.xml_document_id if tipo == "fattura_xml" else invoice.pdf_document_id
        if existing_id is not None:
            document = self.documents.repo.get(existing_id)
            if document is not None:
                return document
        created = self.documents.create(
            DocumentCreate(customer_id=invoice.customer_id, tipo=tipo, titolo=titolo),  # type: ignore[arg-type]
            actor,
        )
        document = self.documents.repo.get(created.id)
        if document is None:  # pragma: no cover - just created in this transaction
            raise NotFound("document", created.id)
        if tipo == "fattura_xml":
            invoice.xml_document_id = document.id
        else:
            invoice.pdf_document_id = document.id
        return document

    def _artifact_prefix(self, invoice: Invoice) -> str:
        if invoice.tipo == "proforma":
            return proforma_storage_prefix(invoice.id)
        if invoice.anno is None or invoice.numero is None:  # pragma: no cover
            raise Conflict(ENTITY, "un documento senza numero non ha un prefisso fiscale")
        return invoice_storage_prefix(invoice.anno, invoice.numero)

    def _xml_filename(self, export: InvoiceForExport) -> str:
        """`IT{cf_o_piva}_{progressivo}.xml`, from the **frozen** emitter identity.

        Fiscal code first, then VAT number: the same order `IdTrasmittente` uses, and
        for the same reason -- the SdI accepts either, and this is what the working
        generator sent. Reading the snapshot rather than the live profile is what keeps
        the name stable after the issuer edits their own data.
        """
        emittente = export.snapshot.emittente
        id_fiscale = normalise_fiscal_id(emittente.codice_fiscale) or normalise_fiscal_id(
            emittente.partita_iva
        )
        if id_fiscale is None:
            raise ValidationFailed(
                "emitter_profile",
                "codice_fiscale",
                "il nome del file XML richiede un codice fiscale o una partita IVA "
                "validi dell'emittente",
                expected="11 cifre oppure 16 caratteri",
            )
        return sdi_filename(id_fiscale, export.anno, export.numero)

    def _store_artifact(
        self,
        invoice: Invoice,
        *,
        kind: ArtifactKind,
        tipo: str,
        titolo: str,
        content_type: str,
        filename: str,
        data: bytes,
        expected_hash: str | None,
        actor: Actor,
    ) -> InvoiceArtifact:
        """Write the bytes, or prove the bytes already there are the same bytes.

        Three outcomes, and they are spec 4's three:

        * no previous hash -- the first successful production, the only moment with
          nothing to compare against. Write version 1 and record the hash;
        * the hash matches and the stored bytes still hash to it -- nothing to do.
          Return the existing version rather than writing an identical one, so a
          download does not grow the history;
        * the hash matches but the bytes are gone or corrupt -- a **repair**: write a
          new version with identical content. Spec 4 allows exactly this and calls it a
          repair, not a modification;
        * the hash differs -- an error to report, never a version to save.
        """
        digest = hashlib.sha256(data).hexdigest()
        if expected_hash is not None and digest != expected_hash:
            raise Conflict(
                ENTITY,
                f"il {kind} rigenerato non coincide con quello originale: e' una "
                "divergenza da segnalare, non una nuova versione da salvare",
                atteso=expected_hash,
                ottenuto=digest,
                campo="xml_hash_sha256" if kind == "xml" else "hash_sha256",
            )

        document = self._artifact_document(invoice, tipo, titolo, actor)
        current = (
            self.documents.repo.version(document.id, document.versione_corrente)
            if document.versione_corrente
            else None
        )
        if current is not None and current.hash_sha256 == digest:
            try:
                stored_ok = (
                    hashlib.sha256(self.storage.get(current.storage_key)).hexdigest() == digest
                )
            except Exception:
                stored_ok = False
            if stored_ok:
                return InvoiceArtifact(
                    kind=kind,
                    document_id=document.id,
                    version_numero=current.numero,
                    filename=filename,
                    content_type=content_type,
                    hash_sha256=digest,
                )

        version = self.documents.add_version(
            document.id,
            data,
            content_type,
            actor,
            storage_prefix=self._artifact_prefix(invoice),
        )
        return InvoiceArtifact(
            kind=kind,
            document_id=document.id,
            version_numero=version.numero,
            filename=filename,
            content_type=content_type,
            hash_sha256=digest,
        )

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

        A `"fatturapa"` row with a stored `xml_document_id` (design 2026-09-23 §5 item
        4/§7 item 6: a single-invoice source, never a `lotto` batch -- design §5 item 3)
        has the original transmitted file already on record, so this hands that file
        back instead of refusing: `_serve_stored_xml` re-hashes the stored bytes
        against `invoice.xml_hash_sha256` and only ever returns an artefact pointing at
        the existing document/version, never a reconstruction `FatturaPAExporter` would
        produce. Every other imported row -- an `"esterno"` row, always, and a
        `"fatturapa"` batch row, which never gets an `xml_document_id` (design §5 item
        3) -- keeps refusing exactly as before: there is no original file on record for
        either to serve.
        """
        actor.require_write("export_invoice_xml")
        invoice = self._require(invoice_id)
        if invoice.tipo != "fattura":
            raise Conflict(
                ENTITY,
                "una proforma non produce un file FatturaPA: non e' un documento fiscale",
                tipo=invoice.tipo,
                stato=invoice.stato,
            )
        if invoice.stato == "bozza":
            raise Conflict(
                ENTITY,
                "una bozza non ha ancora un numero e non produce un file FatturaPA",
                stato=invoice.stato,
            )
        if invoice.importata_da is not None:
            if invoice.importata_da == "fatturapa" and invoice.xml_document_id is not None:
                return self._serve_stored_xml(invoice)
            raise Conflict(
                ENTITY,
                "fattura importata: l'XML e' quello gia' trasmesso allo SdI dal sistema "
                "che l'ha emessa, questo CRM non ne produce un secondo",
                anno=invoice.anno,
                numero=invoice.numero,
            )

        export = self._for_export(invoice)
        # Re-checked here even though `issue` already checked: the snapshot could have
        # been edited out of band, and the two callers of these functions are the whole
        # reason they are module-level rather than methods.
        check_party_exportable(export.snapshot.emittente, "emitter_profile")
        check_party_exportable(export.snapshot.cliente, "customer")
        check_recipient_routing(export.snapshot.cliente)
        check_recipient_identity(export.snapshot.cliente)

        data = FatturaPAExporter().to_bytes(export)
        artifact = self._store_artifact(
            invoice,
            kind="xml",
            tipo="fattura_xml",
            titolo=f"Fattura {numero_completo(export.anno, export.numero)} (XML)",
            content_type="application/xml",
            filename=self._xml_filename(export),
            data=data,
            expected_hash=invoice.xml_hash_sha256,
            actor=actor,
        )
        if invoice.xml_hash_sha256 is None:
            invoice.xml_hash_sha256 = artifact.hash_sha256
        self.session.commit()
        return artifact

    def _serve_stored_xml(self, invoice: Invoice) -> InvoiceArtifact:
        """The original FatturaPA file a `"fatturapa"` single-invoice import already
        stored, served back rather than regenerated (design 2026-09-23 §5 item 4/§7
        item 6): the CRM never produced this XML, so there is no `FatturaPAExporter`
        call and no `expected_hash`-divergence-means-repair branch to offer, unlike
        `_store_artifact` -- either the stored bytes still hash to what the register
        recorded when the row was confirmed, or this refuses rather than handing back
        a file it cannot vouch for.
        """
        document_id = invoice.xml_document_id
        if document_id is None:  # pragma: no cover - `export_xml` only calls this when set
            raise NotFound("invoice_artifact", f"{invoice.id}#xml")
        document = self.documents.repo.get(document_id)
        version = (
            self.documents.repo.version(document.id, document.versione_corrente)
            if document is not None and document.versione_corrente
            else None
        )
        if document is None or version is None:
            raise NotFound("invoice_artifact", f"{invoice.id}#xml")
        # `confirm_import` always pairs `xml_document_id` with `xml_hash_sha256` --
        # both set for a single-invoice source, both `NULL` for a batch one -- so a
        # `"fatturapa"` row reaching here with one but not the other is a state no
        # caller in this codebase produces. Refusing rather than serving unverified
        # bytes keeps "hash-verified" true regardless of a future caller's mistake,
        # instead of resting on that pairing as an unenforced convention.
        if invoice.xml_hash_sha256 is None:
            raise Conflict(
                ENTITY,
                "manca l'hash registrato all'importazione: non si serve un file che "
                "non si puo' verificare",
                anno=invoice.anno,
                numero=invoice.numero,
            )
        digest = hashlib.sha256(self.storage.get(version.storage_key)).hexdigest()
        if digest != invoice.xml_hash_sha256:
            raise Conflict(
                ENTITY,
                "il file XML archiviato non coincide piu' con l'hash registrato "
                "all'importazione: una divergenza da segnalare, non un file da servire",
                atteso=invoice.xml_hash_sha256,
                ottenuto=digest,
                campo="xml_hash_sha256",
            )
        return InvoiceArtifact(
            kind="xml",
            document_id=document.id,
            version_numero=version.numero,
            filename=self._xml_filename(self._for_export(invoice)),
            content_type=version.content_type,
            hash_sha256=digest,
        )

    def produce_artifacts(self, invoice_id: UUID, actor: Actor) -> list[InvoiceArtifact]:
        """Render the PDF, and for an issued invoice the XML too.

        Called by `issue` after its commit, and callable again at any time: both
        artefacts regenerate deterministically from the frozen snapshot, so this is
        idempotent by construction rather than by a guard. That is the property that
        makes the post-commit render of spec 3 safe -- a crash between the commit and
        the render leaves an invoice that is fiscally complete and merely unprinted,
        and the next call finishes the job.

        Byte-identical on re-render, which is only true because `render_pdf` pins
        `--creation-timestamp 0`: Typst otherwise stamps wall-clock compile time into
        every PDF's `/CreationDate`, and slice 2 had to discover that by comparing two
        renders rather than by trusting that the call succeeded.

        The PDF is produced for a proforma as well, from a live view built by
        `_for_export_proforma` rather than the frozen `_for_export` (a proforma never
        freezes); the XML is not, because a proforma is not a fiscal document.
        `export_xml` refuses one on the row's own state. Returns both artefacts
        (`[pdf]`, or `[pdf, xml]` once the fattura is issued) rather than only the
        last one produced, so a caller sees the whole result of one call.
        """
        actor.require_write("produce_invoice_artifacts")
        invoice = self._require(invoice_id)
        if invoice.importata_da is not None:
            raise Conflict(
                ENTITY,
                "fattura importata: il PDF e' l'originale caricato, non si rigenera",
                anno=invoice.anno,
                numero=invoice.numero,
            )
        export = (
            self._for_export_proforma(invoice, actor)
            if invoice.tipo == "proforma"
            else self._for_export(invoice)
        )
        riferimento = invoice.riferimento if invoice.tipo == "proforma" else None

        _, data = invoice_pdf.render_invoice_pdf(
            export, riferimento=riferimento, settings=self.settings
        )
        # `fattura`/`proforma`, the names `DocumentTipo` actually declares -- the PDF is
        # *the* document of its kind, and `fattura_xml` is the one that needs qualifying
        # because it is the second stream for the same invoice.
        if invoice.tipo == "proforma":
            titolo = f"Proforma {invoice.riferimento or invoice.id} (PDF)"
            filename = f"proforma-{(invoice.riferimento or str(invoice.id)).lower()}.pdf"
            tipo = "proforma"
        else:
            titolo = f"Fattura {numero_completo(export.anno, export.numero)} (PDF)"
            filename = f"fattura-{invoice.anno}-{invoice.numero}.pdf"
            tipo = "fattura"

        # No `expected_hash`: unlike the XML, the PDF's bytes are not a fiscal identity
        # the system promises never to change. A template correction should produce a new
        # version, not a divergence error.
        pdf_artifact = self._store_artifact(
            invoice,
            kind="pdf",
            tipo=tipo,
            titolo=titolo,
            content_type="application/pdf",
            filename=filename,
            data=data,
            expected_hash=None,
            actor=actor,
        )
        self.session.commit()
        artifacts = [pdf_artifact]
        if invoice.tipo == "fattura" and invoice.stato != "bozza":
            artifacts.append(self.export_xml(invoice_id, actor))
        return artifacts

    def download(
        self, invoice_id: UUID, kind: ArtifactKind, actor: Actor
    ) -> tuple[bytes, str, str]:
        """`(bytes, content_type, filename)`.

        The download always goes through the API, which is the only place authorisation
        exists on either storage backend (slice 2 §5). The XML's name is the SdI
        convention; the PDF's is a plain, slug-safe name, because nothing downstream
        validates it.
        """
        invoice = self._require(invoice_id)
        document_id = invoice.xml_document_id if kind == "xml" else invoice.pdf_document_id
        if document_id is None:
            raise NotFound("invoice_artifact", f"{invoice_id}#{kind}")
        document = self.documents.repo.get(document_id)
        if document is None or not document.versione_corrente:
            raise NotFound("invoice_artifact", f"{invoice_id}#{kind}")
        version = self.documents.repo.version(document.id, document.versione_corrente)
        if version is None:  # pragma: no cover - versione_corrente points at a real row
            raise NotFound("invoice_artifact", f"{invoice_id}#{kind}")
        if kind == "pdf" and version.content_type != PDF_MIME:
            # The PDF is served as a PDF or not at all (REB-480). `add_version` refuses
            # any other type on this document now, so this is a version written before
            # that: answered as a missing PDF, which the web already words as «non è
            # disponibile» and, where it repairs it, points at «Rigenera documenti».
            raise NotFound("invoice_artifact", f"{invoice_id}#{kind}")
        if kind == "xml":
            filename = self._xml_filename(self._for_export(invoice))
        elif invoice.tipo == "proforma":
            filename = f"proforma-{(invoice.riferimento or str(invoice.id)).lower()}.pdf"
        else:
            filename = f"fattura-{invoice.anno}-{invoice.numero}.pdf"
        return self.storage.get(version.storage_key), version.content_type, filename

    # ---- reads ---------------------------------------------------------------

    def get(self, invoice_id: UUID, actor: Actor) -> InvoiceRead:
        """One row, with its forecast: `scadenza_prevista` is filled here and not in
        `_reads`, because only the detail page shows it and a list page's query budget
        (two statements, `test_the_customer_name_costs_one_query_for_the_whole_page`) is
        not spent on a date nothing on that page prints."""
        invoice = self._require(invoice_id)
        return self._read(invoice).model_copy(
            update={"scadenza_prevista": self._scadenza_prevista(invoice)}
        )

    def _read(self, invoice: Invoice) -> InvoiceRead:
        """The one place an `Invoice` becomes an `InvoiceRead`, so every path -- a
        mutation's answer, `get`, `list` -- hands back the same shape, the customer's name
        included. Mirrors `DealService._read`."""
        return self._reads([invoice])[0]

    def _reads(self, invoices: Sequence[Invoice]) -> list[InvoiceRead]:
        """The batched form, and the reason `customer_ragione_sociale` is resolved here
        and not inside `InvoiceRead` itself: one lookup for the whole page
        (`InvoiceRepository.customer_names`), so a page of invoices costs two queries
        rather than one per row. A schema-level validator or a lazy ORM relationship would
        both put the lookup on the row, which is exactly the N+1 this avoids.

        `model_copy` and not a second `model_validate`: the name is not an attribute of
        `Invoice` at all, so there is nothing on the ORM object for `from_attributes` to
        read -- and the value comes from a `String` column, already the right type.
        """
        names = self.repo.customer_names({invoice.customer_id for invoice in invoices})
        return [
            InvoiceRead.model_validate(invoice).model_copy(
                update={"customer_ragione_sociale": names.get(invoice.customer_id)}
            )
            for invoice in invoices
        ]

    def _scadenza_prevista(self, invoice: Invoice) -> date | None:
        """`scadenza_prevista` of an editable document (REB-326): the date `issue` would
        print if pressed today, so the person sees a wrong term before the XML carries
        it. The row's own `data_scadenza` when written by hand, the customer's terms
        otherwise. An issued or consumed row gets nothing: its date is a fact, not a
        forecast. A space with no fiscal profile yet has no default days, and only a
        customer with days of its own gets a forecast there -- the profile's absence is
        reported by `issue`, not here.
        """
        if not self._is_editable(invoice):
            return None
        if invoice.data_scadenza is not None:
            return invoice.data_scadenza
        giorni, fine_mese = self.repo.payment_terms({invoice.customer_id}).get(
            invoice.customer_id, (None, False)
        )
        if giorni is None:
            try:
                giorni = self.fiscal.snapshot().giorni_scadenza
            except NotFound:
                return None
        return scadenza_da_termini(oggi_in_italia(), giorni, fine_mese=fine_mese)

    def lines(self, invoice_id: UUID, actor: Actor) -> list[InvoiceLineRead]:
        self._require(invoice_id)
        return [InvoiceLineRead.model_validate(r) for r in self.repo.lines(invoice_id)]

    def _require(self, invoice_id: UUID) -> Invoice:
        invoice = self.repo.get(invoice_id)
        if invoice is None:
            raise NotFound(ENTITY, invoice_id)
        return invoice

    # `list` must stay the last method defined in this class -- an unconditional
    # project rule (`test_module_imports.py`). `lines` above returns
    # `list[InvoiceLineRead]`, so it must be defined before this point or its
    # annotation resolves `list` to this method and fails at import on Python 3.13.
    def list(self, query: InvoiceListQuery, actor: Actor) -> InvoicePage:
        rows = self.repo.list(query)
        has_more = len(rows) > query.limit
        items = rows[: query.limit]
        return InvoicePage(
            items=self._reads(items),
            next_cursor=items[-1].id if has_more and items else None,
        )


__all__ = ["ENTITY", "InvoiceService"]
