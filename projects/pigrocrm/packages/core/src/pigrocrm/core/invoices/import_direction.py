"""Direction detection for a parsed invoice (REB-364), ported from mastro's
`src/lib/server/import/direction.ts`, mapped onto PigroCRM's single-tenant-per-space
model (design record `2026-09-23-mastro-invoice-import-onto-pigrocrm-design.md` §3,
§5 item 2).

mastro reads a fixed `accountHolderTaxId` from its own deployment's config file,
because mastro is one deployment for one consultant. PigroCRM has no equivalent
config, and needs none: whichever space's database a request opened holds the
`LegalEntity` rows this classifier can see, and since REB-619 (spec 2026-10-03 §1.5)
there may be several, so "the account holder's own tax id" is the set of the active
Italian aziende's ids, and a match names *which* azienda the file lands on.

Reads only the supplier (`fornitore`), never `trasmissione`: an invoicing agent
transmitting on the account holder's own behalf appears as `trasmissione`, and the
account holder is still the `fornitore` -- mastro's own `direction.ts:1-9` gives the
identical reason for never reading `transmission`, and `import_schemas.
ParsedInvoiceTransmission`'s own docstring restates it on this side.
"""

from collections.abc import Sequence
from typing import Literal

from pigrocrm.core.emitter.models import LegalEntity
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


def match_azienda(
    fornitore: ParsedInvoiceParty, aziende: Sequence[LegalEntity]
) -> LegalEntity | None:
    """The azienda `fornitore` -- the party the parsed document names as having issued
    it -- *is*, compared by fiscal identifier, case- and punctuation-insensitively
    (mirrors mastro's `classifyDirection`, `direction.ts:38-49`), or `None` when it is
    none of them: a supplier's invoice.

    Two independent channels, not one: mastro's own `taxId` is a single string
    because mastro's source document always carries one identifier; PigroCRM's
    `ParsedInvoiceParty`/`LegalEntity` both carry `partita_iva` and `codice_fiscale` as
    separate, independently optional fields (mirrors `import_schemas.
    ParsedInvoiceParty`'s own docstring). A match on *either* channel means the
    fornitore is that azienda: a document can carry only a VAT number, only a fiscal
    code, or both, and the two rows being compared are free to have populated a
    different one of the two.

    `normalise_fiscal_id` (`fatturapa.py`) does the comparison-shape work already
    written and tested for the export path -- strips the `IT` prefix and punctuation,
    upper-cases, and validates the two shapes FPR12 recognises -- reused here rather
    than re-implemented, so a VAT number stored as `"01234567890"` on `LegalEntity` and
    read as `"IT01234567890"` off a document's `IdFiscaleIVA` compare equal.

    Two aziende matched by different channels (X by P.IVA, Y by codice fiscale) is a
    configuration the unique indexes on each id cannot rule out, and it is refused
    rather than resolved by picking one: nothing is written on a file whose issuer is
    ambiguous (spec §1.5). Two aziende matched by the same channel cannot exist, since
    `uq_emitter_profile_partita_iva` and `_codice_fiscale` refuse the second row.

    Raises `ValidationFailed` if no azienda carries either identifier in a
    recognisable shape: there is then no fact to classify against, and guessing
    would be worse than refusing. Deliberately a looser condition than
    `fatturapa.py`'s own `_cedente`, which refuses to *emit* whenever `partita_iva`
    alone is missing (mandatory for a `CedentePrestatore`, `fatturapa.py:913-919`):
    this classifier only needs *some* fact to compare against, so an azienda
    configured with only a `codice_fiscale` still classifies, even though the same
    row could not itself emit a FatturaPA document.
    """
    identities = [
        (a, normalise_fiscal_id(a.partita_iva), normalise_fiscal_id(a.codice_fiscale))
        for a in aziende
    ]
    if not any(piva or cf for _, piva, cf in identities):
        raise ValidationFailed(
            "emitter_profile",
            "partita_iva",
            "nessuna azienda ha una partita IVA o un codice fiscale validi: "
            "non e' possibile classificare la direzione di una fattura importata",
            expected="partita IVA (11 cifre) o codice fiscale (16 caratteri)",
        )
    supplier_piva = normalise_fiscal_id(fornitore.partita_iva)
    supplier_cf = normalise_fiscal_id(fornitore.codice_fiscale)
    matched: list[LegalEntity] = []
    for azienda, piva, cf in identities:
        by_piva = piva is not None and supplier_piva == piva
        by_cf = cf is not None and supplier_cf == cf
        if by_piva or by_cf:
            matched.append(azienda)
    if len(matched) > 1:
        raise ValidationFailed(
            "emitter_profile",
            "codice_fiscale",
            "il fornitore del file corrisponde a due aziende, una per partita IVA e una per "
            "codice fiscale: correggi i dati fiscali delle aziende prima di importare",
            expected="un fornitore che corrisponde a una sola azienda",
        )
    return matched[0] if matched else None


def classify_direction(
    fornitore: ParsedInvoiceParty, aziende: Sequence[LegalEntity]
) -> InvoiceDirection:
    """`"outgoing"` when `fornitore` is one of the space's aziende (`match_azienda`),
    `"incoming"` otherwise."""
    return "outgoing" if match_azienda(fornitore, aziende) is not None else "incoming"


def classify_invoice_direction(
    invoice: ParsedInvoice, aziende: Sequence[LegalEntity]
) -> InvoiceDirection:
    """`classify_direction` against a full `ParsedInvoice`'s own `fornitore` --
    never `invoice.trasmissione` (module docstring above). Mirrors mastro's own
    wrapper (`direction.ts:63-71`)."""
    return classify_direction(invoice.fornitore, aziende)
