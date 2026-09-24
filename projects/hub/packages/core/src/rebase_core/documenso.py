"""Documenso, the signing site, over API v2: the five calls the hub makes (REB-387).

The shapes are the ones phase 1 saw a self-hosted `documenso/documenso:v2.18.0` answer
(`docs/superpowers/specs/2026-09-23-documenso-probe.md` § 4 and § 5). The hub creates an
envelope from a PDF with the freelancer as its one signer and the fields where the
template's blanks landed, reads back the envelope item's id (the create answers only the
envelope's, and the sealed copy is downloaded by item), and distributes it with
`distributionMethod: NONE`, which sends no mail and answers the signing URL: the mail is
the hub's own. Every `emailSettings` flag is off, the owner's «Signing Complete!»
included, and the links never expire.

Everything goes through the `HttpCall` seam, so the tests hand `FakeDocumenso`. A
refusal becomes `DocumensoFailed` carrying Documenso's `message` alone.
"""

import json
import secrets
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from rebase_core.config import Settings
from rebase_core.contracts.fields import ContractFailed
from rebase_core.contracts.render import A4_HEIGHT_PT, A4_WIDTH_PT, SignatureBlank
from rebase_core.errors import DocumensoFailed
from rebase_core.http import (
    MAX_DOWNLOAD_BYTES,
    NETWORK_ERROR_STATUS,
    HttpCall,
    urllib_download_call,
)

API_PREFIX = "/api/v2"
SIGNATURE, DATE = "SIGNATURE", "DATE"
# The two blanks the freelancer fills, by the label the template prints under them.
FIELD_BY_BLANK = {"firma professionista": SIGNATURE, "data firma": DATE}
# The probe's `fieldMeta` for the date: nine points, left-aligned on the rule.
DATE_META: dict[str, Any] = {"type": "date", "fontSize": 9, "textAlign": "left"}
EMAIL_SETTINGS_OFF = {
    "recipientSigningRequest": False,
    "recipientRemoved": False,
    "recipientSigned": False,
    "documentPending": False,
    "documentCompleted": False,
    "documentDeleted": False,
    "ownerDocumentCompleted": False,
    "ownerRecipientExpired": False,
    "ownerDocumentCreated": False,
}
META: dict[str, Any] = {
    "distributionMethod": "NONE",
    "language": "it",
    "timezone": "Europe/Rome",
    "dateFormat": "dd/MM/yyyy",
    "envelopeExpirationPeriod": {"disabled": True},
    "emailSettings": EMAIL_SETTINGS_OFF,
}
COMPLETED, REJECTED, CANCELLED = "completed", "rejected", "cancelled"
UNREACHABLE = "Documenso non risponde: riprova tra qualche minuto."
UNREADABLE = "Documenso ha dato una risposta che non riconosco: riprova tra qualche minuto."


# ---- the fields -----------------------------------------------------------------------


def _percent(value: float, whole: float) -> float:
    return round(value / whole * 100, 3)


def fields_from_blanks(blanks: Sequence[SignatureBlank]) -> list[dict[str, Any]]:
    """Documenso's fields from the blanks Typst placed: percentages of the A4 page from
    its top-left corner, the probe's conversion (§ 9). A blank the signing site does not
    fill is a template the hub does not know, and a document with no signature field
    could be completed with nobody signing it: both are refused."""
    fields: list[dict[str, Any]] = []
    for blank in blanks:
        kind = FIELD_BY_BLANK.get(blank.name)
        if kind is None:
            raise ContractFailed(f"a signing blank the hub does not know: {blank.name!r}")
        field: dict[str, Any] = {
            "type": kind,
            "page": blank.page,
            "positionX": _percent(blank.x, A4_WIDTH_PT),
            "positionY": _percent(blank.y, A4_HEIGHT_PT),
            "width": _percent(blank.width, A4_WIDTH_PT),
            "height": _percent(blank.height, A4_HEIGHT_PT),
        }
        if kind == DATE:
            field["fieldMeta"] = dict(DATE_META)
        fields.append(field)
    if not any(field["type"] == SIGNATURE for field in fields):
        raise ContractFailed("the document has no blank for the freelancer's signature")
    return fields


# ---- the client -----------------------------------------------------------------------


@dataclass(frozen=True)
class Envelope:
    """What `GET /envelope/{id}` says, as far as the hub reads it (probe § 4). `signed_at`
    and `rejection_reason` are the signer's own, never the envelope's `completedAt`,
    which a cancellation sets too."""

    id: str
    status: str
    item_id: str
    external_id: str | None
    signed_at: datetime | None
    rejection_reason: str | None


def _when(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _signer(recipients: object) -> dict[str, Any]:
    if isinstance(recipients, list):
        for recipient in recipients:
            if isinstance(recipient, dict) and recipient.get("role", "SIGNER") == "SIGNER":
                return recipient
    return {}


def _refusal(answer: bytes) -> str:
    """Documenso's own sentence, and never the stack trace its error bodies carry."""
    try:
        message = json.loads(answer).get("message")
    except (ValueError, AttributeError):
        message = None
    if isinstance(message, str) and message.strip():
        return f"Documenso ha rifiutato la richiesta: {message.strip()[:300]}"
    return "Documenso ha rifiutato la richiesta."


def _json_body(value: dict[str, Any]) -> bytes:
    return json.dumps(value).encode()


def _multipart(payload: dict[str, Any], filename: str, pdf: bytes) -> tuple[str, bytes]:
    """`payload` as a JSON string and the PDF as `files`, the two parts the create takes.
    The file name is the hub's own (`lettera-di-incarico-2026-001.pdf`), ASCII, and
    becomes the envelope item's title and the sealed copy's name with `_signed`."""
    boundary = f"rebase-{secrets.token_hex(12)}"
    head = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="payload"\r\n\r\n'
        f"{json.dumps(payload)}\r\n"
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="files"; filename="{filename}"\r\n'
        "Content-Type: application/pdf\r\n\r\n"
    ).encode()
    tail = f"\r\n--{boundary}--\r\n".encode()
    return f"multipart/form-data; boundary={boundary}", head + pdf + tail


class DocumensoClient:
    def __init__(self, base_url: str, token: str, http: HttpCall = urllib_download_call) -> None:
        self.api = base_url.rstrip("/") + API_PREFIX
        self.token = token
        self.http = http

    def create(
        self,
        *,
        title: str,
        external_id: str,
        filename: str,
        pdf: bytes,
        signer_email: str,
        signer_name: str,
        fields: list[dict[str, Any]],
    ) -> str:
        """The envelope, `DRAFT`, with the freelancer as its one `SIGNER`: its id."""
        payload = {
            "type": "DOCUMENT",
            "title": title,
            "externalId": external_id,
            "recipients": [
                {"email": signer_email, "name": signer_name, "role": "SIGNER", "fields": fields}
            ],
            "meta": META,
        }
        content_type, body = _multipart(payload, filename, pdf)
        answer = self._json("POST", "/envelope/create", body, content_type)
        envelope_id = answer.get("id")
        if not isinstance(envelope_id, str) or not envelope_id:
            raise DocumensoFailed(UNREADABLE, f"create answered {answer!r}")
        return envelope_id

    def get(self, envelope_id: str) -> Envelope:
        answer = self._json("GET", f"/envelope/{envelope_id}")
        items = answer.get("envelopeItems")
        first = items[0] if isinstance(items, list) and items else None
        item_id = first.get("id") if isinstance(first, dict) else None
        status = answer.get("status")
        if not isinstance(item_id, str) or not isinstance(status, str):
            raise DocumensoFailed(UNREADABLE, f"envelope {envelope_id}: no item or no status")
        signer = _signer(answer.get("recipients"))
        external = answer.get("externalId")
        reason = signer.get("rejectionReason")
        return Envelope(
            id=envelope_id,
            status=status,
            item_id=item_id,
            external_id=external if isinstance(external, str) else None,
            signed_at=_when(signer.get("signedAt")),
            rejection_reason=reason if isinstance(reason, str) and reason else None,
        )

    def distribute(self, envelope_id: str) -> str:
        """`PENDING`, with no mail from Documenso: the signing URL of the one signer."""
        body = _json_body({"envelopeId": envelope_id, "meta": {"distributionMethod": "NONE"}})
        answer = self._json("POST", "/envelope/distribute", body)
        url = _signer(answer.get("recipients")).get("signingUrl")
        if answer.get("success") is not True or not isinstance(url, str) or not url:
            raise DocumensoFailed(
                UNREADABLE, f"distribute answered no signing URL for {envelope_id}"
            )
        return url

    def download_signed(self, item_id: str) -> bytes:
        _status, body = self._call("GET", f"/envelope/item/{item_id}/download?version=signed")
        if len(body) > MAX_DOWNLOAD_BYTES:
            raise DocumensoFailed(UNREADABLE, f"the signed copy of {item_id} passes the cap")
        if not body.startswith(b"%PDF-"):
            raise DocumensoFailed(UNREADABLE, f"the signed copy of {item_id} is not a PDF")
        return body

    def cancel(self, envelope_id: str, reason: str) -> None:
        """Only a `PENDING` envelope can be cancelled; a draft answers 400 (probe § 4)."""
        body = _json_body({"envelopeId": envelope_id, "reason": reason})
        answer = self._json("POST", "/envelope/cancel", body)
        if answer.get("success") is not True:
            raise DocumensoFailed(UNREADABLE, f"cancel answered {answer!r}")

    def ping(self) -> None:
        """One page of the team's envelopes, read and dropped: whether the instance
        answers and the token opens it (`rebase documenso-check`, REB-393)."""
        self._call("GET", "/envelope")

    def _call(
        self, method: str, path: str, body: bytes = b"", content_type: str | None = None
    ) -> tuple[int, bytes]:
        headers = {"Authorization": self.token}
        if content_type is not None:
            headers["Content-Type"] = content_type
        try:
            status, answer = self.http(method, f"{self.api}{path}", headers, body)
        except Exception as exc:  # urllib raises on a refused connection or a timeout
            raise DocumensoFailed(UNREACHABLE, f"{method} {path}: {exc!r}") from exc
        if status == NETWORK_ERROR_STATUS:
            raise DocumensoFailed(UNREACHABLE, f"{method} {path}: no response")
        if not 200 <= status < 300:
            raise DocumensoFailed(_refusal(answer), f"{method} {path}: HTTP {status}")
        return status, answer

    def _json(
        self,
        method: str,
        path: str,
        body: bytes = b"",
        content_type: str = "application/json",
    ) -> dict[str, Any]:
        _status, answer = self._call(method, path, body, content_type if body else None)
        try:
            parsed = json.loads(answer)
        except ValueError as exc:
            raise DocumensoFailed(UNREADABLE, f"{method} {path}: not JSON") from exc
        if not isinstance(parsed, dict):
            raise DocumensoFailed(UNREADABLE, f"{method} {path}: not a JSON object")
        return parsed


def client_from_settings(
    settings: Settings, http: HttpCall | None = None
) -> DocumensoClient | None:
    """`None` without a URL or a token: signing is off on this environment, and says so.
    `http` is the seam, for the check command's test."""
    if not settings.documenso_url or not settings.documenso_api_token:
        return None
    return DocumensoClient(
        settings.documenso_url, settings.documenso_api_token, http or urllib_download_call
    )


# ---- what an envelope's outcome is --------------------------------------------------------


@dataclass(frozen=True)
class Outcome:
    """What happened to an envelope, from the webhook or from «Aggiorna stato»:
    `completed` (with the signer's date), `rejected` (with the signer's reason) or
    `cancelled`."""

    envelope_id: str
    kind: str
    signed_at: datetime | None = None
    reason: str | None = None


class WebhookRecipient(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    role: str = "SIGNER"
    signed_at: datetime | None = Field(default=None, alias="signedAt")
    rejection_reason: str | None = Field(default=None, alias="rejectionReason")


class WebhookEnvelope(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    envelope_id: str = Field(alias="envelopeId", min_length=1, max_length=100)
    status: str | None = None
    completed_at: datetime | None = Field(default=None, alias="completedAt")
    recipients: list[WebhookRecipient] = Field(default_factory=list)


class WebhookBody(BaseModel):
    """What Documenso POSTs (probe § 5): the event, upper case, and the envelope. The rest
    of the body (`createdAt`, `webhookEndpoint`, the numeric `id`, the recipients'
    tokens) is read by nobody."""

    model_config = ConfigDict(extra="ignore")

    event: str = Field(max_length=60)
    payload: WebhookEnvelope


def outcome_from_webhook(body: WebhookBody) -> Outcome | None:
    """The three events that move a document; every other one (created, sent, opened,
    signed by one of several, reminders, templates) moves nothing."""
    envelope = body.payload
    signer = next((r for r in envelope.recipients if r.role == "SIGNER"), None)
    if body.event == "DOCUMENT_COMPLETED":
        signed_at = signer.signed_at if signer is not None else None
        if signed_at is None and envelope.status == "COMPLETED":
            signed_at = envelope.completed_at
        return Outcome(envelope.envelope_id, COMPLETED, signed_at=signed_at)
    if body.event == "DOCUMENT_REJECTED":
        reason = signer.rejection_reason if signer is not None else None
        return Outcome(envelope.envelope_id, REJECTED, reason=reason)
    if body.event == "DOCUMENT_CANCELLED":
        return Outcome(envelope.envelope_id, CANCELLED)
    return None


def outcome_from_envelope(envelope: Envelope) -> Outcome | None:
    """«Aggiorna stato»: the same outcomes, read from the envelope's status."""
    if envelope.status == "COMPLETED":
        return Outcome(envelope.id, COMPLETED, signed_at=envelope.signed_at)
    if envelope.status == "REJECTED":
        return Outcome(envelope.id, REJECTED, reason=envelope.rejection_reason)
    if envelope.status == "CANCELLED":
        return Outcome(envelope.id, CANCELLED)
    return None
