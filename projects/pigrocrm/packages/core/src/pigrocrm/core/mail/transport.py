"""The mail seam: what a mail is, who sends it, and Resend behind it (spec 2026-09-12 §6.1).

The seam exists so tests never send and production never guesses: `sender_from_settings`
answers `None` without a key, and the API turns that into a 503 sentence rather than a
mail that does not arrive. A call that fails (no answer, a refusal) never raises past
`send`, `submit` or `status`; it leaves a warning carrying the status and nothing else,
because a caller that drops the answer (the link by mail, the digest's retry) has the log
as the only trace a mail did not leave. No address and no key are ever logged.

A provider's 2xx is only «I took it»: the mail can still bounce, and one to an address
that bounced before is suppressed without leaving at all (REB-678: an invitation to a
mistyped address was accepted, bounced, and nothing in the product said so). `submit`
therefore answers the id the provider filed the mail under, and `status` asks what became
of it, for the callers that show a person the outcome (the invitations).

Same shape as `rebase_core/mail.py` on purpose, and not an import of it: the two
products do not import each other (root `AGENTS.md`), and eighty lines are cheaper than
a dependency between two release trains.
"""

import base64
import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from http.client import HTTPException
from typing import Literal, Protocol

from pigrocrm.core.config import Settings
from pigrocrm.core.http import DEFAULT_TIMEOUT_SECONDS

logger = logging.getLogger(__name__)

RESEND_URL = "https://api.resend.com/emails"
# Resend sits behind Cloudflare, which answers `403 error code: 1010` to urllib's
# default `Python-urllib/3.x` signature and never reaches the API behind it. Every
# mail this module sent was refused there between the feature shipping and REB-261
# (2026-09-16), silently, because a refusal is only a `False` nobody reads. The same
# name `pigrocrm.core.tenants.hub` already uses, and the same lesson `rebase_core.http`
# learned on 2026-09-10.
USER_AGENT = "pigrocrm/0.1 (+https://pigro.letsrebase.com)"

HttpCall = Callable[[str, str, dict[str, str], bytes], tuple[int, bytes]]

# What a call can fail with before any status exists: a socket (`OSError` covers the URL
# errors, the timeout and the refused connection), a response `http.client` could not
# read (`HTTPException`), and a header it refused to write (`ValueError`, whose message
# quotes the offending value, here the bearer key: it must not reach a traceback). Any
# other exception is a bug in this code and is left to surface.
_CALL_FAILED = (OSError, HTTPException, ValueError)


def urllib_call(method: str, url: str, headers: dict[str, str], body: bytes) -> tuple[int, bytes]:
    """The one HTTP call this module makes, as a function so a test can replace it.

    The request names itself, unless the caller already named it. An empty body is no
    body: a `GET` must not leave with a form content type and a length of zero."""
    sent = {"User-Agent": USER_AGENT, **headers}
    request = urllib.request.Request(url, data=body or None, method=method, headers=sent)
    try:
        with urllib.request.urlopen(request, timeout=DEFAULT_TIMEOUT_SECONDS) as response:  # noqa: S310
            return int(response.status), bytes(response.read())
    except urllib.error.HTTPError as exc:
        return int(exc.code), bytes(exc.read())


@dataclass(frozen=True)
class Attachment:
    """A file that travels with a mail: Resend takes it base64-encoded under `content`."""

    filename: str
    content: bytes


@dataclass(frozen=True)
class Mail:
    to: str
    subject: str
    text: str
    html: str | None = None
    attachments: tuple[Attachment, ...] = ()


# What a provider says became of a mail it accepted, in this product's words. `sent` and
# `delayed` are the two a later look may still change; every other one is where a mail
# ends, so nobody asks again. `failed` also stands for a mail the provider never took.
MailState = Literal["sent", "delayed", "delivered", "bounced", "suppressed", "complained", "failed"]
OPEN_MAIL_STATES: tuple[MailState, ...] = ("sent", "delayed")

# Resend's `last_event`, as `GET /emails/{id}` answers it (its own event names). An opened
# or clicked mail was delivered; a name nobody mapped is `None`, never a guess.
RESEND_STATES: dict[str, MailState] = {
    "queued": "sent",
    "scheduled": "sent",
    "sent": "sent",
    "delivery_delayed": "delayed",
    "delivered": "delivered",
    "opened": "delivered",
    "clicked": "delivered",
    "bounced": "bounced",
    "suppressed": "suppressed",
    "complained": "complained",
    "failed": "failed",
    "canceled": "failed",
}


@dataclass(frozen=True)
class Receipt:
    """What the provider answered when it was handed a mail: whether it took it, and the
    id it filed it under, the only handle for asking afterwards what became of it."""

    accepted: bool
    id: str | None = None


class EmailSender(Protocol):
    def send(self, mail: Mail) -> bool:
        """`True` when the provider accepted it. Never raises."""
        ...


class TrackedSender(EmailSender, Protocol):
    """An `EmailSender` that can also say what became of a mail it accepted, for the
    callers that show a person the outcome instead of dropping it."""

    def submit(self, mail: Mail) -> Receipt:
        """`send`, with the provider's id for the mail. Never raises."""
        ...

    def status(self, mail_id: str) -> MailState | None:
        """What the provider last recorded for `mail_id`, or `None` when it cannot say:
        no answer, a refusal (a key that may only send cannot read), or an event this
        module has no word for. Never raises."""
        ...


class RecordingSender:
    """Keeps every mail in a list. For tests, and for reading a link in development.

    `accepts=False` is a provider that refuses every mail; `states` is what `status`
    answers by id (a mail not in it is still `sent`); `readable=False` is a provider
    that cannot be asked."""

    def __init__(self, *, accepts: bool = True, readable: bool = True) -> None:
        self.sent: list[Mail] = []
        self.accepts = accepts
        self.readable = readable
        self.states: dict[str, MailState] = {}

    def submit(self, mail: Mail) -> Receipt:
        if not self.accepts:
            return Receipt(False)
        self.sent.append(mail)
        return Receipt(True, f"recorded-{len(self.sent)}")

    def send(self, mail: Mail) -> bool:
        return self.submit(mail).accepted

    def status(self, mail_id: str) -> MailState | None:
        return self.states.get(mail_id, "sent") if self.readable else None


class ResendSender:
    """Resend's `POST /emails`: one JSON object, the key as a bearer token."""

    def __init__(self, api_key: str, sender: str, http: HttpCall | None = None) -> None:
        self.api_key = api_key
        self.sender = sender
        self.http = http or urllib_call

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    def submit(self, mail: Mail) -> Receipt:
        body: dict[str, object] = {
            "from": self.sender,
            "to": [mail.to],
            "subject": mail.subject,
            "text": mail.text,
        }
        if mail.html is not None:
            body["html"] = mail.html
        if mail.attachments:
            body["attachments"] = [
                {"filename": item.filename, "content": base64.b64encode(item.content).decode()}
                for item in mail.attachments
            ]
        payload = json.dumps(body).encode()
        try:
            status, raw = self.http("POST", RESEND_URL, self._headers(), payload)
        except _CALL_FAILED:
            # A caller that drops the answer has this line as the only trace the mail
            # did not leave. The status, and nothing else: neither the address nor the
            # key may reach a log.
            logger.warning("mail non inviata: la chiamata al provider non ha risposto")
            return Receipt(False)
        if not 200 <= status < 300:
            logger.warning("mail non inviata: il provider ha risposto %s", status)
            return Receipt(False)
        return Receipt(True, _json_field(raw, "id"))

    def send(self, mail: Mail) -> bool:
        return self.submit(mail).accepted

    def status(self, mail_id: str) -> MailState | None:
        url = f"{RESEND_URL}/{urllib.parse.quote(mail_id, safe='')}"
        try:
            status, raw = self.http("GET", url, self._headers(), b"")
        except _CALL_FAILED:
            logger.warning("stato della mail non letto: il provider non ha risposto")
            return None
        if not 200 <= status < 300:
            logger.warning("stato della mail non letto: il provider ha risposto %s", status)
            return None
        event = _json_field(raw, "last_event")
        return RESEND_STATES.get(event) if event is not None else None


def _json_field(raw: bytes, name: str) -> str | None:
    """`name` from a JSON object body when it is a non-empty string, else `None`: a
    provider body that is not what it was last time is not worth a failed send."""
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    value = data.get(name) if isinstance(data, dict) else None
    return value if isinstance(value, str) and value else None


def sender_from_settings(settings: Settings) -> TrackedSender | None:
    if not settings.resend_api_key:
        return None
    return ResendSender(settings.resend_api_key, settings.mail_from)
