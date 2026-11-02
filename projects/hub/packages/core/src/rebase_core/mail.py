"""Outbound mail: a seam and one provider.

The seam exists so tests never send and production never guesses: `sender_from_settings`
answers `None` without a key, and the API turns that into a 503 sentence rather than a
mail that does not arrive. Like `conversions.py`, nothing here raises past `send` and
nothing logs an address or the key.

The mails themselves live beside it by family: `mail_member` (the magic link, the
welcome), `mail_contracts` (signing and engagements), `mail_team` (the team builder and
the talent cloud), all in the box `mail_layout` draws. The HTML version of each is the
landing's visual system translated into what mail clients render; the plain text stays
beside it as the fallback and as what every test reads.
"""

import base64
import json
import logging
from dataclasses import dataclass
from http.client import HTTPException
from typing import Protocol

from rebase_core.config import Settings
from rebase_core.http import HttpCall, urllib_call

_log = logging.getLogger(__name__)

RESEND_URL = "https://api.resend.com/emails"


@dataclass(frozen=True)
class Attachment:
    """A file a mail carries: the name the reader saves it under, and its bytes."""

    filename: str
    content: bytes


@dataclass(frozen=True)
class Mail:
    """The text is required and is what arrives everywhere; the HTML, when there is one,
    is the same words in the landing's box for the clients that render it. Since REB-387
    the signed contracts leave as attachments; every other mail carries none."""

    to: str
    subject: str
    text: str
    html: str | None = None
    attachments: tuple[Attachment, ...] = ()


class EmailSender(Protocol):
    def send(self, mail: Mail) -> bool:
        """`True` when the provider accepted it. Never raises."""
        ...


class RecordingSender:
    """Keeps every mail in a list. For tests, and for reading the link in development."""

    def __init__(self) -> None:
        self.sent: list[Mail] = []

    def send(self, mail: Mail) -> bool:
        self.sent.append(mail)
        return True


class ResendSender:
    """Resend's `POST /emails`: one JSON object, the key as a bearer token."""

    def __init__(self, api_key: str, sender: str, http: HttpCall | None = None) -> None:
        self.api_key = api_key
        self.sender = sender
        self.http = http or urllib_call

    def send(self, mail: Mail) -> bool:
        body: dict[str, object] = {
            "from": self.sender,
            "to": [mail.to],
            "subject": mail.subject,
            "text": mail.text,
        }
        if mail.html is not None:
            body["html"] = mail.html
        if mail.attachments:
            # Resend takes each file as base64 inside the JSON body, beside its name.
            body["attachments"] = [
                {
                    "filename": item.filename,
                    "content": base64.b64encode(item.content).decode("ascii"),
                }
                for item in mail.attachments
            ]
        payload = json.dumps(body).encode()
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        try:
            status, _ = self.http("POST", RESEND_URL, headers, payload)
        except (OSError, HTTPException) as error:
            _log.warning("Resend unreachable: %s", type(error).__name__)
            return False
        if 200 <= status < 300:
            return True
        # The status alone: Resend's body may name the domain or the key's id. A 401/403
        # is a key or a domain to fix, a 429 the day's quota spent, a 5xx their outage;
        # all of them are one line the operator can tell apart.
        _log.warning("Resend refused the mail: status %s (%s)", status, _meaning(status))
        return False


def _meaning(status: int) -> str:
    if status in (401, 403):
        return "key or sending domain refused"
    if status == 429:
        return "rate or daily quota spent"
    if status >= 500:
        return "provider outage"
    return "request refused"


def sender_from_settings(settings: Settings) -> EmailSender | None:
    if not settings.resend_api_key:
        return None
    return ResendSender(settings.resend_api_key, settings.mail_from)


def send_after_response(sender: EmailSender, mail: Mail, label: str) -> None:
    """For a background task: sends `mail` once the response has gone out. A refusal is
    logged under `label`, which names what the mail was for (a request's id, a grant's),
    never the address, the body or the key: the operator needs to know the provider said
    no, not to whom."""
    if not sender.send(mail):
        _log.warning("%s: the provider refused the mail", label)
