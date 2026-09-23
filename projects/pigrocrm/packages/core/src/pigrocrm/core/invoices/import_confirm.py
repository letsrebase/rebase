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

**One write function.** The only thing this module adds beyond `import_review`'s
own classification is `map_parsed_invoice_to_import`, a pure translation of a
confirmed `ParsedInvoice` onto `InvoiceImport`'s own fields (design §5 item 2)
-- mirroring mastro's own `mapInvoiceToInput` (`persist.ts:91-112`). The actual
write is `InvoiceService.import_issued` itself, called once per invoice inside
`InvoiceService.confirm_import`: this is the direct guarantee against
divergence between the hand-declared and the newly-parsed path, since both run
the same register rules at the same call.

**`importata_da` stays `"esterno"`, on purpose.** Widening the column's
`Literal` to a second value is design §5 item 4/§7 item 6, a later, unsigned-off
follow-up -- not this issue's. Nothing in the schema ties `importata_da` to the
nullness of `xml_document_id`/`xml_hash_sha256` (design §5 item 3, confirmed by
reading `models.py`), so a `"esterno"` row carrying a hash-verified
`xml_document_id` is additive, not a migration of the existing path's own
guarantees.
"""

from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from pigrocrm.core.invoices.import_schemas import ParsedInvoice
from pigrocrm.core.invoices.schemas import InvoiceImport, InvoiceLineImport, InvoiceRead

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


class ConfirmedInvoiceRead(BaseModel):
    """One row of `confirm_invoice_import`'s own output: one per invoice the
    reviewed document parses into, mirroring `ReviewedInvoiceRead`'s own shape.

    `fattura` is set only for `"imported"` (freshly written) and
    `"already_present"` (the row already on record at that natural key) --
    never for a refusal, since nothing was written or matched. `buchi_non_
    dichiarati` is set only for `"imported"`, mirroring `POST /api/invoices/
    import`'s own response: the numbers still missing under this invoice's own
    `anno`, so a caller sees in one round trip what `declare_invoice_register_
    gaps` still has to cover.
    """

    model_config = ConfigDict(extra="forbid")

    document_id: UUID
    outcome: InvoiceConfirmOutcome
    fattura: InvoiceRead | None = None
    buchi_non_dichiarati: list[int] | None = None


class InvoiceConfirmRequest(BaseModel):
    """One already-archived document plus the one human decision this issue's
    scope adds: which `Customer` to attach when no exact tax-id match exists
    (`review_invoice_import`'s own `"needs_customer_confirmation"`). Creating a
    customer inside the same transaction is design §7 item 5's own follow-up,
    not built here: today's caller resolves or creates the `Customer` first,
    through the existing customer surface, and hands its id here.

    `customer_id`, when given, overrides whatever the current tax-id match
    would find on its own -- the human's decision always wins over the
    automatic match, exactly as `"needs_customer_confirmation"`'s own name
    promises a caller who reads it.
    """

    model_config = ConfigDict(extra="forbid")

    document_id: UUID
    customer_id: UUID | None = None


class InvoiceConfirmResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    righe: list[ConfirmedInvoiceRead]


def map_parsed_invoice_to_import(
    invoice: ParsedInvoice, *, anno: int, numero: int, customer_id: UUID
) -> InvoiceImport:
    """The confirmed `ParsedInvoice` onto `InvoiceImport`'s own fields (design §5
    item 2), mirroring mastro's own `mapInvoiceToInput` (`persist.ts:91-112`):
    `imponibile`/`imposta` are already the document-level sums `ParsedInvoice`
    itself carries (its own docstring), never re-summed here, and `righe` is a
    field-for-field copy -- `ParsedInvoiceLine` and `InvoiceLineImport` name the
    same five facts about a billed line the same way, on purpose (`import_
    schemas`'s own module docstring).

    `natura` is copied straight from `ParsedInvoiceLine.natura` -- the line's
    own declared exemption code, read directly off `DettaglioLinee/Natura` by
    the parser. `riferimento_normativo`, which FatturaPA never repeats at the
    line level, is looked up from `invoice.riepiloghi` by the pair `(aliquota_
    iva, natura)` -- never by rate alone, which this codebase's own export-side
    `totals.RiepilogoGroup` already treats as the real grouping key, and which
    a rate-only lookup would get wrong on any invoice mixing two exemption
    codes at the same rate (`ParsedInvoiceLine`'s own docstring). This is not
    optional: `invoice_lines`'s own `ck_invoice_lines_natura_agrees_with_rate`
    requires `natura IS NOT NULL` on every zero-rate line and `NULL` on every
    other one, so a missing `natura` fails that constraint on exactly the
    exempt-rate invoices this parser exists to import.

    `anno`/`numero` are the caller's own `import_review.natural_key(invoice)`,
    never re-derived here: by the time this is called the caller has already
    confirmed that key is new (`classify_parsed_invoice` returned `"ready"`),
    and `import_issued`'s own `data.anno != data.data_emissione.year` check is
    what actually enforces the register's invariant, not a second copy of it
    here.

    `importata_da` is left at `InvoiceImport`'s own default, `"esterno"` (module
    docstring above). `data_scadenza`, `causale`, `competenza_da`/`competenza_a`,
    `pdf_sorgente`, `note_interne` and `trasmessa_esternamente_il` all stay
    unset too: none of them has a source on `ParsedInvoice` today, so `import_
    issued`'s own defaults (the regime's `giorni_scadenza` for the due date,
    chief among them) apply exactly as they would for a hand-typed import that
    left the same fields out.
    """
    riferimento_by_pair: dict[tuple[Decimal, str | None], str | None] = {}
    for riepilogo in invoice.riepiloghi:
        riferimento_by_pair.setdefault(
            (riepilogo.aliquota_iva, riepilogo.natura), riepilogo.riferimento_normativo
        )
    return InvoiceImport(
        anno=anno,
        numero=numero,
        data_emissione=invoice.data_emissione,
        customer_id=customer_id,
        righe=[
            InvoiceLineImport(
                descrizione=riga.descrizione,
                quantita=riga.quantita,
                prezzo_unitario=riga.prezzo_unitario,
                prezzo_totale=riga.prezzo_totale,
                aliquota_iva=riga.aliquota_iva,
                natura=riga.natura,
                riferimento_normativo=riferimento_by_pair.get((riga.aliquota_iva, riga.natura)),
            )
            for riga in invoice.righe
        ],
        imponibile=invoice.imponibile,
        imposta=invoice.imposta,
        bollo=invoice.bollo if invoice.bollo is not None else Decimal("0.00"),
        totale=invoice.totale,
    )
