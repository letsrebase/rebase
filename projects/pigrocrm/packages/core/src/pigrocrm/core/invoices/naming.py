"""Deterministic names for things that leave the system.

Every function here is a pure function of stored integers. None reads a label, a
title, or any other user-visible string: the previous system recovered the fiscal progressive from
a display title with `/Fattura\\s+(\\d+)/i`, which made the number on a legal document
a derivative of a caption someone could rename.
"""

import re
from uuid import UUID

from pigrocrm.core.errors import ValidationFailed
from pigrocrm.core.invoices.schemas import MAX_NUMERO

ENTITY = "invoice"

_BASE36_DIGITS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
_PROGRESSIVO_WIDTH = 5
_YEAR_STRIDE = 10_000

# An `IdCodice` is either an 11-digit VAT number or a 16-character fiscal code -- the
# two forms the previous system's own normalisation accepted, which is the highest-value line in
# that file. `.fullmatch` everywhere, never `.match` with `$`: `$` matches before a
# trailing newline, and a name with a newline in it reaches a filesystem and a
# `Content-Disposition` header as something neither agreed to.
FISCAL_ID_RE = re.compile(r"\d{11}|[A-Z0-9]{16}")
NUMERO_COMPLETO_RE = re.compile(r"\d{4}/\d{1,4}")
RIFERIMENTO_PROFORMA_RE = re.compile(r"PROV-\d{4}-\d{4,6}")


def _to_base36(value: int, width: int) -> str:
    digits: list[str] = []
    while value:
        value, remainder = divmod(value, 36)
        digits.append(_BASE36_DIGITS[remainder])
    return "".join(reversed(digits)).rjust(width, "0")


def progressivo_invio(anno: int, numero: int) -> str:
    """`ProgressivoInvio`, and the same token the file name carries.

    It identifies the *file*, not the invoice, which is why the previous system deriving it from the
    invoice progressive was wrong in principle -- but it must also be **deterministic**,
    because spec 14.6 requires a re-export to be byte-identical to the original. A
    fresh random or clock-derived value would satisfy the first property and break the
    second. `anno * 10000 + numero` in base 36 satisfies both: it is a function of the
    invoice's own identity, unique across years, and stable forever.

    Five characters is enough while `numero <= 9999` (36**5 = 60 466 176, comfortably
    above 2999 * 10000 + 9999). Past that the mapping stops being injective across
    years, so emission refuses rather than producing a name that could collide.
    """
    if not 1 <= numero <= MAX_NUMERO:
        raise ValidationFailed(
            ENTITY,
            "numero",
            f"un progressivo oltre {MAX_NUMERO} renderebbe ambiguo il nome del file XML",
            expected=f"un numero fra 1 e {MAX_NUMERO}",
        )
    return _to_base36(anno * _YEAR_STRIDE + numero, _PROGRESSIVO_WIDTH)


def sdi_filename(id_fiscale: str, anno: int, numero: int) -> str:
    """`IT{cf_o_piva}_{progressivo}.xml`, the SdI's own convention.

    This is the **download** name, set in `Content-Disposition`, not a storage key:
    `storage/base.py`'s key pattern is lowercase-only by deliberate decision (two keys
    differing only in case name the same file on APFS and NTFS), so an uppercase `IT`
    prefix could never be one. The name matters because the file goes to an
    intermediary, which often validates the name before the content.
    """
    if not FISCAL_ID_RE.fullmatch(id_fiscale):
        raise ValidationFailed(
            "emitter_profile",
            "partita_iva",
            "il nome del file XML richiede una partita IVA di 11 cifre o un codice "
            "fiscale di 16 caratteri",
            expected="11 cifre oppure 16 caratteri alfanumerici maiuscoli",
        )
    return f"IT{id_fiscale}_{progressivo_invio(anno, numero)}.xml"


def invoice_storage_prefix(azienda_id: UUID, anno: int, numero: int) -> str:
    """`fatture/{azienda_id}/{anno}/{numero}` -- so a file pulled out of context is
    still identifiable, which is mechanism 4 of the four that keep a proforma from
    being mistaken for an invoice. The azienda first since REB-619, because `2026/1`
    names one invoice per azienda; every key written before it stays on its row and is
    never moved (spec 2026-10-03 §1.4)."""
    return f"fatture/{azienda_id}/{anno}/{numero}"


def proforma_storage_prefix(invoice_id: UUID) -> str:
    """`proforma/{id}` -- a different prefix, deliberately, from
    `invoice_storage_prefix`. A proforma has no number, so its id is its only stable
    handle."""
    return f"proforma/{invoice_id}"


def numero_completo(anno: int, numero: int) -> str:
    """The `Numero` printed on the document and sent in the XML.

    `{anno}/{numero}`, not the previous system's `"{n}/00"`: that form carried no year at all, on a
    progressive that was global and never reset, so two invoices from different years
    were indistinguishable by number.
    """
    return f"{anno}/{numero}"


def proforma_riferimento(anno: int, sequenza: int) -> str:
    """`PROV-{anno}-{sequenza:04d}`.

    The non-numeric prefix is load-bearing: no fiscal-number pattern can accept it, so
    a proforma cannot be read as an invoice by any regex, in this codebase or in a
    spreadsheet somebody builds later.
    """
    return f"PROV-{anno}-{sequenza:04d}"


__all__ = [
    "FISCAL_ID_RE",
    "NUMERO_COMPLETO_RE",
    "RIFERIMENTO_PROFORMA_RE",
    "invoice_storage_prefix",
    "numero_completo",
    "proforma_riferimento",
    "proforma_storage_prefix",
    "progressivo_invio",
    "sdi_filename",
]
