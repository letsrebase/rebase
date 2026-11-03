"""Every Pydantic shape the invoice domain exposes.

Widths and scales mirror `invoices/models.py` exactly. That is not defensive
duplication: without it an over-long string reaches Postgres as `DataError` and a
value beyond a `Numeric`'s capacity as `NumericValueOutOfRange`, neither of which is
an `IntegrityError`, so no handler catches either and the caller's session is left
poisoned. This project has paid for that class of defect seven times.

The eighth was different, and it is why this paragraph now has a second one: a bound
here can only ever cover a column the caller *writes*. `quantity` and `unit_price`
mirror `Numeric(12, 6)` faithfully and their **product** lands in a `Numeric(12, 2)`,
which no bound on two factors can express -- 100000 x 100000 is two valid six-digit
factors and an eleven-digit result. Every *derived* amount is therefore bounded in
`InvoiceService`, before the flush, by `totals.py::overflows_money_column`. Mirroring the
column widths is necessary and is not sufficient.
"""

import datetime as dt
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

# The strict check for an id that arrives *from outside*, imported rather than
# re-typed: `drive/query.py` is the module that owns that shape, `drive/reader.py`
# applies this very function to the `file_id` of every read, and a second copy of the
# pattern here would be one edit away from disagreeing with the one the URL builder
# actually enforces.
from pigrocrm.core.drive.query import checked_outside_id
from pigrocrm.core.errors import ValidationFailed
from pigrocrm.core.fiscal.schemas import FiscalSnapshot
from pigrocrm.core.validation import SafeStr

InvoiceType = Literal["invoice", "proforma"]
InvoiceStatus = Literal["draft", "issued", "cancelled", "confirmed", "consumed"]
PaymentStatus = Literal["unpaid", "paid"]
ArtifactKind = Literal["pdf", "xml"]

# Two state machines share one column: the legal `(tipo, stato)` pairs are the table
# `CHECK` `ck_invoices_type_status` (`invoices/models.py`), so `stato = 'consumata'` is not
# reachable on a `invoice` from any write path.

SNAPSHOT_VERSION = 1
SDI_DOCUMENT_TYPE = "TD01"
CURRENCY = "EUR"

# FPR12 widths, which the columns mirror: `Descrizione` is String1000Type, `Causale` is
# String200Type, `UnitaMisura` is String10Type, VAT nature is 2-4 characters.
DESCRIPTION_MAX_LENGTH = 1000
MEMO_MAX_LENGTH = 200

# `DatiOrdineAcquisto` (REB-693): IdDocumento is String20Type, CodiceCIG and CodiceCUP
# are String15Type. The columns mirror them like every width above.
PURCHASE_ORDER_NUMBER_MAX_LENGTH = 20
ORDER_CODE_MAX_LENGTH = 15

# Stripped before the width: the service strips too, so a value padded by a paste
# (" J41E22000110005 ") must not be refused for a width it does not have once stripped.
# The constraints sit on the bare `str` and `SafeStr`'s NUL check wraps them from the
# outside: a constraint added on top of `SafeStr` would run its width on the raw value.
PurchaseOrderNumberStr = Annotated[
    str,
    StringConstraints(strip_whitespace=True, max_length=PURCHASE_ORDER_NUMBER_MAX_LENGTH),
    *SafeStr.__metadata__,
]
OrderCodeStr = Annotated[
    str,
    StringConstraints(strip_whitespace=True, max_length=ORDER_CODE_MAX_LENGTH),
    *SafeStr.__metadata__,
]
UNIT_OF_MEASURE_MAX_LENGTH = 10
VAT_NATURE_MAX_LENGTH = 4
REFERENCE_MAX_LENGTH = 30
CANCELLATION_REASON_MAX_LENGTH = 500
SDI_DOCUMENT_TYPE_MAX_LENGTH = 4
CURRENCY_MAX_LENGTH = 3
HASH_LENGTH = 64

MONEY_MAX_DIGITS = 12
MONEY_DECIMAL_PLACES = 2
FACTOR_MAX_DIGITS = 12
FACTOR_DECIMAL_PLACES = 6
RATE_MAX_DIGITS = 5
RATE_DECIMAL_PLACES = 2

# A `line_number` is contiguous from 1, so this doubles as the ceiling on both the
# line count and the line number. 200 lines is far more document than anyone posts,
# and the bound is what stops an unbounded list from becoming an unbounded number of
# INSERTs in one transaction.
MAX_LINES = 200
# Spec 8.4: the SdI file name embeds `anno * 10000 + numero`, so a five-digit numero
# would collide with the next year's. Emission is refused past this rather than a name
# quietly becoming ambiguous.
MAX_NUMBER = 9999
YEAR_MIN = 2000
YEAR_MAX = 2999


class PartySnapshot(BaseModel):
    """One party's identity and address as it was at emission.

    Frozen, and `extra="forbid"`: a stored snapshot that carries a field this model
    does not know about must fail loudly rather than be read with that field silently
    dropped, because the thing being reconstructed is a fiscal document.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    legal_name: str
    vat_number: str | None
    tax_code: str | None
    sdi_code: str | None
    certified_email: str | None
    address: str
    cap: str
    city: str
    province: str
    country: str
    email: str | None
    phone: str | None
    website: str | None


class InvoiceSnapshot(BaseModel):
    """What makes a re-render faithful: a customer who moves does not rewrite an
    invoice from two years ago (spec 8.3).

    `versione` is required and has no default on purpose. A default would let a
    payload with no version validate as version 1, which is exactly the guess the
    column exists to prevent.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    version: int
    legal_entity: PartySnapshot
    customer: PartySnapshot
    tax: FiscalSnapshot


class InvoiceLineIn(BaseModel):
    """One line as a caller supplies it.

    `vat_nature` and `legal_reference` are deliberately absent: they are the
    `RegimeStrategy`'s answer, and accepting them here would put the table constraint
    `(aliquota_iva = 0) = (natura IS NOT NULL)` within reach of a request body.
    `vat_rate` is optional and means "use the regime's default"; under the
    flat-rate regime the regime refuses anything but zero anyway.
    """

    model_config = ConfigDict(extra="forbid")

    description: SafeStr = Field(max_length=DESCRIPTION_MAX_LENGTH)
    quantity: Decimal = Field(
        default=Decimal("1.000000"),
        max_digits=FACTOR_MAX_DIGITS,
        decimal_places=FACTOR_DECIMAL_PLACES,
    )
    unit_of_measure: SafeStr | None = Field(default=None, max_length=UNIT_OF_MEASURE_MAX_LENGTH)
    unit_price: Decimal = Field(max_digits=FACTOR_MAX_DIGITS, decimal_places=FACTOR_DECIMAL_PLACES)
    discount_percentage: Decimal | None = Field(
        default=None, max_digits=RATE_MAX_DIGITS, decimal_places=RATE_DECIMAL_PLACES
    )
    discount_amount: Decimal | None = Field(
        default=None, max_digits=MONEY_MAX_DIGITS, decimal_places=MONEY_DECIMAL_PLACES
    )
    vat_rate: Decimal | None = Field(
        default=None, max_digits=RATE_MAX_DIGITS, decimal_places=RATE_DECIMAL_PLACES
    )


class InvoiceLineRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    invoice_id: UUID
    line_number: int
    description: str
    quantity: Decimal
    unit_of_measure: str | None
    unit_price: Decimal
    discount_percentage: Decimal | None
    discount_amount: Decimal | None
    total_price: Decimal
    vat_rate: Decimal
    vat_nature: str | None
    legal_reference: str | None


class InvoiceCreate(BaseModel):
    """A draft invoice or a proforma. Neither has a number: a number is assigned only
    at emission, which is why "a failed creation burns a number" is impossible by
    construction rather than by care."""

    customer_id: UUID
    deal_id: UUID | None = None
    type: InvoiceType = "invoice"
    memo: SafeStr | None = Field(default=None, max_length=MEMO_MAX_LENGTH)
    internal_notes: SafeStr | None = None
    # A proforma's own document date (ORB-63): omitted means today in the issuer's
    # calendar. Refused on a `invoice`, whose date is the register's and is decided at
    # `issue` (spec 6.2). The service applies both rules; the schema only carries it.
    issue_date: dt.date | None = None
    # The accrual period (ORB-61): both or neither, in order, checked by the service
    # against the merged row and by the table on every write path.
    accrual_from: dt.date | None = None
    accrual_to: dt.date | None = None
    # The customer's purchase order (REB-693): its number, and optionally its date, CIG
    # and CUP. The widths are the columns'; stripping, case and ANAC's lengths are
    # `invoices/order.py`'s, applied by the service on the merged row.
    purchase_order_number: PurchaseOrderNumberStr | None = None
    purchase_order_date: dt.date | None = None
    purchase_order_tender_code: OrderCodeStr | None = None
    purchase_order_project_code: OrderCodeStr | None = None
    lines: list[InvoiceLineIn] = Field(default_factory=list, max_length=MAX_LINES)
    custom_fields: dict[str, Any] = {}


class InvoiceUpdate(BaseModel):
    """What a draft may still change, and what stays mutable after emission (spec 4).

    Lines are replaced in bulk, `payment_status` and `payment_date` go through
    `set_payment_state`, and `status` goes through `issue`/`annul`. `supplied_changes`
    reads this with `exclude_unset`, so an explicit `null` clears a column and an omitted
    key leaves it alone -- which is what lets the three `date` columns sit here without
    reproducing A14.

    `memo`, the accrual period, the purchase order and a proforma's `issue_date`
    are on this schema because a draft has to be correctable before it is issued, and
    they are frozen afterwards by `InvoiceService.update`, which raises `ImmutableField`
    -- not by leaving them off the schema, which would have made a draft's own subject line
    unfixable. The period is cleared as a pair and a proforma's date is never cleared;
    an invoice's `issue_date` is refused here outright, since `issue` owns it.
    """

    model_config = ConfigDict(extra="forbid")

    memo: SafeStr | None = Field(default=None, max_length=MEMO_MAX_LENGTH)
    internal_notes: SafeStr | None = None
    issue_date: dt.date | None = None
    # The due date, written by hand on a document that is still editable (REB-326).
    # Set, it is what `issue` prints and what the invoice born from a proforma carries;
    # cleared (an explicit null), `issue` derives it again from the customer's terms.
    # Frozen with the rest of the header afterwards, like the three dates above.
    due_date: dt.date | None = None
    accrual_from: dt.date | None = None
    accrual_to: dt.date | None = None
    # The purchase order (REB-693), frozen with the rest of the header after emission.
    # An explicit null clears a field; the service refuses a date, CIG or CUP left
    # without the number on the merged row.
    purchase_order_number: PurchaseOrderNumberStr | None = None
    purchase_order_date: dt.date | None = None
    purchase_order_tender_code: OrderCodeStr | None = None
    purchase_order_project_code: OrderCodeStr | None = None
    custom_fields: dict[str, Any] | None = None


class InvoiceIssue(BaseModel):
    """`issue_date` may be back-dated within the current year (spec 6.2); omitted
    means today in the issuer's own calendar, never a UTC projection of an instant."""

    model_config = ConfigDict(extra="forbid")

    issue_date: dt.date | None = None


class InvoiceAnnul(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: SafeStr = Field(min_length=1, max_length=CANCELLATION_REASON_MAX_LENGTH)


class InvoiceTransmitted(BaseModel):
    model_config = ConfigDict(extra="forbid")

    date: dt.date


class PaymentState(BaseModel):
    """`payment_status` and `payment_date` move together: "collected with no date" and
    "a date but not collected" are both nonsense, and a single method taking both
    required values has no spelling for either."""

    model_config = ConfigDict(extra="forbid")

    payment_status: PaymentStatus
    payment_date: dt.date | None = None


class InvoiceLineImport(BaseModel):
    """One line of an invoice issued elsewhere, exactly as that document printed it.

    Unlike `InvoiceLineIn`, `vat_nature`, `legal_reference` and `total_price` are
    accepted: the regime strategy did not compute this document and must not rewrite it.
    The service still checks that the declared totals add up (§3.3).
    """

    model_config = ConfigDict(extra="forbid")

    description: SafeStr = Field(max_length=DESCRIPTION_MAX_LENGTH)
    quantity: Decimal = Field(
        default=Decimal("1.000000"),
        max_digits=FACTOR_MAX_DIGITS,
        decimal_places=FACTOR_DECIMAL_PLACES,
    )
    unit_of_measure: SafeStr | None = Field(default=None, max_length=UNIT_OF_MEASURE_MAX_LENGTH)
    unit_price: Decimal = Field(max_digits=FACTOR_MAX_DIGITS, decimal_places=FACTOR_DECIMAL_PLACES)
    total_price: Decimal = Field(max_digits=MONEY_MAX_DIGITS, decimal_places=MONEY_DECIMAL_PLACES)
    vat_rate: Decimal = Field(max_digits=RATE_MAX_DIGITS, decimal_places=RATE_DECIMAL_PLACES)
    vat_nature: SafeStr | None = Field(default=None, max_length=VAT_NATURE_MAX_LENGTH)
    legal_reference: SafeStr | None = None


class PdfSource(BaseModel):
    """Where the original PDF already is: a `documents` row with an uploaded version
    (9A), or a file on the owner's Drive (9C).

    Exactly one of the two, and the `model_validator` is what says so rather than a
    check in the service: two sources would leave "which one is the original?" to be
    answered by whichever branch the service happened to test first, and none would
    make `pdf_sorgente: {}` mean "no PDF" -- which already has a spelling, omitting the
    field entirely.

    `drive_file_id` is held to `drive/query.py`'s strict pattern here, at the edge,
    because the string reaches a Drive URL: `read_bytes` checks it again on the way past
    (the same `checked_outside_id`), and both checks stay -- this one so the refusal names
    `pdf_source.drive_file_id` to the caller who typed it, that one so no future
    caller of the reader can skip it.
    """

    model_config = ConfigDict(extra="forbid")

    document_id: UUID | None = None
    drive_file_id: SafeStr | None = None

    @model_validator(mode="after")
    def _exactly_one_source(self) -> "PdfSource":
        if (self.document_id is None) == (self.drive_file_id is None):
            raise ValueError(
                "indica esattamente uno fra document_id e drive_file_id: "
                "un PDF originale ha una sola provenienza"
            )
        if self.drive_file_id is not None:
            # Translated into the `ValueError` pydantic understands rather than allowed
            # to escape as a `ValidationFailed`: this is a schema, and a caller that
            # builds a `PdfSource` gets one kind of error for every malformed field.
            try:
                checked_outside_id(self.drive_file_id, field="pdf_sorgente.drive_file_id")
            except ValidationFailed as exc:
                raise ValueError(exc.message) from exc
        return self


class InvoiceImport(BaseModel):
    """An invoice issued by the previous system, declared field by field (spec 9 §3.3).

    `year`/`number` come from the caller because they are facts about a document that
    exists; the counter follows them (§3.2 rule 3). Totals are declared **and** verified,
    never recomputed: the document commands, the CRM checks the sums.

    The identity checked is `imponibile + imposta == totale`, and `stamp_duty` is **not** part
    of it: the stamp duty is declared alongside, validated non-negative, and never added
    to the total. `DatiBollo/BolloVirtuale` says the issuer settled it virtually (slice 3
    §6.1 rule 4 and §7.2), which is why `sum_totals` stores the same identity for a
    natively issued invoice -- and the previous system's own register agrees: its «Totale» column
    always equals «Imp. Reddito».

    No `reference`: `invoices.reference` is constrained by
    `ck_invoices_reference_only_on_proforma` to `NULL` on every `tipo = 'fattura'`
    row, and an import always produces a `invoice`. The previous system's free-text description
    belongs in `memo` and in the line's own `description`.
    """

    model_config = ConfigDict(extra="forbid")

    year: int = Field(ge=2000, le=2100)
    # `le=MAX_NUMBER`, not merely `ge=1`. Here the number is *declared* by the caller and
    # the counter follows it (§3.2 rule 3), so nothing downstream re-derives it: a slipped
    # five-digit value -- the previous system prints its own document ids as `900142`, one keystroke
    # away from the register number -- would raise `last_number` to it irreversibly, make every
    # later export refuse (`InvoiceForExport` bounds `number` at `MAX_NUMBER`, because the
    # SdI file name embeds `anno * 10000 + numero`), and turn `undeclared_gaps` into a
    # two-hundred-thousand-element list. Refused at the schema, before any lock is taken.
    number: int = Field(ge=1, le=MAX_NUMBER)
    issue_date: dt.date
    due_date: dt.date | None = None
    customer_id: UUID
    deal_id: UUID | None = None
    memo: SafeStr | None = Field(default=None, max_length=MEMO_MAX_LENGTH)
    # Declared, like everything else about an imported document, when the source knew
    # it; the previous system did not carry a period, so the usual import leaves both
    # out and the row stays `NULL` (ORB-61).
    accrual_from: dt.date | None = None
    accrual_to: dt.date | None = None
    lines: list[InvoiceLineImport] = Field(min_length=1, max_length=MAX_LINES)
    taxable_amount: Decimal = Field(
        max_digits=MONEY_MAX_DIGITS, decimal_places=MONEY_DECIMAL_PLACES
    )
    vat_amount: Decimal = Field(max_digits=MONEY_MAX_DIGITS, decimal_places=MONEY_DECIMAL_PLACES)
    stamp_duty: Decimal = Field(max_digits=MONEY_MAX_DIGITS, decimal_places=MONEY_DECIMAL_PLACES)
    total: Decimal = Field(max_digits=MONEY_MAX_DIGITS, decimal_places=MONEY_DECIMAL_PLACES)
    payment_status: PaymentStatus = "unpaid"
    payment_date: dt.date | None = None
    transmitted_externally_at: dt.date | None = None
    pdf_source: PdfSource | None = None
    internal_notes: SafeStr | None = None
    # `"external"` and not the name of the tool the invoice came out of: the CRM's whole
    # interest in the field is "this document was issued somewhere else, so there is no
    # XML and no PDF of ours" -- and the value is read back by the API and printed by the
    # web client, which makes a product name here product copy. Fixed to this one value,
    # on purpose (design 2026-09-23 §5 item 4): this schema is what a *caller* declares
    # through the hand-declare door (`POST /api/invoices/import`, `import_issued_invoice`),
    # and a caller-supplied `"fatturapa"` here would be an unbacked claim -- nothing on
    # this door ever supplies or checks an `xml_document_id`. `InvoiceService.import_issued`
    # takes `imported_from` as its own keyword-only parameter, alongside `xml_document_id`/
    # `xml_hash_sha256`, precisely so `"fatturapa"` can only ever reach a row through
    # `InvoiceService.confirm_import` -- the one caller that has actually parsed a document.
    imported_from: Literal["external"] = "external"


class RegisterGapIn(BaseModel):
    """One declared hole in the numbering sequence -- a `number` the previous system
    used but that will never be imported, with the reason a reader needs (spec 9 §3.4)."""

    model_config = ConfigDict(extra="forbid")

    # The same bound as `InvoiceImport.number`: a declared gap is a number *of this
    # register*, so it lives in the range the register can carry and nowhere wider.
    number: int = Field(ge=1, le=MAX_NUMBER)
    reason: SafeStr = Field(max_length=CANCELLATION_REASON_MAX_LENGTH)


class RegisterGapsDeclare(BaseModel):
    """A batch declaration: gaps are typically discovered together, at the end of an
    import run, so the caller declares them as one list rather than one call each."""

    model_config = ConfigDict(extra="forbid")

    gaps: list[RegisterGapIn] = Field(min_length=1, max_length=200)


class RegisterGapRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    year: int
    number: int
    reason: str
    declared_by: UUID | None
    declared_at: datetime


class InvoiceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    customer_id: UUID
    deal_id: UUID | None
    # The legal entity that issues or issued it (REB-619): read-only here, the default
    # legal entity on creation until the customer chain of milestone 3 decides it.
    legal_entity_id: UUID
    type: str
    status: str
    year: int | None
    number: int | None
    reference: str | None
    issue_date: dt.date | None
    due_date: dt.date | None
    accrual_from: dt.date | None
    accrual_to: dt.date | None
    purchase_order_number: str | None
    purchase_order_date: dt.date | None
    purchase_order_tender_code: str | None
    purchase_order_project_code: str | None
    sdi_document_type: str
    currency: str
    taxable_amount: Decimal
    vat_amount: Decimal
    stamp_duty: Decimal
    total: Decimal
    memo: str | None
    payment_status: str
    payment_date: dt.date | None
    transmitted_externally_at: dt.date | None
    xml_hash_sha256: str | None
    pdf_document_id: UUID | None
    xml_document_id: UUID | None
    imported_from: str | None
    origin_proforma_id: UUID | None
    cancelled_at: dt.date | None
    cancellation_reason: str | None
    internal_notes: str | None
    snapshot_version: int | None
    # On a document that can still change: the due date «Emetti» would print if pressed
    # today, either the one written on the row or the one the customer's terms give
    # (REB-326). `None` once issued, when `due_date` is the fact and there is
    # nothing to forecast. Filled by `InvoiceService.get` alone: it needs the customer's
    # terms and the profile, two more statements a list page (which prints no forecast)
    # must not pay per page.
    expected_due_date: dt.date | None = None
    # The customer's `legal_name`, denormalised onto the read shape exactly as
    # `DealRead.customer_legal_name` is (ORB-98): the name lives on `customers` and
    # renaming a customer must not need a second write here. `InvoiceService._read`
    # fills it from one batched lookup per page (`InvoiceRepository.customer_names`),
    # which is why the default is `None` -- a bare `model_validate(invoice)` reads an
    # `Invoice` that has no such attribute at all.
    #
    # Typed `str | None` although `invoices.customer_id` is NOT NULL: the association
    # always exists, but this schema validates whatever the lookup found, and a name that
    # could not be resolved must read as absent rather than crash the whole page.
    #
    # Read-only: on no Create, Update or Import schema, so `native_fields`
    # (schema_registry.py derives it from the Create schema) never lists it either.
    customer_legal_name: str | None = None
    custom_fields: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class InvoiceListQuery(BaseModel):
    customer_id: UUID | None = None
    deal_id: UUID | None = None
    legal_entity_id: UUID | None = None
    type: InvoiceType | None = None
    status: InvoiceStatus | None = None
    year: int | None = Field(default=None, ge=YEAR_MIN, le=YEAR_MAX)
    # `unpaid` selects the receivables as the dashboard counts them: an issued
    # invoice nobody has paid, never a draft or a proforma, though every row carries the
    # value from birth (REB-325, `InvoiceRepository.list`). `paid` is the column.
    payment_status: PaymentStatus | None = None
    # The drill-through of the operational dashboard's "scaduto e non incassato" card
    # (§6.2). A boolean and not a free-text filter: it selects one fixed predicate, and the
    # card that links here counts rows with that same predicate function
    # (`_overdue_predicate`), so the two cannot drift apart. Same shape as
    # `DocumentListQuery.only_unwon_deals`.
    overdue: bool = False
    # The way back from a consumed proforma to the invoice it became. The invoice carries
    # `origin_proforma_id`; the proforma carries nothing, by design (a row is written
    # once at emission and the proforma is frozen at the same moment), so the page of a
    # consumed proforma asks the list for the row that points at it (ORB-134).
    origin_proforma_id: UUID | None = None
    # A consumed proforma is the antecedent of the fattura that carries its number, not
    # a document of its own, and a list that shows both doubles every issued proforma
    # (ORB-169). The web sends this whenever no `status` is chosen; asked by state, the
    # consumed ones still answer. A predicate here and not in the browser, so the
    # keyset pagination and the page the API answers stay one thing.
    exclude_consumed: bool = False
    # Bounded here, not only on the router: an MCP tool builds this object directly,
    # with no `Query(...)` bound sitting between it and this schema.
    limit: int = Field(default=50, ge=1, le=200)
    cursor: UUID | None = None


class InvoicePage(BaseModel):
    items: list[InvoiceRead]
    next_cursor: UUID | None


class InvoiceArtifact(BaseModel):
    """What `export_xml`/`render_pdf` report. Never the bytes: an MCP tool returns an
    identifier and the bytes are fetched over REST (slice 2 §7)."""

    kind: ArtifactKind
    document_id: UUID
    version_number: int
    filename: str
    content_type: str
    hash_sha256: str = Field(min_length=HASH_LENGTH, max_length=HASH_LENGTH)


class InvoiceForExport(BaseModel):
    """Everything `InvoicePaExporter` reads, and nothing else.

    A distinct type rather than `InvoiceRead` plus its lines, because the exporter must
    be provably unable to read `legal_entities`, `customers` or `fiscal_profile`: what
    it can see is what was frozen. That is what makes a re-export reproducible and
    what lets the whole generator be tested with no database at all.
    """

    model_config = ConfigDict(frozen=True)

    year: int = Field(ge=YEAR_MIN, le=YEAR_MAX)
    number: int = Field(ge=1, le=MAX_NUMBER)
    issue_date: dt.date
    due_date: dt.date | None
    sdi_document_type: str = Field(max_length=SDI_DOCUMENT_TYPE_MAX_LENGTH)
    currency: str = Field(max_length=CURRENCY_MAX_LENGTH)
    taxable_amount: Decimal = Field(
        max_digits=MONEY_MAX_DIGITS, decimal_places=MONEY_DECIMAL_PLACES
    )
    vat_amount: Decimal = Field(max_digits=MONEY_MAX_DIGITS, decimal_places=MONEY_DECIMAL_PLACES)
    stamp_duty: Decimal = Field(max_digits=MONEY_MAX_DIGITS, decimal_places=MONEY_DECIMAL_PLACES)
    total: Decimal = Field(max_digits=MONEY_MAX_DIGITS, decimal_places=MONEY_DECIMAL_PLACES)
    memo: str | None = Field(default=None, max_length=MEMO_MAX_LENGTH)
    # Read off the row, like `memo`, and frozen by the same rule: `InvoiceService.
    # update` refuses both once the document has left the draft state, so what the
    # exporter writes on every `DettaglioLinee` is what the document said at issue.
    accrual_from: dt.date | None = None
    accrual_to: dt.date | None = None
    # Read off the row and frozen by the same rule as the period (REB-693): what the
    # exporter writes in `DatiOrdineAcquisto` is what the document said at issue.
    purchase_order_number: str | None = Field(
        default=None, max_length=PURCHASE_ORDER_NUMBER_MAX_LENGTH
    )
    purchase_order_date: dt.date | None = None
    purchase_order_tender_code: str | None = Field(default=None, max_length=ORDER_CODE_MAX_LENGTH)
    purchase_order_project_code: str | None = Field(default=None, max_length=ORDER_CODE_MAX_LENGTH)
    snapshot: InvoiceSnapshot
    lines: tuple[InvoiceLineRead, ...]
