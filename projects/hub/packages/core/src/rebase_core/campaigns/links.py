"""A button that leads out of the hub (REB-530): «Un link» and the address it carries.

The address is the admin's, typed into the editor, and it goes into every mail of the
campaign, so it is checked the way the rest of the hub checks a link a person wrote
(`rebase_core.schemas._https_url`): https with a host, nothing that reads differently
in a browser than in `urlsplit`, no credentials in front of the host. A link measures
the one thing the hub can see of a page it does not own, the click Resend reports
(`campaign_recipients.primo_clic_at`), so «Un link» and the action «clic» go together
(DECISIONS.md, 2026-09-28)."""

import re
from urllib.parse import urlsplit

from rebase_core.mail import SITE
from rebase_core.models import CAMPAIGN_BUTTON_URL_MAX_LENGTH

LINK = "link"
CLICK = "clic"

LINK_URL_MISSING = "Scrivi il link a cui porta il bottone."
LINK_URL_TOO_LONG = (
    f"Il link del bottone è troppo lungo: al massimo {CAMPAIGN_BUTTON_URL_MAX_LENGTH} caratteri."
)
LINK_URL_NOT_HTTPS = "Il link del bottone deve iniziare con https://."
LINK_URL_INVALID = "Il link del bottone non è un indirizzo valido."
LINK_URL_UNUSED = "Il link del bottone serve solo quando il bottone porta a «Un link»."
LINK_MEASURES_CLICK = (
    "Un bottone verso un link misura il clic: di una pagina fuori dal hub vediamo solo quello."
)
CLICK_NEEDS_LINK = "Il clic si misura come azione solo quando il bottone porta a «Un link»."

# letsrebase.com, from the one constant the mails already link the site with.
OUR_DOMAIN = (urlsplit(SITE).hostname or "").lower()

_HTTPS = "https://"
_SCHEME = re.compile(r"([a-z][a-z0-9+.-]*):", re.IGNORECASE)
_AUTHORITY_END = re.compile(r"[/?#]")
# The WHATWG URL standard's forbidden host code points a browser refuses and
# `urlsplit` lets through into `hostname` (the delimiters never get that far).
_FORBIDDEN_HOST = frozenset("<>^|[]:")
_NUMBER = re.compile(r"\d+|0[xX][0-9a-fA-F]*")
_OCTET = r"(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)"
_IPV4 = re.compile(rf"{_OCTET}(?:\.{_OCTET}){{3}}")


def link_problem(url: str) -> str | None:
    """Why `url`, already stripped, cannot be a button's address, or `None`. The editor
    runs the same steps in the same order (`linkProblem`, `crea-campagna/form.ts`), and
    what it cannot parse (`new URL` throws) is refused here too, so a request that skips
    the editor gets the same 422 and never a 500 or a link a browser cannot open."""
    if not url:
        return LINK_URL_MISSING
    if len(url) > CAMPAIGN_BUTTON_URL_MAX_LENGTH:
        return LINK_URL_TOO_LONG
    # A browser reads `\` as `/`, so `https://evil.io\@lu.ma/` is lu.ma to `urlsplit`
    # and evil.io to the person who clicks; a space or a control character is no link.
    if "\\" in url or any(ch.isspace() or not ch.isprintable() for ch in url):
        return LINK_URL_INVALID
    scheme = _SCHEME.match(url)
    if scheme is None or scheme.group(1).lower() != "https":
        return LINK_URL_NOT_HTTPS
    if url[: len(_HTTPS)].lower() != _HTTPS:
        return LINK_URL_INVALID  # `https:lu.ma`: no host for `urlsplit`
    authority = _AUTHORITY_END.split(url[len(_HTTPS) :], maxsplit=1)[0]
    # Credentials in front of the host, and a percent-encoded host, which a browser
    # decodes (or refuses) and `urlsplit` does not: neither belongs in a campaign link.
    if "@" in authority or "%" in authority:
        return LINK_URL_INVALID
    try:
        parts = urlsplit(url)  # raises on a broken `[...]` host
        _ = parts.port  # raises on a port out of range or not a number
    except ValueError:
        return LINK_URL_INVALID
    host = parts.hostname
    if not host:
        return LINK_URL_INVALID
    if not authority.startswith("["):  # `urlsplit` has already checked an IPv6 literal
        if any(ch in _FORBIDDEN_HOST for ch in host):
            return LINK_URL_INVALID
        # A last label that is a number makes the host an IPv4 address for a browser,
        # which refuses `999.1.1.1`: only four numbers up to 255 pass, on both sides.
        name = host.removesuffix(".")
        if _NUMBER.fullmatch(name.rsplit(".", 1)[-1]) and not _IPV4.fullmatch(name):
            return LINK_URL_INVALID
        if not all(_punycode_ok(label) for label in name.split(".")):
            return LINK_URL_INVALID
    return None


def _punycode_ok(label: str) -> bool:
    """A browser refuses an `xn--` label that does not decode to printable text
    (`xn--a`, `xn--`); `urlsplit` takes any of them."""
    if not label.startswith("xn--"):
        return True
    try:
        decoded = label[4:].encode("ascii").decode("punycode")
    except UnicodeError:
        return False
    return bool(decoded) and decoded.isprintable()


def button_problem(meta: str, url: str | None, azione: str) -> tuple[str, str] | None:
    """The field and the sentence a campaign's button breaks, or `None`: the address
    goes with «Un link» and only with it, and so does the click."""
    if meta == LINK:
        problem = link_problem(url or "")
        if problem is not None:
            return "bottone_url", problem
        if azione != CLICK:
            return "azione", LINK_MEASURES_CLICK
        return None
    if url is not None:
        return "bottone_url", LINK_URL_UNUSED
    if azione == CLICK:
        return "azione", CLICK_NEEDS_LINK
    return None


def is_ours(url: str) -> bool:
    """Whether `url` is on letsrebase.com or one of its subdomains: only there does the
    button carry our utm parameters. The host is compared, never searched, so
    `letsrebase.com.evil.io` and `evil.io/letsrebase.com` are not ours."""
    host = (urlsplit(url).hostname or "").lower()
    return bool(OUR_DOMAIN) and (host == OUR_DOMAIN or host.endswith(f".{OUR_DOMAIN}"))


def with_query(url: str, query: str) -> str:
    """`url` with `query` added after whatever query it already has and before its
    fragment. For an address with neither, the hub's own, it is `url?query` exactly,
    byte for byte what the button carried before links existed."""
    base, hash_mark, fragment = url.partition("#")
    joiner = "&" if "?" in base else "?"
    if base.endswith(("?", "&")):
        joiner = ""
    return f"{base}{joiner}{query}{hash_mark}{fragment}"
