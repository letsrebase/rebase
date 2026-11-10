"""Confirming a reviewed invoice import onto the existing register (REB-366),
converging with `import_issued` instead of diverging from it -- the design
record's own `confirm_invoice_import` (`2026-09-23-mastro-invoice-import-onto-
pigrocrm-design.md` §4-5, §7 item 4).

**Never trusts the review response.** `InvoiceService.confirm_import` re-reads
the document's own stored bytes and re-runs `import_review.detect_adapter`/
`classify_parsed_invoice`/`match_customer` against the database's *current*
state -- never whatever an earlier `review_invoice_import` call computed --
for the same reason mastro's own `persist.ts:6-13` gives: "the structured
document wins... simpler than trusting the client not to have tampered with it."

**One write function.** Beyond `import_review`'s own classification, this
module adds `map_parsed_invoice_to_import`, a pure translation of a confirmed
`ParsedInvoice` onto `InvoiceImport`'s own fields (design §5 item 2) --
mirroring mastro's own `mapInvoiceToInput` (`persist.ts:91-112`) -- and
`map_parsed_party_to_customer`, the same translation for the matched
counterparty onto `OrganizationCreate` when no `Organization` already carries its tax
id (design §7 item 5). The actual writes are `InvoiceService.import_issued`
and `OrganizationService._insert`, called once per invoice/customer inside
`InvoiceService.confirm_import`, sharing its one session: this is the direct
guarantee against divergence between the hand-declared and the newly-parsed
path, since both run the same register rules at the same call, and against a
customer committed with no invoice, since both writes share one commit.

**`imported_from` is `"fatturapa"`, never `"external"`, and never through
`InvoiceImport` itself.** This issue's own follow-up to REB-366 (design §5
item 4/§7 item 6) needed a way to write `"fatturapa"` without also letting a
*caller* of the hand-declare door (`POST /api/invoices/import`,
`import_issued_invoice`) claim it: `InvoiceImport.imported_from` stays fixed to
`Literal["external"]`, and `InvoiceService.import_issued` instead takes
`imported_from` as its own keyword-only parameter -- exactly the shape
`xml_document_id`/`xml_hash_sha256` already use, and for the identical reason.
`InvoiceService.confirm_import` is the one caller that ever passes
`imported_from="fatturapa"`, because it is the one caller that has actually
parsed a document: a document this CRM parsed itself is a materially
different kind of provenance from one a human re-typed by hand, and the
invoice list's own badge (`InvoiceStateBadge.tsx`) needs the distinction to
say so honestly without a hand-declared row ever being able to fake it.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from pigrocrm.core.invoices.fatturapa.normalise import normalise_fiscal_id
from pigrocrm.core.invoices.import_schemas import ParsedInvoice, ParsedInvoiceParty
from pigrocrm.core.invoices.schemas import InvoiceImport, InvoiceLineImport, InvoiceRead
from pigrocrm.core.organizations.schemas import OrganizationCreate

InvoiceConfirmOutcome = Literal[
    "imported",
    "already_present",
    "conflict",
    "incoming_skipped",
    "needs_customer_confirmation",
    "unclaimed",
]
"""`review_invoice_import`'s own `InvoiceReviewOutcome`, with `"ready"` replaced
by `"imported"`: by the time `confirm_invoice_import` returns, an invoice that
classified `"ready"` either has been written -- `"imported"` -- or the call
refused to write it, reported under one of the other five outcomes, unchanged
from `review_invoice_import`'s own vocabulary."""


@dataclass(frozen=True, slots=True)
class ConfirmedInvoice:
    """One invoice's confirmation before it is attached to anything that names its
    source: `ConfirmedInvoiceRead` adds the `document_id` of an archived document,
    `UploadedInvoiceConfirmRead` the name and hash of an uploaded file. The fields mean
    what they mean on `ConfirmedInvoiceRead`; `party_key` names the invoice's customer in
    an upload's decisions and `note` says why an `"unclaimed"` file did not parse."""

    outcome: InvoiceConfirmOutcome
    legal_entity_id: UUID | None = None
    invoice: InvoiceRead | None = None
    undeclared_gaps: list[int] | None = None
    party_key: str | None = None
    note: str | None = None
    existing_organization_id: UUID | None = None


class ConfirmedInvoiceRead(BaseModel):
    """One row of `confirm_invoice_import`'s own output: one per invoice the
    reviewed document parses into, mirroring `ReviewedInvoiceRead`'s own shape.

    `invoice` is set only for `"imported"` (freshly written) and
    `"already_present"` (the row already on record at that natural key) --
    never for a refusal, since nothing was written or matched. `buchi_non_
    dichiarati` is set only for `"imported"`, mirroring `POST /api/invoices/
    import`'s own response: the numbers still missing under this invoice's own
    `year`, so a caller sees in one round trip what `declare_invoice_register_
    gaps` still has to cover.
    """

    model_config = ConfigDict(extra="forbid")

    document_id: UUID
    outcome: InvoiceConfirmOutcome
    # The legal entity the file landed on, or was checked against (REB-619, spec §1.5);
    # `None` for `incoming_skipped` and `unclaimed`, which land nowhere.
    legal_entity_id: UUID | None = None
    # On a `needs_customer_confirmation` row: the organization this customer's tax id belongs
    # to under another legal entity, to be shared with `share_organization` (REB-970).
    existing_organization_id: UUID | None = None
    invoice: InvoiceRead | None = None
    undeclared_gaps: list[int] | None = None


class InvoiceConfirmRequest(BaseModel):
    """One already-archived document plus the two human decisions this issue's
    scope adds: which `Organization` to attach when no exact tax-id match exists, and
    whether to create one (`review_invoice_import`'s own `"needs_customer_
    confirmation"`).

    `customer_id`, when given, overrides whatever the current tax-id match
    would find on its own -- the human's decision always wins over the
    automatic match, exactly as `"needs_customer_confirmation"`'s own name
    promises a caller who reads it.

    `create_organization`, when true and `customer_id` is omitted and no exact
    match is found, creates the matched party as a new `Organization` instead of
    reporting `"needs_customer_confirmation"` -- inside the same transaction
    as the invoice write (design §7 item 5), never a premature commit of an
    orphan customer. One document's `lotto` batch can carry several invoices
    from the same new counterparty: every one of them attaches to the same
    freshly-created row, never one `Organization` per invoice.
    """

    model_config = ConfigDict(extra="forbid")

    document_id: UUID
    customer_id: UUID | None = None
    create_organization: bool = False
    share_organization: bool = False


class InvoiceConfirmResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lines: list[ConfirmedInvoiceRead]


def map_parsed_invoice_to_import(
    invoice: ParsedInvoice, *, year: int, number: int, customer_id: UUID
) -> InvoiceImport:
    """The confirmed `ParsedInvoice` onto `InvoiceImport`'s own fields (design §5
    item 2), mirroring mastro's own `mapInvoiceToInput` (`persist.ts:91-112`):
    `taxable_amount`/`vat_amount` are already the document-level sums `ParsedInvoice`
    itself carries (its own docstring), never re-summed here, and `lines` is a
    field-for-field copy -- `ParsedInvoiceLine` and `InvoiceLineImport` name the
    same five facts about a billed line the same way, on purpose (`import_
    schemas`'s own module docstring).

    `vat_nature` is copied straight from `ParsedInvoiceLine.vat_nature` -- the line's
    own declared exemption code, read directly off `DettaglioLinee/Natura` by
    the parser. `legal_reference`, which FatturaPA never repeats at the
    line level, is looked up from `invoice.recaps` by the pair `(aliquota_
    iva, natura)` -- never by rate alone, which this codebase's own export-side
    `totals.RecapGroup` already treats as the real grouping key, and which
    a rate-only lookup would get wrong on any invoice mixing two exemption
    codes at the same rate (`ParsedInvoiceLine`'s own docstring). This is not
    optional: `invoice_lines`'s own `ck_invoice_lines_vat_nature_agrees_with_rate`
    requires `natura IS NOT NULL` on every zero-rate line and `NULL` on every
    other one, so a missing `vat_nature` fails that constraint on exactly the
    exempt-rate invoices this parser exists to import.

    `year`/`number` are the caller's own `import_review.natural_key(invoice)`,
    never re-derived here: by the time this is called the caller has already
    confirmed that key is new (`classify_parsed_invoice` returned `"ready"`),
    and `import_issued`'s own `data.anno != data.data_emissione.year` check is
    what actually enforces the register's invariant, not a second copy of it
    here.

    `imported_from` is left at `InvoiceImport`'s own fixed `"external"` -- this
    return value is never written as-is (module docstring above): the caller,
    `InvoiceService.confirm_import`, passes `imported_from="fatturapa"` to
    `import_issued` itself as a separate keyword argument, the same way it
    already passes `xml_document_id`/`xml_hash_sha256`, so a value only this
    module's own caller can ever set never round-trips through a schema a
    hand-declare caller could also populate. `due_date`, `memo`,
    `accrual_from`/`accrual_to`, `pdf_source`, `internal_notes` and
    `transmitted_externally_at` all stay unset too: none of them has a source
    on `ParsedInvoice` today, so `import_issued`'s own defaults (the regime's
    `due_days` for the due date, chief among them) apply exactly as
    they would for a hand-typed import that left the same fields out.
    """
    reference_by_pair: dict[tuple[Decimal, str | None], str | None] = {}
    for recap in invoice.recaps:
        reference_by_pair.setdefault((recap.vat_rate, recap.vat_nature), recap.legal_reference)
    return InvoiceImport(
        year=year,
        number=number,
        issue_date=invoice.issue_date,
        customer_id=customer_id,
        lines=[
            InvoiceLineImport(
                description=line.description,
                quantity=line.quantity,
                unit_price=line.unit_price,
                total_price=line.total_price,
                vat_rate=line.vat_rate,
                vat_nature=line.vat_nature,
                legal_reference=reference_by_pair.get((line.vat_rate, line.vat_nature)),
            )
            for line in invoice.lines
        ],
        taxable_amount=invoice.taxable_amount,
        vat_amount=invoice.vat_amount,
        stamp_duty=invoice.stamp_duty if invoice.stamp_duty is not None else Decimal("0.00"),
        total=invoice.total,
        withholding_amount=invoice.withholding_amount,
        split_payment=invoice.split_payment,
    )


def map_parsed_party_to_customer(party: ParsedInvoiceParty) -> OrganizationCreate:
    """The parsed counterparty exactly as the confirmed invoice's own document
    states it, onto `OrganizationCreate`'s fields (design §7 item 5): the row
    `confirm_import` creates when `create_organization` is set and no `Organization`
    already carries this tax id -- never a second, hand-typed shape for the
    same facts `import_review.match_customer` already reads off the same
    `ParsedInvoiceParty` to look them up.

    `vat_number`/`tax_code` are normalised through the same `normalise_
    fiscal_id` `match_customer` itself calls first. `ParsedInvoiceParty.partita_
    iva` carries the country prefix FPR12 requires (`"IT01234567890"`, its own
    docstring), while `Organization.vat_number` stores the bare eleven digits
    `OrganizationService`'s own `_check_fiscal` enforces -- skipping this step
    would create a row `match_customer` could never find again on the next
    invoice from the same counterparty.
    """
    return OrganizationCreate(
        legal_name=party.legal_name,
        vat_number=normalise_fiscal_id(party.vat_number),
        tax_code=normalise_fiscal_id(party.tax_code),
        address=party.address,
        postal_code=party.postal_code,
        city=party.city,
        province=party.province,
        country=party.country,
    )
