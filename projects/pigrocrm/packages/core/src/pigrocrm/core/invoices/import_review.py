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

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from pigrocrm.core.contracts.models import Contract, RateCard
from pigrocrm.core.contracts.repository import ContractRepository, RateCardRepository
from pigrocrm.core.customers.repository import CustomerRepository
from pigrocrm.core.invoices.fatturapa.normalise import normalise_fiscal_id
from pigrocrm.core.invoices.fatturapa_import import invoice_pa_fpr_12_adapter
from pigrocrm.core.invoices.import_adapter import InvoiceFormatAdapter
from pigrocrm.core.invoices.import_classification import classify_parsed_invoice
from pigrocrm.core.invoices.import_schemas import ParsedInvoice, ParsedInvoiceParty
from pigrocrm.core.invoices.models import Invoice
from pigrocrm.core.invoices.naming import NUMBER_COMPLETE_RE
from pigrocrm.core.invoices.repository import InvoiceRepository
from pigrocrm.core.legal_entities.models import LegalEntity
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
REGISTERED_ADAPTERS: tuple[InvoiceFormatAdapter, ...] = (invoice_pa_fpr_12_adapter,)

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
`"needs_customer_confirmation"` -- an otherwise-`ready` invoice whose `customer`
matches no `Customer` on file by `vat_number`/`tax_code` -- and
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
    # The legal entity the file's supplier matched (REB-619, spec §1.5): the register it
    # was checked against and the one `confirm_invoice_import` writes it on. `None`
    # on an `incoming_skipped` or `unclaimed` row, which lands nowhere.
    legal_entity_id: UUID | None = None
    matched_customer_id: UUID | None = None
    # REB-369: one entry per `invoice.lines`, positionally aligned -- never merged
    # into `ParsedInvoiceLine` itself, which stays "exactly as the document states
    # it" (its own docstring) and carries no computed field. `None` for the whole
    # invoice when there is no matched customer to resolve a contract from at all
    # (every non-`"ready"` outcome); once matched, `None` per line where no complete
    # day-rate match was found for that line specifically.
    days_mappings: list[WorkUnitDayMappingProposal | None] | None = None


class InvoiceReviewRequest(BaseModel):
    """One or more `document_id`s, never raw bytes -- the MCP rule
    (`tools/families/documents.py`'s own "the download of bytes never goes through MCP")
    applied symmetrically to the *input* side, and the same shape `InvoiceImport.
    pdf_sorgente` already uses for a `documents` row that is already on file."""

    model_config = ConfigDict(extra="forbid")

    document_ids: list[UUID] = Field(min_length=1)


class InvoiceReviewResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lines: list[ReviewedInvoiceRead]


def detect_adapter(content: bytes) -> InvoiceFormatAdapter | None:
    for adapter in REGISTERED_ADAPTERS:
        if adapter.detect(content):
            return adapter
    return None


def natural_key(invoice: ParsedInvoice) -> tuple[int, int] | None:
    """The `(anno, numero)` PigroCRM's own register would hold this invoice under,
    derived from the document's own declared `number` -- never from a caller's
    choice, since there is no confirm step here to ask one of. `number` is free
    text on the source document (FatturaPA's own `Numero`, `ParsedInvoice.number`
    is a `SafeStr` with no character-set restriction beyond rejecting an embedded
    NUL, so a hostile or malformed sender's text reaches here unfiltered), so two
    shapes are recognised: the bare digits a typical invoicing tool prints
    (`"6"`, paired with `issue_date`'s year), and the `number_complete` shape
    PigroCRM's own exporter prints (`"2026/6"`, `naming.NUMBER_COMPLETE_RE`),
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
    raw = invoice.number.strip()
    if NUMBER_COMPLETE_RE.fullmatch(raw):
        year_str, number_str = raw.split("/", 1)
        return int(year_str), int(number_str)
    try:
        return invoice.issue_date.year, int(raw)
    except ValueError:
        return None


def existing_for(session: Session, invoice: ParsedInvoice, legal_entity_id: UUID) -> Invoice | None:
    """What `classify_parsed_invoice` should compare this invoice's bytes against:
    the legal entity's register row at the invoice's own natural key, or -- when that key
    cannot be derived at all (`natural_key` above) -- a synthetic hashless row.
    `check_invoice_duplicate`'s own NULL-hash rule then answers `"conflict"` for that
    row: there is nothing to compare against, so nothing can be proven new, the same
    conservative default `import_dedup` already applies to a `NULL`-hash register row.

    Called through `classify_parsed_invoice`'s `existing_for` callback, once the
    legal entity is known (REB-619): an incoming invoice never reaches it, and an outgoing
    one is looked up on the register it would land on and no other.
    """
    key = natural_key(invoice)
    if key is None:
        return Invoice(xml_hash_sha256=None)
    year, number = key
    return InvoiceRepository(session).existing_by_number(legal_entity_id, year, number)


def register_lookup(
    session: Session, invoice: ParsedInvoice
) -> Callable[[LegalEntity], Invoice | None]:
    """`existing_for` with the session and the invoice bound, in the shape
    `classify_parsed_invoice` takes: the legal entity is the one argument left, since it
    is the one fact the classifier learns."""
    return lambda legal_entity: existing_for(session, invoice, legal_entity.id)


def match_customer(
    session: Session, customer: ParsedInvoiceParty, legal_entity_id: UUID | None = None
) -> UUID | None:
    """The customer on file for `customer`, by tax id, among those of `legal_entity_id` when
    given: an invoice written on one legal entity's register attaches only to that legal entity's
    customers (REB-783)."""
    vat_number = normalise_fiscal_id(customer.vat_number)
    cf = normalise_fiscal_id(customer.tax_code)
    found = CustomerRepository(session).match_by_fiscal_id(
        vat_number=vat_number, tax_code=cf, legal_entity_id=legal_entity_id
    )
    return found.id if found is not None else None


# A customer's contracts with their rate cards, read once per `review_invoices` call:
# several invoices of one upload usually belong to the same customer.
CustomerContracts = dict[UUID, list[tuple[Contract, list[RateCard]]]]


def _contracts_of(
    session: Session, cache: CustomerContracts, customer_id: UUID
) -> list[tuple[Contract, list[RateCard]]]:
    if customer_id not in cache:
        rate_card_repo = RateCardRepository(session)
        cache[customer_id] = [
            (contract, rate_card_repo.list_for_contract(contract.id))
            for contract in ContractRepository(session).list_for_customer(customer_id)
        ]
    return cache[customer_id]


def _day_rate_contract_for_customer(
    session: Session, cache: CustomerContracts, customer_id: UUID, issue_date: date
) -> tuple[Contract, list[RateCard]] | None:
    """The one contract, among `customer_id`'s, whose own rate card is a day-rate
    one on the invoice's own `issue_date` -- mastro's `resolveActiveContractId`
    (`analyze/+server.ts:37-46`), translated onto a schema with no contract
    lifecycle to read yet: `Contract.status` is written by `ContractService.set_state`
    and read by nothing that decides here, so "the one contract this invoice's days
    could belong to" is read off pricing instead of a status no rule consults.
    Zero or several candidates propose nothing, mirroring mastro's own reasoning
    exactly: the tax id already proved whose invoice this is, but nothing on the
    document says which of several concurrent engagements its days belong to.

    Returned with that contract's rate cards. Reads `list_for_customer`, not the
    paginated, caller-facing `list()`, whose default page would silently drop a
    candidate past the first page.
    """
    candidates = [
        (contract, cards)
        for contract, cards in _contracts_of(session, cache, customer_id)
        if (card := resolve_rate_card(cards, issue_date)) is not None and card.type == "daily"
    ]
    return candidates[0] if len(candidates) == 1 else None


def _propose_day_mappings(
    session: Session, cache: CustomerContracts, invoice: ParsedInvoice, customer_id: UUID
) -> list[WorkUnitDayMappingProposal | None]:
    """REB-369: one entry per `invoice.lines`, `None` where the customer resolves
    to no single day-rate contract, that contract has no recorded, unbilled days,
    or `propose_day_mapping` itself found no complete match for that particular
    line."""
    none_per_line: list[WorkUnitDayMappingProposal | None] = [None] * len(invoice.lines)
    found = _day_rate_contract_for_customer(session, cache, customer_id, invoice.issue_date)
    if found is None:
        return none_per_line
    contract, rate_cards = found
    eligible_days = WorkUnitRepository(session).unbilled_for_contract(contract.id)
    if not eligible_days:
        return none_per_line
    return [
        propose_day_mapping(
            riga.quantity, riga.total_price, invoice.issue_date, eligible_days, rate_cards
        )
        for riga in invoice.lines
    ]


@dataclass(frozen=True, slots=True)
class ReviewedInvoice:
    """One invoice's verdict before it is attached to anything that names its source:
    `review_content` adds the `document_id` of an archived document, `InvoiceService.
    review_upload` the name and hash of a file that was never archived. `invoice` is
    `None` exactly for an `"unclaimed"` verdict, and `note` then says why when the bytes
    looked like a registered format and did not parse as one."""

    outcome: InvoiceReviewOutcome
    invoice: ParsedInvoice | None = None
    legal_entity_id: UUID | None = None
    matched_customer_id: UUID | None = None
    days_mappings: list[WorkUnitDayMappingProposal | None] | None = None
    note: str | None = None


def review_invoices(
    session: Session, content: bytes, legal_entities: Sequence[LegalEntity]
) -> list[ReviewedInvoice]:
    """The whole review step for one document's already-read bytes: `detect`,
    `parse`, `classify_parsed_invoice` per invoice against the space's active Italian
    legal entities (REB-619), and -- only for an otherwise-`"ready"` one -- the customer
    match this issue adds on top of REB-364. Reads through `session`; writes nothing.
    """
    adapter = detect_adapter(content)
    if adapter is None:
        return [ReviewedInvoice(outcome="unclaimed")]

    # A document that passed `detect` and does not parse (an unknown TipoDocumento, no
    # DatiRiepilogo, a decimal that is not one) is one file the person has to look at,
    # not a reason to refuse everything sent with it: `"unclaimed"`, with the adapter's
    # own sentence as the note. `ValueError` is what `InvoiceFormatAdapter.parse` says it
    # raises, and `InvoicePaFormatError` is one.
    try:
        parsed = adapter.parse(content)
    except ValueError as exc:
        return [ReviewedInvoice(outcome="unclaimed", note=str(exc))]

    contracts: CustomerContracts = {}
    rows: list[ReviewedInvoice] = []
    for invoice in parsed:
        classification, legal_entity = classify_parsed_invoice(
            invoice, legal_entities, existing_for=register_lookup(session, invoice), content=content
        )
        outcome: InvoiceReviewOutcome = classification
        matched_customer_id: UUID | None = None
        days_mappings: list[WorkUnitDayMappingProposal | None] | None = None
        if outcome == "ready":
            matched_customer_id = match_customer(
                session, invoice.customer, legal_entity.id if legal_entity is not None else None
            )
            if matched_customer_id is None:
                outcome = "needs_customer_confirmation"
            else:
                days_mappings = _propose_day_mappings(
                    session, contracts, invoice, matched_customer_id
                )
        rows.append(
            ReviewedInvoice(
                outcome=outcome,
                invoice=invoice,
                legal_entity_id=legal_entity.id if legal_entity is not None else None,
                matched_customer_id=matched_customer_id,
                days_mappings=days_mappings,
            )
        )
    return rows


def review_content(
    session: Session,
    content: bytes,
    legal_entities: Sequence[LegalEntity],
    document_id: UUID,
) -> list[ReviewedInvoiceRead]:
    """`review_invoices` for the bytes of an archived document, each verdict named by
    that document's id."""
    return [
        ReviewedInvoiceRead(
            document_id=document_id,
            outcome=row.outcome,
            invoice=row.invoice,
            legal_entity_id=row.legal_entity_id,
            matched_customer_id=row.matched_customer_id,
            days_mappings=row.days_mappings,
        )
        for row in review_invoices(session, content, legal_entities)
    ]
