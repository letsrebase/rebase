"""The neutral parsed-invoice shape (REB-363), ported from mastro's
`src/lib/server/import/invoice.ts`.

Every structured invoice format an adapter parses maps onto `ParsedInvoice` --
translated into PigroCRM's own vocabulary (`taxable_amount`/`vat_amount` where mastro says
`taxableAmount`/`taxAmount`, `supplier`/`customer` where it says
`supplier`/`customer`), never the other way around: a second format adapter is a
translation into this shape, not a change to it.

Deliberately not `InvoiceImport` (`invoices/schemas.py`): that schema is "what a
caller declares" (`extra="forbid"`, hand-typed totals, a fixed `Literal["external"]`
provenance); this one is "what a structured document itself states". Collapsing the
two would force every future parser to also satisfy `InvoiceImport`'s
hand-declaration contract, which has nothing to do with parsing. The design record is
`docs/superpowers/specs/2026-09-23-mastro-invoice-import-onto-pigrocrm-design.md` §5.

Every model here is frozen and `extra="forbid"`: a `ParsedInvoice` is read once,
straight out of `bytes` nobody has vetted yet, and a value carrying a field this
shape does not recognise must fail loudly rather than have it silently dropped --
the same reasoning `PartySnapshot` states for itself.
"""

from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict

from pigrocrm.core.validation import SafeStr

ParsedInvoiceDocumentType = Literal[
    "invoice",
    "invoice_advance",
    "fee_advance",
    "note_credit",
    "note_debit",
    "fee_note",
]
"""What kind of document this is, named after what the FatturaPA `TipoDocumento`
code means (TD01..TD06) rather than after the code itself -- mirroring mastro's own
`InvoiceDocumentType`, named after the concept rather than one format's code list,
so a second format adapter maps its own codes onto the same six names."""

ParsedInvoiceDueDateSource = Literal["document", "calculated"]
"""Mirrors mastro's `InvoiceDueDateSource`: whether a payment instalment's due date
was read verbatim from the document, or computed here from a relative term (a
reference date plus a day count) the document expressed instead -- never invented
from anything outside the document itself."""


class ParsedInvoiceParty(BaseModel):
    """One party to a parsed invoice, exactly as the source document states it --
    not `PartySnapshot` (what the CRM's own emission recorded at the time) and not
    a `Customer` row (what a caller declares about an entity on file): this is what
    the document itself claims about whoever issued it or whoever it was issued to,
    before any matching against the register has happened.

    `vat_number`/`tax_code` are two first-class, independently optional
    fields rather than mastro's single `taxId` (which changes shape depending on
    which identifier the source document happened to carry): PigroCRM already
    treats both as first-class columns on a customer and on a legal entity, so there is
    no "whichever one" concept to port here. `vat_number` carries `IdPaese` +
    `IdCodice` concatenated (e.g. `"IT01234567890"`) exactly as mastro's
    `fiscalIdString` does, since a later direction-detection step needs the
    identifier a FatturaPA document actually guarantees. A concrete adapter
    enforces that at least one of the two is present when the source schema
    itself allows neither -- FatturaPA's `CessionarioCommittente` is the one case
    (mastro's `mapCustomer` throws for the same reason); `CedentePrestatore`
    always carries `IdFiscaleIVA`, mandatory in FatturaPA's own schema.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    legal_name: SafeStr
    vat_number: SafeStr | None = None
    tax_code: SafeStr | None = None
    address: SafeStr
    cap: SafeStr
    city: SafeStr
    province: SafeStr | None = None
    country: SafeStr


class ParsedInvoiceLine(BaseModel):
    """One billed line, mirroring mastro's `InvoiceLine` (`invoice.ts:65-73`) with
    the same field names `InvoiceLineImport` already uses for a hand-declared line
    -- it is the same fact, read two different ways.

    `vat_rate` is the line's own VAT rate. `vat_nature`, when the rate is zero,
    is the line's *own* declared exemption code (FatturaPA's `DettaglioLinee/
    Natura`) -- read directly from the line, never reconstructed from a
    `ParsedInvoiceTaxSummary` block matched by rate alone: a document can carry
    more than one `recaps` entry at the same zero rate with a *different*
    `vat_nature` each (mixed-exemption invoices are routine, and this codebase's own
    export side already treats `(aliquota_iva, natura)` as the real grouping key
    -- `totals.RecapGroup`'s own docstring), so a rate-only lookup would
    silently mistag a line that belongs to the other group. `riferimento_
    normativo`, which FatturaPA never repeats at the line level, is still the
    matching `recaps` entry's own field, looked up by the pair.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    description: SafeStr
    quantity: Decimal
    unit_price: Decimal
    total_price: Decimal
    vat_rate: Decimal
    vat_nature: SafeStr | None = None


class ParsedInvoiceTaxSummary(BaseModel):
    """One VAT-rate summary block (FatturaPA's `DatiRiepilogo`). A real invoice can
    carry more than one -- mixed-rate invoices are routine -- which is why this is
    a list on `ParsedInvoice` rather than a single flat pair.

    `legal_reference` is the literal wording the issuing document carries,
    already in the language it was written in -- never translated, never replaced
    by a jurisdiction's own wording for the same `vat_nature` code: this is what a
    specific document actually said, which can legitimately disagree with what the
    code should say (a supplier's software using outdated wording).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    vat_rate: Decimal
    vat_nature: SafeStr | None = None
    legal_reference: SafeStr | None = None
    taxable_amount: Decimal
    vat_amount: Decimal


class ParsedInvoiceSocialCharge(BaseModel):
    """A social-security fund contribution charged on the invoice (FatturaPA's
    `DatiCassaPrevidenziale`). An invoice can carry more than one fund, though a
    single consultant only ever pays into one; kept as a list so a second one is a
    longer list, not a dropped field.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    pension_fund_type: SafeStr
    pension_fund_rate: Decimal
    pension_fund_contribution_amount: Decimal
    pension_fund_taxable_amount: Decimal | None = None
    vat_rate: Decimal


class ParsedInvoicePaymentInstallment(BaseModel):
    """One instalment of a payment plan. `due_date` is either read verbatim
    from the document (FatturaPA's `DataScadenzaPagamento`) or computed from a
    relative term the document expresses instead (`DataRiferimentoTerminiPagamento`
    plus `GiorniTerminiPagamento`) -- never invented from anything outside the
    document itself. `due_date_origin` tells a reader which case produced it.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    due_date: date
    due_date_origin: ParsedInvoiceDueDateSource
    amount: Decimal
    payment_method: SafeStr
    iban: SafeStr | None = None


class ParsedInvoicePaymentTerms(BaseModel):
    """One payment-terms block: a condition code plus the instalments it governs.
    FatturaPA allows more than one block when a document mixes payment
    conditions; kept as a list on `ParsedInvoice` for the same reason as
    `recaps`.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    payment_terms: SafeStr
    rate: list[ParsedInvoicePaymentInstallment]


class ParsedInvoiceTransmission(BaseModel):
    """Who actually sent this document to the Sistema di Interscambio, and that
    transmission's own sequence number. Deliberately not where direction detection
    looks: when an invoicing service files on the account holder's behalf,
    `id_transmitter` is the *service*, and the account holder appears only as
    `supplier`. Reading this field for direction would misclassify every invoice a
    service transmits on someone else's behalf.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id_transmitter: SafeStr
    send_sequential: SafeStr


class ParsedInvoice(BaseModel):
    """A single invoice, exactly as a structured document states it -- mastro's
    `Invoice` (`invoice.ts:169-196`), translated into PigroCRM's own vocabulary.

    `taxable_amount`/`vat_amount` are the sums of `recaps[].taxable_amount`/`.vat_amount`,
    kept as their own fields because the fiscal domain already names them at the
    invoice level (mirrors `InvoiceImport`). `total` is the document's own stated
    total, never derived by summing the fields above -- a document is free to
    round or add charges this shape does not model, and the total it declares is
    the one that must reconcile with what was actually paid.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    number: SafeStr
    issue_date: date
    sdi_document_type: ParsedInvoiceDocumentType
    currency: SafeStr
    supplier: ParsedInvoiceParty
    customer: ParsedInvoiceParty
    lines: list[ParsedInvoiceLine]
    recaps: list[ParsedInvoiceTaxSummary]
    taxable_amount: Decimal
    vat_amount: Decimal
    total: Decimal
    stamp_duty: Decimal | None = None
    social_security_pension_fund: list[ParsedInvoiceSocialCharge]
    termini_payment: list[ParsedInvoicePaymentTerms]
    transmission: ParsedInvoiceTransmission
