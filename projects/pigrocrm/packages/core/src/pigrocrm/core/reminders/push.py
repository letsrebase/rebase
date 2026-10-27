"""Reminders as web push notifications (spec 2026-10-26 §6.1, REB-738).

**The transport is `pywebpush`**: the RFC 8291 payload encryption and the RFC 8292 VAPID
signature, which are the two things nobody should write twice. This module is the seam
around it, the way `mail.py` is the seam around Resend: `PushSender` is what the channel
calls, `WebPushSender` is the one that talks to a push service, `RecordingPushSender` is
the one a test reads, and nothing past `send` raises.

**The server's identity is one VAPID key pair per environment**, kept with the other
secrets (`Settings.vapid_private_key`, `vapid_subject`). Only the private half is
configured: the public half is derived from it (`vapid_public_key`), which is what the app
hands the browser when it subscribes, so a pair that does not match cannot be configured.
No key, no push: `push_sender_from_settings` answers `None`, the settings page says push is
not available on this installation, and a reminder is never held back by it.

**What the channel does** (`PushChannel`): a notification per reminder, at once, to every
browser the person subscribed from, outside their quiet hours. A browser announces what
reached the person's list after it subscribed, and no longer than a push service would have
held it (`TTL_SECONDS`) ago: never the backlog, so a person who turns push on with thirty
reminders open, or on again for a type, is not sent thirty notifications. A reminder is
recorded as delivered when at least one browser's push service accepted it, a
subscription the push service reports gone (404 or 410) is deleted with the ledger writes,
and any other failure writes nothing, so the next pass tries again (§8.3).

**The endpoint is a URL this server calls.** Only a browser vendor's push service is accepted
when it is stored (`push_subscriptions.py`); the sender resolves its host before each send
and refuses one that does not resolve to public addresses, never follows a redirect, sends
nothing but the encrypted payload, and gives the whole phase `PHASE_SECONDS`.
"""

from __future__ import annotations

import base64
import ipaddress
import json
import logging
import socket
import time
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal, Protocol
from urllib.parse import urlsplit
from uuid import UUID
from zoneinfo import ZoneInfo

import requests
from cryptography.hazmat.primitives import serialization
from py_vapid import Vapid  # type: ignore[import-untyped]
from pywebpush import WebPushException, webpush  # type: ignore[import-untyped]
from sqlalchemy import select
from sqlalchemy.orm import Session

from pigrocrm.core.config import Settings
from pigrocrm.core.reminders.dispatch import (
    Accepted,
    Candidate,
    Channel,
    ChannelResult,
    PassContext,
    Prepared,
)
from pigrocrm.core.reminders.links import record_path, url_prefix
from pigrocrm.core.reminders.push_subscriptions import PushSubscription

logger = logging.getLogger(__name__)

PUSH = "push"
# How long a push service holds a message for a browser that is off: a day. Zero, which is
# the library's default, tells the service to drop it unless the browser is connected at
# that instant, and a phone in a pocket is not.
TTL_SECONDS = 24 * 60 * 60
SEND_TIMEOUT_SECONDS = 10
# How long one pass may spend sending push, in all: the pass holds the space's lock while it
# sends, and a slow push service must not make it hold it for everybody else's turn. What is
# not sent by then stays unrecorded and goes in the next pass.
PHASE_SECONDS = 120
# What a notification's two texts may weigh in UTF-8: far under the ~4 KB a push service takes,
# with room for the URL, the tag and the encryption's own overhead.
TITLE_BYTES = 300
BODY_BYTES = 1000

Outcome = Literal["accepted", "gone", "failed"]


@dataclass(frozen=True)
class PushTarget:
    """A subscription as the sender needs it: plain values, no session."""

    endpoint: str
    p256dh: str
    auth: str
    created_at: datetime


def _clip(text: str, limit: int) -> str:
    """`text` cut to `limit` bytes of UTF-8 on a character boundary, with an ellipsis."""
    raw = text.encode()
    if len(raw) <= limit:
        return text
    return raw[: limit - 3].decode(errors="ignore") + "..."


@dataclass(frozen=True)
class PushMessage:
    """What the service worker shows: a title, a body, the path that opens the record, a
    tag (the reminder's key, so a reminder that climbs replaces its own notification) and
    whether the person's settings call it urgent."""

    title: str
    body: str
    url: str
    tag: str
    urgent: bool

    def payload(self) -> str:
        """The JSON the worker reads. A push service takes about 4 KB, and a title is text
        somebody typed (a timer's description is up to 2000 characters), so the two texts are
        cut to a byte budget here, where the size is known, and not in the worker."""
        return json.dumps(
            {
                "title": _clip(self.title, TITLE_BYTES),
                "body": _clip(self.body, BODY_BYTES),
                "url": self.url,
                "tag": self.tag,
            },
            ensure_ascii=False,
        )


class PushSender(Protocol):
    def send(self, target: PushTarget, message: PushMessage) -> Outcome:
        """`accepted` when the push service took the message, `gone` when it says the
        subscription no longer exists, `failed` for everything else. Never raises."""
        ...


def resolves_publicly(endpoint: str) -> bool:
    """Whether the endpoint's host resolves, and only to public addresses. A name can be
    pointed at an address inside this network after it was stored; this is the check made
    at the moment of calling it."""
    host = urlsplit(endpoint).hostname
    if not host:
        return False
    try:
        # An address literal is its own answer: nothing to resolve.
        return ipaddress.ip_address(host).is_global
    except ValueError:
        pass
    try:
        found = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except OSError:
        return False
    return bool(found) and all(
        ipaddress.ip_address(str(info[4][0]).split("%")[0]).is_global for info in found
    )


class WebPushSender:
    def __init__(
        self,
        private_key: str,
        subject: str,
        *,
        guard: Callable[[str], bool] = resolves_publicly,
        timeout: float = SEND_TIMEOUT_SECONDS,
    ) -> None:
        self.vapid = Vapid.from_string(private_key)
        self.subject = subject
        self.guard = guard
        self.timeout = timeout

    def send(self, target: PushTarget, message: PushMessage) -> Outcome:
        if not self.guard(target.endpoint):
            logger.warning("push non inviato: l'indirizzo del servizio non è pubblico")
            return "failed"
        # A session of its own with redirects refused: a push service answers 201 and
        # never sends the caller elsewhere, so a redirect is somebody else's doing.
        session = requests.Session()
        session.max_redirects = 0
        try:
            webpush(
                {
                    "endpoint": target.endpoint,
                    "keys": {"p256dh": target.p256dh, "auth": target.auth},
                },
                message.payload(),
                vapid_private_key=self.vapid,
                vapid_claims={"sub": self.subject},
                ttl=TTL_SECONDS,
                timeout=self.timeout,
                headers={"Urgency": "high" if message.urgent else "normal"},
                requests_session=session,
            )
        except WebPushException as exc:
            status = exc.response.status_code if exc.response is not None else None
            if status in (404, 410):
                return "gone"
            # The status and nothing else: the endpoint is a capability, and the
            # exception's text quotes it.
            logger.warning("push non inviato: il servizio ha risposto %s", status)
            return "failed"
        except Exception:  # noqa: BLE001 - the seam's contract is "never raises"
            logger.warning("push non inviato: il servizio non ha risposto")
            return "failed"
        finally:
            session.close()
        return "accepted"


class RecordingPushSender:
    """Keeps every message in a list. For tests: `outcomes` is what `send` answers by
    endpoint (`accepted` for one not in it)."""

    def __init__(self) -> None:
        self.sent: list[tuple[PushTarget, PushMessage]] = []
        self.outcomes: dict[str, Outcome] = {}

    def send(self, target: PushTarget, message: PushMessage) -> Outcome:
        self.sent.append((target, message))
        return self.outcomes.get(target.endpoint, "accepted")


# ---- the server's key pair ---------------------------------------------------------


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def vapid_public_key(private_key: str) -> str:
    """The public half of a configured private key, as the browser's
    `applicationServerKey` wants it: the uncompressed P-256 point, base64url."""
    public = Vapid.from_string(private_key).public_key
    assert public is not None  # a loaded private key always has its public half
    return _b64url(
        public.public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
        )
    )


def generate_vapid_keys() -> tuple[str, str]:
    """`(private, public)` of a new pair, both base64url: what an operator puts in
    `PIGROCRM_VAPID_PRIVATE_KEY`, and what the browser will be handed."""
    vapid = Vapid()
    vapid.generate_keys()
    private = vapid.private_key
    assert private is not None
    raw = private.private_numbers().private_value.to_bytes(32, "big")
    key = _b64url(raw)
    return key, vapid_public_key(key)


def push_public_key(settings: Settings) -> str | None:
    """The key the browser subscribes with, or `None` when this installation has no valid
    private key and push is therefore not available."""
    if not settings.vapid_private_key:
        return None
    try:
        return vapid_public_key(settings.vapid_private_key)
    except Exception:  # noqa: BLE001 - a malformed key is "not configured", never a 500
        logger.warning("push non disponibile: la chiave VAPID privata non è valida")
        return None


def push_sender_from_settings(settings: Settings) -> PushSender | None:
    if push_public_key(settings) is None:
        return None
    return WebPushSender(settings.vapid_private_key, settings.vapid_subject)


# ---- the channel -------------------------------------------------------------------


class PushChannel(Channel):
    name = PUSH

    def __init__(self, sender: PushSender | None) -> None:
        self.sender = sender

    def prepare(
        self, session: Session, context: PassContext, candidates: Sequence[Candidate]
    ) -> Prepared:
        sender = self.sender
        if sender is None:
            return Prepared(PUSH, 0, ChannelResult)
        users = {candidate.reminder.user_id for candidate in candidates}
        targets: dict[UUID, list[PushTarget]] = defaultdict(list)
        if users:
            for row in session.scalars(
                select(PushSubscription)
                .where(PushSubscription.user_id.in_(users))
                .order_by(PushSubscription.created_at, PushSubscription.id)
            ):
                targets[row.user_id].append(
                    PushTarget(row.endpoint, row.p256dh, row.auth, row.created_at)
                )
        prefix = url_prefix(context.public_url)

        planned: list[tuple[Accepted, PushMessage, list[PushTarget]]] = []
        for candidate in candidates:
            reminder = candidate.reminder
            if PUSH not in candidate.effective.channels or candidate.quiet:
                continue
            identity = candidate.identity(PUSH)
            if context.covered(identity, reminder.severity):
                continue
            # A browser announces what reached the list after it subscribed, and a reminder
            # that has been there longer than a push service would hold it is not news. One
            # listed inside the person's quiet hours was being held, not left behind, so
            # a Friday night's reminder is still news on Monday morning.
            listed_local = candidate.listed_at.astimezone(ZoneInfo(context.settings.timezone))
            held = candidate.general.quiet_at(listed_local.weekday(), listed_local.time())
            if not held and context.now - candidate.listed_at > timedelta(seconds=TTL_SECONDS):
                continue
            reachable = [
                t for t in targets[reminder.user_id] if t.created_at <= candidate.listed_at
            ]
            if not reachable:
                continue
            message = PushMessage(
                title=reminder.title,
                body=reminder.detail,
                url=f"{prefix}{record_path(reminder.entity, reminder.entity_id)}",
                tag=reminder.key,
                urgent=candidate.effective.is_urgent(reminder.severity),
            )
            planned.append((Accepted(identity, reminder.severity), message, reachable))

        def send() -> ChannelResult:
            result = ChannelResult()
            gone: set[str] = set()
            deadline = time.monotonic() + PHASE_SECONDS
            for accepted, message, reachable in planned:
                took = False
                for target in reachable:
                    if target.endpoint in gone:
                        continue
                    if time.monotonic() > deadline:
                        logger.warning(
                            "push: il tempo della passata è finito, il resto alla prossima"
                        )
                        # What browsers already took of this reminder is recorded: sending
                        # it to them again would sound it twice.
                        if took:
                            result.accepted.append(accepted)
                        return result
                    outcome = sender.send(target, message)
                    if outcome == "accepted":
                        took = True
                        result.sent[PUSH] = result.sent.get(PUSH, 0) + 1
                    elif outcome == "gone":
                        gone.add(target.endpoint)
                        result.gone_endpoints.append(target.endpoint)
                if took:
                    result.accepted.append(accepted)
            return result

        return Prepared(PUSH, sum(len(reachable) for _, _, reachable in planned), send)
