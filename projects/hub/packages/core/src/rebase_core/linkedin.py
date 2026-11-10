"""LinkedIn addresses: the one place one is checked, given its stored shape and compared.

Moved out of `schemas` (REB-800) so `models` can normalise on write without importing
the schemas that import it. A profile is stored as `https://www.linkedin.com/in/<name>`
with the name decoded and in lower case, because LinkedIn reads `Ada` and `ada` as one
profile, and `linkedin_key` is what two addresses are compared by: the same function for
the unique index, the duplicate finder and the migration's normalising pass.
"""

import re
from urllib.parse import unquote, urlsplit, urlunsplit

LINKEDIN_URL_MAX_LENGTH = 300
LINKEDIN_HOST = "linkedin.com"

# Characters that must never reach a stored value. C0 (tab, CR, LF included), DEL and
# C1 are refused because `urllib.parse` strips tab/CR/LF from a URL *before* parsing
# it, so a value with a newline in it validated as a URL and was then stored whole --
# `.../in/ada\nBcc: qualcuno@altrove.it` was accepted. The same string is later read by
# an assistant drafting mail to these people, which is where a newline stops being
# cosmetic. The bidi controls are refused for the mirror-image reason: they make a
# stored name render as a different name, so what an admin reads is not what is there.
BIDI_CONTROLS = frozenset("\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069")


def reject_control_characters(value: str) -> str:
    """Refuses rather than strips, and looks at the value *before* it is trimmed or
    parsed, so nothing invisible is deleted on the way to the column -- the reasoning
    `validation.py` records for `SafeStr`, which closes only `\x00`. Note that Python's
    own `str.strip()` treats `\n`, `\t` and even `\x85` as whitespace, so checking
    after trimming would let a trailing control character disappear in silence."""
    for character in value:
        if character < " " or character == "\x7f" or "\x80" <= character <= "\x9f":
            raise ValueError("il testo contiene un carattere di controllo, non ammesso")
        if character in BIDI_CONTROLS:
            raise ValueError("il testo contiene un carattere di direzione, non ammesso")
    return value


LINKEDIN_PROFILE = "https://www.linkedin.com/in/"
# What an address may be before it is normalised: a share link carries its tracking, so
# the raw value is bounded well above the column and the stored one is checked again.
LINKEDIN_INPUT_MAX_LENGTH = 2000
# A scheme written out (`https://`, `javascript:`), as opposed to an address pasted
# without one (`linkedin.com/in/ada`). No dot in it, so `linkedin.com:443/...` is not
# read as the scheme `linkedin.com`.
_HAS_SCHEME = re.compile(r"^[a-z][a-z0-9+-]*:", re.IGNORECASE)
# The name in `/in/<name>`, and whatever LinkedIn appends after it (`/details/`, `/it`).
_PROFILE_PATH = re.compile(r"^/in/([^/]*)(?:/.*)?$", re.IGNORECASE)
# What a profile's name is made of once decoded: letters and digits of any script, `-`
# and `_`. Anything else in that segment is not a profile, and is refused rather than
# stored, because the name is interpolated into a link an admin clicks.
_PROFILE_NAME = re.compile(r"[\w-]+")


def normalise_linkedin(value: str | None) -> str | None:
    """The one place a LinkedIn address is checked and given its stored shape.

    Until ORB-203 the value was stored exactly as typed and anything short of
    `https://...linkedin.com/...` was refused, so `linkedin.com/in/ada` pasted from a
    phone was a 422 and `?utm_source=share` reached the admin list. Now a personal
    profile, however it arrives (no scheme, `http`, `it.` or `www.`, a trailing slash,
    query or fragment), is stored as `https://www.linkedin.com/in/<name>`, the name
    percent-decoded and in lower case (REB-800), so `jürgen` and `j%C3%BCrgen`, `Ada` and
    `ada`, are one person. Any other page on
    LinkedIn (a company, an old `/pub/` address) is rebuilt from its host and path over
    `https`. What is not on LinkedIn is still refused, and what is checked is what is
    stored: `urllib.parse` strips tab, CR and LF before parsing, so control characters
    are refused on the raw value first."""
    if value is None or not value.strip():
        return None
    trimmed = reject_control_characters(value).strip()
    refused = ValueError(f"serve l'indirizzo di un profilo su {LINKEDIN_HOST}, oppure niente")
    # A browser reads `\` as `/` in an https address and `urlsplit` does not, so
    # `https://evil.com\.linkedin.com/x` is LinkedIn here and evil.com in the admin's
    # browser. No LinkedIn address has one.
    if "\\" in trimmed:
        raise refused
    candidate = trimmed if _HAS_SCHEME.match(trimmed) else f"https://{trimmed}"
    parts = urlsplit(candidate)
    host = (parts.hostname or "").lower()
    # The host is compared, never searched: `https://evil.com/linkedin.com/x` and
    # `https://linkedin.com.evil.com/` are not profiles, and `urlsplit` decides which
    # part of the string is the host. `javascript:...//linkedin.com/` has a scheme of
    # its own and fails here too. Credentials or a port in front of the host are refused
    # rather than carried into a stored link.
    if (
        parts.scheme.lower() not in {"http", "https"}
        or not (host == LINKEDIN_HOST or host.endswith(f".{LINKEDIN_HOST}"))
        or parts.username is not None
        or parts.netloc.lower() != host
    ):
        raise refused
    profile = _PROFILE_PATH.match(parts.path)
    if profile:
        name = unquote(profile.group(1)).lower()
        if not _PROFILE_NAME.fullmatch(name):
            raise refused
        stored = LINKEDIN_PROFILE + name
    else:
        stored = urlunsplit(("https", host, parts.path, parts.query, parts.fragment))
    if len(stored) > LINKEDIN_URL_MAX_LENGTH:
        raise ValueError(f"al massimo {LINKEDIN_URL_MAX_LENGTH} caratteri")
    return stored


def canonical_linkedin(value: str | None) -> str | None:
    """`normalise_linkedin` for a value that is already stored or comes from code that
    validated it elsewhere: the canonical form when the value is a LinkedIn address, the
    trimmed text untouched when it is not (a model attribute must never raise on a row
    that predates the rule), `None` for nothing."""
    if value is None or not value.strip():
        return None
    try:
        return normalise_linkedin(value)
    except ValueError:
        return value.strip()


def linkedin_key(value: str | None) -> str | None:
    """What two LinkedIn addresses are compared by: a personal profile in its stored
    shape, in lower case, or `None`. **Only a personal profile identifies a person**:
    `https://linkedin.com`, a company page or an old `/pub/` address can be stored, and
    two people may type the same one, so none of them is ever a reason to treat two
    people as one. Every comparison of two people's LinkedIn goes through this (the
    signup check, the duplicate finder, the lead lists) and the unique indexes carry the
    same condition (`PROFILE_SQL_PREDICATE`)."""
    canonical = canonical_linkedin(value)
    if canonical is None or not canonical.startswith(LINKEDIN_PROFILE):
        return None
    return canonical.lower()


# The condition the two unique indexes and the migration's duplicate check share with
# `linkedin_key`: a stored value is a personal profile.
PROFILE_SQL_PREDICATE = f"linkedin_url LIKE '{LINKEDIN_PROFILE}%'"
