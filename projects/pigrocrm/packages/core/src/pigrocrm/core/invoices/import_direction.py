"""Direction detection for a parsed invoice (REB-364), ported from mastro's
`src/lib/server/import/direction.ts`, mapped onto PigroCRM's single-tenant-per-space
model (design record `2026-09-23-mastro-invoice-import-onto-pigrocrm-design.md` §3,
§5 item 2).

mastro reads a fixed `accountHolderTaxId` from its own deployment's config file,
because mastro is one deployment for one consultant. PigroCRM has no equivalent
config, and needs none: whichever space's database a request opened holds the
`LegalEntity` rows this classifier can see, and since REB-619 (spec 2026-10-03 §1.5)
there may be several, so "the account holder's own tax id" is the set of the active
Italian legal entities's ids, and a match names *which* legal entity the file lands on.

Reads only the supplier (`supplier`), never `transmission`: an invoicing agent
transmitting on the account holder's own behalf appears as `transmission`, and the
account holder is still the `supplier` -- mastro's own `direction.ts:1-9` gives the
identical reason for never reading `transmission`, and `import_schemas.
ParsedInvoiceTransmission`'s own docstring restates it on this side.
"""

from collections.abc import Sequence
from typing import Literal

from pigrocrm.core.errors import ValidationFailed
from pigrocrm.core.invoices.fatturapa.normalise import normalise_fiscal_id
from pigrocrm.core.invoices.import_schemas import ParsedInvoiceParty
from pigrocrm.core.legal_entities.models import LegalEntity

InvoiceDirection = Literal["outgoing", "incoming"]
"""`"outgoing"` -- the account holder issued this invoice: revenue, exactly what
every row in the `invoices` table already means (design §3 -- there is no
`supplier`/supplier column on `Invoice` at all, because until now every row was
implicitly one). `"incoming"` -- a supplier billed the account holder: an invoice
PigroCRM has no representation for today, parsed and reported but never written
(design §3)."""


def match_legal_entity(
    supplier: ParsedInvoiceParty, legal_entities: Sequence[LegalEntity]
) -> LegalEntity | None:
    """The legal entity `supplier` -- the party the parsed document names as having issued
    it -- *is*, compared by fiscal identifier, case- and punctuation-insensitively
    (mirrors mastro's `classifyDirection`, `direction.ts:38-49`), or `None` when it is
    none of them: a supplier's invoice.

    Two independent channels, not one: mastro's own `taxId` is a single string
    because mastro's source document always carries one identifier; PigroCRM's
    `ParsedInvoiceParty`/`LegalEntity` both carry `vat_number` and `tax_code` as
    separate, independently optional fields (mirrors `import_schemas.
    ParsedInvoiceParty`'s own docstring). A match on *either* channel means the
    supplier is that legal entity: a document can carry only a VAT number, only a fiscal
    code, or both, and the two rows being compared are free to have populated a
    different one of the two.

    `normalise_fiscal_id` (`fatturapa/normalise.py`) does the comparison-shape work already
    written and tested for the export path -- strips the `IT` prefix and punctuation,
    upper-cases, and validates the two shapes FPR12 recognises -- reused here rather
    than re-implemented, so a VAT number stored as `"01234567890"` on `LegalEntity` and
    read as `"IT01234567890"` off a document's `IdFiscaleIVA` compare equal.

    Two legal entities matched by different channels (X by P.IVA, Y by codice fiscale) is a
    configuration the unique indexes on each id cannot rule out, and it is refused
    rather than resolved by picking one: nothing is written on a file whose issuer is
    ambiguous (spec §1.5). Two legal entities matched by the same channel cannot exist, since
    `uq_legal_entities_vat_number` and `_codice_fiscale` refuse the second row.

    Raises `ValidationFailed` if no legal entity carries either identifier in a
    recognisable shape: there is then no fact to classify against, and guessing
    would be worse than refusing. Deliberately a looser condition than
    `fatturapa/exporter.py`'s own `_supplier`, which refuses to *emit* whenever `vat_number`
    alone is missing (mandatory for a `CedentePrestatore`, `fatturapa/exporter.py`):
    this classifier only needs *some* fact to compare against, so a legal entity
    configured with only a `tax_code` still classifies, even though the same
    row could not itself emit a FatturaPA document.
    """
    identities = [
        (a, normalise_fiscal_id(a.vat_number), normalise_fiscal_id(a.tax_code))
        for a in legal_entities
    ]
    if not any(vat_number or cf for _, vat_number, cf in identities):
        raise ValidationFailed(
            "legal_entity",
            "vat_number",
            "nessuna azienda ha una partita IVA o un codice fiscale validi: "
            "non e' possibile classificare la direzione di una fattura importata",
            expected="partita IVA (11 cifre) o codice fiscale (16 caratteri)",
        )
    supplier_vat_number = normalise_fiscal_id(supplier.vat_number)
    supplier_cf = normalise_fiscal_id(supplier.tax_code)
    matched: list[LegalEntity] = []
    for legal_entity, vat_number, cf in identities:
        by_vat_number = vat_number is not None and supplier_vat_number == vat_number
        by_cf = cf is not None and supplier_cf == cf
        if by_vat_number or by_cf:
            matched.append(legal_entity)
    if len(matched) > 1:
        raise ValidationFailed(
            "legal_entity",
            "tax_code",
            "il fornitore del file corrisponde a due aziende, una per partita IVA e una per "
            "codice fiscale: correggi i dati fiscali delle aziende prima di importare",
            expected="un fornitore che corrisponde a una sola azienda",
        )
    return matched[0] if matched else None


def classify_direction(
    supplier: ParsedInvoiceParty, legal_entities: Sequence[LegalEntity]
) -> InvoiceDirection:
    """`"outgoing"` when `supplier` is one of the space's aziende (`match_legal_entity`),
    `"incoming"` otherwise."""
    return "outgoing" if match_legal_entity(supplier, legal_entities) is not None else "incoming"
