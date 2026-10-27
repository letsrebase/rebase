"""Resend's `POST /emails` for campaigns: `mail.ResendSender` plus the three things a
campaign needs and the member area's mail does not. It keeps the id, carries tags and
custom headers, and sends an `Idempotency-Key` equal to the recipient row's id, so a
tick that dies after Resend accepted a mail re-sends the same key and gets the same
answer instead of a second mail (spec § 5.4). Never raises; never logs an address."""

import json
from dataclasses import dataclass
from typing import Literal, Protocol

from rebase_core.campaigns.render import RenderedMail
from rebase_core.config import Settings
from rebase_core.http import HttpCall, urllib_call
from rebase_core.mail import RESEND_URL

# `fermati`: Resend refused the key or the domain (401/403), an answer every other
# mail of the list would get too, so the tick stops instead of burning the list.
# `quota`: a 429 that names Resend's daily or monthly quota, not its request rate
# (REB-779): every other mail of the day would get it too, and it is no attempt of the
# row's. `dettaglio` keeps which quota, so the page can say when the rest leaves.
Esito = Literal["accettata", "riprova", "rifiutata", "fermati", "quota"]

QUOTA_DAILY = "daily_quota_exceeded"
QUOTA_MONTHLY = "monthly_quota_exceeded"


@dataclass(frozen=True)
class SendOutcome:
    esito: Esito
    resend_id: str | None = None
    dettaglio: str = ""


class CampaignSender(Protocol):
    def send(self, rendered: RenderedMail, idempotency_key: str) -> SendOutcome: ...


def _field(raw: bytes, name: str) -> str:
    try:
        value = json.loads(raw or b"{}").get(name)
    except (ValueError, AttributeError):
        return ""
    return value if isinstance(value, str) else ""


class ResendCampaignSender:
    def __init__(self, api_key: str, sender: str, http: HttpCall | None = None) -> None:
        self.api_key, self.sender, self.http = api_key, sender, http or urllib_call

    def send(self, rendered: RenderedMail, idempotency_key: str) -> SendOutcome:
        mail = rendered.mail
        body: dict[str, object] = {
            "from": self.sender,
            "to": [mail.to],
            "subject": mail.subject,
            "text": mail.text,
            "headers": rendered.headers,
            "tags": [{"name": k, "value": v} for k, v in rendered.tags.items()],
        }
        if mail.html is not None:
            body["html"] = mail.html
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Idempotency-Key": idempotency_key,
        }
        try:
            status, raw = self.http("POST", RESEND_URL, headers, json.dumps(body).encode())
        except Exception:  # noqa: BLE001 - the seam's contract is "never raises"
            return SendOutcome("riprova", dettaglio="nessuna risposta da Resend")
        if 200 <= status < 300:
            return SendOutcome("accettata", _field(raw, "id") or None)
        name = _field(raw, "name")
        if status == 409 and name == "concurrent_idempotent_requests":
            return SendOutcome("riprova", dettaglio=name)
        if status == 429 and name in (QUOTA_DAILY, QUOTA_MONTHLY):
            return SendOutcome("quota", dettaglio=name)
        if status == 429 or status >= 500:
            return SendOutcome("riprova", dettaglio=f"Resend {status}")
        if status in (401, 403):
            # A revoked or restricted key, or a domain Resend no longer sends for: the
            # status alone, since the body may name the domain or the key's id.
            return SendOutcome("fermati", dettaglio=f"Resend {status}")
        return SendOutcome("rifiutata", dettaglio=f"Resend {status} {name}".strip())


class RecordingCampaignSender:
    """Keeps every mail and key; answers the queued outcomes first, then accepts."""

    def __init__(self, outcomes: list[SendOutcome] | None = None) -> None:
        self.sent: list[RenderedMail] = []
        self.keys: list[str] = []
        self._outcomes = list(outcomes or [])

    def send(self, rendered: RenderedMail, idempotency_key: str) -> SendOutcome:
        self.sent.append(rendered)
        self.keys.append(idempotency_key)
        if self._outcomes:
            return self._outcomes.pop(0)
        return SendOutcome("accettata", f"rec-{len(self.sent)}")


def campaign_sender_from_settings(settings: Settings) -> CampaignSender | None:
    if not settings.resend_api_key:
        return None
    return ResendCampaignSender(settings.resend_api_key, settings.campaign_from)
