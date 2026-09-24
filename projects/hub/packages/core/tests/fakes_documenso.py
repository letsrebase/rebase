"""A Documenso in a dict, behind the `HttpCall` seam (REB-387): the signing tests' fake,
the way `RecordingSender` is the mail's.

It answers the five calls the hub makes, in the shapes the phase 1 probe recorded
(`docs/superpowers/specs/2026-09-23-documenso-probe.md` § 4), keeps what every create
carried, and plays the rest of the world on request: the signer (`sign`, `reject`), a
refusal with Documenso's error body (`fail`), a dead network (`down`), and the body
Documenso would POST to the webhook (`webhook`)."""

import email
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from email.message import Message
from typing import Any
from urllib.parse import urlsplit

from rebase_core.documenso import DocumensoClient

BASE = "http://documenso.test"
TOKEN = "api_fake0123456789"
SIGNING_HOST = "https://firma.letsrebase.test"


def _iso(value: datetime | None) -> str | None:
    return value.isoformat().replace("+00:00", "Z") if value is not None else None


def _error(message: str, status: int) -> bytes:
    """Documenso's error body: the sentence, and a stack trace no admin may read."""
    return json.dumps(
        {
            "message": message,
            "code": "BAD_REQUEST",
            "data": {
                "code": "BAD_REQUEST",
                "httpStatus": status,
                "stack": "Error: at handler (/app/node_modules/@documenso/api/index.js:1:1)",
            },
        }
    ).encode()


def _form(content_type: str, body: bytes) -> dict[str, tuple[str | None, bytes]]:
    """The multipart body's parts, by name: (file name, bytes)."""
    message = email.message_from_bytes(f"Content-Type: {content_type}\r\n\r\n".encode() + body)
    parts: dict[str, tuple[str | None, bytes]] = {}
    for part in message.get_payload():
        part_message: Message = part
        name = part_message.get_param("name", header="content-disposition")
        parts[str(name)] = (part_message.get_filename(), part_message.get_payload(decode=True))
    return parts


@dataclass
class FakeEnvelope:
    id: str
    item_id: str
    payload: dict[str, Any]
    filename: str
    pdf: bytes
    token: str
    status: str = "DRAFT"
    signed_at: datetime | None = None
    completed_at: datetime | None = None
    rejection_reason: str | None = None


@dataclass
class FakeDocumenso:
    envelopes: dict[str, FakeEnvelope] = field(default_factory=dict)
    calls: list[tuple[str, str]] = field(default_factory=list)
    bodies: list[bytes] = field(default_factory=list)
    headers: list[dict[str, str]] = field(default_factory=list)
    failures: dict[str, tuple[int, str]] = field(default_factory=dict)
    outages: set[str] = field(default_factory=set)

    # ---- the test's hand -------------------------------------------------------------

    def client(self) -> DocumensoClient:
        return DocumensoClient(BASE, TOKEN, http=self)

    def created(self) -> list[dict[str, Any]]:
        return [envelope.payload for envelope in self.envelopes.values()]

    def fail(self, operation: str, status: int = 400, message: str = "Qualcosa non va") -> None:
        """The next `operation` answers `status` with Documenso's error body."""
        self.failures[operation] = (status, message)

    def down(self, operation: str) -> None:
        """The next `operation` raises the way urllib does on a refused connection."""
        self.outages.add(operation)

    def sign(self, envelope_id: str, at: datetime) -> None:
        """The signer's own `signedAt`; `completedAt` a few minutes later, as Documenso's
        own envelope timestamp actually is (probe § 4) -- kept apart from `signedAt` on
        purpose (REB-391), so a test that reads the wrong one notices."""
        envelope = self.envelopes[envelope_id]
        envelope.status = "COMPLETED"
        envelope.signed_at = at
        envelope.completed_at = at + timedelta(minutes=3)

    def reject(self, envelope_id: str, reason: str) -> None:
        envelope = self.envelopes[envelope_id]
        envelope.status, envelope.rejection_reason = "REJECTED", reason

    def signed_pdf(self, envelope_id: str) -> bytes:
        """The sealed copy: the original and Documenso's certificate page."""
        return self.envelopes[envelope_id].pdf + b"\n%certificato della firma\n"

    def webhook(self, envelope_id: str, event: str) -> dict[str, Any]:
        """What Documenso POSTs for `event` (probe § 5): the envelope, with the legacy
        numeric `id` and `envelopeId` beside it."""
        shown = self._envelope_json(self.envelopes[envelope_id])
        shown.pop("envelopeItems")
        payload = {**shown, "id": 7, "envelopeId": envelope_id, "teamId": 1, "userId": 1}
        return {
            "event": event,
            "payload": payload,
            "createdAt": _iso(datetime.now(UTC)),
            "webhookEndpoint": "http://api:8000/api/hub/documenso/webhook",
        }

    # ---- the seam ----------------------------------------------------------------------

    def __call__(
        self, method: str, url: str, headers: dict[str, str], body: bytes
    ) -> tuple[int, bytes]:
        parts = urlsplit(url)
        path = parts.path.removeprefix("/api/v2")
        self.calls.append((method, path + (f"?{parts.query}" if parts.query else "")))
        self.bodies.append(body)
        self.headers.append(dict(headers))
        operation = self._operation(method, path)
        if operation in self.outages:
            self.outages.discard(operation)
            raise OSError("connection refused")
        if headers.get("Authorization") != TOKEN:
            return 401, _error("Invalid session or API token.", 401)
        if operation in self.failures:
            status, message = self.failures.pop(operation)
            return status, _error(message, status)
        if operation == "create":
            return self._create(headers, body)
        if operation == "distribute":
            return self._distribute(body)
        if operation == "cancel":
            return self._cancel(body)
        if operation == "download":
            return self._download(path.split("/")[-2], parts.query)
        if operation == "get":
            return self._get(path.split("/")[-1])
        return 404, _error("Not found", 404)

    @staticmethod
    def _operation(method: str, path: str) -> str:
        if method == "POST" and path in (
            "/envelope/create",
            "/envelope/distribute",
            "/envelope/cancel",
        ):
            return path.rsplit("/", 1)[-1]
        if method == "GET" and path.startswith("/envelope/item/") and path.endswith("/download"):
            return "download"
        if method == "GET" and path.startswith("/envelope/"):
            return "get"
        return "unknown"

    def _envelope_json(self, envelope: FakeEnvelope) -> dict[str, Any]:
        recipient = envelope.payload["recipients"][0]
        signing_status = {"COMPLETED": "SIGNED", "REJECTED": "REJECTED"}.get(
            envelope.status, "NOT_SIGNED"
        )
        return {
            "id": envelope.id,
            "status": envelope.status,
            "externalId": envelope.payload.get("externalId"),
            "title": envelope.payload.get("title"),
            "completedAt": _iso(envelope.completed_at),
            "envelopeItems": [{"id": envelope.item_id, "title": envelope.filename}],
            "recipients": [
                {
                    "id": 1,
                    "email": recipient["email"],
                    "name": recipient["name"],
                    "role": "SIGNER",
                    "signingStatus": signing_status,
                    "signedAt": _iso(envelope.signed_at),
                    "rejectionReason": envelope.rejection_reason,
                    "token": envelope.token,
                }
            ],
        }

    def _create(self, headers: dict[str, str], body: bytes) -> tuple[int, bytes]:
        form = _form(headers["Content-Type"], body)
        number = len(self.envelopes) + 1
        filename, pdf = form["files"]
        envelope = FakeEnvelope(
            id=f"envelope_{number:04d}",
            item_id=f"envelope_item_{number:04d}",
            payload=json.loads(form["payload"][1]),
            filename=filename or "",
            pdf=pdf,
            token=f"token{number:04d}",
        )
        self.envelopes[envelope.id] = envelope
        return 200, json.dumps({"id": envelope.id}).encode()

    def _get(self, envelope_id: str) -> tuple[int, bytes]:
        envelope = self.envelopes.get(envelope_id)
        if envelope is None:
            return 404, _error("Envelope not found", 404)
        return 200, json.dumps(self._envelope_json(envelope)).encode()

    def _distribute(self, body: bytes) -> tuple[int, bytes]:
        envelope = self.envelopes.get(json.loads(body)["envelopeId"])
        if envelope is None:
            return 404, _error("Envelope not found", 404)
        envelope.status = "PENDING"
        recipient = self._envelope_json(envelope)["recipients"][0]
        recipient["signingUrl"] = f"{SIGNING_HOST}/sign/{envelope.token}"
        answer = {"success": True, "id": envelope.id, "recipients": [recipient]}
        return 200, json.dumps(answer).encode()

    def _cancel(self, body: bytes) -> tuple[int, bytes]:
        envelope = self.envelopes.get(json.loads(body)["envelopeId"])
        if envelope is None:
            return 404, _error("Envelope not found", 404)
        if envelope.status != "PENDING":
            return 400, _error("Only pending documents can be cancelled", 400)
        envelope.status, envelope.completed_at = "CANCELLED", datetime.now(UTC)
        return 200, json.dumps({"success": True}).encode()

    def _download(self, item_id: str, query: str) -> tuple[int, bytes]:
        envelope = next((e for e in self.envelopes.values() if e.item_id == item_id), None)
        if envelope is None or query != "version=signed":
            return 404, _error("Envelope item not found", 404)
        return 200, self.signed_pdf(envelope.id)
