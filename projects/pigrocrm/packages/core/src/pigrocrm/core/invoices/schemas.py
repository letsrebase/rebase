"""Every Pydantic shape the invoice domain exposes.

Widths and scales mirror `invoices/models.py` exactly. That is not defensive
duplication: without it an over-long string reaches Postgres as `DataError` and a
value beyond a `Numeric`'s capacity as `NumericValueOutOfRange`, neither of which is
an `IntegrityError`, so no handler catches either and the caller's session is left
poisoned. This project has paid for that class of defect seven times.

The eighth was different, and it is why this paragraph now has a second one: a bound
here can only ever cover a column the caller *writes*. `quantita` and `prezzo_unitario`
mirror `Numeric(12, 6)` faithfully and their **product** lands in a `Numeric(12, 2)`,
which no bound on two factors can express -- 100000 x 100000 is two valid six-digit
factors and an eleven-digit result. Every *derived* amount is therefore bounded in
`InvoiceService`, before the flush, by `totals.py::overflows_money_column`. Mirroring the
column widths is necessary and is not sufficient.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

# The strict check for an id that arrives *from outside*, imported rather than
# re-typed: `drive/query.py` is the module that owns that shape, `drive/reader.py`
# applies this very function to the `file_id` of every read, and a second copy of the
# pattern here would be one edit away from disagreeing with the one the URL builder
# actually enforces.
from pigrocrm.core.drive.query import checked_outside_id
from pigrocrm.core.errors import ValidationFailed
from pigrocrm.core.fiscal.schemas import FiscalSnapshot
from pigrocrm.core.validation import SafeStr

InvoiceTipo = Literal["fattura", "proforma"]
InvoiceStato = Literal["bozza", "emessa", "annullata", "confermata", "consumata"]
StatoPagamento = Literal["da_incassare", "incassato"]
ArtifactKind = Literal["pdf", "xml"]

# Two state machines share one column, so the legal pairs are declared once here and
# enforced as a table `CHECK` in the migration. A convention in the service would
# leave `stato = 'consumata'` reachable on a `fattura` from any other write path.
ALLOWED_STATI: dict[str, frozenset[str]] = {
    "fattura": frozenset({"bozza", "emessa", "annullata"}),
    "proforma": frozenset({"bozza", "confermata", "consumata"}),
}

# Transitions, as a table rather than a chain of `if`s -- the same shape
# `documents/service.py::OFFER_TRANSITIONS` already uses, and read by the UI to decide
# which buttons exist. `emessa`, `annullata` and `consumata` are terminal.
STATO_TRANSITIONS: dict[str, frozenset[str]] = {
    "bozza": frozenset({"emessa", "confermata"}),
    "confermata": frozenset({"consumata"}),
    "emessa": frozenset({"annullata"}),
    "annullata": frozenset(),
    "consumata": frozenset(),
}

SNAPSHOT_VERSIONE = 1
TIPO_DOCUMENTO = "TD01"
DIVISA = "EUR"

# FPR12 widths, which the columns mirror: Descrizione is String1000Type, Causale is
# String200Type, UnitaMisura is String10Type, Natura is 2-4 characters.
DESCRIZIONE_MAX_LENGTH = 1000
CAUSALE_MAX_LENGTH = 200
UNITA_MISURA_MAX_LENGTH = 10
NATURA_MAX_LENGTH = 4
RIFERIMENTO_MAX_LENGTH = 30
MOTIVO_ANNULLAMENTO_MAX_LENGTH = 500
TIPO_DOCUMENTO_MAX_LENGTH = 4
DIVISA_MAX_LENGTH = 3
HASH_LENGTH = 64

MONEY_MAX_DIGITS = 12
MONEY_DECIMAL_PLACES = 2
FACTOR_MAX_DIGITS = 12
FACTOR_DECIMAL_PLACES = 6
RATE_MAX_DIGITS = 5
RATE_DECIMAL_PLACES = 2

# A `numero_linea` is contiguous from 1, so this doubles as the ceiling on both the
# line count and the line number. 200 lines is far more document than anyone posts,
# and the bound is what stops an unbounded list from becoming an unbounded number of
# INSERTs in one transaction.
MAX_LINES = 200
# Spec 8.4: the SdI file name embeds `anno * 10000 + numero`, so a five-digit numero
# would collide with the next year's. Emission is refused past this rather than a name
# quietly becoming ambiguous.
MAX_NUMERO = 9999
ANNO_MIN = 2000
ANNO_MAX = 2999


class PartySnapshot(BaseModel):
    """One party's identity and address as it was at emission.

    Frozen, and `extra="forbid"`: a stored snapshot that carries a field this model
    does not know about must fail loudly rather than be read with that field silently
    dropped, because the thing being reconstructed is a fiscal document.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    ragione_sociale: str
    partita_iva: str | None
    codice_fiscale: str | None
    codice_sdi: str | None
    pec: str | None
    indirizzo: str
    cap: str
    comune: str
    provincia: str
    nazione: str
    email: str | None
    telefono: str | None
    sito_web: str | None


class InvoiceSnapshot(BaseModel):
    """What makes a re-render faithful: a customer who moves does not rewrite an
    invoice from two years ago (spec 8.3).

    `versione` is required and has no default on purpose. A default would let a
    payload with no version validate as version 1, which is exactly the guess the
    column exists to prevent.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    versione: int
    emittente: PartySnapshot
    cliente: PartySnapshot
    fiscale: FiscalSnapshot


class InvoiceLineIn(BaseModel):
    """One line as a caller supplies it.

    `natura` and `riferimento_normativo` are deliberately absent: they are the
    `RegimeStrategy`'s answer, and accepting them here would put the table constraint
    `(aliquota_iva = 0) = (natura IS NOT NULL)` within reach of a request body.
    `aliquota_iva` is optional and means "use the regime's default"; under the
    forfettario the regime refuses anything but zero anyway.
    """

    model_config = ConfigDict(extra="forbid")

    descrizione: SafeStr = Field(max_length=DESCRIZIONE_MAX_LENGTH)
    quantita: Decimal = Field(
        default=Decimal("1.000000"),
        max_digits=FACTOR_MAX_DIGITS,
        decimal_places=FACTOR_DECIMAL_PLACES,
    )
    unita_misura: SafeStr | None = Field(default=None, max_length=UNITA_MISURA_MAX_LENGTH)
    prezzo_unitario: Decimal = Field(
        max_digits=FACTOR_MAX_DIGITS, decimal_places=FACTOR_DECIMAL_PLACES
    )
    sconto_percentuale: Decimal | None = Field(
        default=None, max_digits=RATE_MAX_DIGITS, decimal_places=RATE_DECIMAL_PLACES
    )
    sconto_importo: Decimal | None = Field(
        default=None, max_digits=MONEY_MAX_DIGITS, decimal_places=MONEY_DECIMAL_PLACES
    )
    aliquota_iva: Decimal | None = Field(
        default=None, max_digits=RATE_MAX_DIGITS, decimal_places=RATE_DECIMAL_PLACES
    )


class InvoiceLineRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    invoice_id: UUID
    numero_linea: int
    descrizione: str
    quantita: Decimal
    unita_misura: str | None
    prezzo_unitario: Decimal
    sconto_percentuale: Decimal | None
    sconto_importo: Decimal | None
    prezzo_totale: Decimal
    aliquota_iva: Decimal
    natura: str | None
    riferimento_normativo: str | None


class InvoiceCreate(BaseModel):
    """A draft invoice or a proforma. Neither has a number: a number is assigned only
    at emission, which is why "a failed creation burns a number" is impossible by
    construction rather than by care."""

    customer_id: UUID
    deal_id: UUID | None = None
    tipo: InvoiceTipo = "fattura"
    causale: SafeStr | None = Field(default=None, max_length=CAUSALE_MAX_LENGTH)
    note_interne: SafeStr | None = None
    # A proforma's own document date (ORB-63): omitted means today in the issuer's
    # calendar. Refused on a `fattura`, whose date is the register's and is decided at
    # `issue` (spec 6.2). The service applies both rules; the schema only carries it.
    data_emissione: date | None = None
    # The accrual period (ORB-61): both or neither, in order, checked by the service
    # against the merged row and by the table on every write path.
    competenza_da: date | None = None
    competenza_a: date | None = None
    righe: list[InvoiceLineIn] = Field(default_factory=list, max_length=MAX_LINES)
    custom_fields: dict[str, Any] = {}


class InvoiceUpdate(BaseModel):
    """What a draft may still change, and what stays mutable after emission (spec 4).

    Lines are replaced in bulk, `stato_pagamento` and `data_incasso` go through
    `set_payment_state`, and `stato` goes through `issue`/`annul`. `supplied_changes`
    reads this with `exclude_unset`, so an explicit `null` clears a column and an omitted
    key leaves it alone -- which is what lets the three `date` columns sit here without
    reproducing A14.

    `causale`, the accrual period and a proforma's `data_emissione` are on this schema
    because a draft has to be correctable before it is issued, and they are frozen
    afterwards by `InvoiceService.update`, which raises `ImmutableField` -- not by
    leaving them off the schema, which would have made a draft's own subject line
    unfixable. The period is cleared as a pair and a proforma's date is never cleared;
    a fattura's `data_emissione` is refused here outright, since `issue` owns it.
    """

    model_config = ConfigDict(extra="forbid")

    causale: SafeStr | None = Field(default=None, max_length=CAUSALE_MAX_LENGTH)
    note_interne: SafeStr | None = None
    data_emissione: date | None = None
    # The due date, written by hand on a document that is still editable (REB-326).
    # Set, it is what `issue` prints and what the fattura born from a proforma carries;
    # cleared (an explicit null), `issue` derives it again from the customer's terms.
    # Frozen with the rest of the header afterwards, like the three dates above.
    data_scadenza: date | None = None
    competenza_da: date | None = None
    competenza_a: date | None = None
    custom_fields: dict[str, Any] | None = None


class InvoiceIssue(BaseModel):
    """`data_emissione` may be back-dated within the current year (spec 6.2); omitted
    means today in the issuer's own calendar, never a UTC projection of an instant."""

    model_config = ConfigDict(extra="forbid")

    data_emissione: date | None = None


class InvoiceAnnul(BaseModel):
    model_config = ConfigDict(extra="forbid")

    motivo: SafeStr = Field(min_length=1, max_length=MOTIVO_ANNULLAMENTO_MAX_LENGTH)


class InvoiceTransmitted(BaseModel):
    model_config = ConfigDict(extra="forbid")

    data: date


class PaymentState(BaseModel):
    """`stato_pagamento` and `data_incasso` move together: "collected with no date" and
    "a date but not collected" are both nonsense, and a single method taking both
    required values has no spelling for either."""

    model_config = ConfigDict(extra="forbid")

    stato_pagamento: StatoPagamento
    data_incasso: date | None = None


class InvoiceLineImport(BaseModel):
    """One line of an invoice issued elsewhere, exactly as that document printed it.

    Unlike `InvoiceLineIn`, `natura`, `riferimento_normativo` and `prezzo_totale` are
    accepted: the regime strategy did not compute this document and must not rewrite it.
    The service still checks that the declared totals add up (§3.3).
    """

    model_config = ConfigDict(extra="forbid")

    descrizione: SafeStr = Field(max_length=DESCRIZIONE_MAX_LENGTH)
    quantita: Decimal = Field(
        default=Decimal("1.000000"),
        max_digits=FACTOR_MAX_DIGITS,
        decimal_places=FACTOR_DECIMAL_PLACES,
    )
    unita_misura: SafeStr | None = Field(default=None, max_length=UNITA_MISURA_MAX_LENGTH)
    prezzo_unitario: Decimal = Field(
        max_digits=FACTOR_MAX_DIGITS, decimal_places=FACTOR_DECIMAL_PLACES
    )
    prezzo_totale: Decimal = Field(max_digits=MONEY_MAX_DIGITS, decimal_places=MONEY_DECIMAL_PLACES)
    aliquota_iva: Decimal = Field(max_digits=RATE_MAX_DIGITS, decimal_places=RATE_DECIMAL_PLACES)
    natura: SafeStr | None = Field(default=None, max_length=NATURA_MAX_LENGTH)
    riferimento_normativo: SafeStr | None = None


class PdfSorgente(BaseModel):
    """Where the original PDF already is: a `documents` row with an uploaded version
    (9A), or a file on the titolare's Drive (9C).

    Exactly one of the two, and the `model_validator` is what says so rather than a
    check in the service: two sources would leave "which one is the original?" to be
    answered by whichever branch the service happened to test first, and none would
    make `pdf_sorgente: {}` mean "no PDF" -- which already has a spelling, omitting the
    field entirely.

    `drive_file_id` is held to `drive/query.py`'s strict pattern here, at the edge,
    because the string reaches a Drive URL: `read_bytes` checks it again on the way past
    (the same `checked_outside_id`), and both checks stay -- this one so the refusal names
    `pdf_sorgente.drive_file_id` to the caller who typed it, that one so no future
    caller of the reader can skip it.
    """

    model_config = ConfigDict(extra="forbid")

    document_id: UUID | None = None
    drive_file_id: SafeStr | None = None

    @model_validator(mode="after")
    def _exactly_one_source(self) -> "PdfSorgente":
        if (self.document_id is None) == (self.drive_file_id is None):
            raise ValueError(
                "indica esattamente uno fra document_id e drive_file_id: "
                "un PDF originale ha una sola provenienza"
            )
        if self.drive_file_id is not None:
            # Translated into the `ValueError` pydantic understands rather than allowed
            # to escape as a `ValidationFailed`: this is a schema, and a caller that
            # builds a `PdfSorgente` gets one kind of error for every malformed field.
            try:
                checked_outside_id(self.drive_file_id, field="pdf_sorgente.drive_file_id")
            except ValidationFailed as exc:
                raise ValueError(exc.message) from exc
        return self


class InvoiceImport(BaseModel):
    """A fattura issued by the previous system, declared field by field (spec 9 §3.3).

    `anno`/`numero` come from the caller because they are facts about a document that
    exists; the counter follows them (§3.2 rule 3). Totals are declared **and** verified,
    never recomputed: the document commands, the CRM checks the sums.

    The identity checked is `imponibile + imposta == totale`, and `bollo` is **not** part
    of it: the stamp duty is declared alongside, validated non-negative, and never added
    to the total. `DatiBollo/BolloVirtuale` says the issuer settled it virtually (slice 3
    §6.1 rule 4 and §7.2), which is why `sum_totals` stores the same identity for a
    natively issued invoice -- and the previous system's own register agrees: its «Totale» column
    always equals «Imp. Reddito».

    No `riferimento`: `invoices.riferimento` is constrained by
    `ck_invoices_riferimento_only_on_proforma` to `NULL` on every `tipo = 'fattura'`
    row, and an import always produces a `fattura`. The previous system's free-text description
    belongs in `causale` and in the line's own `descrizione`.
    """

    model_config = ConfigDict(extra="forbid")

    anno: int = Field(ge=2000, le=2100)
    # `le=MAX_NUMERO`, not merely `ge=1`. Here the number is *declared* by the caller and
    # the counter follows it (§3.2 rule 3), so nothing downstream re-derives it: a slipped
    # five-digit value -- the previous system prints its own document ids as `900142`, one keystroke
    # away from the register number -- would raise `ultimo_numero` to it irreversibly, make every
    # later export refuse (`InvoiceForExport` bounds `numero` at `MAX_NUMERO`, because the
    # SdI file name embeds `anno * 10000 + numero`), and turn `undeclared_gaps` into a
    # two-hundred-thousand-element list. Refused at the schema, before any lock is taken.
    numero: int = Field(ge=1, le=MAX_NUMERO)
    data_emissione: date
    data_scadenza: date | None = None
    customer_id: UUID
    deal_id: UUID | None = None
    causale: SafeStr | None = Field(default=None, max_length=CAUSALE_MAX_LENGTH)
    # Declared, like everything else about an imported document, when the source knew
    # it; the previous system did not carry a period, so the usual import leaves both
    # out and the row stays `NULL` (ORB-61).
    competenza_da: date | None = None
    competenza_a: date | None = None
    righe: list[InvoiceLineImport] = Field(min_length=1, max_length=MAX_LINES)
    imponibile: Decimal = Field(max_digits=MONEY_MAX_DIGITS, decimal_places=MONEY_DECIMAL_PLACES)
    imposta: Decimal = Field(max_digits=MONEY_MAX_DIGITS, decimal_places=MONEY_DECIMAL_PLACES)
    bollo: Decimal = Field(max_digits=MONEY_MAX_DIGITS, decimal_places=MONEY_DECIMAL_PLACES)
    totale: Decimal = Field(max_digits=MONEY_MAX_DIGITS, decimal_places=MONEY_DECIMAL_PLACES)
    stato_pagamento: StatoPagamento = "da_incassare"
    data_incasso: date | None = None
    trasmessa_esternamente_il: date | None = None
    pdf_sorgente: PdfSorgente | None = None
    note_interne: SafeStr | None = None
    # `"esterno"` and not the name of the tool the invoice came out of: the CRM's whole
    # interest in the field is "this document was issued somewhere else, so there is no
    # XML and no PDF of ours" -- and the value is read back by the API and printed by the
    # web client, which makes a product name here product copy.
    importata_da: Literal["esterno"] = "esterno"


class RegisterGapIn(BaseModel):
    """One declared hole in the numbering sequence -- a `numero` the previous system
    used but that will never be imported, with the reason a reader needs (spec 9 §3.4)."""

    model_config = ConfigDict(extra="forbid")

    # The same bound as `InvoiceImport.numero`: a declared gap is a number *of this
    # register*, so it lives in the range the register can carry and nowhere wider.
    numero: int = Field(ge=1, le=MAX_NUMERO)
    motivo: SafeStr = Field(max_length=MOTIVO_ANNULLAMENTO_MAX_LENGTH)


class RegisterGapsDeclare(BaseModel):
    """A batch declaration: gaps are typically discovered together, at the end of an
    import run, so the caller declares them as one list rather than one call each."""

    model_config = ConfigDict(extra="forbid")

    buchi: list[RegisterGapIn] = Field(min_length=1, max_length=200)


class RegisterGapRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    anno: int
    numero: int
    motivo: str
    dichiarato_da: UUID | None
    dichiarato_il: datetime


class InvoiceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    customer_id: UUID
    deal_id: UUID | None
    tipo: str
    stato: str
    anno: int | None
    numero: int | None
    riferimento: str | None
    data_emissione: date | None
    data_scadenza: date | None
    competenza_da: date | None
    competenza_a: date | None
    tipo_documento: str
    divisa: str
    imponibile: Decimal
    imposta: Decimal
    bollo: Decimal
    totale: Decimal
    causale: str | None
    stato_pagamento: str
    data_incasso: date | None
    trasmessa_esternamente_il: date | None
    xml_hash_sha256: str | None
    pdf_document_id: UUID | None
    xml_document_id: UUID | None
    importata_da: str | None
    origine_proforma_id: UUID | None
    annullata_il: date | None
    motivo_annullamento: str | None
    note_interne: str | None
    snapshot_versione: int | None
    # On a document that can still change: the due date «Emetti» would print if pressed
    # today, either the one written on the row or the one the customer's terms give
    # (REB-326). `None` once issued, when `data_scadenza` is the fact and there is
    # nothing to forecast. Filled by `InvoiceService.get` alone: it needs the customer's
    # terms and the profile, two more statements a list page (which prints no forecast)
    # must not pay per page.
    scadenza_prevista: date | None = None
    # The customer's `ragione_sociale`, denormalised onto the read shape exactly as
    # `DealRead.customer_ragione_sociale` is (ORB-98): the name lives on `customers` and
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
    customer_ragione_sociale: str | None = None
    custom_fields: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class InvoiceListQuery(BaseModel):
    customer_id: UUID | None = None
    deal_id: UUID | None = None
    tipo: InvoiceTipo | None = None
    stato: InvoiceStato | None = None
    anno: int | None = Field(default=None, ge=ANNO_MIN, le=ANNO_MAX)
    # `da_incassare` selects the receivables as the dashboard counts them: an issued
    # fattura nobody has paid, never a draft or a proforma, though every row carries the
    # value from birth (REB-325, `InvoiceRepository.list`). `incassato` is the column.
    stato_pagamento: StatoPagamento | None = None
    # The drill-through of the operational dashboard's "scaduto e non incassato" card
    # (§6.2). A boolean and not a free-text filter: it selects one fixed predicate, and the
    # card that links here counts rows with that same predicate function
    # (`_overdue_predicate`), so the two cannot drift apart. Same shape as
    # `DocumentListQuery.solo_deal_non_vinto`.
    scadute: bool = False
    # The way back from a consumed proforma to the fattura it became. The fattura carries
    # `origine_proforma_id`; the proforma carries nothing, by design (a row is written
    # once at emission and the proforma is frozen at the same moment), so the page of a
    # consumed proforma asks the list for the row that points at it (ORB-134).
    origine_proforma_id: UUID | None = None
    # A consumed proforma is the antecedent of the fattura that carries its number, not
    # a document of its own, and a list that shows both doubles every issued proforma
    # (ORB-169). The web sends this whenever no `stato` is chosen; asked by state, the
    # consumed ones still answer. A predicate here and not in the browser, so the
    # keyset pagination and the page the API answers stay one thing.
    escludi_consumate: bool = False
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
    version_numero: int
    filename: str
    content_type: str
    hash_sha256: str = Field(min_length=HASH_LENGTH, max_length=HASH_LENGTH)


class InvoiceForExport(BaseModel):
    """Everything `FatturaPAExporter` reads, and nothing else.

    A distinct type rather than `InvoiceRead` plus its lines, because the exporter must
    be provably unable to read `emitter_profile`, `customers` or `fiscal_profile`: what
    it can see is what was frozen. That is what makes a re-export reproducible and
    what lets the whole generator be tested with no database at all.
    """

    model_config = ConfigDict(frozen=True)

    anno: int = Field(ge=ANNO_MIN, le=ANNO_MAX)
    numero: int = Field(ge=1, le=MAX_NUMERO)
    data_emissione: date
    data_scadenza: date | None
    tipo_documento: str = Field(max_length=TIPO_DOCUMENTO_MAX_LENGTH)
    divisa: str = Field(max_length=DIVISA_MAX_LENGTH)
    imponibile: Decimal = Field(max_digits=MONEY_MAX_DIGITS, decimal_places=MONEY_DECIMAL_PLACES)
    imposta: Decimal = Field(max_digits=MONEY_MAX_DIGITS, decimal_places=MONEY_DECIMAL_PLACES)
    bollo: Decimal = Field(max_digits=MONEY_MAX_DIGITS, decimal_places=MONEY_DECIMAL_PLACES)
    totale: Decimal = Field(max_digits=MONEY_MAX_DIGITS, decimal_places=MONEY_DECIMAL_PLACES)
    causale: str | None = Field(default=None, max_length=CAUSALE_MAX_LENGTH)
    # Read off the row, like `causale`, and frozen by the same rule: `InvoiceService.
    # update` refuses both once the document has left the draft state, so what the
    # exporter writes on every `DettaglioLinee` is what the document said at issue.
    competenza_da: date | None = None
    competenza_a: date | None = None
    snapshot: InvoiceSnapshot
    righe: tuple[InvoiceLineRead, ...]
