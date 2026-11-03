"""Building the message Gmail will send.

Same structure as the previous system's `buildRawEmailMessage` -- `multipart/mixed`, base64url for
the `raw` field -- and none of its three defects:

1. **`Message-ID` is always present**, generated here, with the right-hand side derived
   from the configured domain. It is what makes the thread correct when the client
   replies, and what makes the reconciliation of spec 6.3 exact rather than heuristic.
2. **`In-Reply-To` and `References` are set** when replying or chasing inside an
   existing thread. Without them the reminder arrives detached, and the first thing the
   recipient does is ask for the invoice again.
3. **The body declares `quoted-printable` or `base64`, with `charset="UTF-8"`. Never
   `7bit`.** the previous system declares `7bit` over text containing `à` and `’`; it works by
   accident until the first client that takes the declaration literally.

Built on `email.message.EmailMessage` from the standard library rather than by string
concatenation. That is the point of the third defect: the encoding rules are subtle,
they are already implemented correctly, and hand-rolling them is what produced the bug.

Nothing here logs, and nothing that leaves this module carries a body or an address: a
`ValidationFailed` raised below names the *field* and, for an address, quotes only the
one value the caller must fix. The bodies passing through are somebody's private
correspondence.
"""

import base64
import re
import time
from collections.abc import Sequence
from dataclasses import dataclass
from email.message import EmailMessage
from email.policy import SMTP
from email.utils import formataddr

from pigrocrm.core.db import uuid7
from pigrocrm.core.errors import ValidationFailed

# Deliberately strict: these strings become RFC822 headers.
_ADDRESS = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,63}")
# Same shape as `query._SAFE_MESSAGE_ID`, plus the angle brackets, and for the same
# reason it is not folded to lower case: the local part of a Message-ID is
# case-sensitive (RFC 5322 3.6.4). The two must agree, because the id minted here is
# handed to `rfc822msgid_query` later and a value this module accepts but that one
# refuses would fail on the reconciliation path -- the worst place to find out.
_MESSAGE_ID = re.compile(r"<[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,63}>")
_CONTROL = re.compile(r"[\r\n\x00]")
# `type/subtype` and nothing else. `EmailMessage` takes the subtype verbatim, so
# `pdf; name="evil"` would become a header parameter, and a CRLF in it raises a bare
# `ValueError` that surfaces as a 500. Types are case-insensitive (RFC 2045 5.1); the
# value is lower-cased before it is checked and used.
_MIME_TYPE = re.compile(r"[a-z0-9.+\-]+/[a-z0-9.+\-]+")


@dataclass(frozen=True)
class OutgoingAttachment:
    filename: str
    mime: str
    content: bytes


def new_message_id(domain: str) -> str:
    """`<uuid7hex.epoch@domain>`. UUIDv7 for the left half so two ids minted in the same
    second still differ, and the epoch so a human reading a header can date it."""
    if not _ADDRESS.fullmatch(f"x@{domain}"):
        raise ValidationFailed(
            "email_draft",
            "message_id_domain",
            "non è un dominio valido",
            expected="esempio.it",
        )
    return f"<{uuid7().hex}.{int(time.time())}@{domain}>"


def _header(value: str, field: str) -> str:
    """No CR, LF or NUL in a header value. A subject carrying CRLF would let a caller
    append arbitrary headers -- a `Bcc`, for instance -- which is header injection, the
    same class of defect as SQL injection and with the same non-fix (hoping nobody
    tries).

    `EmailMessage` would raise on some of these by itself, but not on all of them and not
    with a message the composer can show a field name from, so the check is explicit and
    every header value goes through it.
    """
    if _CONTROL.search(value):
        raise ValidationFailed(
            "email_draft",
            field,
            "non può contenere ritorni a capo o byte NUL",
            expected="una sola riga di testo",
        )
    return value


def _addresses(values: Sequence[str], field: str) -> list[str]:
    """Each entry is exactly one address, never a list. A caller passing
    `"ada@acme.it, altro@altrove.it"` in one element would otherwise add a recipient
    nobody chose -- the same defect as header injection, one comma cheaper.
    """
    checked: list[str] = []
    for value in values:
        candidate = value.strip()
        if not _ADDRESS.fullmatch(candidate):
            raise ValidationFailed(
                "email_draft", field, f"{candidate!r} non è un indirizzo email valido"
            )
        checked.append(candidate)
    return checked


def build_rfc822(
    *,
    from_address: str,
    from_name: str,
    to: Sequence[str],
    cc: Sequence[str],
    subject: str,
    body_text: str,
    message_id: str,
    in_reply_to: str = "",
    references: str = "",
    attachments: Sequence[OutgoingAttachment] = (),
) -> bytes:
    recipients = _addresses(to, "to")
    if not recipients:
        raise ValidationFailed("email_draft", "to", "serve almeno un destinatario")
    copies = _addresses(cc, "cc")
    sender = _addresses([from_address], "from_address")[0]
    if not _MESSAGE_ID.fullmatch(message_id):
        raise ValidationFailed(
            "email_draft",
            "message_id",
            "deve avere la forma <local@dominio.tld>",
            expected="<abc123.1723000000@crm.esempio.it>",
        )

    # `policy=SMTP` for CRLF line endings and RFC-correct folding: this byte string is
    # base64url-encoded straight into `users.messages.send`, so it is wire format, not a
    # local file.
    message = EmailMessage(policy=SMTP)
    # `EmailMessage` applies RFC 2047 encoded-words to a header containing non-ASCII by
    # itself, which is the other half of what the previous system got wrong -- its subjects were raw
    # UTF-8 in a header field.
    # `formataddr` and not an f-string, and this is not cosmetic. The display name is
    # `emitter_profile.ragione_sociale`, which is somebody's own business name: an
    # unquoted `Bianchi, Rossi e Associati <io@example.it>` parses as **two** addresses
    # -- a bare `Bianchi` and then the real one -- because the comma is the address-list
    # separator. `formataddr` quotes the name when it has to, escapes an embedded quote,
    # and encodes a non-ASCII one as an RFC 2047 word, which is the same three rules
    # `_header` cannot express because they are about structure rather than about
    # control characters. `_header` still runs first: it is what refuses the CRLF.
    message["From"] = formataddr((_header(from_name, "from_name"), sender)) if from_name else sender
    message["To"] = ", ".join(recipients)
    if copies:
        message["Cc"] = ", ".join(copies)
    message["Subject"] = _header(subject, "subject")
    message["Message-ID"] = message_id
    # Set only when there is something to say. An empty `In-Reply-To` is not the same as
    # no `In-Reply-To`: a client reading the first threads a fresh message onto nothing.
    if in_reply_to:
        message["In-Reply-To"] = _header(in_reply_to, "in_reply_to")
    if references:
        message["References"] = _header(references, "references")

    # `cte="quoted-printable"` explicitly, never the default: the default picks 7bit for
    # a body that happens to be ASCII today, and an Italian body one edit later is not.
    # Choosing it unconditionally means there is no branch in which 7bit can appear.
    message.set_content(body_text, subtype="plain", charset="utf-8", cte="quoted-printable")

    for attachment in attachments:
        mime = attachment.mime.lower()
        if not _MIME_TYPE.fullmatch(mime):
            raise ValidationFailed(
                "email_draft",
                "attachment_mime",
                "non è un tipo di file valido",
                expected="tipo/sottotipo, per esempio application/pdf",
            )
        maintype, _, subtype = mime.partition("/")
        message.add_attachment(
            attachment.content,
            maintype=maintype,
            subtype=subtype,
            filename=_header(attachment.filename, "attachment_filename"),
        )

    return message.as_bytes()


def to_base64url(raw: bytes) -> str:
    """What `users.messages.send` wants in its `raw` field: base64url, unpadded."""
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")
