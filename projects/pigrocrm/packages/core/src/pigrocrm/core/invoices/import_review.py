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

from datetime import date
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from pigrocrm.core.contracts.models import Contract
from pigrocrm.core.contracts.repository import ContractRepository, RateCardRepository
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
from pigrocrm.core.work_units.day_mapping import (
    WorkUnitDayMappingProposal,
    propose_day_mapping,
    resolve_rate_card,
)
from pigrocrm.core.work_units.repository import WorkUnitRepository

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
    # REB-369: one entry per `invoice.righe`, positionally aligned -- never merged
    # into `ParsedInvoiceLine` itself, which stays "exactly as the document states
    # it" (its own docstring) and carries no computed field. `None` for the whole
    # invoice when there is no matched customer to resolve a contract from at all
    # (every non-`"ready"` outcome); once matched, `None` per line where no complete
    # day-rate match was found for that line specifically.
    mappature_giorni: list[WorkUnitDayMappingProposal | None] | None = None


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


def _day_rate_contract_for_customer(
    session: Session, customer_id: UUID, data_emissione: date
) -> Contract | None:
    """The one contract, among `customer_id`'s, whose own rate card is a day-rate
    one on the invoice's own `data_emissione` -- mastro's `resolveActiveContractId`
    (`analyze/+server.ts:37-46`), translated onto a schema with no contract
    lifecycle to read yet: `Contract.stato` carries no meaning today
    (`ContractRepository.list_active`'s own docstring -- "every row today is
    `bozza`", no transition endpoint exists), so "the one contract this invoice's
    days could belong to" is read off pricing instead of a status nothing can set.
    Zero or several candidates propose nothing, mirroring mastro's own reasoning
    exactly: the tax id already proved whose invoice this is, but nothing on the
    document says which of several concurrent engagements its days belong to.

    Reads `list_active()`, not the paginated, caller-facing `list()`: this is an
    internal "read everything this owner has" computation, the same shape
    `WorkUnitRepository.unbilled_for_contract` and `list_active()` itself already
    are, and `list()`'s own default page (50 rows, independent reviewer's own
    finding) would silently drop a candidate past the first page instead of
    genuinely seeing every contract this customer has.
    """
    rate_card_repo = RateCardRepository(session)
    candidates = []
    for contract in ContractRepository(session).list_active():
        if contract.customer_id != customer_id:
            continue
        card = resolve_rate_card(rate_card_repo.list_for_contract(contract.id), data_emissione)
        if card is not None and card.tipo == "giornaliero":
            candidates.append(contract)
    return candidates[0] if len(candidates) == 1 else None


def _propose_day_mappings(
    session: Session, invoice: ParsedInvoice, customer_id: UUID
) -> list[WorkUnitDayMappingProposal | None]:
    """REB-369: one entry per `invoice.righe`, `None` where the customer resolves
    to no single day-rate contract, that contract has no recorded, unbilled days,
    or `propose_day_mapping` itself found no complete match for that particular
    line."""
    none_per_line: list[WorkUnitDayMappingProposal | None] = [None] * len(invoice.righe)
    contract = _day_rate_contract_for_customer(session, customer_id, invoice.data_emissione)
    if contract is None:
        return none_per_line
    eligible_days = WorkUnitRepository(session).unbilled_for_contract(contract.id)
    if not eligible_days:
        return none_per_line
    rate_cards = RateCardRepository(session).list_for_contract(contract.id)
    return [
        propose_day_mapping(
            riga.quantita, riga.prezzo_totale, invoice.data_emissione, eligible_days, rate_cards
        )
        for riga in invoice.righe
    ]


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
        mappature_giorni: list[WorkUnitDayMappingProposal | None] | None = None
        if outcome == "ready":
            matched_customer_id = match_customer(session, invoice.cliente)
            if matched_customer_id is None:
                outcome = "needs_customer_confirmation"
            else:
                mappature_giorni = _propose_day_mappings(session, invoice, matched_customer_id)
        rows.append(
            ReviewedInvoiceRead(
                document_id=document_id,
                outcome=outcome,
                invoice=invoice,
                matched_customer_id=matched_customer_id,
                mappature_giorni=mappature_giorni,
            )
        )
    return rows
