"""LinkedIn addresses, in the shape the hub stores them (`rebase_core.linkedin`).

A personal profile, however it arrives (no scheme, `http`, `it.` or `www.`, a trailing slash,
a share link's query), becomes `https://www.linkedin.com/in/<name>`, the name percent-decoded
and in lower case, so `Ada` and `ada` are one profile. Any other page on LinkedIn is rebuilt
from its host and path over `https`; what is not on LinkedIn is refused. The CRM and the hub
are separate projects with no shared package, so this is a port, and the two must agree: the
leads door hands the hub's stored value over unchanged.
"""

import re
from urllib.parse import unquote, urlsplit, urlunsplit

LINKEDIN_URL_MAX_LENGTH = 300
LINKEDIN_HOST = "linkedin.com"
LINKEDIN_PROFILE = "https://www.linkedin.com/in/"

_BIDI_CONTROLS = frozenset("\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069")
_HAS_SCHEME = re.compile(r"^[a-z][a-z0-9+-]*:", re.IGNORECASE)
_PROFILE_PATH = re.compile(r"^/in/([^/]*)(?:/.*)?$", re.IGNORECASE)
_PROFILE_NAME = re.compile(r"[\w-]+")
_REFUSED = f"serve l'indirizzo di un profilo su {LINKEDIN_HOST}"


def _reject_control_characters(value: str) -> None:
    for character in value:
        if (
            character < " "
            or character == "\x7f"
            or "\x80" <= character <= "\x9f"
            or character in _BIDI_CONTROLS
        ):
            raise ValueError("il testo contiene un carattere di controllo, non ammesso")


def normalise_linkedin(value: str) -> str:
    """The stored shape of a LinkedIn address; `ValueError` (an Italian sentence) when the
    value is not one. Checked on the raw value first, since `urllib.parse` strips tab, CR and
    LF before parsing, and a `\\` is read as `/` by a browser and by no parser here."""
    _reject_control_characters(value)
    trimmed = value.strip()
    if "\\" in trimmed:
        raise ValueError(_REFUSED)
    candidate = trimmed if _HAS_SCHEME.match(trimmed) else f"https://{trimmed}"
    parts = urlsplit(candidate)
    host = (parts.hostname or "").lower()
    if (
        parts.scheme.lower() not in {"http", "https"}
        or not (host == LINKEDIN_HOST or host.endswith(f".{LINKEDIN_HOST}"))
        or parts.username is not None
        or parts.netloc.lower() != host
    ):
        raise ValueError(_REFUSED)
    profile = _PROFILE_PATH.match(parts.path)
    if profile:
        name = unquote(profile.group(1)).lower()
        if not _PROFILE_NAME.fullmatch(name):
            raise ValueError(_REFUSED)
        stored = LINKEDIN_PROFILE + name
    else:
        stored = urlunsplit(("https", host, parts.path, parts.query, parts.fragment))
    if len(stored) > LINKEDIN_URL_MAX_LENGTH:
        raise ValueError(f"al massimo {LINKEDIN_URL_MAX_LENGTH} caratteri")
    return stored
