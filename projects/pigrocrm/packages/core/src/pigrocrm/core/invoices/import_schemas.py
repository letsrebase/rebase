"""The neutral parsed-invoice shape (REB-363), ported from mastro's
`src/lib/server/import/invoice.ts`.

Every structured invoice format an adapter parses maps onto `ParsedInvoice` --
translated into PigroCRM's own vocabulary (`imponibile`/`imposta` where mastro says
`taxableAmount`/`taxAmount`, `fornitore`/`cliente` where it says
`supplier`/`customer`), never the other way around: a second format adapter is a
translation into this shape, not a change to it.

Deliberately not `InvoiceImport` (`invoices/schemas.py`): that schema is "what a
caller declares" (`extra="forbid"`, hand-typed totals, a fixed `Literal["esterno"]`
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
    "fattura",
    "acconto_fattura",
    "acconto_parcella",
    "nota_credito",
    "nota_debito",
    "parcella",
]
"""What kind of document this is, named after what the FatturaPA `TipoDocumento`
code means (TD01..TD06) rather than after the code itself -- mirroring mastro's own
`InvoiceDocumentType`, named after the concept rather than one format's code list,
so a second format adapter maps its own codes onto the same six names."""

ParsedInvoiceDueDateSource = Literal["documento", "calcolata"]
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

    `partita_iva`/`codice_fiscale` are two first-class, independently optional
    fields rather than mastro's single `taxId` (which changes shape depending on
    which identifier the source document happened to carry): PigroCRM already
    treats both as first-class columns on `Customer`/`EmitterProfile`, so there is
    no "whichever one" concept to port here. `partita_iva` carries `IdPaese` +
    `IdCodice` concatenated (e.g. `"IT01234567890"`) exactly as mastro's
    `fiscalIdString` does, since a later direction-detection step needs the
    identifier a FatturaPA document actually guarantees. A concrete adapter
    enforces that at least one of the two is present when the source schema
    itself allows neither -- FatturaPA's `CessionarioCommittente` is the one case
    (mastro's `mapCustomer` throws for the same reason); `CedentePrestatore`
    always carries `IdFiscaleIVA`, mandatory in FatturaPA's own schema.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    ragione_sociale: SafeStr
    partita_iva: SafeStr | None = None
    codice_fiscale: SafeStr | None = None
    indirizzo: SafeStr
    cap: SafeStr
    comune: SafeStr
    provincia: SafeStr | None = None
    nazione: SafeStr


class ParsedInvoiceLine(BaseModel):
    """One billed line, mirroring mastro's `InvoiceLine` (`invoice.ts:65-73`) with
    the same field names `InvoiceLineImport` already uses for a hand-declared line
    -- it is the same fact, read two different ways.

    `aliquota_iva` is the line's own VAT rate, which is what ties it back to the
    `ParsedInvoiceTaxSummary` block it was folded into.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    descrizione: SafeStr
    quantita: Decimal
    prezzo_unitario: Decimal
    prezzo_totale: Decimal
    aliquota_iva: Decimal


class ParsedInvoiceTaxSummary(BaseModel):
    """One VAT-rate summary block (FatturaPA's `DatiRiepilogo`). A real invoice can
    carry more than one -- mixed-rate invoices are routine -- which is why this is
    a list on `ParsedInvoice` rather than a single flat pair.

    `riferimento_normativo` is the literal wording the issuing document carries,
    already in the language it was written in -- never translated, never replaced
    by a jurisdiction's own wording for the same `natura` code: this is what a
    specific document actually said, which can legitimately disagree with what the
    code should say (a supplier's software using outdated wording).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    aliquota_iva: Decimal
    natura: SafeStr | None = None
    riferimento_normativo: SafeStr | None = None
    imponibile: Decimal
    imposta: Decimal


class ParsedInvoiceSocialCharge(BaseModel):
    """A social-security fund contribution charged on the invoice (FatturaPA's
    `DatiCassaPrevidenziale`). An invoice can carry more than one fund, though a
    single consultant only ever pays into one; kept as a list so a second one is a
    longer list, not a dropped field.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    tipo_cassa: SafeStr
    aliquota_cassa: Decimal
    importo_contributo_cassa: Decimal
    imponibile_cassa: Decimal | None = None
    aliquota_iva: Decimal


class ParsedInvoicePaymentInstallment(BaseModel):
    """One instalment of a payment plan. `data_scadenza` is either read verbatim
    from the document (FatturaPA's `DataScadenzaPagamento`) or computed from a
    relative term the document expresses instead (`DataRiferimentoTerminiPagamento`
    plus `GiorniTerminiPagamento`) -- never invented from anything outside the
    document itself. `origine_scadenza` tells a reader which case produced it.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    data_scadenza: date
    origine_scadenza: ParsedInvoiceDueDateSource
    importo: Decimal
    modalita_pagamento: SafeStr
    iban: SafeStr | None = None


class ParsedInvoicePaymentTerms(BaseModel):
    """One payment-terms block: a condition code plus the instalments it governs.
    FatturaPA allows more than one block when a document mixes payment
    conditions; kept as a list on `ParsedInvoice` for the same reason as
    `riepiloghi`.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    condizioni_pagamento: SafeStr
    rate: list[ParsedInvoicePaymentInstallment]


class ParsedInvoiceTransmission(BaseModel):
    """Who actually sent this document to the Sistema di Interscambio, and that
    transmission's own sequence number. Deliberately not where direction detection
    looks: when an invoicing service files on the account holder's behalf,
    `id_trasmittente` is the *service*, and the account holder appears only as
    `fornitore`. Reading this field for direction would misclassify every invoice a
    service transmits on someone else's behalf.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id_trasmittente: SafeStr
    progressivo_invio: SafeStr


class ParsedInvoice(BaseModel):
    """A single invoice, exactly as a structured document states it -- mastro's
    `Invoice` (`invoice.ts:169-196`), translated into PigroCRM's own vocabulary.

    `imponibile`/`imposta` are the sums of `riepiloghi[].imponibile`/`.imposta`,
    kept as their own fields because the fiscal domain already names them at the
    invoice level (mirrors `InvoiceImport`). `totale` is the document's own stated
    total, never derived by summing the fields above -- a document is free to
    round or add charges this shape does not model, and the total it declares is
    the one that must reconcile with what was actually paid.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    numero: SafeStr
    data_emissione: date
    tipo_documento: ParsedInvoiceDocumentType
    divisa: SafeStr
    fornitore: ParsedInvoiceParty
    cliente: ParsedInvoiceParty
    righe: list[ParsedInvoiceLine]
    riepiloghi: list[ParsedInvoiceTaxSummary]
    imponibile: Decimal
    imposta: Decimal
    totale: Decimal
    bollo: Decimal | None = None
    cassa_previdenziale: list[ParsedInvoiceSocialCharge]
    termini_pagamento: list[ParsedInvoicePaymentTerms]
    trasmissione: ParsedInvoiceTransmission
