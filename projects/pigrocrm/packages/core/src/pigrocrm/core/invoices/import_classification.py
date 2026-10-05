"""The combined outcome of classifying a parsed invoice's direction and checking
it against the register (REB-364) -- one entry point wiring `import_direction`
and `import_dedup` together the way the review step (REB-365) needs them.

Mirrors mastro's own `importer.ts`/`review.ts` outcome tagging: never a silent
drop, always one named outcome per invoice. Deliberately a subset of the full
review-step vocabulary (design record §4): `"needs_customer_confirmation"` is a
customer-matching concern this issue does not touch, so it is not named here --
this module answers only "which direction, and is it already on the register",
and its `"ready"` means exactly "outgoing, not a duplicate", nothing about the
customer yet.
"""

from collections.abc import Callable, Sequence
from typing import Literal

from pigrocrm.core.emitter.models import LegalEntity
from pigrocrm.core.invoices.import_dedup import check_invoice_duplicate
from pigrocrm.core.invoices.import_direction import match_azienda
from pigrocrm.core.invoices.import_schemas import ParsedInvoice
from pigrocrm.core.invoices.models import Invoice

InvoiceImportClassification = Literal["ready", "already_present", "conflict", "incoming_skipped"]
"""`"incoming_skipped"` -- a supplier's invoice: classified and reported, never
checked against the register at all (there is nothing there for it, design §3)
and never written. `"already_present"`/`"conflict"` -- an outgoing invoice, checked
against the register: the same rules `import_dedup.check_invoice_duplicate`
names. `"ready"` -- outgoing, and no invoice on record at this natural key yet:
safe to carry on to whatever this issue does not decide (customer matching,
REB-365; the actual write, REB-366)."""


def classify_parsed_invoice(
    invoice: ParsedInvoice,
    aziende: Sequence[LegalEntity],
    *,
    existing_for: Callable[[LegalEntity], Invoice | None],
    content: bytes,
) -> tuple[InvoiceImportClassification, LegalEntity | None]:
    """Direction first, register second -- exactly the order mastro's own
    `importer.ts` runs them in, and for the same reason: an incoming invoice has
    no natural key to check at all (PigroCRM's register only ever held its own
    aziende's outgoing invoices), so checking duplication before direction would be
    checking a fact that does not apply.

    Answers the classification and the azienda the file lands on (REB-619, spec
    §1.5): `None` with `"incoming_skipped"`, the matched azienda otherwise, which is
    whose register `existing_for` is asked about. `existing_for` is the caller's own
    `InvoiceRepository.existing_by_number(azienda.id, anno, numero)` lookup at
    whatever natural key the caller has already derived for this invoice, deferred
    until the azienda is known, so an incoming invoice costs no register read at all.
    `content` is the raw bytes of the document being imported.
    """
    azienda = match_azienda(invoice.fornitore, aziende)
    if azienda is None:
        return "incoming_skipped", None
    duplicate = check_invoice_duplicate(existing_for(azienda), content)
    if duplicate == "new":
        return "ready", azienda
    return duplicate, azienda
