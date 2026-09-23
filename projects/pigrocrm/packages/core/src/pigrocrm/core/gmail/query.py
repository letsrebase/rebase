"""The relevance mechanism, as pure functions.

Spec 4 names the failure mode to avoid: synchronising a mailbox. A mailbox holds the
newsletters, the Amazon receipts, the messages from the children's school, and the
conversations with clients. Ingesting all of it and filtering afterwards means all of
it went through the process -- a broken promise even if 98% is then discarded.

So the filter is applied *server-side*, inside the `q`, and this module is the only
place a Gmail URL can be built at all. Two guards, because either alone is one edit
from being defeated:

  * `messages_list_url` refuses a `q` with no address-bearing clause, which makes the
    bug spec 4.1 names unconstructible rather than merely forbidden;
  * `tests/test_gmail_query.py` walks the AST of every source file in the repository
    and fails if any of them holds a Gmail host or a listing path in a string literal,
    so a caller that wants to bypass this module has nowhere left to write the URL.

`tests/test_gmail_sync.py` adds the third once there is a sync to run: the same
property asserted over the requests the fake transport actually received.

Everything here is a pure function over validated input. Nothing in this module reads
the database, and nothing in it accepts free text: there is deliberately no builder
that takes a caller-supplied Gmail search string, because spec 8.2 and 12 say no
surface in this slice offers one, and the cheapest way to keep that true is to have
nothing to call.
"""

import re
from collections.abc import Sequence
from urllib.parse import urlencode, urlparse

from pigrocrm.core.errors import ValidationFailed

GMAIL_API_ROOT = "https://gmail.googleapis.com/gmail/v1/users/me"
# Twenty: Gmail's `q` has a practical length limit, and twenty addresses at two clauses
# each fit with margin. Configurable via PIGROCRM_GMAIL_SYNC_ADDRESS_BATCH_SIZE so the
# number can be corrected without touching code.
ADDRESS_BATCH_DEFAULT = 20
MAX_RESULTS_PER_PAGE = 100

# Deliberately stricter than RFC 5322: this string is interpolated into a Gmail query
# expression, so anything that could terminate a clause or introduce an operator has to
# be impossible, not merely unusual. `re.fullmatch`, never `re.match` with `$` -- `$`
# also matches before a trailing newline, which is exactly the character an injection
# would use.
_SAFE_ADDRESS = re.compile(r"[a-z0-9._%+\-]+@[a-z0-9.\-]+\.[a-z]{2,63}")
# The local part of a Message-ID is case-sensitive (RFC 5322 3.6.4), so this one is not
# folded to lower case the way an address is. Same shape otherwise, and the same
# reason: it is interpolated into a query expression.
_SAFE_MESSAGE_ID = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,63}")
# What makes a query legitimate: at least one clause naming an actual address. The `@`
# is load-bearing and not decoration -- `from:acme` is not a filter on a known
# correspondent, it is a Gmail free-text search over sender display names, which is the
# broad search this whole module exists to prevent.
_HAS_ADDRESS_CLAUSE = re.compile(r"\b(?:from|to|cc|bcc|rfc822msgid):\S*@\S")
# Gmail's own ids, which are hex-ish but documented only as opaque. Constrained here
# because they are interpolated into a *path*: a `/` or a `..` in one would address a
# different endpoint entirely.
_SAFE_ID = re.compile(r"[A-Za-z0-9_\-]{1,128}")
# An attachment id is the same alphabet and a wholly different length: Gmail's are
# hundreds of characters, sometimes over a thousand, where a message id is sixteen. One
# pattern for both would have to be as loose as this one, which would stop `_SAFE_ID`
# from saying anything useful about a message id -- so there are two, and each is as
# tight as its subject allows. An id past this ceiling is refused, never truncated: half
# an attachment id addresses nothing.
_SAFE_ATTACHMENT_ID = re.compile(r"[A-Za-z0-9_\-]{1,4096}")


def _checked(address: str) -> str:
    normalised = address.strip().lower()
    if not _SAFE_ADDRESS.fullmatch(normalised):
        raise ValidationFailed(
            "gmail_query",
            "address",
            "non è un indirizzo email interpolabile in una query Gmail",
            expected="local@dominio.tld, senza spazi, parentesi o virgolette",
        )
    return normalised


def _checked_id(value: str) -> str:
    if not _SAFE_ID.fullmatch(value):
        raise ValidationFailed(
            "gmail_query", "id", "un id Gmail contiene solo lettere, cifre, - e _"
        )
    return value


def _checked_attachment_id(value: str) -> str:
    if not _SAFE_ATTACHMENT_ID.fullmatch(value):
        raise ValidationFailed(
            "gmail_query",
            "attachment_id",
            "un id di allegato Gmail contiene solo lettere, cifre, - e _",
        )
    return value


def build_address_clause(addresses: Sequence[str]) -> str:
    """`(from:a OR to:a OR from:b OR to:b …)` -- both directions per address, because a
    conversation is relevant whoever started it."""
    if not addresses:
        raise ValidationFailed(
            "gmail_query", "addresses", "una clausola di indirizzi non può essere vuota"
        )
    terms: list[str] = []
    for address in addresses:
        safe = _checked(address)
        terms.append(f"from:{safe}")
        terms.append(f"to:{safe}")
    return "(" + " OR ".join(terms) + ")"


# A registrable name with at least one dot, in the same restricted alphabet as
# `_SAFE_ADDRESS`'s domain part and for the same reason: it is interpolated into a `q`.
_SAFE_DOMAIN = re.compile(r"[a-z0-9][a-z0-9.\-]*\.[a-z]{2,63}")
# Domains that are half the planet. `from:@gmail.com` names nobody in particular, so a
# customer whose only address is a webmail one has no domain of its own to discover by.
WEBMAIL_DOMAINS: frozenset[str] = frozenset(
    {
        "gmail.com",
        "googlemail.com",
        "outlook.com",
        "outlook.it",
        "hotmail.com",
        "hotmail.it",
        "live.com",
        "live.it",
        "yahoo.com",
        "yahoo.it",
        "icloud.com",
        "me.com",
        "libero.it",
        "virgilio.it",
        "tiscali.it",
        "alice.it",
        "tin.it",
        "fastwebnet.it",
        "protonmail.com",
        "proton.me",
        "pec.it",
    }
)


# Where the mail of certified email (PEC) providers comes from. A PEC address names its
# provider, not the company that holds it, so it proposes nobody (REB-223).
PEC_PROVIDER_DOMAINS: frozenset[str] = frozenset(
    {
        "pec.it",
        "legalmail.it",
        "arubapec.it",
        "pec.aruba.it",
        "postecert.it",
        "cert.legalmail.it",
        "pec.libero.it",
        "sicurezzapostale.it",
        "casellapec.com",
    }
)


def is_provider_domain(domain: str) -> bool:
    """A domain that names a mail provider rather than an organisation: the webmails
    above, the PEC providers, and any `pec.` subdomain, which is how a company's own
    certified mailbox is usually hosted. An address there says nothing about which
    customer the person works for."""
    normalised = domain.strip().lower()
    return (
        normalised in WEBMAIL_DOMAINS
        or normalised in PEC_PROVIDER_DOMAINS
        or normalised.startswith("pec.")
    )


def _checked_domain(domain: str) -> str:
    normalised = domain.strip().lower()
    if not _SAFE_DOMAIN.fullmatch(normalised):
        raise ValidationFailed(
            "gmail_query",
            "domain",
            "non è un dominio interpolabile in una query Gmail",
            expected="dominio.tld, senza chiocciola, spazi, parentesi o virgolette",
        )
    return normalised


def build_domain_clause(domain: str) -> str:
    """`(from:@dominio OR to:@dominio)` -- every address at one customer's domain, in
    both directions.

    The `@` is the whole difference between this and the broad search spec 4 forbids:
    `from:example.com` is a free-text match over display names and addresses alike,
    while `from:@example.com` matches the address and nothing else. It is also what
    lets `messages_list_url` accept the clause, whose guard looks for exactly that
    character behind the operator.
    """
    safe = _checked_domain(domain)
    return f"(from:@{safe} OR to:@{safe})"


def discovery_query(domain: str) -> str:
    """The one query discovery issues. No `after:`, deliberately: the question is "who
    at this customer have I ever corresponded with", and a horizon would answer a
    different one."""
    return build_domain_clause(domain)


def customer_domain(*, sito_web: str | None, email: str | None) -> str | None:
    """The domain a customer's correspondents share, read from the record and never
    typed by the caller: the website first, the customer's own email second.

    `None` when there is nothing to derive from, or when what there is names a webmail
    provider -- an answer of "nobody in particular" is worse than no answer, because it
    looks like one.
    """
    for candidate in (_website_host(sito_web), _email_domain(email)):
        if candidate and candidate not in WEBMAIL_DOMAINS and _SAFE_DOMAIN.fullmatch(candidate):
            return candidate
    return None


def _website_host(sito_web: str | None) -> str | None:
    value = (sito_web or "").strip().lower()
    if not value:
        return None
    if "://" not in value:
        value = f"http://{value}"
    host = urlparse(value).hostname or ""
    return host.removeprefix("www.") or None


def _email_domain(email: str | None) -> str | None:
    value = (email or "").strip().lower()
    _, at, domain = value.rpartition("@")
    return domain if at and domain else None


def build_list_queries(
    addresses: Sequence[str], *, after_epoch: int, batch_size: int = ADDRESS_BATCH_DEFAULT
) -> tuple[str, ...]:
    """One query per batch of addresses. An empty roster yields **no** queries -- not
    one unfiltered query, which is the whole point.

    Duplicates are collapsed, in order: the roster is assembled from customers, people
    and deals, so the same address arrives more than once routinely, and three
    identical clauses would spend a third of the `q`'s length limit on nothing.

    `after:` takes epoch seconds, not a date: a date loses the hours and forces
    re-reading an entire day on every cycle.

    `after_epoch=0` means "no horizon", and it is spelled by leaving the clause out.
    Gmail answers *nothing* to a literal `after:0` -- found in production, where a full
    backfill came back empty against a mailbox whose threads discovery had just listed,
    while the fake happily read it as "since the epoch" and every test passed.
    """
    if batch_size < 1:
        raise ValidationFailed("gmail_query", "batch_size", "deve essere almeno 1", expected=">= 1")
    if after_epoch < 0:
        raise ValidationFailed("gmail_query", "after_epoch", "non può essere negativo")
    horizon = f" after:{after_epoch}" if after_epoch else ""
    unique = list(dict.fromkeys(_checked(address) for address in addresses))
    return tuple(
        f"{build_address_clause(unique[start : start + batch_size])}{horizon}"
        for start in range(0, len(unique), batch_size)
    )


def messages_list_url(
    query: str, *, page_token: str | None = None, max_results: int = MAX_RESULTS_PER_PAGE
) -> str:
    """The only way to build a `users.messages.list` URL in this codebase.

    It refuses a `q` with no address-bearing clause. That refusal is the mechanism of
    spec 4.1: a listing without an address filter cannot be constructed, so it cannot
    be shipped by accident.
    """
    if not _HAS_ADDRESS_CLAUSE.search(query):
        raise ValidationFailed(
            "gmail_query",
            "q",
            "un elenco di messaggi senza un filtro su un indirizzo noto è un bug, "
            "non una ricerca ampia",
            expected="una clausola from:, to: o rfc822msgid: che nomini un indirizzo",
        )
    params: dict[str, str] = {"q": query, "maxResults": str(max_results)}
    if page_token:
        params["pageToken"] = page_token
    return f"{GMAIL_API_ROOT}/messages?{urlencode(params)}"


def thread_get_url(thread_id: str) -> str:
    return f"{GMAIL_API_ROOT}/threads/{_checked_id(thread_id)}?format=full"


def message_get_url(message_id: str) -> str:
    return f"{GMAIL_API_ROOT}/messages/{_checked_id(message_id)}?format=full"


def attachment_get_url(message_id: str, attachment_id: str) -> str:
    """The bytes of one attachment, `users.messages.attachments.get`.

    Both ids are checked, and the attachment id is the reason that matters more here
    than anywhere else in this module: it does not come from a person, it comes out of
    Gmail's own message payload, and it is interpolated into a *path*. A payload
    carrying a `/` or a `..` in that field would address a different endpoint of
    somebody's mailbox. Gmail's attachment ids are far longer than a message id, so
    they are measured against `_SAFE_ATTACHMENT_ID`, which exists for that reason.

    The answer is JSON with the content base64url-encoded in `data`, not the raw file:
    that is Gmail's shape, and `attachment_text.py` is what decodes it.
    """
    return (
        f"{GMAIL_API_ROOT}/messages/{_checked_id(message_id)}"
        f"/attachments/{_checked_attachment_id(attachment_id)}"
    )


# The one endpoint in this slice that is not built from a caller's input: there is
# nothing to interpolate into it. It lives here all the same, because
# `test_gmail_query.py` walks the AST of every source file and fails on a Gmail host in
# any string literal outside this module -- and that guard is worth more than the
# convenience of writing the constant next to the code that posts to it. Assembled from
# `GMAIL_API_ROOT` so the host is stated exactly once.
GMAIL_SEND_URL = f"{GMAIL_API_ROOT}/messages/send"


def sent_since_query(mailbox: str, *, after_epoch: int) -> str:
    """The mail the connected mailbox itself sent since `after_epoch`: what the customer
    proposals read (spec 2026-09-16 §5, REB-223).

    The one listing besides discovery that is not built from the roster, and it keeps
    the rule this module exists for: its only address clause names an address, the
    mailbox's own, so `messages_list_url` accepts it like any other. Sent mail and not
    the whole mailbox on purpose: somebody the owner has written to is a relationship,
    while what merely arrives (newsletters, receipts, cold outreach) is the noise spec 4
    keeps out. The threads found this way are read as headers only
    (`thread_metadata_url`) and stored nowhere.
    """
    if after_epoch < 1:
        raise ValidationFailed(
            "gmail_query", "after_epoch", "le proposte leggono un periodo, non tutta la casella"
        )
    # `-in:draft`: a draft is from the mailbox too, and nobody has been written to yet.
    return f"from:{_checked(mailbox)} -in:draft after:{after_epoch}"


# The headers a proposal needs: who wrote and who was written to.
METADATA_HEADERS: tuple[str, ...] = ("From", "To", "Cc")
# What of each message comes back at all. `format=metadata` alone still carries the
# thread's and each message's `snippet`, the first lines of the body, and the labels:
# the partial response keeps the id, the date and the headers asked for, and nothing of
# what anybody wrote leaves Google for a proposal.
METADATA_FIELDS = "messages(id,internalDate,payload/headers)"


def thread_metadata_url(thread_id: str) -> str:
    """A thread as its headers only: the participants and the dates, for the customer
    proposals. `thread_get_url` is the whole thread, for the sync that stores it."""
    params = [
        ("format", "metadata"),
        *(("metadataHeaders", name) for name in METADATA_HEADERS),
        ("fields", METADATA_FIELDS),
    ]
    return f"{GMAIL_API_ROOT}/threads/{_checked_id(thread_id)}?{urlencode(params)}"


def rfc822msgid_query(message_id_header: str) -> str:
    """Finds one exact message by the `Message-ID` we generated ourselves. One of the
    few queries not built from the address roster (with discovery's domain clause and
    the proposals' own mailbox, `sent_since_query`), and it is allowed
    because it is *more* specific, not less: it names a single message, and one we
    created. Used only by the send reconciliation of spec 6.3."""
    stripped = message_id_header.strip().strip("<>")
    if not _SAFE_MESSAGE_ID.fullmatch(stripped):
        raise ValidationFailed(
            "gmail_query", "message_id_header", "non è un Message-ID interpolabile"
        )
    return f"rfc822msgid:{stripped}"
