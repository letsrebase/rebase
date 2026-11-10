"""Documenso, the signing site, over API v2: the six calls PigroCRM makes (REB-1019, moved from the
hub's `rebase_core.documenso`, REB-387 and REB-432).

The shapes are the ones phase 1 saw a self-hosted `documenso/documenso:v2.18.0` answer
(`projects/hub/docs/superpowers/specs/2026-09-23-documenso-probe.md` § 4 and § 5). An envelope is
created from a PDF with the freelancer as its one signer and the fields where the template's
blanks landed, the envelope item's id is read back (the create answers only the envelope's, and
the sealed copy is downloaded by item) and it is distributed with `distributionMethod: NONE`,
which sends no mail and answers the signing URL: the mail is PigroCRM's own. Every `emailSettings`
flag is off, the owner's «Signing Complete!» included, and the links never expire.

**The `externalId` is `<tenant id>:<document id>`** (`external_id` below), so the webhook, which
reaches the root and not a space, reads the space from it. Documenso takes any string there: its
v2.18.0 schema is `z.string().trim().max(255)` (`ZDocumentExternalIdSchema`) and the column is a
plain nullable `String` with no index, so a colon and 73 characters are fine (checked against the
source on 2026-11-10; the hub sent the bare document id, which `parse_external_id` still reads).

Everything goes through the `HttpCall` seam, so the tests hand `FakeDocumenso`. A refusal becomes
`DocumensoFailed` carrying Documenso's `message` alone.
"""

import json
import secrets
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from http.client import HTTPException
from typing import Any, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from pigrocrm.core.config import Settings
from pigrocrm.core.documenso.transport import SIGNED_COPY_MAX_BYTES, documenso_call
from pigrocrm.core.errors import DocumensoFailed, ValidationFailed
from pigrocrm.core.http import NETWORK_ERROR_STATUS, HttpCall

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
# What a call can fail with before any status exists: a socket (`OSError` covers the URL errors,
# the timeout and the refused connection) and a reply cut off or malformed mid-read. Anything else
# is a bug, not an outage.
_NETWORK_ERRORS = (OSError, HTTPException)
# A4 in points, the page the letters are typeset on (`letter.typ.template`, `paper: "a4"`).
A4_WIDTH_PT = 595.2756
A4_HEIGHT_PT = 841.8898


# ---- the external id ------------------------------------------------------------------------


def external_id(tenant_id: UUID | None, document_id: UUID) -> str:
    """What an envelope carries to find its document again: `<tenant id>:<document id>`, the space
    first. The root installation is no registry row and has no tenant id: it writes `root`, the
    name that is reserved against signups, so it can never be a space's."""
    return f"{tenant_id if tenant_id is not None else ROOT_TENANT}:{document_id}"


ROOT_TENANT = "root"


@dataclass(frozen=True)
class ExternalId:
    """An envelope's `externalId` read back. `tenant` is the registry id of the space (a string, as
    it was written), `ROOT_TENANT` for the root installation, and `None` for the bare document id
    the hub wrote (an envelope sent before the cutover, which resolves in the leads space)."""

    tenant: str | None
    document_id: UUID

    @property
    def qualified(self) -> bool:
        return self.tenant is not None


def parse_external_id(value: str | None) -> ExternalId | None:
    """`None` for anything that is not an id this code wrote or read: another system's envelope
    on the same Documenso, a title typed by hand, a malformed string. The webhook ignores those."""
    if not value:
        return None
    head, separator, tail = value.partition(":")
    try:
        if not separator:
            return ExternalId(None, UUID(head))
        if head != ROOT_TENANT:
            UUID(head)
        return ExternalId(head, UUID(tail))
    except ValueError:
        return None


# ---- the fields -----------------------------------------------------------------------------


class SignatureBox(Protocol):
    """Where Typst placed a blank the signing site fills: `name` is the label under it, `page`
    counts from 1 and the rest are points from the page's top-left corner. Satisfied by
    `collaborators.letter_pdf.SignatureBlank`; here only so the client imports nothing of the
    letters."""

    @property
    def name(self) -> str: ...

    @property
    def page(self) -> int: ...

    @property
    def x(self) -> float: ...

    @property
    def y(self) -> float: ...

    @property
    def width(self) -> float: ...

    @property
    def height(self) -> float: ...


def _percent(value: float, whole: float) -> float:
    return round(value / whole * 100, 3)


def fields_from_blanks(blanks: Sequence[SignatureBox]) -> list[dict[str, Any]]:
    """Documenso's fields from the blanks Typst placed: percentages of the A4 page from its
    top-left corner, the probe's conversion (§ 9). A blank the signing site does not fill is a
    text this code does not know, and a document with no signature field could be completed with
    nobody signing it: both are refused."""
    fields: list[dict[str, Any]] = []
    for blank in blanks:
        kind = FIELD_BY_BLANK.get(blank.name)
        if kind is None:
            raise ValidationFailed(
                "letter",
                "signature",
                f"il testo ha uno spazio di firma che la firma elettronica non conosce: "
                f"«{blank.name}»",
                expected="firma professionista e data firma",
            )
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
        raise ValidationFailed(
            "letter",
            "signature",
            "il documento non ha lo spazio per la firma del freelance",
            expected="uno spazio «firma professionista»",
        )
    return fields


# ---- the client -----------------------------------------------------------------------------


@dataclass(frozen=True)
class Envelope:
    """What `GET /envelope/{id}` says, as far as this code reads it (probe § 4). `signed_at` and
    `rejection_reason` are the signer's own, never the envelope's `completedAt`, which a
    cancellation sets too."""

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


# Quote and line breaks: what could close `filename="..."` early or end its header line.
_HEADER_BREAKERS = {ord(char): None for char in '"\r\n'}


def _multipart(payload: dict[str, Any], filename: str, pdf: bytes) -> tuple[str, bytes]:
    """`payload` as a JSON string and the PDF as `files`, the two parts the create takes. The file
    name becomes the envelope item's title and the sealed copy's name with `_signed`; the
    characters that would end the quoted header value or the header line are dropped, so no name
    can add a header or a part of its own."""
    filename = filename.translate(_HEADER_BREAKERS)
    boundary = f"pigrocrm-{secrets.token_hex(12)}"
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
    def __init__(self, base_url: str, token: str, http: HttpCall = documenso_call) -> None:
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
        _status, body = self._call(
            "GET",
            f"/envelope/item/{item_id}/download?version=signed",
            max_bytes=SIGNED_COPY_MAX_BYTES,
        )
        if len(body) > SIGNED_COPY_MAX_BYTES:
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

    def delete(self, envelope_id: str) -> None:
        """Documenso hard-deletes a draft or a pending envelope outright, and merely soft-deletes
        (kept, `deletedAt` set) a completed one, answering success either way: unlike `cancel`,
        nothing in its v2.18.0 source refuses on the envelope's state (probe § 4, REB-432). A
        refusal here is `DocumensoFailed` the same as every other call: the envelope no longer
        existing, or this token having no access to it, are what it actually refuses on."""
        body = _json_body({"envelopeId": envelope_id})
        answer = self._json("POST", "/envelope/delete", body)
        if answer.get("success") is not True:
            raise DocumensoFailed(UNREADABLE, f"delete answered {answer!r}")

    def ping(self) -> None:
        """One page of the team's envelopes, read and dropped: whether the instance answers and the
        token opens it (`pigrocrm documenso-check`). Parsed as JSON like every other call, so a
        200 that is not Documenso (a captive portal, a login page at the wrong URL) does not pass
        as reachable."""
        self._json("GET", "/envelope")

    def _call(
        self,
        method: str,
        path: str,
        body: bytes = b"",
        content_type: str | None = None,
        *,
        max_bytes: int | None = None,
    ) -> tuple[int, bytes]:
        headers = {"Authorization": self.token}
        if content_type is not None:
            headers["Content-Type"] = content_type
        try:
            status, answer, _headers = self.http(
                method, f"{self.api}{path}", headers, body, max_bytes=max_bytes
            )
        except _NETWORK_ERRORS as exc:
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


def signing_configured(settings: Settings) -> bool:
    """Both the URL and the token, or signing is off on this installation."""
    return bool(settings.documenso_url and settings.documenso_api_token)


def client_from_settings(
    settings: Settings, http: HttpCall | None = None
) -> DocumensoClient | None:
    """`None` without a URL or a token: signing is off on this installation, and says so. `http` is
    the seam, for a test's fake."""
    if not signing_configured(settings):
        return None
    return DocumensoClient(
        settings.documenso_url, settings.documenso_api_token, http or documenso_call
    )


# ---- what an envelope's outcome is ----------------------------------------------------------


@dataclass(frozen=True)
class Outcome:
    """What happened to an envelope, from the webhook or from a refresh: `completed` (with the
    signer's date), `rejected` (with the signer's reason) or `cancelled`."""

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
    external_id: str | None = Field(default=None, alias="externalId", max_length=300)
    status: str | None = None
    completed_at: datetime | None = Field(default=None, alias="completedAt")
    recipients: list[WebhookRecipient] = Field(default_factory=list)


class WebhookBody(BaseModel):
    """What Documenso POSTs (probe § 5): the event, upper case, and the envelope. The rest of the
    body (`createdAt`, `webhookEndpoint`, the numeric `id`, the recipients' tokens) is read by
    nobody."""

    model_config = ConfigDict(extra="ignore")

    event: str = Field(max_length=60)
    payload: WebhookEnvelope


def outcome_from_webhook(body: WebhookBody) -> Outcome | None:
    """The three events that move a document; every other one (created, sent, opened, signed by
    one of several, reminders, templates) moves nothing."""
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
    """A refresh: the same outcomes, read from the envelope's status."""
    if envelope.status == "COMPLETED":
        return Outcome(envelope.id, COMPLETED, signed_at=envelope.signed_at)
    if envelope.status == "REJECTED":
        return Outcome(envelope.id, REJECTED, reason=envelope.rejection_reason)
    if envelope.status == "CANCELLED":
        return Outcome(envelope.id, CANCELLED)
    return None
