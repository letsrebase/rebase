"""Natural-key/hash duplicate check for a parsed invoice against PigroCRM's own
register (REB-364), ported from mastro's `src/lib/server/import/dedup.ts`.

The natural key itself -- `(anno, numero)` -- is already the register's own unique
constraint (`uq_invoices_anno_numero`), and `InvoiceRepository.existing_by_number`
is its lookup; nothing here re-derives either. What this module adds is the hash
comparison mastro's own `naturalInvoiceKey` folds into the same natural-key check:
given the invoice already on record at that number (or none), and the content of
the bytes being imported now, is this the same document seen before, a different
one filed under the same number, or a genuinely new number.

One rule stated explicitly because it is easy to get backwards: a numbered invoice
already on record with **no** stored hash is `"conflict"`, never
`"already_present"`, whatever the incoming bytes are -- there is nothing to
compare against, so nothing can be proven identical. This mirrors the state the
existing `importata_da: "esterno"` path already produces on purpose
(`xml_hash_sha256` stays `NULL` by construction, spec
`2026-09-04-slice-9-import-storico-e-google-drive-design.md` §3.1) and, per the
newer design record's §5 item 3, the exact state a `lotto` batch's own rows are
left in too -- so re-importing either kind of already-registered-but-hashless
invoice always reports a conflict, the conservative default, never an identity it
cannot prove.
"""

import hashlib
from typing import Literal

from pigrocrm.core.invoices.models import Invoice

InvoiceDuplicateOutcome = Literal["new", "already_present", "conflict"]
"""`"new"` -- no invoice on record at this `(anno, numero)`: nothing to compare
against, safe to proceed. `"already_present"` -- the invoice on record at this
number has a stored hash and it matches the incoming bytes exactly: the same
document, seen before. `"conflict"` -- the number is on record and either its
stored hash differs from the incoming bytes, or it has no stored hash at all
(module docstring above): a human must resolve which is right, never assumed."""


def check_invoice_duplicate(existing: Invoice | None, content: bytes) -> InvoiceDuplicateOutcome:
    """`existing` is whatever `InvoiceRepository.existing_by_number(anno, numero)`
    found at the natural key the invoice being imported now declares, or `None`.
    `content` is the raw bytes of the document being imported.

    Hashed here with the same `hashlib.sha256(...).hexdigest()` `InvoiceService`'s
    own XML-export path already uses to fill and compare `xml_hash_sha256`
    (`service.py`), so a stored hash and a freshly computed one are always the
    same digest of the same algorithm -- one comparator, not two independently
    maintained ones.
    """
    if existing is None:
        return "new"
    if existing.xml_hash_sha256 is not None:
        digest = hashlib.sha256(content).hexdigest()
        if existing.xml_hash_sha256 == digest:
            return "already_present"
    return "conflict"
