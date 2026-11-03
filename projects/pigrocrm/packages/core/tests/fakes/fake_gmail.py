"""An in-memory Gmail that speaks the same HTTP surface `GmailTransport` uses.

A fake of the *transport*, on the model of `fakes/fake_drive.py`, and for the same
reason: URL building, the `q` string, the RFC822 body and the error handling are the
parts most likely to be wrong, so they must run for real. What is replaced is the
network, nothing above it.

`requests` is the point of this class. Every request is recorded with its parsed query
string, so a test can assert on **what was asked of Google** and not merely on what
ended up in the database. Spec 4.1 requires exactly that: "a `list` without a `q` is a
bug, and it is verified by a test that inspects the requests received by the fake
transport -- not by a convention written in a comment."
"""

import base64
import hashlib
import json
import time
from dataclasses import dataclass, field
from email import message_from_bytes
from email.message import EmailMessage
from email.policy import default as default_policy
from typing import Any
from urllib.parse import parse_qs, urlparse

from fakes.gmail_query import matches

TOKEN_HOST = "oauth2.googleapis.com"
API_HOST = "gmail.googleapis.com"


@dataclass(frozen=True)
class RecordedRequest:
    method: str
    host: str
    path: str
    query: dict[str, list[str]]
    body: bytes | None

    @property
    def q(self) -> str | None:
        """The Gmail search expression, if this was a listing."""
        values = self.query.get("q")
        return values[0] if values else None

    @property
    def is_messages_list(self) -> bool:
        return self.method == "GET" and self.path.endswith("/messages")

    @property
    def is_messages_send(self) -> bool:
        return self.method == "POST" and self.path.endswith("/messages/send")


@dataclass
class FakeMessage:
    id: str
    thread_id: str
    headers: dict[str, str]
    body_text: str = ""
    body_html: str = ""
    internal_date_ms: int = 0
    label_ids: list[str] = field(default_factory=lambda: ["INBOX"])
    # `{"filename": ..., "mime": ..., "size": ...}`, plus an optional `"content"` of
    # real bytes. The bytes are what `users.messages.attachments.get` hands back, and
    # they are optional because most tests here care only that a name, a type and a size
    # reach the sync -- which is all PigroCRM stores of an attachment (spec 5.4).
    attachments: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class FakeGmail:
    """Configure the mailbox, then hand `self` to `GmailTransport(http=fake)`."""

    messages: dict[str, FakeMessage] = field(default_factory=dict)
    requests: list[RecordedRequest] = field(default_factory=list)
    token_requests: int = 0
    # A refresh token that Google has revoked. When set, the token endpoint answers
    # the real 400 body, once per call, forever -- because that is what a revoked
    # grant does. It never heals.
    revoked: bool = False
    access_token: str = "ya29.fake-access-token"
    refresh_token: str = "1//0gFakeRefresh"
    # Google only ever returns an ID token from the *code* exchange, and only because
    # `openid` is among the requested scopes. Empty by default so a test that does not
    # care about identity does not have to build one.
    id_token: str = ""
    # The trap `prompt=consent` exists to avoid: a second authorisation for a user who
    # already consented comes back 200, complete, and without a refresh token at all.
    omit_refresh_token: bool = False
    # PKCE, verified the way Google verifies it. When set, an `authorization_code`
    # exchange is refused with the real `invalid_grant` body unless the posted
    # `code_verifier` hashes (S256, base64url, unpadded) to this challenge. Off by
    # default because the refresh-token tests have no authorisation request behind
    # them and there is no challenge to compare against.
    expected_code_challenge: str | None = None
    # Google's authorisation codes are single-use; a replay answers `invalid_grant`.
    # Opt-in rather than always-on: a test that exchanges the same placeholder code
    # twice on purpose is usually testing something else entirely.
    single_use_codes: bool = False
    used_codes: set[str] = field(default_factory=set)
    expires_in: int = 3599
    granted_scopes: tuple[str, ...] = ()
    # A queue of (status, body, headers) consumed FIFO before normal handling. One
    # transient failure is `fail_with=[(503, b"{}", {})]`; a rate limit that names its
    # delay is `[(429, b"{}", {"Retry-After": "2"})]`.
    fail_with: list[tuple[int, bytes, dict[str, str]]] = field(default_factory=list)
    # A refusal aimed at `messages.send` and at nothing else, as `(status, body)`.
    #
    # `fail_with` cannot express this and must not be used for it: that queue is
    # consumed by whichever call arrives first, and the first call of a send is the
    # *token refresh*. An entry meant for the send would answer the refresh instead,
    # the send would never happen, and the test would pass while proving nothing about
    # how a refusal from Gmail is handled. The RFC822 is still decoded and checked
    # before the refusal is returned, for the same reason `_send` does it at all.
    refuse_send_with: tuple[int, bytes] | None = None
    # Set to answer a send with a socket-level failure instead of a result, for the
    # "unknown outcome" path of spec 6.3(b).
    timeout_on_send: bool = False
    # And the half of that path that decides everything: a lost answer is not a lost
    # message. With this set the mail really did arrive and only the response fell on
    # the floor, so the reconciliation must find it -- which is the case the previous system gets
    # wrong in production. Off by default: `timeout_on_send` alone means nothing was
    # delivered, and the two together are the pair the reconciliation has to separate.
    deliver_on_timeout: bool = False
    # Whether this Gmail replaces the `Message-ID` the client supplied. Off by default
    # because that is what the verification note currently models; it is the switch that
    # turns `UNVERIFIED` into the `NO` branch, so the reconciliation can be tested
    # against the assumption being *wrong* rather than only against it holding. See
    # `docs/superpowers/notes/2026-08-20-gmail-message-id-verification.md`.
    rewrites_message_id: bool = False

    # ---- the seam ---------------------------------------------------------------

    def __call__(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        body: bytes | None,
        *,
        max_bytes: int | None = None,
    ) -> tuple[int, bytes, dict[str, str]]:
        parsed = urlparse(url)
        recorded = RecordedRequest(
            method=method,
            host=parsed.netloc,
            path=parsed.path,
            query=parse_qs(parsed.query),
            body=body,
        )
        self.requests.append(recorded)

        if self.fail_with:
            return self.fail_with.pop(0)

        if parsed.netloc == TOKEN_HOST:
            return self._token(recorded)
        if recorded.is_messages_send:
            return self._send()
        if recorded.is_messages_list:
            return self._list(recorded)
        if "/threads/" in parsed.path:
            return self._thread(parsed.path.rsplit("/", 1)[-1])
        if "/attachments/" in parsed.path:
            # Before the `/messages/` branch, which would otherwise read the attachment
            # id as a message id and answer 404 -- the same trap the real path shape
            # sets, since an attachment URL contains both segments.
            message_id, _, attachment_id = parsed.path.partition("/attachments/")
            return self._attachment(message_id.rsplit("/", 1)[-1], attachment_id)
        if "/messages/" in parsed.path:
            return self._message(parsed.path.rsplit("/", 1)[-1])
        return self._not_found()

    # ---- endpoints --------------------------------------------------------------

    def _attachment(self, message_id: str, attachment_id: str) -> tuple[int, bytes, dict[str, str]]:
        """`users.messages.attachments.get`: the bytes, base64url, inside JSON.

        The ids are matched rather than trusted -- `att-N` is handed out by
        `_message_payload` in the order the attachments were declared, so asking for
        `att-2` really does get the second one. A caller that invents an id gets the 404
        Gmail gives, which is what makes "the id came from the payload" testable.
        """
        message = self.messages.get(message_id)
        if message is None:
            return self._not_found()
        try:
            index = int(attachment_id.removeprefix("att-"))
        except ValueError:
            return self._not_found()
        if not 1 <= index <= len(message.attachments):
            return self._not_found()
        attachment = message.attachments[index - 1]
        content = attachment.get("content") or b""
        payload = {
            "size": attachment.get("size", len(content)),
            "data": base64.urlsafe_b64encode(content).decode().rstrip("="),
        }
        return 200, json.dumps(payload).encode(), {}

    @staticmethod
    def _not_found() -> tuple[int, bytes, dict[str, str]]:
        return 404, json.dumps({"error": {"status": "NOT_FOUND"}}).encode(), {}

    @staticmethod
    def _invalid_grant(description: str) -> tuple[int, bytes, dict[str, str]]:
        """The exact shape Google returns for a grant it will not honour -- a bare
        string under "error", not an object. That is the dialect a parser reading only
        the Gmail API's shape loses, and losing it is the previous system's defect."""
        return (
            400,
            json.dumps({"error": "invalid_grant", "error_description": description}).encode(),
            {},
        )

    def _check_authorization_code(
        self, fields: dict[str, list[str]]
    ) -> tuple[int, bytes, dict[str, str]] | None:
        """The two checks Google performs on a code exchange that a fake ignoring the
        request body cannot perform at all -- and which are precisely the two an
        attacker has to defeat. `None` means the exchange may proceed."""
        if self.expected_code_challenge is not None:
            verifier = (fields.get("code_verifier") or [""])[0]
            digest = hashlib.sha256(verifier.encode()).digest()
            challenge = base64.urlsafe_b64encode(digest).decode().rstrip("=")
            if challenge != self.expected_code_challenge:
                return self._invalid_grant("code_verifier does not match code_challenge")
        code = (fields.get("code") or [""])[0]
        if self.single_use_codes and code in self.used_codes:
            return self._invalid_grant("Code was already redeemed.")
        self.used_codes.add(code)
        return None

    def _token(self, recorded: RecordedRequest) -> tuple[int, bytes, dict[str, str]]:
        self.token_requests += 1
        fields = parse_qs((recorded.body or b"").decode())
        if fields.get("grant_type") == ["authorization_code"]:
            refusal = self._check_authorization_code(fields)
            if refusal is not None:
                return refusal
        if self.revoked:
            return self._invalid_grant("Token has been expired or revoked.")
        payload: dict[str, Any] = {
            "access_token": self.access_token,
            "expires_in": self.expires_in,
            "token_type": "Bearer",
        }
        if self.granted_scopes:
            payload["scope"] = " ".join(self.granted_scopes)
        if not self.omit_refresh_token:
            payload["refresh_token"] = self.refresh_token
        if self.id_token:
            payload["id_token"] = self.id_token
        return 200, json.dumps(payload).encode(), {}

    def _send(self) -> tuple[int, bytes, dict[str, str]]:
        raw = self._decode_raw(self.requests[-1].body)
        if self.refuse_send_with is not None:
            # Gmail looked at the message and said no. Nothing is registered, which is
            # the whole difference between this and `timeout_on_send`.
            status, body = self.refuse_send_with
            return status, body, {}
        if self.timeout_on_send:
            if self.deliver_on_timeout:
                self._register_sent(raw)
            # The synthetic status `urllib_call` produces when no HTTP response was
            # ever received. This is the "we do not know" case of spec 6.3(b).
            return 599, json.dumps({"error": {"message": "timed out"}}).encode(), {}
        message = self._register_sent(raw)
        return (
            200,
            json.dumps({"id": message.id, "threadId": message.thread_id}).encode(),
            {},
        )

    @staticmethod
    def _decode_raw(body: bytes | None) -> EmailMessage:
        """The RFC822 out of `{"raw": base64url(...)}`, parsed.

        Parsed rather than regex-scanned because the builder under test folds long
        headers and RFC 2047-encodes non-ASCII ones, and a fake that read the bytes
        naively would disagree with Gmail about a subject with an accent in it -- which
        is exactly the class of defect this slice exists to close.
        """
        payload = json.loads((body or b"{}").decode())
        raw = payload.get("raw")
        if not isinstance(raw, str):
            raise AssertionError("messages.send was called without a base64url `raw` field")
        decoded = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
        parsed = message_from_bytes(decoded, _class=EmailMessage, policy=default_policy)
        assert isinstance(parsed, EmailMessage)
        return parsed

    def _register_sent(self, raw: EmailMessage) -> FakeMessage:
        """Store the sent message in the mailbox, **keeping the Message-ID we supplied**.

        That is a modelled assumption, not a measured fact: see
        `docs/superpowers/notes/2026-08-20-gmail-message-id-verification.md`, which
        records the check as UNVERIFIED, and `test_gmail_message_id_contract.py`, which
        fails the moment that note records `NO`. Registering the message at all is what
        makes B2-6's reconciliation tests meaningful -- a fake that could never lose a
        send would make the one behaviour they are about unobservable.
        """
        message_id = str(raw["Message-ID"] or "")
        if not message_id:
            raise AssertionError(
                "messages.send was called with an RFC822 carrying no Message-ID. Spec 6.2 "
                "rule 1: the id is minted before the call, and the reconciliation of spec "
                "6.3 has nothing to look the message up by without it."
            )
        gmail_id = f"sent-{len([r for r in self.requests if r.is_messages_send])}"
        if self.rewrites_message_id:
            # The `NO` branch of the note, made observable. Many providers rewrite
            # `Message-ID` so the right-hand side names their own infrastructure, and
            # whether Gmail's API path does is the one thing nobody has measured. With
            # this set the message really is in the mailbox and the `rfc822msgid` lookup
            # cannot find it -- which is precisely the shape of "reported `fallito` for
            # mail sitting in Sent".
            message_id = f"<{gmail_id}@mail.gmail.com>"
        message = FakeMessage(
            id=gmail_id,
            thread_id=f"thread-{gmail_id}",
            headers={
                name: (message_id if name == "Message-ID" else str(raw[name] or ""))
                # `In-Reply-To`/`References` are carried because Gmail carries them, and
                # because they are how a reminder stays attached to the invoice above it
                # (spec 6.2 rule 2). A fake that dropped them would make a detached
                # reminder -- the defect -- indistinguishable from a threaded one.
                for name in (
                    "From",
                    "To",
                    "Cc",
                    "Subject",
                    "Message-ID",
                    "In-Reply-To",
                    "References",
                )
                if raw[name] is not None
            },
            body_text=self._body_text(raw),
            # Gmail stamps its own arrival time, and it is what the fallback match of
            # spec 6.3 compares against, so it has to be a real instant rather than 0.
            internal_date_ms=int(time.time() * 1000),
            label_ids=["SENT"],
        )
        self.messages[message.id] = message
        return message

    @staticmethod
    def _body_text(raw: EmailMessage) -> str:
        """The decoded body, verbatim -- CRLF line endings and trailing newline included.

        Not tidied, because Gmail does not tidy it: `format=full` hands back the body as
        it travelled, and a fake that stripped the wire's own line endings would let a
        caller compare against `\\n`-terminated text and never notice that what actually
        reaches a recipient is `\\r\\n`.
        """
        body = raw.get_body(preferencelist=("plain",))
        if body is None:
            return ""
        content = body.get_content()
        assert isinstance(content, str)
        return content

    def _list(self, recorded: RecordedRequest) -> tuple[int, bytes, dict[str, str]]:
        """Matches on the `q` the way Gmail does for the operators this slice uses:
        `from:`, `to:`, `after:` and `rfc822msgid:`. Deliberately not a full Gmail
        query engine -- but deliberately *not* a stub that ignores `q` either, because
        a fake that returns everything regardless would make the relevance test
        vacuous."""
        query = recorded.q or ""
        hits = [message for message in self.messages.values() if matches(message, query)]
        hits.sort(key=lambda message: message.internal_date_ms)
        return (
            200,
            json.dumps(
                {
                    "messages": [{"id": m.id, "threadId": m.thread_id} for m in hits],
                    "resultSizeEstimate": len(hits),
                }
            ).encode(),
            {},
        )

    def _thread(self, thread_id: str) -> tuple[int, bytes, dict[str, str]]:
        members = [m for m in self.messages.values() if m.thread_id == thread_id]
        if not members:
            return self._not_found()
        members.sort(key=lambda message: message.internal_date_ms)
        return (
            200,
            json.dumps({"id": thread_id, "messages": [self._as_api(m) for m in members]}).encode(),
            {},
        )

    def _message(self, message_id: str) -> tuple[int, bytes, dict[str, str]]:
        message = self.messages.get(message_id)
        if message is None:
            return self._not_found()
        return 200, json.dumps(self._as_api(message)).encode(), {}

    # ---- shaping ----------------------------------------------------------------

    def _as_api(self, message: FakeMessage) -> dict[str, Any]:
        """Gmail's own `format=full` shape: base64url parts, headers as a list of
        name/value pairs, `multipart/alternative` when both a text and an HTML part
        exist. The parser under test has to cope with the real shape."""

        def b64(text: str) -> str:
            return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")

        parts: list[dict[str, Any]] = []
        if message.body_text:
            parts.append(
                {
                    "mimeType": "text/plain",
                    "body": {"data": b64(message.body_text), "size": len(message.body_text)},
                }
            )
        if message.body_html:
            parts.append(
                {
                    "mimeType": "text/html",
                    "body": {"data": b64(message.body_html), "size": len(message.body_html)},
                }
            )
        for index, attachment in enumerate(message.attachments, start=1):
            parts.append(
                {
                    "mimeType": attachment["mime"],
                    "filename": attachment["filename"],
                    # A distinct id per attachment: Gmail gives each one its own, and a
                    # fake that hands out "att-1" for all of them would let a parser
                    # that overwrites by id look correct.
                    "body": {"attachmentId": f"att-{index}", "size": attachment["size"]},
                }
            )
        return {
            "id": message.id,
            "threadId": message.thread_id,
            "labelIds": message.label_ids,
            "snippet": (message.body_text or message.body_html)[:120],
            "internalDate": str(message.internal_date_ms),
            "payload": {
                "mimeType": self._payload_mime_type(message, parts),
                "headers": [{"name": k, "value": v} for k, v in message.headers.items()],
                "parts": parts if len(parts) > 1 else [],
                "body": parts[0]["body"] if len(parts) == 1 else {"size": 0},
            },
        }

    @staticmethod
    def _payload_mime_type(message: FakeMessage, parts: list[dict[str, Any]]) -> str:
        """The real distinction, not a stand-in for it: Gmail sends
        `multipart/alternative` for a text+HTML message and `multipart/mixed` only
        once an attachment is involved. A parser that walks `parts` on `mixed` alone
        would drop every body in the mailbox, and a fake that always said `mixed`
        would never catch it."""
        if message.attachments:
            return "multipart/mixed"
        if len(parts) > 1:
            return "multipart/alternative"
        if parts:
            mime_type = parts[0]["mimeType"]
            assert isinstance(mime_type, str)
            return mime_type
        return "text/plain"
