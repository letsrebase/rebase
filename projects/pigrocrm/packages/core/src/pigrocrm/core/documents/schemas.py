from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from pigrocrm.core.db import CURSOR_MAX_LENGTH, SortDirection, SortSpec, SortWhitelist
from pigrocrm.core.documents.models import Document
from pigrocrm.core.validation import SafeStr

# `fattura` is the PDF of an issued invoice, `fattura_xml` its FatturaPA file, and
# `proforma` the PDF of a provisional one (which has no XML). Two `documents` rows per
# issued invoice, not one: a `document_versions` chain is a linear history of one
# logical file with one `hash_sha256` used for deduplication and integrity, so putting
# two formats in it would make "version 3" ambiguous and the two hashes incomparable.
# `rapporto_ore` is the timesheet PDF archived on the deal (slice 4 §10.2). A
# String(20) column plus a Literal, never a Postgres ENUM: a new value costs a
# constant, not an ALTER TYPE.
DocumentTipo = Literal[
    "offerta",
    "contratto",
    "verbale",
    "documento",
    "fattura",
    "fattura_xml",
    "proforma",
    "rapporto_ore",
]
OfferState = Literal["bozza", "inviata", "accettata", "rifiutata"]

TITOLO_MAX_LENGTH = 200

# The content type is chosen from this allowlist, never echoed from the request: the
# value reaches a `Content-Disposition` header and a browser's own sniffing later, and
# `text/html` there is a stored XSS with the CRM's own origin behind it.
ALLOWED_CONTENT_TYPES: dict[str, str] = {
    "application/pdf": ".pdf",
    "application/xml": ".xml",
    "text/markdown": ".md",
    "text/plain": ".txt",
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
}

CONTENT_TYPE_MAX_LENGTH = 100
STORAGE_KEY_MAX_LENGTH = 255
# Postgres `Integer` tops out at 2**31-1; 100 MB is far below it and is a defensible
# ceiling for a document nobody wants to email either.
DIMENSIONE_MAX = 100 * 1024 * 1024


class DocumentCreate(BaseModel):
    """`customer_id`, `deal_id` and `contract_id` are all optional here and mutually
    exclusive -- exactly one must be supplied; the service raises `ValidationFailed`
    when zero or more than one is given, and the database check constraint
    (`ck_documents_customer_xor_deal`) is the second line under concurrency."""

    customer_id: UUID | None = None
    deal_id: UUID | None = None
    contract_id: UUID | None = None
    tipo: DocumentTipo = "documento"
    titolo: SafeStr = Field(max_length=TITOLO_MAX_LENGTH)
    stato: OfferState | None = None
    custom_fields: dict[str, Any] = {}


class DocumentUpdate(BaseModel):
    """`stato` is deliberately absent: an offer's state changes only through a
    dedicated state-transition method, which takes a required non-nullable literal.
    Since task 4B-1 closed A14 a `null` here *would* clear the column, which is exactly
    why the field stays off this schema -- see `DocumentService.set_offer_state`."""

    model_config = ConfigDict(extra="forbid")

    titolo: SafeStr | None = Field(default=None, max_length=TITOLO_MAX_LENGTH)
    custom_fields: dict[str, Any] | None = None


class DocumentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    customer_id: UUID | None
    deal_id: UUID | None
    contract_id: UUID | None
    tipo: str
    titolo: str
    stato: str | None
    # Derived, never supplied: on no Create or Update schema, because a caller who could
    # set it could claim an offer has been waiting since January.
    stato_dal: date | None
    versione_corrente: int
    custom_fields: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class DocumentVersionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    document_id: UUID
    numero: int
    template_id: UUID | None
    storage_key: str
    content_type: str
    dimensione: int
    hash_sha256: str
    creato_da: UUID | None
    created_at: datetime


# Residuo R9. Mirrors CUSTOMER_SORTS (customers/schemas.py) -- see there for why three
# keys and why `created_at` is the default.
DOCUMENT_SORTS = SortWhitelist(
    specs=(
        SortSpec(key="created_at", column=Document.created_at, kind="datetime", nullable=False),
        SortSpec(key="updated_at", column=Document.updated_at, kind="datetime", nullable=False),
        SortSpec(key="titolo", column=Document.titolo, kind="text", nullable=False),
    ),
    default_key="created_at",
)


class DocumentListQuery(BaseModel):
    customer_id: UUID | None = None
    deal_id: UUID | None = None
    contract_id: UUID | None = None
    tipo: DocumentTipo | None = None
    stato: OfferState | None = None
    # New in slice 6: spec §8.1 makes `titolo` searchable, and task A13's "vedi tutti"
    # link for the Documento class lands on this filter. `SafeStr` for the same reason
    # the other three carry it -- a NUL byte in a query parameter raises a raw
    # `ValueError` out of psycopg, which nothing here handles.
    search: SafeStr | None = None
    # The drill-through of the commercial dashboard's inconsistency signal (§6.2). A
    # boolean and not a free-text filter: it selects one fixed predicate, and the card that
    # links here counts rows with that same predicate function
    # (`_accepted_with_unwon_deal_predicate`), so the two cannot drift apart.
    solo_deal_non_vinto: bool = False
    # Bounded here, not only on a future router: an MCP tool could build this object
    # directly, with no router-level Query(...) bound sitting between it and this
    # schema.
    limit: int = Field(default=50, ge=1, le=200)
    cursor: str | None = Field(default=None, max_length=CURSOR_MAX_LENGTH)
    sort: SafeStr | None = None
    dir: SortDirection = "asc"


class DocumentPage(BaseModel):
    items: list[DocumentRead]
    next_cursor: str | None


class DocumentFromTemplate(BaseModel):
    """One call: pick a template, fill its variables, get a document with a PDF."""

    template_id: UUID
    customer_id: UUID | None = None
    deal_id: UUID | None = None
    titolo: SafeStr = Field(max_length=TITOLO_MAX_LENGTH)
    # The values for the template's declared variables. Free-form by nature -- the
    # template decides what it wants, and `render_template` rejects a missing required
    # one by name before anything is written.
    variabili: dict[str, Any] = {}
    custom_fields: dict[str, Any] = {}


class DocumentTextRead(BaseModel):
    """The readable text of one archived document, and the sentence that says whose it is.

    Not a `DocumentVersionRead` with a field added: a version is metadata about a file
    (its size, its hash, who uploaded it) and this is the file's *content*, which is a
    client's contract or somebody else's invoice. Keeping them apart is what lets every
    listing of versions stay cheap and free of anybody's text.

    `provenienza` is `core/text.py::PROVENIENZA`, carried through verbatim: whoever
    reads `testo` is reading words written outside this system, and an agent that reads
    its own instructions as text must be told so in the same payload rather than in a
    tool description it saw once. `troncato` is not `testo == ""` -- see `FileText`.
    """

    document_id: UUID
    numero: int
    titolo: str
    testo: str
    mime: str
    troncato: bool
    provenienza: str
