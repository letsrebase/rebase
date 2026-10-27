"""REB-738: the push transport, and the subscriptions it sends to.

`WebPushSender` is run for real against a push service of this module's own, a local HTTP
server that keeps what it is handed: the body is decrypted with the subscription's private
key (RFC 8291), the VAPID header is verified against the key the app hands the browser
(RFC 8292), and the status codes a push service answers are mapped. The headless Chrome
run in the pull request is the other half: a real browser, a real push service.
"""

from __future__ import annotations

import base64
import json
import os
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from uuid import uuid4

import http_ece
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.auth.models import User
from pigrocrm.core.config import Settings
from pigrocrm.core.errors import Conflict
from pigrocrm.core.reminders.links import (
    WITHOUT_PAGE,
    absolute,
    known_entities,
    record_path,
    url_prefix,
)
from pigrocrm.core.reminders.push import (
    PushMessage,
    PushTarget,
    WebPushSender,
    generate_vapid_keys,
    push_public_key,
    push_sender_from_settings,
    resolves_publicly,
    vapid_public_key,
)
from pigrocrm.core.reminders.push_subscriptions import (
    PushSubscription,
    PushSubscriptionService,
    PushSubscriptionWrite,
    accepts_endpoint,
)

SUBJECT = "mailto:ops@example.test"


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


@dataclass
class Received:
    path: str
    headers: dict[str, str]
    body: bytes


@dataclass
class PushService:
    """A push service: answers the status it is told to and keeps every request."""

    url: str
    status: int = 201
    delay: float = 0.0
    requests: list[Received] = field(default_factory=list)


@pytest.fixture
def service() -> Iterator[PushService]:
    holder: PushService

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802 - the stdlib's name
            length = int(self.headers.get("Content-Length", 0))
            holder.requests.append(
                Received(self.path, dict(self.headers.items()), self.rfile.read(length))
            )
            if holder.delay:
                threading.Event().wait(holder.delay)
            if self.path.startswith("/redirect"):
                self.send_response(302)
                self.send_header("Location", "/secret")
            else:
                self.send_response(holder.status)
            self.end_headers()

        def log_message(self, *args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    holder = PushService(url=f"http://127.0.0.1:{server.server_port}")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield holder
    finally:
        server.shutdown()
        server.server_close()


@dataclass
class Browser:
    """What a browser holds for one subscription: the private key and the auth secret
    that decrypt what is sent to it."""

    key: ec.EllipticCurvePrivateKey
    auth: bytes

    @classmethod
    def new(cls) -> Browser:
        return cls(ec.generate_private_key(ec.SECP256R1()), os.urandom(16))

    def target(self, endpoint: str) -> PushTarget:
        public = self.key.public_key().public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
        )
        return PushTarget(endpoint, _b64url(public), _b64url(self.auth), datetime.now(UTC))

    def read(self, body: bytes) -> dict[str, str]:
        clear = http_ece.decrypt(body, private_key=self.key, auth_secret=self.auth)
        return json.loads(clear)


MESSAGE = PushMessage(
    title="Fattura scaduta: n. 12",
    body="Scaduta da 31 giorni · Acme S.r.l.",
    url="/acme/app/invoices/1",
    tag="invoice_overdue:1",
    urgent=True,
)


def _sender(private: str, *, timeout: float = 5) -> WebPushSender:
    return WebPushSender(private, SUBJECT, guard=lambda _: True, timeout=timeout)


# -- the transport, against a push service -------------------------------------------------------


def test_the_payload_reaches_the_browser_encrypted_and_signed_by_the_servers_key(
    service: PushService,
) -> None:
    private, public = generate_vapid_keys()
    browser = Browser.new()

    outcome = _sender(private).send(browser.target(f"{service.url}/push/abc"), MESSAGE)

    assert outcome == "accepted"
    received = service.requests[0]
    assert received.path == "/push/abc"
    # Only the browser can read it: what travelled is not the text.
    assert b"Fattura" not in received.body
    assert browser.read(received.body) == {
        "title": "Fattura scaduta: n. 12",
        "body": "Scaduta da 31 giorni · Acme S.r.l.",
        "url": "/acme/app/invoices/1",
        "tag": "invoice_overdue:1",
    }
    headers = {name.lower(): value for name, value in received.headers.items()}
    assert headers["content-encoding"] == "aes128gcm"
    assert headers["ttl"] == "86400", "held a day for a phone that is off, never dropped at once"
    assert headers["urgency"] == "high"
    # The VAPID header: a JWT for this push service, signed by the key the browser was
    # handed when it subscribed, which is the public half of the configured private one.
    scheme, _, rest = headers["authorization"].partition(" ")
    assert scheme == "vapid"
    fields = dict(part.strip().split("=", 1) for part in rest.split(","))
    assert fields["k"] == public
    public_key = ec.EllipticCurvePublicKey.from_encoded_point(
        ec.SECP256R1(), base64.urlsafe_b64decode(public + "=" * (-len(public) % 4))
    )
    claims = jwt.decode(
        fields["t"],
        public_key,
        algorithms=["ES256"],
        audience=service.url,
        options={"verify_exp": True},
    )
    assert claims["sub"] == SUBJECT


def test_a_message_that_is_not_urgent_is_sent_with_normal_urgency(service: PushService) -> None:
    private, _ = generate_vapid_keys()
    quiet = PushMessage("t", "b", "/app", "k", urgent=False)

    _sender(private).send(Browser.new().target(f"{service.url}/push/x"), quiet)

    headers = {name.lower(): value for name, value in service.requests[0].headers.items()}
    assert headers["urgency"] == "normal"


@pytest.mark.parametrize(
    ("status", "outcome"),
    [(404, "gone"), (410, "gone"), (500, "failed"), (403, "failed"), (429, "failed")],
)
def test_the_status_a_push_service_answers_says_whether_to_keep_the_subscription(
    service: PushService, status: int, outcome: str
) -> None:
    private, _ = generate_vapid_keys()
    service.status = status

    assert _sender(private).send(Browser.new().target(f"{service.url}/push/x"), MESSAGE) == outcome


def test_a_redirect_is_never_followed(service: PushService) -> None:
    private, _ = generate_vapid_keys()

    outcome = _sender(private).send(Browser.new().target(f"{service.url}/redirect"), MESSAGE)

    assert outcome == "failed"
    assert [r.path for r in service.requests] == ["/redirect"], "/secret was never asked for"


def test_a_push_service_that_does_not_answer_in_time_is_a_failure_not_a_hang(
    service: PushService,
) -> None:
    private, _ = generate_vapid_keys()
    service.delay = 1.5

    outcome = _sender(private, timeout=0.2).send(
        Browser.new().target(f"{service.url}/slow"), MESSAGE
    )

    assert outcome == "failed"


def test_an_endpoint_that_resolves_to_this_machine_is_not_called_at_all(
    service: PushService,
) -> None:
    private, _ = generate_vapid_keys()
    default_guard = WebPushSender(private, SUBJECT)  # the real guard

    outcome = default_guard.send(Browser.new().target(f"{service.url}/push/x"), MESSAGE)

    assert outcome == "failed"
    assert service.requests == []


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://localhost/x",
        "https://127.0.0.1/x",
        "https://[::1]/x",
        "https://10.0.0.5/x",
        "https:///x",
    ],
)
def test_the_guard_refuses_what_is_not_the_public_internet(endpoint: str) -> None:
    assert resolves_publicly(endpoint) is False


# -- the server's key pair -----------------------------------------------------------------------


def test_the_public_key_is_the_one_derived_from_the_private_one() -> None:
    private, public = generate_vapid_keys()

    assert vapid_public_key(private) == public
    raw = base64.urlsafe_b64decode(public + "=" * (-len(public) % 4))
    assert len(raw) == 65 and raw[0] == 4, "an uncompressed P-256 point, what the browser wants"


def test_push_is_not_available_without_a_valid_key() -> None:
    assert push_public_key(Settings(_env_file=None)) is None  # type: ignore[call-arg]
    assert push_sender_from_settings(Settings(_env_file=None)) is None  # type: ignore[call-arg]
    broken = Settings(vapid_private_key="not a key", _env_file=None)  # type: ignore[call-arg]
    assert push_public_key(broken) is None, "a malformed key is not configured, never a crash"
    assert push_sender_from_settings(broken) is None


def test_a_configured_key_gives_a_sender_and_the_key_the_browser_subscribes_with() -> None:
    private, public = generate_vapid_keys()
    settings = Settings(vapid_private_key=private, _env_file=None)  # type: ignore[call-arg]

    assert push_public_key(settings) == public
    assert push_sender_from_settings(settings) is not None
    assert private not in repr(settings), "a secret never reaches a log"


# -- which endpoints are accepted ----------------------------------------------------------------


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://fcm.googleapis.com/fcm/send/abc:APA91b",
        "https://updates.push.services.mozilla.com/wpush/v2/gAAAA",
        "https://web.push.apple.com/QQ",
        "https://wns2-par02p.notify.windows.com/w/?token=abc",
    ],
)
def test_the_endpoints_the_browser_vendors_issue_are_accepted(endpoint: str) -> None:
    assert accepts_endpoint(endpoint)


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://fcm.googleapis.com/fcm/send/abc",
        "https://127.0.0.1/x",
        "https://192.168.1.10/x",
        "https://localhost/x",
        "https://intranet/x",
        "https://printer.local/x",
        "https://metadata.internal/x",
        "https://user:pass@fcm.googleapis.com/x",
        "file:///etc/passwd",
        # Not the vendors' own, though public and HTTPS: the server would call anything.
        "https://push.example.test/a",
        "https://attacker.example/fcm.googleapis.com",
        "https://fcm.googleapis.com.attacker.example/x",
        "https://notfcm.googleapis.com/x",
        "https://" + "a" * 3000 + ".example.test/x",
        "not a url",
    ],
)
def test_an_endpoint_no_push_service_would_issue_is_refused(endpoint: str) -> None:
    assert not accepts_endpoint(endpoint)


# -- the subscription service --------------------------------------------------------------------


def _write(endpoint: str = "https://fcm.googleapis.com/fcm/send/abc") -> PushSubscriptionWrite:
    target = Browser.new().target(endpoint)
    return PushSubscriptionWrite(endpoint=endpoint, p256dh=target.p256dh, auth=target.auth)


def _user(session: Session, ruolo: str = "collaboratore") -> tuple[User, Actor]:
    user = User(email=f"u-{uuid4()}@example.test", password_hash="x", nome="Persona", ruolo=ruolo)
    session.add(user)
    session.flush()
    return user, Actor(id=user.id, type="user", role=user.ruolo)  # type: ignore[arg-type]


def test_a_person_subscribes_and_sees_their_browsers(db_session: Session) -> None:
    _, actor = _user(db_session)
    service = PushSubscriptionService(db_session)

    saved = service.subscribe(actor, _write(), "Mozilla/5.0 (Android) Chrome/130")

    assert saved.user_agent == "Mozilla/5.0 (Android) Chrome/130"
    assert [row.endpoint for row in service.mine(actor)] == [
        "https://fcm.googleapis.com/fcm/send/abc"
    ]


def test_subscribing_again_from_the_same_browser_refreshes_the_keys_and_adds_no_row(
    db_session: Session,
) -> None:
    _, actor = _user(db_session)
    service = PushSubscriptionService(db_session)
    first = service.subscribe(actor, _write())

    second = service.subscribe(actor, _write())

    assert second.id == first.id
    assert len(service.mine(actor)) == 1


def test_a_browser_somebody_else_subscribed_from_becomes_the_new_persons(
    db_session: Session,
) -> None:
    _, one = _user(db_session)
    _, other = _user(db_session)
    service = PushSubscriptionService(db_session)
    service.subscribe(one, _write())

    service.subscribe(other, _write())

    assert service.mine(one) == [], "their reminders never reach a screen the next person reads"
    assert len(service.mine(other)) == 1


def test_unsubscribing_removes_only_the_callers_own_and_twice_is_fine(db_session: Session) -> None:
    _, one = _user(db_session)
    _, other = _user(db_session)
    service = PushSubscriptionService(db_session)
    service.subscribe(one, _write("https://fcm.googleapis.com/fcm/send/one"))
    service.subscribe(other, _write("https://fcm.googleapis.com/fcm/send/other"))

    service.unsubscribe(one, "https://fcm.googleapis.com/fcm/send/other")
    assert len(service.mine(other)) == 1, "somebody else's is not theirs to remove"

    service.unsubscribe(one, "https://fcm.googleapis.com/fcm/send/one")
    service.unsubscribe(one, "https://fcm.googleapis.com/fcm/send/one")
    assert service.mine(one) == []
    assert db_session.scalars(select(PushSubscription)).all()


def test_a_browser_that_changes_hands_starts_over_for_its_new_person(db_session: Session) -> None:
    """Its age is what keeps a person from being sent the backlog (`push.py`): the
    previous person's age says nothing about the next one."""
    _, one = _user(db_session)
    _, other = _user(db_session)
    service = PushSubscriptionService(db_session)
    first = service.subscribe(one, _write())
    row = db_session.get(PushSubscription, first.id)
    assert row is not None
    row.created_at = datetime(2020, 1, 1, tzinfo=UTC)
    db_session.commit()

    again_same = service.subscribe(one, _write())
    assert again_same.created_at == datetime(2020, 1, 1, tzinfo=UTC), (
        "the same person keeps its age"
    )

    moved = service.subscribe(other, _write())
    assert moved.created_at > datetime(2020, 1, 1, tzinfo=UTC)


def test_a_person_keeps_at_most_ten_browsers_and_the_oldest_goes(db_session: Session) -> None:
    _, actor = _user(db_session)
    _, other = _user(db_session)
    service = PushSubscriptionService(db_session)
    service.subscribe(other, _write("https://fcm.googleapis.com/fcm/send/not-mine"))

    for number in range(12):
        service.subscribe(actor, _write(f"https://fcm.googleapis.com/fcm/send/b{number:02d}"))

    kept = {row.endpoint.rsplit("/", 1)[1] for row in service.mine(actor)}
    assert kept == {f"b{number:02d}" for number in range(2, 12)}
    assert len(service.mine(other)) == 1, "somebody else's are not counted or touched"


def test_a_job_has_no_browser(db_session: Session) -> None:
    with pytest.raises(Conflict):
        PushSubscriptionService(db_session).subscribe(Actor.system(), _write())


@pytest.mark.parametrize(
    "change",
    [
        {"endpoint": "http://fcm.googleapis.com/x"},
        {"p256dh": "AAAA"},
        {"p256dh": _b64url(b"\x03" + bytes(64))},
        {"auth": "AAAA"},
        {"auth": "***"},
    ],
)
def test_a_subscription_that_is_not_one_is_refused(change: dict[str, str]) -> None:
    good = _write()
    with pytest.raises(ValidationError):
        PushSubscriptionWrite(**{**good.model_dump(), **change})


# -- where a notification opens ------------------------------------------------------------------


def test_every_record_a_detector_names_has_a_path_or_is_listed_as_having_none() -> None:
    """The web's table (`features/reminders/links.ts`) and this one answer for the same
    entities: a detector that starts emitting a new one has to be added to both, or listed
    here as having no page."""
    import re
    from pathlib import Path

    detectors = Path(__file__).resolve().parents[1] / "src/pigrocrm/core/reminders/detectors"
    emitted = {
        match
        for path in detectors.glob("*.py")
        for match in re.findall(r'entity="([a-z_]+)"', path.read_text())
    }
    assert emitted, "the scan found the detectors"
    assert emitted <= known_entities(), sorted(emitted - known_entities())
    web = (
        Path(__file__).resolve().parents[3] / "apps/web/src/features/reminders/links.ts"
    ).read_text()
    for entity in known_entities() - WITHOUT_PAGE:
        assert re.search(rf"^\s+{entity}:", web, re.MULTILINE), f"{entity} has no row in links.ts"


def test_paths_open_the_record_inside_the_app() -> None:
    attivita = uuid4()
    assert record_path("attivita", attivita) == f"/app/activities?apri={attivita}"
    assert record_path("invoice", attivita) == f"/app/invoices/{attivita}"
    assert record_path("proforma", attivita) == f"/app/invoices/{attivita}"
    assert record_path("timer", attivita) == "/app/hours"
    assert record_path("job", attivita) == "/app/avvisi", "no page of its own: the list"
    assert record_path("something-new", attivita) == "/app/avvisi"


def test_links_stay_on_the_space_the_person_is_in() -> None:
    assert url_prefix("https://pigro.example.test/acme") == "/acme"
    assert url_prefix("https://pigro.example.test") == ""
    assert url_prefix("") == ""
    assert absolute("https://pigro.example.test/acme/", "/app/avvisi") == (
        "https://pigro.example.test/acme/app/avvisi"
    )


def test_a_long_title_is_cut_to_a_byte_budget_a_push_service_accepts() -> None:
    long = PushMessage(
        "é" * 2000, "😀" * 2000, "/acme/app/hours", "timer_running_long:1", urgent=True
    )

    payload = long.payload()

    assert len(payload.encode()) < 2000, "far under the ~4 KB a push service takes"
    decoded = json.loads(payload)
    assert decoded["title"].endswith("...") and decoded["body"].endswith("...")
    assert decoded["url"] == "/acme/app/hours" and decoded["tag"] == "timer_running_long:1"
    short = PushMessage("t", "b", "/u", "k", urgent=False)
    assert json.loads(short.payload())["title"] == "t", "a short text is untouched"
