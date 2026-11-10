"""Gmail's `format=full` JSON, turned into the shapes this slice stores.

Positions taken, all from spec 5.4:

* **Only `text/plain`.** If a message is HTML-only, the text conversion is stored and
  `body_html_discarded` is set. Email HTML carries tracking pixels, remote CSS and
  script; rendering it would make the CRM a beacon and an XSS surface. For the original
  there is "open in Gmail".
* **Bodies are truncated with a marker**, at `gmail_body_max_bytes` (256 KB), the same
  discipline as `activities/sanitize.py`: a generous limit that only bites on the
  anomalous.
* **No attachment bytes**, ever. Name, MIME type and size only.
* Nothing here raises on an odd message. A parser that throws stops an entire sync on
  one strange row, and mailboxes are full of strange rows.

Nothing in this module logs, and nothing that leaves it is put into an exception. The
values passing through are somebody's private correspondence: a body in a log line or
in a traceback is the same disclosure as a body in a public page, only harder to find
afterwards.
"""

import base64
import binascii
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

BODY_TRUNCATION_MARKER = "\n\n[…] messaggio troncato da PigroCRM"

_ADDRESS_IN_HEADER = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,63}")
_TAG = re.compile(r"<[^>]+>")
_SCRIPT_OR_STYLE = re.compile(r"<(script|style)\b[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
_WHITESPACE = re.compile(r"[ \t]*\n[ \t]*")
_HTML_ENTITIES = (
    ("&nbsp;", " "),
    ("&lt;", "<"),
    ("&gt;", ">"),
    ("&quot;", '"'),
    ("&#39;", "'"),
    # `&amp;` last, deliberately: unescaping it first would turn `&amp;lt;` into `&lt;`
    # and then into `<`, which is how a text-only pipeline re-invents a tag.
    ("&amp;", "&"),
)


@dataclass(frozen=True)
class ParsedAttachment:
    filename: str
    mime: str
    size: int


@dataclass(frozen=True)
class ParsedMessage:
    gmail_message_id: str
    gmail_thread_id: str
    message_id_header: str
    in_reply_to: str
    references: str
    from_address: str
    to_addresses: list[str]
    cc_addresses: list[str]
    subject: str
    snippet: str
    internal_date: datetime
    body_text: str
    body_truncated: bool
    body_html_discarded: bool
    attachments: list[ParsedAttachment] = field(default_factory=list)
    # Address -> the display name the header gave it, for every participant that had
    # one. Read by discovery, which has to suggest *who* an address is; the stored
    # mirror never keeps it (spec 5.4 keeps addresses, not names).
    display_names: dict[str, str] = field(default_factory=dict)

    def direction_is_inbound(self, mailbox_address: str) -> bool:
        """Compared against the *connected mailbox* and never against the roster: the
        roster holds the people written to, so asking it would call every message
        inbound.

        It lives on the parsed message, and not inline in the sync, because two callers
        need the same answer -- the `direction` column and the timeline entry, which
        must not announce our own reply as something that arrived -- and a rule stated
        twice is a rule that will eventually be stated differently.
        """
        return self.from_address != mailbox_address.strip().lower()


def header_addresses(raw: str) -> list[str]:
    """Every address in a `To`/`Cc` header, lowercased and deduplicated. A regex rather
    than `email.utils.getaddresses` because a display name containing a comma --
    `"Rossi, Bob" <bob@acme.it>` -- is exactly where naive splitting invents a
    recipient, and because only the addresses are stored."""
    return list(dict.fromkeys(match.group(0).lower() for match in _ADDRESS_IN_HEADER.finditer(raw)))


# The text a header gives before an address: `Sarah Miller <sarah@…>`, `"Miller, Sarah"
# <sarah@…>`, or nothing at all for a bare `sarah@…`. Lazy and bounded by the previous
# match, so in `a@x.it, Sarah Miller <sarah@x.it>` the second name does not swallow the
# first address.
_PARTICIPANT_IN_HEADER = re.compile(
    r'(?:"?([^"<>@,]*?)"?\s*<\s*)?([A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,63})\s*>?'
)


def header_display_names(raw: str) -> dict[str, str]:
    """`{address: display name}` for every participant of a `From`/`To`/`Cc` header
    that carried a name. An address without one is simply absent, and the first name
    seen for an address wins."""
    names: dict[str, str] = {}
    for match in _PARTICIPANT_IN_HEADER.finditer(raw):
        name = (match.group(1) or "").strip().strip(",").strip().strip('"').strip()
        address = match.group(2).lower()
        if name and address not in names:
            names[address] = name
    return names


def _decode_b64url(data: str) -> str:
    """Gmail strips the `=` padding; a decoder that requires it fails on most
    messages. `errors="replace"` is deliberately *not* used -- a body that will not
    decode is stored empty rather than sprinkled with replacement characters."""
    padded = data + "=" * (-len(data) % 4)
    try:
        return base64.urlsafe_b64decode(padded).decode("utf-8")
    except (binascii.Error, ValueError, UnicodeDecodeError):
        return ""


def _html_to_text(html: str) -> str:
    without_code = _SCRIPT_OR_STYLE.sub(" ", html)
    text = _TAG.sub("", without_code)
    for entity, char in _HTML_ENTITIES:
        text = text.replace(entity, char)
    return _WHITESPACE.sub("\n", text).strip()


def _truncate(text: str, body_max_bytes: int) -> tuple[str, bool]:
    encoded = text.encode("utf-8")
    if len(encoded) <= body_max_bytes:
        return text, False
    # Decode with errors="ignore" so a cut landing inside a multi-byte character drops
    # that character rather than storing U+FFFD. Stored mail with replacement
    # characters in it is mail nobody trusts.
    return encoded[:body_max_bytes].decode("utf-8", errors="ignore") + BODY_TRUNCATION_MARKER, True


def _walk(part: dict[str, Any]) -> list[dict[str, Any]]:
    """Every MIME part, depth first. Recursive and not a single pass over `parts`,
    because the ordinary shape of a message with an attachment is a `multipart/mixed`
    whose first child is the `multipart/alternative` holding the actual bodies: a
    one-level scan finds no `text/plain` in it at all."""
    found = [part]
    for child in part.get("parts") or []:
        if isinstance(child, dict):
            found.extend(_walk(child))
    return found


def _part_data(part: dict[str, Any] | None) -> str:
    if part is None:
        return ""
    body = part.get("body")
    return str(body.get("data") or "") if isinstance(body, dict) else ""


def _first_part(parts: list[dict[str, Any]], mime: str) -> dict[str, Any] | None:
    return next((p for p in parts if p.get("mimeType") == mime and _part_data(p)), None)


def parse_message(
    payload: dict[str, Any], *, body_max_bytes: int, store_bodies: bool
) -> ParsedMessage:
    raw_root = payload.get("payload")
    root: dict[str, Any] = raw_root if isinstance(raw_root, dict) else {}
    headers = {
        str(entry.get("name", "")).lower(): str(entry.get("value", ""))
        for entry in (root.get("headers") or [])
        if isinstance(entry, dict)
    }

    parts = _walk(root)
    plain = _first_part(parts, "text/plain")
    html = _first_part(parts, "text/html")

    body_text = ""
    body_truncated = False
    html_discarded = False
    if store_bodies:
        if plain is not None:
            body_text = _decode_b64url(_part_data(plain))
        elif html is not None:
            body_text = _html_to_text(_decode_b64url(_part_data(html)))
            html_discarded = True
        body_text, body_truncated = _truncate(body_text, body_max_bytes)

    attachments = [
        ParsedAttachment(
            filename=str(part.get("filename") or ""),
            mime=str(part.get("mimeType") or "application/octet-stream"),
            size=int((part.get("body") or {}).get("size") or 0),
        )
        for part in parts
        if part.get("filename")
    ]

    try:
        internal_ms = int(payload.get("internalDate") or 0)
    except (TypeError, ValueError):
        internal_ms = 0

    from_addresses = header_addresses(headers.get("from", ""))
    display_names: dict[str, str] = {}
    for header_name in ("from", "to", "cc"):
        for address, name in header_display_names(headers.get(header_name, "")).items():
            display_names.setdefault(address, name)
    return ParsedMessage(
        gmail_message_id=str(payload.get("id") or ""),
        gmail_thread_id=str(payload.get("threadId") or ""),
        message_id_header=headers.get("message-id", ""),
        in_reply_to=headers.get("in-reply-to", ""),
        references=headers.get("references", ""),
        # The first address only: a `From` carries exactly one mailbox in every message
        # this slice will ever see, and taking the first is what makes the value
        # comparable to `google_accounts.email_address` and to the roster.
        from_address=from_addresses[0] if from_addresses else "",
        to_addresses=header_addresses(headers.get("to", "")),
        cc_addresses=header_addresses(headers.get("cc", "")),
        subject=headers.get("subject", ""),
        snippet=str(payload.get("snippet") or ""),
        display_names=display_names,
        internal_date=datetime.fromtimestamp(internal_ms / 1000, tz=UTC),
        body_text=body_text,
        body_truncated=body_truncated,
        body_html_discarded=html_discarded,
        attachments=attachments,
    )
