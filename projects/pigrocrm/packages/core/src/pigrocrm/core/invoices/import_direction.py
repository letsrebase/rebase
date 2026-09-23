"""Direction detection for a parsed invoice (REB-364), ported from mastro's
`src/lib/server/import/direction.ts`, mapped onto PigroCRM's single-tenant-per-space
model (design record `2026-09-23-mastro-invoice-import-onto-pigrocrm-design.md` §3,
§5 item 2).

mastro reads a fixed `accountHolderTaxId` from its own deployment's config file,
because mastro is one deployment for one consultant. PigroCRM has no equivalent
config, and needs none: whichever space's database a request opened is the only
`EmitterProfile` row this classifier can even see, so "the account holder's own tax
id" is simply that row (design §3) -- no tenant-aware plumbing.

Reads only the supplier (`fornitore`), never `trasmissione`: an invoicing agent
transmitting on the account holder's own behalf appears as `trasmissione`, and the
account holder is still the `fornitore` -- mastro's own `direction.ts:1-9` gives the
identical reason for never reading `transmission`, and `import_schemas.
ParsedInvoiceTransmission`'s own docstring restates it on this side.
"""

from typing import Literal

from pigrocrm.core.emitter.models import EmitterProfile
from pigrocrm.core.errors import ValidationFailed
from pigrocrm.core.invoices.fatturapa import normalise_fiscal_id
from pigrocrm.core.invoices.import_schemas import ParsedInvoice, ParsedInvoiceParty

InvoiceDirection = Literal["outgoing", "incoming"]
"""`"outgoing"` -- the account holder issued this invoice: revenue, exactly what
every row in the `invoices` table already means (design §3 -- there is no
`fornitore`/supplier column on `Invoice` at all, because until now every row was
implicitly one). `"incoming"` -- a supplier billed the account holder: an invoice
PigroCRM has no representation for today, parsed and reported but never written
(design §3)."""


def classify_direction(fornitore: ParsedInvoiceParty, emitter: EmitterProfile) -> InvoiceDirection:
    """Whether `fornitore` -- the party the parsed document names as having issued
    it -- *is* the account holder, compared by fiscal identifier, case- and
    punctuation-insensitively (mirrors mastro's `classifyDirection`,
    `direction.ts:38-49`).

    Two independent channels, not one: mastro's own `taxId` is a single string
    because mastro's source document always carries one identifier; PigroCRM's
    `ParsedInvoiceParty`/`EmitterProfile` both carry `partita_iva` and
    `codice_fiscale` as separate, independently optional fields (mirrors
    `import_schemas.ParsedInvoiceParty`'s own docstring). A match on *either*
    channel means the fornitore is the account holder: a document can carry only
    a VAT number, only a fiscal code, or both, and the two rows being compared are
    free to have populated a different one of the two.

    `normalise_fiscal_id` (`fatturapa.py`) does the comparison-shape work already
    written and tested for the export path -- strips the `IT` prefix and
    punctuation, upper-cases, and validates the two shapes FPR12 recognises --
    reused here rather than re-implemented, so a VAT number stored as
    `"01234567890"` on `EmitterProfile` and read as `"IT01234567890"` off a
    document's `IdFiscaleIVA` compare equal.

    Raises `ValidationFailed` if `emitter` itself carries neither identifier in a
    recognisable shape: there is then no fact to classify against, and guessing
    would be worse than refusing. Deliberately a looser condition than
    `fatturapa.py`'s own `_cedente`, which refuses to *emit* whenever
    `partita_iva` alone is missing (mandatory for a `CedentePrestatore`,
    `fatturapa.py:913-919`): this classifier only needs *some* fact to compare
    against, so an emitter configured with only a `codice_fiscale` still
    classifies, even though the same row could not itself emit a FatturaPA
    document.
    """
    emitter_piva = normalise_fiscal_id(emitter.partita_iva)
    emitter_cf = normalise_fiscal_id(emitter.codice_fiscale)
    if emitter_piva is None and emitter_cf is None:
        raise ValidationFailed(
            "emitter_profile",
            "partita_iva",
            "l'emittente non ha ne' una partita IVA ne' un codice fiscale validi: "
            "non e' possibile classificare la direzione di una fattura importata",
            expected="partita IVA (11 cifre) o codice fiscale (16 caratteri)",
        )
    supplier_piva = normalise_fiscal_id(fornitore.partita_iva)
    supplier_cf = normalise_fiscal_id(fornitore.codice_fiscale)
    is_account_holder = (emitter_piva is not None and supplier_piva == emitter_piva) or (
        emitter_cf is not None and supplier_cf == emitter_cf
    )
    return "outgoing" if is_account_holder else "incoming"


def classify_invoice_direction(invoice: ParsedInvoice, emitter: EmitterProfile) -> InvoiceDirection:
    """`classify_direction` against a full `ParsedInvoice`'s own `fornitore` --
    never `invoice.trasmissione` (module docstring above). Mirrors mastro's own
    wrapper (`direction.ts:63-71`)."""
    return classify_direction(invoice.fornitore, emitter)
