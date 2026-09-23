"""The read-only review step (REB-365), converging `import_adapter`, `import_
classification` and a customer match onto one per-document answer -- the design
record's own `review_invoice_import` (`2026-09-23-mastro-invoice-import-onto-
pigrocrm-design.md` §4, §7 item 3), mirroring mastro's pure `buildReview`.

**No database write of any kind.** Every function below either parses bytes already
in memory or reads through a repository; nothing here ever adds, flushes or commits
a row. `InvoiceService.review_import` is the one caller that also reads a
document's own stored bytes back (a real I/O read, never a write) before handing
them to `review_content`.

**Idempotent by construction.** `review_content` is a pure function of `(content,
emitter, existing)`: the same document reviewed twice reads the same rows and
returns the same verdict, because nothing it touches changes between the two calls
-- exactly the "Done when" the issue names.
"""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from pigrocrm.core.customers.repository import CustomerRepository
from pigrocrm.core.emitter.models import EmitterProfile
from pigrocrm.core.invoices.fatturapa import normalise_fiscal_id
from pigrocrm.core.invoices.fatturapa_import import fattura_pa_fpr12_adapter
from pigrocrm.core.invoices.import_adapter import InvoiceFormatAdapter
from pigrocrm.core.invoices.import_classification import classify_parsed_invoice
from pigrocrm.core.invoices.import_schemas import ParsedInvoice, ParsedInvoiceParty
from pigrocrm.core.invoices.models import Invoice
from pigrocrm.core.invoices.naming import NUMERO_COMPLETO_RE
from pigrocrm.core.invoices.repository import InvoiceRepository

# Every adapter `review_content` tries `detect` with, in order -- the first (and,
# today, only) member of what mastro calls a format registry (`registry.ts:11-23`).
# REB-363 built the protocol and the one concrete adapter but no registry of its
# own; this is that registry's first, minimal shape. A second format is a new
# adapter appended here, never a change to `review_content` itself.
REGISTERED_ADAPTERS: tuple[InvoiceFormatAdapter, ...] = (fattura_pa_fpr12_adapter,)

InvoiceReviewOutcome = Literal[
    "ready",
    "needs_customer_confirmation",
    "already_present",
    "conflict",
    "incoming_skipped",
    "unclaimed",
]
"""REB-364's own `InvoiceImportClassification` (`ready`/`already_present`/
`conflict`/`incoming_skipped`) plus the two facts only this issue adds:
`"needs_customer_confirmation"` -- an otherwise-`ready` invoice whose `cliente`
matches no `Customer` on file by `partita_iva`/`codice_fiscale` -- and
`"unclaimed"`, for a document no registered adapter's `detect` recognises at all:
never a silent drop, mirroring mastro's own `importer.ts:31-33` outcome for a file
no adapter claims."""


class ReviewedInvoiceRead(BaseModel):
    """One row of `review_invoice_import`'s own output: one per invoice a reviewed
    document actually parses into, or exactly one `"unclaimed"` row for a document
    no adapter recognised at all -- so a caller never has to distinguish "found
    nothing" from "found nothing and I dropped it"."""

    model_config = ConfigDict(extra="forbid")

    document_id: UUID
    outcome: InvoiceReviewOutcome
    invoice: ParsedInvoice | None = None
    matched_customer_id: UUID | None = None


class InvoiceReviewRequest(BaseModel):
    """One or more `document_id`s, never raw bytes -- the MCP rule
    (`tools/__init__.py`'s own "the download of bytes never goes through MCP")
    applied symmetrically to the *input* side, and the same shape `InvoiceImport.
    pdf_sorgente` already uses for a `documents` row that is already on file."""

    model_config = ConfigDict(extra="forbid")

    document_ids: list[UUID] = Field(min_length=1)


class InvoiceReviewResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    righe: list[ReviewedInvoiceRead]


def detect_adapter(content: bytes) -> InvoiceFormatAdapter | None:
    for adapter in REGISTERED_ADAPTERS:
        if adapter.detect(content):
            return adapter
    return None


def natural_key(invoice: ParsedInvoice) -> tuple[int, int] | None:
    """The `(anno, numero)` PigroCRM's own register would hold this invoice under,
    derived from the document's own declared `numero` -- never from a caller's
    choice, since there is no confirm step here to ask one of. `numero` is free
    text on the source document (FatturaPA's own `Numero`, `ParsedInvoice.numero`
    is a `SafeStr` with no character-set restriction beyond rejecting an embedded
    NUL, so a hostile or malformed sender's text reaches here unfiltered), so two
    shapes are recognised: the bare digits a typical invoicing tool prints
    (`"6"`, paired with `data_emissione`'s year), and the `numero_completo` shape
    PigroCRM's own exporter prints (`"2026/6"`, `naming.NUMERO_COMPLETO_RE`),
    which also carries its own year. Anything else -- a free-text document id
    like the previous system's `"900142"` is *not* this case, it is plain digits
    and falls into the first branch; a genuinely non-numeric id like `"FT-6"`,
    or a Unicode digit `int()` itself cannot parse, is -- carries no PigroCRM
    natural key at all, and `None` says so rather than guessing or raising.
    `int(raw)` inside `try/except ValueError`, not a `str.isdigit()` pre-check:
    `isdigit()` is true of Unicode code points `int()` still rejects (e.g. a
    superscript digit), which would otherwise turn one malformed `Numero` into
    an unhandled crash of the whole review call instead of the `None` this
    function promises.
    """
    raw = invoice.numero.strip()
    if NUMERO_COMPLETO_RE.fullmatch(raw):
        anno_str, numero_str = raw.split("/", 1)
        return int(anno_str), int(numero_str)
    try:
        return invoice.data_emissione.year, int(raw)
    except ValueError:
        return None


def existing_for(session: Session, invoice: ParsedInvoice) -> Invoice | None:
    """What `classify_parsed_invoice` should compare this invoice's bytes against:
    the register row at its own natural key, or -- when that key cannot be derived
    at all (`natural_key` above) -- a synthetic hashless row. `check_invoice_
    duplicate`'s own NULL-hash rule then answers `"conflict"` for that row: there is
    nothing to compare against, so nothing can be proven new, the same conservative
    default `import_dedup` already applies to a `NULL`-hash register row.

    Always runs one indexed lookup, whatever the invoice's direction turns out to
    be: `existing`, a keyword argument, is evaluated before `classify_parsed_
    invoice` is even called, so there is no way to defer it to after that
    function's own direction check. Harmless on a read-only path -- the result is
    simply unread for an `incoming` invoice, which `classify_parsed_invoice`
    itself never touches `existing` for -- but real, and cheap enough (one
    single-row lookup by the register's own unique `(anno, numero)` index) not to
    be worth restructuring into a lazy call just to avoid it.
    """
    key = natural_key(invoice)
    if key is None:
        return Invoice(xml_hash_sha256=None)
    anno, numero = key
    return InvoiceRepository(session).existing_by_number(anno, numero)


def match_customer(session: Session, cliente: ParsedInvoiceParty) -> UUID | None:
    piva = normalise_fiscal_id(cliente.partita_iva)
    cf = normalise_fiscal_id(cliente.codice_fiscale)
    customer = CustomerRepository(session).match_by_fiscal_id(partita_iva=piva, codice_fiscale=cf)
    return customer.id if customer is not None else None


def review_content(
    session: Session,
    content: bytes,
    emitter: EmitterProfile,
    document_id: UUID,
) -> list[ReviewedInvoiceRead]:
    """The whole review step for one document's already-read bytes: `detect`,
    `parse`, `classify_parsed_invoice` per invoice, and -- only for an otherwise-
    `"ready"` one -- the customer match this issue adds on top of REB-364. Reads
    through `session`; writes nothing.
    """
    adapter = detect_adapter(content)
    if adapter is None:
        return [ReviewedInvoiceRead(document_id=document_id, outcome="unclaimed")]

    rows: list[ReviewedInvoiceRead] = []
    for invoice in adapter.parse(content):
        outcome: InvoiceReviewOutcome = classify_parsed_invoice(
            invoice, emitter, existing=existing_for(session, invoice), content=content
        )
        matched_customer_id: UUID | None = None
        if outcome == "ready":
            matched_customer_id = match_customer(session, invoice.cliente)
            if matched_customer_id is None:
                outcome = "needs_customer_confirmation"
        rows.append(
            ReviewedInvoiceRead(
                document_id=document_id,
                outcome=outcome,
                invoice=invoice,
                matched_customer_id=matched_customer_id,
            )
        )
    return rows
