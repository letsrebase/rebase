"""The purchase order an invoice answers (REB-693), and the rules its four values obey.

Pure: no session, no row. `InvoiceService` passes the values a write will store, the
merged row's on an update, and stores what comes back, so a CIG typed in lower case is
kept upper case and a malformed one is refused with its field name before the flush.
The table CHECK `ck_invoices_ordine_needs_numero` refuses the orphan case again on every
other write path.

Stricter than the FPR12 types on purpose. `String15Type` would take any fifteen Basic
Latin characters for a CIG; ANAC's CIG is ten letters or digits and a CUP fifteen, and a
character missing from either is caught here, when it is typed, rather than by the
customer's accounts office after the invoice has gone.
"""

import re
from dataclasses import dataclass
from datetime import date

from pigrocrm.core.errors import ValidationFailed
from pigrocrm.core.invoices.schemas import ORDINE_NUMERO_MAX_LENGTH

ENTITY = "invoice"

ORDINE_FIELDS: tuple[str, ...] = ("ordine_numero", "ordine_data", "ordine_cig", "ordine_cup")

# Printable Basic Latin. `String20Type` is `\p{IsBasicLatin}{1,20}`, which also admits
# control characters; a tab inside an identifier is never what somebody meant.
_NUMERO_RE = re.compile(r"[\x20-\x7e]+")
# Checked as typed, before upper-casing: `str.upper()` expands some characters into ASCII
# (`ß` into `SS`, `ﬁ` into `FI`), so a value the caller never typed would pass.
_CIG_RE = re.compile(r"[A-Za-z0-9]{10}")
_CUP_RE = re.compile(r"[A-Za-z0-9]{15}")


@dataclass(frozen=True)
class Ordine:
    numero: str | None
    data: date | None
    cig: str | None
    cup: str | None

    def columns(self) -> dict[str, str | date | None]:
        """The four values under the invoice's own column names, in `ORDINE_FIELDS`
        order."""
        return dict(zip(ORDINE_FIELDS, (self.numero, self.data, self.cig, self.cup), strict=True))


def _numero(value: str | None) -> str | None:
    if value is None:
        return None
    pulito = value.strip()
    if not pulito:
        raise ValidationFailed(
            ENTITY,
            "ordine_numero",
            "il numero dell'ordine e' vuoto: per togliere l'ordine svuota tutti i suoi campi "
            "dall'applicazione",
            expected=f"da 1 a {ORDINE_NUMERO_MAX_LENGTH} caratteri",
        )
    if len(pulito) > ORDINE_NUMERO_MAX_LENGTH:
        raise ValidationFailed(
            ENTITY,
            "ordine_numero",
            f"il numero dell'ordine ha al massimo {ORDINE_NUMERO_MAX_LENGTH} caratteri",
            expected=f"da 1 a {ORDINE_NUMERO_MAX_LENGTH} caratteri",
        )
    offending = next((c for c in pulito if not _NUMERO_RE.fullmatch(c)), None)
    if offending is not None:
        # Name the character and its code point: an en dash or a no-break space pasted
        # from a PDF looks exactly like the ASCII one, and U+XXXX is what finds it.
        raise ValidationFailed(
            ENTITY,
            "ordine_numero",
            f"il numero dell'ordine contiene il carattere «{offending}» "
            f"(U+{ord(offending):04X}), che la fattura elettronica non ammette: "
            "scrivilo con lettere senza accento, cifre, spazi e punteggiatura semplice",
            expected="lettere senza accento, cifre, spazi e punteggiatura semplice",
        )
    return pulito


def _codice(
    value: str | None, field: str, pattern: re.Pattern[str], reason: str, expected: str
) -> str | None:
    if value is None:
        return None
    pulito = value.strip()
    if not pattern.fullmatch(pulito):
        raise ValidationFailed(ENTITY, field, reason, expected=expected)
    return pulito.upper()


def checked_ordine(
    numero: str | None, data: date | None, cig: str | None, cup: str | None
) -> Ordine:
    """The four values as they will be stored, or a `ValidationFailed` naming the field.

    `None` is "no value". An empty number is refused rather than read as `None`: the web
    sends `null` for an empty input, so an empty string is a caller's mistake, and
    taking it as a clear would let a stray `""` drop the order a CIG still needs.
    """
    numero_pulito = _numero(numero)
    cig_pulito = _codice(
        cig, "ordine_cig", _CIG_RE, "il CIG ha 10 caratteri, lettere e cifre", "10 lettere o cifre"
    )
    cup_pulito = _codice(
        cup, "ordine_cup", _CUP_RE, "il CUP ha 15 caratteri, lettere e cifre", "15 lettere o cifre"
    )
    if numero_pulito is None and (
        data is not None or cig_pulito is not None or cup_pulito is not None
    ):
        raise ValidationFailed(
            ENTITY,
            "ordine_numero",
            "indica il numero dell'ordine a cui si riferiscono data, CIG e CUP",
            expected="il numero dell'ordine del cliente",
        )
    return Ordine(numero_pulito, data, cig_pulito, cup_pulito)
