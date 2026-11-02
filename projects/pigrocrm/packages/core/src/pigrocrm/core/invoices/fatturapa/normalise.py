"""Value normalisation shared by the pre-export checks and the writer."""

import re

from pigrocrm.core.invoices.fatturapa.constants import CAP_ESTERO, LATIN_SPELLINGS
from pigrocrm.core.invoices.naming import FISCAL_ID_RE
from pigrocrm.core.invoices.schemas import PartySnapshot


def latinise(value: str) -> str:
    """`value` with its typographic punctuation spelled in Latin-1.

    What the writer emits and what the pre-check measures, in one function so the two
    cannot disagree: an em dash becomes a hyphen, curly quotes become straight ones, an
    ellipsis becomes three full stops. Anything not in `LATIN_SPELLINGS` comes back
    unchanged, and if it is outside the Latin range `check_text` refuses it by name.
    """
    return value.translate(LATIN_SPELLINGS)


# Punctuation and the "IT" prefix are stripped before a fiscal identifier is matched,
# exactly as the previous system's own normalisation did.
_FISCAL_ID_NOISE = re.compile(r"[^0-9A-Za-z]")


def normalise_fiscal_id(value: str | None) -> str | None:
    """An 11-digit VAT number or a 16-character fiscal code, or `None`.

    Strips the `IT` prefix and any punctuation, upper-cases, and then accepts only the
    two shapes FPR12 recognises. **Anything else returns `None` so the caller omits
    the element** rather than emitting it malformed -- the highest-value line carried
    over from the previous system's generator, because a malformed `IdCodice` is an outright
    rejection while an absent element usually passes.
    """
    if not value:
        return None
    cleaned = _FISCAL_ID_NOISE.sub("", value).upper()
    if cleaned.startswith("IT") and len(cleaned) == 13:
        cleaned = cleaned[2:]
    return cleaned if FISCAL_ID_RE.fullmatch(cleaned) else None


def normalise_foreign_fiscal_id(value: str | None) -> str | None:
    """The same cleaning as `normalise_fiscal_id`, without Italy's shape rule.

    That function accepts exactly two forms -- an eleven-digit VAT number or a
    sixteen-character fiscal code -- and answers `None` to everything else so the caller
    omits a malformed element rather than emitting one. Correct for an Italian party, and
    the third place a foreign one was silently dropped: a British VAT of nine digits is
    not malformed, it is simply not Italian, and returning `None` for it produced an
    invoice with no `IdFiscaleIVA` at all rather than an error anybody could see.

    Nothing is asserted about the shape here on purpose. Twenty-eight countries' registers
    have twenty-eight shapes, and a guess at one of them would refuse a valid number --
    the failure this whole change exists to stop. Length is the one bound FPR12 itself
    gives, and it is enforced where every other width is, in `_text`.
    """
    if not value:
        return None
    cleaned = _FISCAL_ID_NOISE.sub("", value).upper()
    return cleaned or None


def is_italian(party: PartySnapshot) -> bool:
    return (party.nazione or "").strip().upper() == "IT"


def indirizzo_xml(party: PartySnapshot) -> str:
    """The text `_sede` writes into `Indirizzo`, and the text `check_widths` measures.

    One function for both on purpose: the pre-check exists to refuse before a register
    number is spent whatever the writer would refuse after, and a foreign address is
    the one case where the two texts differ. `CAP` carries the `00000` placeholder for
    a party outside Italy, so the real postcode has no element of its own and is
    appended here instead. A record that already holds the placeholder (every foreign
    customer entered before ORB-38 does) gets the address alone: `00000` is the SdI's
    convention, not a postcode.
    """
    indirizzo = (party.indirizzo or "").strip()
    if is_italian(party):
        return indirizzo
    cap = (party.cap or "").strip()
    if not cap or cap == CAP_ESTERO:
        return indirizzo
    return f"{indirizzo}, {cap}"
