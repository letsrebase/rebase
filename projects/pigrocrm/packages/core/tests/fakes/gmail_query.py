"""Just enough of Gmail's `q` to make the relevance test meaningful.

Supports `from:`, `to:`, `after:`, `rfc822msgid:`, `-in:draft`, parenthesised groups and
`OR`.
Everything else raises, on purpose: a fake that silently ignores an operator the
production code relies on turns a passing test into a false statement.
"""

import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fakes.fake_gmail import FakeMessage

_TERM = re.compile(r"(from|to|after|rfc822msgid):(\S+)")


def matches(message: "FakeMessage", query: str) -> bool:
    if not query.strip():
        raise AssertionError(
            "FakeGmail received a messages.list with an empty q. Spec 4.1: a listing "
            "without an address filter is a bug, not a broad search."
        )

    groups = re.findall(r"\(([^)]*)\)", query)
    outside = re.sub(r"\([^)]*\)", " ", query)

    # The customer proposals (REB-223) leave drafts out: a draft carries the DRAFT label.
    if "-in:draft" in outside.split():
        if "DRAFT" in message.label_ids:
            return False
        outside = " ".join(token for token in outside.split() if token != "-in:draft")

    unknown = [token for token in outside.split() if ":" in token and not _TERM.fullmatch(token)]
    if unknown:
        raise AssertionError(f"FakeGmail does not implement the Gmail operator(s) {unknown}")

    # Terms outside any group are ANDed; a parenthesised group is ORed internally.
    for group in groups:
        if not any(_term_matches(message, term) for term in _TERM.findall(group)):
            return False
    return all(_term_matches(message, term) for term in _TERM.findall(outside))


def _after_epoch_seconds(value: str) -> int:
    """Gmail accepts both `after:1700000000` and `after:2026/08/01`, and the query
    builder is free to pick either. Accepting only the epoch form here would make the
    fake reject a legal query -- the same class of mistake as ignoring an operator,
    just failing in the other direction."""
    if value.isdigit():
        if int(value) == 0:
            raise AssertionError(
                "FakeGmail refuses `after:0`: real Gmail answers an empty page to it, so a "
                "fake that reads it as 'since the epoch' would let a broken full backfill "
                "pass. Leave the clause out to mean 'no horizon'."
            )
        return int(value)
    try:
        parsed = datetime.strptime(value, "%Y/%m/%d").replace(tzinfo=UTC)
    except ValueError:
        raise AssertionError(
            f"FakeGmail cannot read the Gmail date {value!r}: `after:` takes epoch "
            "seconds or YYYY/MM/DD"
        ) from None
    return int(parsed.timestamp())


def _term_matches(message: "FakeMessage", term: tuple[str, str]) -> bool:
    operator, value = term
    if operator == "from":
        return value.lower() in message.headers.get("From", "").lower()
    if operator == "to":
        haystack = " ".join(message.headers.get(name, "") for name in ("To", "Cc", "Bcc")).lower()
        return value.lower() in haystack
    if operator == "after":
        return message.internal_date_ms >= _after_epoch_seconds(value) * 1000
    if operator == "rfc822msgid":
        return message.headers.get("Message-ID", "").strip("<>") == value.strip("<>")
    raise AssertionError(f"unreachable operator {operator}")
