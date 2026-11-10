"""IMAP and PEC mailboxes: connecting with a sealed password, TLS only, the health states and
the read of headers and BODYSTRUCTURE (REB-1015, § 2.2 of
`2026-11-09-incoming-documents-inbox-design.md`).

Every test talks to `fakes/fake_imap.py`, a server of this suite's own on the loopback
interface, over a real socket and a real TLS handshake. No test reaches a real account.
"""

import base64
import datetime as dt
import socket
from collections.abc import Iterator
from uuid import uuid4

import pytest
from fakes.fake_imap import (
    HOST,
    PASSWORD,
    USERNAME,
    FakeImap,
    Msg,
    Part,
    ServerState,
    loopback,
)
from sqlalchemy import select
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.auth.models import User
from pigrocrm.core.config import Settings
from pigrocrm.core.errors import (
    AgentForbidden,
    Conflict,
    NotFound,
    PermissionDenied,
    ValidationFailed,
)
from pigrocrm.core.mailboxes.credential import open_password
from pigrocrm.core.mailboxes.imap_client import (
    ImapFailure,
    ImapReader,
    ImapTarget,
    MessageHead,
    parse_bodystructure,
    validate_target,
)
from pigrocrm.core.mailboxes.imap_service import ImapMailboxService
from pigrocrm.core.mailboxes.imap_sync import ImapSyncService
from pigrocrm.core.mailboxes.models import Mailbox, MailboxPurpose
from pigrocrm.core.mailboxes.schemas import ImapMailboxCreate
from pigrocrm.core.reminders.push import NotPublicAddress, resolve_public
from pigrocrm.core.tenants.google import space_mailbox_token_key

KEY = base64.b64encode(b"m" * 32).decode()
NOW = dt.datetime(2026, 11, 10, 12, 0, tzinfo=dt.UTC)


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "jwt_secret": "x" * 32,
        "mailbox_token_key": KEY,
        "_env_file": None,
    }
    return Settings(**{**base, **overrides})  # type: ignore[arg-type]


@pytest.fixture
def server() -> Iterator[FakeImap]:
    fake = FakeImap(ServerState())
    fake.start()
    yield fake
    fake.stop()


@pytest.fixture
def starttls_server() -> Iterator[FakeImap]:
    fake = FakeImap(ServerState(mode="starttls"))
    fake.start()
    yield fake
    fake.stop()


def _user(session: Session, role: str = "admin") -> Actor:
    user = User(
        email=f"u-{uuid4().hex[:8]}@example.it",
        name="U",
        password_hash="x",
        role=role,
        active=True,
    )
    session.add(user)
    session.flush()
    return Actor(id=user.id, type="user", role=role)  # type: ignore[arg-type]


def _service(session: Session, fake: FakeImap, **settings: object) -> ImapMailboxService:
    return ImapMailboxService(
        session,
        settings=_settings(**settings),
        resolve=loopback,
        context=fake.client_context(),
    )


def _create(fake: FakeImap, **overrides: object) -> ImapMailboxCreate:
    data: dict[str, object] = {
        "address": USERNAME,
        "host": HOST,
        "port": fake.port,
        "security": "tls",
        "username": USERNAME,
        "password": PASSWORD,
        "purposes": ["billing"],
        "space_owned": True,
    }
    return ImapMailboxCreate(**{**data, **overrides})  # type: ignore[arg-type]


class _Sink:
    purpose = "billing"

    def __init__(self) -> None:
        self.seen: list[MessageHead] = []

    def accept(self, mailbox: Mailbox, heads: list[MessageHead]) -> None:
        self.seen.extend(heads)


def _sync(session: Session, fake: FakeImap, sinks: tuple[_Sink, ...] = ()) -> ImapSyncService:
    return ImapSyncService(
        session,
        settings=_settings(),
        sinks=sinks,
        resolve=loopback,
        context=fake.client_context(),
    )


# --- connecting -----------------------------------------------------------------------------


def test_connecting_stores_the_mailbox_with_a_sealed_password(
    db_session: Session, server: FakeImap
) -> None:
    admin = _user(db_session)

    read = _service(db_session, server).connect(admin, _create(server))

    assert (read.provider, read.health, read.space_owned, read.purposes) == (
        "imap",
        "active",
        True,
        ["billing"],
    )
    mailbox = db_session.get(Mailbox, read.id)
    assert mailbox is not None
    assert mailbox.password_ciphertext is not None
    assert PASSWORD.encode() not in mailbox.password_ciphertext
    assert open_password(mailbox, _settings()) == PASSWORD
    assert PASSWORD not in read.model_dump_json()
    assert server.state.logins == 1


def test_the_password_does_not_open_under_another_spaces_key(
    db_session: Session, server: FakeImap
) -> None:
    """A ciphertext copied into another space's database must not open there."""
    admin = _user(db_session)
    read = _service(
        db_session, server, mailbox_token_key=space_mailbox_token_key(KEY, "uno")
    ).connect(admin, _create(server))
    mailbox = db_session.get(Mailbox, read.id)
    assert mailbox is not None
    other = _settings(mailbox_token_key=space_mailbox_token_key(KEY, "due"))
    with pytest.raises(Conflict, match="PIGROCRM_MAILBOX_TOKEN_KEY"):
        open_password(mailbox, other)
    assert space_mailbox_token_key(KEY, "uno") != space_mailbox_token_key(KEY, "due") != KEY


def test_the_mailbox_key_is_its_own_and_empty_without_a_valid_root_key() -> None:
    assert space_mailbox_token_key("", "uno") == ""
    assert space_mailbox_token_key("not base64", "uno") == ""


def test_an_installation_with_no_key_refuses_before_it_reaches_the_server(
    db_session: Session, server: FakeImap
) -> None:
    with pytest.raises(Conflict, match="PIGROCRM_MAILBOX_TOKEN_KEY"):
        _service(db_session, server, mailbox_token_key="").connect(
            _user(db_session), _create(server)
        )
    assert server.state.logins == 0


def test_a_token_cannot_connect_change_or_disconnect_a_mailbox(
    db_session: Session, server: FakeImap
) -> None:
    admin = _user(db_session)
    service = _service(db_session, server)
    mailbox_id = service.connect(admin, _create(server)).id
    token = Actor(id=admin.id, type="mcp", role="admin", full_access=True)

    with pytest.raises(AgentForbidden):
        service.connect(token, _create(server, address="other@studio.test"))
    with pytest.raises(AgentForbidden):
        service.change_password(token, mailbox_id, "new")
    with pytest.raises(AgentForbidden):
        service.disconnect(token, mailbox_id)
    assert server.state.logins == 1, "a refused token never reaches the server"


def test_only_an_unscoped_admin_connects_the_spaces_mailbox_and_a_person_their_own(
    db_session: Session, server: FakeImap
) -> None:
    service = _service(db_session, server)
    collaborator = _user(db_session, "collaborator")
    readonly = _user(db_session, "readonly")

    with pytest.raises(PermissionDenied):
        service.connect(collaborator, _create(server))
    with pytest.raises(PermissionDenied):
        service.connect(readonly, _create(server, space_owned=False, purposes=["correspondence"]))

    own = service.connect(
        collaborator, _create(server, space_owned=False, purposes=["correspondence"])
    )
    assert (own.space_owned, own.owner_user_id) == (False, collaborator.id)


def test_billing_is_refused_on_a_personal_mailbox(db_session: Session, server: FakeImap) -> None:
    with pytest.raises(ValidationFailed, match="space-owned"):
        _service(db_session, server).connect(
            _user(db_session), _create(server, space_owned=False, purposes=["billing"])
        )


def test_somebody_elses_mailbox_is_not_found(db_session: Session, server: FakeImap) -> None:
    service = _service(db_session, server)
    owner, other = _user(db_session), _user(db_session)
    own = service.connect(owner, _create(server, space_owned=False, purposes=["correspondence"]))

    with pytest.raises(NotFound):
        service.disconnect(other, own.id)
    assert [m.id for m in service.list(other)] == [m.id for m in service.list(other) if False]
    assert [m.id for m in service.list(owner)] == [own.id]


def test_a_refused_password_stores_nothing(db_session: Session, server: FakeImap) -> None:
    with pytest.raises(ValidationFailed) as refused:
        _service(db_session, server).connect(_user(db_session), _create(server, password="wrong"))

    assert refused.value.details["field"] == "password"
    assert "wrong" not in refused.value.message
    assert db_session.scalar(select(Mailbox.id).where(Mailbox.provider == "imap")) is None
    assert db_session.scalar(select(MailboxPurpose.id)) is None


def test_the_same_address_is_not_connected_twice(db_session: Session, server: FakeImap) -> None:
    admin = _user(db_session)
    service = _service(db_session, server)
    service.connect(admin, _create(server))

    with pytest.raises(Conflict, match="già collegata"):
        service.connect(admin, _create(server, address=USERNAME.upper()))


# --- the connection's security --------------------------------------------------------------


@pytest.mark.parametrize(
    ("host", "port", "security", "why"),
    [
        ("imap.test", 143, "tls", "143"),
        ("imap.test", 993, "starttls", "993"),
        ("imap.test", 993, "none", "cifrata"),
        ("imap.test", 993, "plain", "cifrata"),
        ("imap.test", 0, "tls", "porta"),
        ("imap.test", 70000, "tls", "porta"),
        ("https://imap.test", 993, "tls", "https"),
        ("", 993, "tls", "server"),
    ],
)
def test_a_plaintext_or_malformed_target_is_refused_before_any_network(
    host: str, port: int, security: str, why: str
) -> None:
    with pytest.raises(ValueError, match=why):
        validate_target(host, port, security)


def test_a_plaintext_security_is_not_even_a_choice_of_the_request() -> None:
    with pytest.raises(ValueError):
        ImapMailboxCreate(
            address="a@b.it",
            host="h.it",
            port=143,
            security="none",  # type: ignore[arg-type]
            username="u",
            password="p",
            purposes=["billing"],
        )


@pytest.mark.parametrize(
    "address", ["127.0.0.1", "10.0.0.5", "192.168.1.9", "169.254.169.254", "::1"]
)
def test_a_host_resolving_to_a_non_global_address_is_refused(address: str) -> None:
    def lookup(host: str, port: int, **_: object) -> list[tuple[object, ...]]:
        return [(0, 0, 0, "", (address, port))]

    with pytest.raises(NotPublicAddress):
        resolve_public("mail.example.com", 993, lookup=lookup)


def test_one_non_global_address_among_global_ones_refuses_the_host() -> None:
    def lookup(host: str, port: int, **_: object) -> list[tuple[object, ...]]:
        return [(0, 0, 0, "", ("93.184.216.34", port)), (0, 0, 0, "", ("10.0.0.5", port))]

    with pytest.raises(NotPublicAddress):
        resolve_public("mail.example.com", 993, lookup=lookup)


def test_the_default_connection_refuses_a_loopback_host_and_reports_it_to_the_person(
    db_session: Session, server: FakeImap
) -> None:
    service = ImapMailboxService(db_session, settings=_settings(), context=server.client_context())

    with pytest.raises(ValidationFailed, match="indirizzo pubblico"):
        service.connect(_user(db_session), _create(server, host="localhost"))
    assert server.state.logins == 0


def test_the_connection_goes_to_the_checked_address_and_the_name_is_not_looked_up_again(
    server: FakeImap, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The resolver answers once; a second lookup by the socket layer would be the way a check
    is walked around."""
    lookups: list[str] = []
    real = socket.getaddrinfo

    def counting(host: object, *args: object, **kwargs: object) -> object:
        lookups.append(str(host))
        return real(host, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(socket, "getaddrinfo", counting)
    target = ImapTarget(HOST, server.port, "tls", USERNAME, PASSWORD)
    with ImapReader(target, resolve=loopback, context=server.client_context()) as reader:
        reader.select_inbox()
    assert HOST not in lookups


def test_starttls_encrypts_before_the_login(starttls_server: FakeImap) -> None:
    target = ImapTarget(HOST, starttls_server.port, "starttls", USERNAME, PASSWORD)
    with ImapReader(target, resolve=loopback, context=starttls_server.client_context()) as reader:
        reader.select_inbox()
    commands = starttls_server.state.commands
    assert commands.index("STARTTLS") < commands.index("LOGIN <redacted>")


def test_a_server_that_does_not_offer_starttls_is_not_logged_into(
    starttls_server: FakeImap,
) -> None:
    starttls_server.state.offer_starttls = False
    target = ImapTarget(HOST, starttls_server.port, "starttls", USERNAME, PASSWORD)

    with pytest.raises(ImapFailure, match="STARTTLS") as refused:
        ImapReader(target, resolve=loopback, context=starttls_server.client_context()).__enter__()

    assert refused.value.kind == "transient"
    assert starttls_server.state.logins == 0


def test_an_untrusted_certificate_is_refused(server: FakeImap) -> None:
    """The reader's own default context trusts the system's authorities, not this fake's."""
    target = ImapTarget(HOST, server.port, "tls", USERNAME, PASSWORD)

    with pytest.raises(ImapFailure, match="certificato"):
        ImapReader(target, resolve=loopback).__enter__()
    assert server.state.logins == 0


def test_a_certificate_for_another_name_is_refused(server: FakeImap) -> None:
    target = ImapTarget("other.test", server.port, "tls", USERNAME, PASSWORD)

    with pytest.raises(ImapFailure, match="certificato"):
        ImapReader(target, resolve=loopback, context=server.client_context()).__enter__()


def test_a_non_ascii_password_logs_in(server: FakeImap) -> None:
    server.state.password = "pässwörd-€"
    target = ImapTarget(HOST, server.port, "tls", USERNAME, "pässwörd-€")
    with ImapReader(target, resolve=loopback, context=server.client_context()) as reader:
        reader.select_inbox()
    assert server.state.logins == 1


# --- reading --------------------------------------------------------------------------------


def _messages(server: FakeImap) -> None:
    day = dt.timedelta(days=1)
    server.state.messages = [
        Msg(1, NOW - 100 * day, subject="old"),
        Msg(
            2,
            NOW - 3 * day,
            subject="invoice",
            parts=(Part("text/plain"), Part("application/pdf", "f.pdf", 4000)),
        ),
        Msg(
            3,
            NOW - 1 * day,
            subject="logo",
            parts=(
                Part("text/html"),
                Part("image/png", "logo.png", 800, inline=True, content_id="<c1>"),
                Part("application/xml", "IT01.xml", 2000),
            ),
        ),
    ]


def test_the_reader_asks_for_headers_and_structure_and_nothing_else(
    db_session: Session, server: FakeImap
) -> None:
    _messages(server)
    admin = _user(db_session)
    mailbox_id = _service(db_session, server).connect(admin, _create(server)).id
    sink = _Sink()

    report = _sync(db_session, server, (sink,)).sync(mailbox_id, now=NOW)

    assert (report.health, report.heads_read) == ("active", 2), "the 100-day-old one is past 90"
    asked = " | ".join(server.state.commands).upper()
    assert "BODY.PEEK[HEADER.FIELDS" in asked and "BODYSTRUCTURE" in asked
    for forbidden in (
        "RFC822",
        "BODY[",
        "BODY.PEEK[1",
        "BODY.PEEK[]",
        "STORE",
        "EXPUNGE",
        "COPY",
        "DELETE",
    ):
        assert forbidden not in asked.replace("BODY.PEEK[HEADER.FIELDS", ""), forbidden
    assert "EXAMINE" in asked or "SELECT" in asked


def test_a_head_carries_the_headers_and_the_parts_the_structure_names(
    db_session: Session, server: FakeImap
) -> None:
    _messages(server)
    mailbox_id = _service(db_session, server).connect(_user(db_session), _create(server)).id
    sink = _Sink()

    _sync(db_session, server, (sink,)).sync(mailbox_id, now=NOW)

    by_subject = {head.subject: head for head in sink.seen}
    invoice = by_subject["invoice"]
    assert (invoice.from_address, invoice.to_addresses, invoice.uid, invoice.uidvalidity) == (
        "fornitore@acme.test",
        ("fatture@studio.test",),
        2,
        7,
    )
    assert invoice.internal_date == NOW - dt.timedelta(days=3)
    assert [(p.path, p.mime_type, p.filename, p.size) for p in invoice.parts] == [
        ("1", "text/plain", None, 100),
        ("2", "application/pdf", "f.pdf", 4000),
    ]
    logo = by_subject["logo"]
    assert [(p.mime_type, p.inline) for p in logo.parts] == [
        ("text/html", False),
        ("image/png", True),
        ("application/xml", False),
    ]
    assert [p.filename for p in logo.parts] == [None, "logo.png", "IT01.xml"]


def test_the_watermark_moves_and_the_next_cycle_hands_over_only_what_is_new(
    db_session: Session, server: FakeImap
) -> None:
    _messages(server)
    mailbox_id = _service(db_session, server).connect(_user(db_session), _create(server)).id
    sink = _Sink()
    service = _sync(db_session, server, (sink,))
    service.sync(mailbox_id, now=NOW)
    purpose = db_session.scalar(select(MailboxPurpose).where(MailboxPurpose.purpose == "billing"))
    assert purpose is not None
    assert purpose.sync_watermark == NOW - dt.timedelta(days=1)

    server.state.messages.append(Msg(4, NOW - dt.timedelta(hours=2), subject="new"))
    sink.seen.clear()
    service.sync(mailbox_id, now=NOW)

    # The boundary message is handed over again, on purpose: a sink is idempotent.
    assert [head.subject for head in sink.seen] == ["logo", "new"]
    assert purpose.sync_watermark == NOW - dt.timedelta(hours=2)


def test_a_purpose_with_no_sink_is_not_read_and_keeps_its_watermark(
    db_session: Session, server: FakeImap
) -> None:
    _messages(server)
    mailbox_id = _service(db_session, server).connect(_user(db_session), _create(server)).id

    report = _sync(db_session, server).sync(mailbox_id, now=NOW)

    assert (report.health, report.heads_read) == ("active", 0)
    assert not any("FETCH" in c for c in server.state.commands)
    purpose = db_session.scalar(select(MailboxPurpose).where(MailboxPurpose.purpose == "billing"))
    assert purpose is not None and purpose.sync_watermark is None


# --- health ---------------------------------------------------------------------------------


def test_a_refused_password_is_auth_failed_and_is_not_tried_again(
    db_session: Session, server: FakeImap
) -> None:
    mailbox_id = _service(db_session, server).connect(_user(db_session), _create(server)).id
    server.state.password = "changed-at-the-provider"
    service = _sync(db_session, server)

    first = service.sync(mailbox_id, now=NOW)
    logins = server.state.logins
    second = service.sync(mailbox_id, now=NOW)

    assert first.health == "auth_failed" and second.health == "auth_failed"
    assert second.skipped is not None
    assert server.state.logins == logins, "a refused password is not sent again"
    mailbox = db_session.get(Mailbox, mailbox_id)
    assert mailbox is not None
    assert mailbox.last_error is not None and "password" in mailbox.last_error
    assert PASSWORD not in mailbox.last_error and "NO [" not in mailbox.last_error


def test_a_server_that_is_down_is_failing_and_recovers_by_itself(
    db_session: Session, server: FakeImap
) -> None:
    mailbox_id = _service(db_session, server).connect(_user(db_session), _create(server)).id
    service = _sync(db_session, server)
    server.state.login_reply = "NO [UNAVAILABLE]"

    first = service.sync(mailbox_id, now=NOW)
    mailbox = db_session.get(Mailbox, mailbox_id)
    assert mailbox is not None
    began = mailbox.failing_since
    again = service.sync(mailbox_id, now=NOW + dt.timedelta(minutes=15))

    assert (first.health, again.health) == ("failing", "failing")
    assert began == NOW and mailbox.failing_since == began, "failing_since is when the run began"

    server.state.login_reply = "OK"
    healed = service.sync(mailbox_id, now=NOW + dt.timedelta(minutes=30))
    assert healed.health == "active"
    assert (mailbox.status, mailbox.last_error, mailbox.failing_since) == ("active", None, None)


def test_a_connection_that_drops_is_failing(db_session: Session, server: FakeImap) -> None:
    mailbox_id = _service(db_session, server).connect(_user(db_session), _create(server)).id
    server.state.login_reply = "BYE"

    assert _sync(db_session, server).sync(mailbox_id, now=NOW).health == "failing"


def test_a_new_password_brings_the_mailbox_back_and_a_wrong_one_changes_nothing(
    db_session: Session, server: FakeImap
) -> None:
    admin = _user(db_session)
    service = _service(db_session, server)
    mailbox_id = service.connect(admin, _create(server)).id
    server.state.password = "rotated"
    _sync(db_session, server).sync(mailbox_id, now=NOW)

    with pytest.raises(ValidationFailed):
        service.change_password(admin, mailbox_id, "still-wrong")
    mailbox = db_session.get(Mailbox, mailbox_id)
    assert mailbox is not None and mailbox.status == "auth_failed"

    read = service.change_password(admin, mailbox_id, "rotated")
    assert (read.health, read.last_error) == ("active", None)
    assert open_password(mailbox, _settings()) == "rotated"


def test_disconnecting_forgets_the_password_and_keeps_the_mailbox_and_its_purposes(
    db_session: Session, server: FakeImap
) -> None:
    admin = _user(db_session)
    service = _service(db_session, server)
    mailbox_id = service.connect(admin, _create(server)).id

    read = service.disconnect(admin, mailbox_id)

    mailbox = db_session.get(Mailbox, mailbox_id)
    assert mailbox is not None
    assert (read.health, mailbox.password_ciphertext, mailbox.password_nonce) == (
        "disconnected",
        None,
        None,
    )
    assert read.purposes == ["billing"]
    with pytest.raises(NotFound):
        _sync(db_session, server).sync(mailbox_id, now=NOW)
    # Typing the password again connects it again.
    assert service.change_password(admin, mailbox_id, PASSWORD).health == "active"


def test_the_schema_keeps_an_imap_mailbox_whole_and_a_gmail_one_bare(db_session: Session) -> None:
    from sqlalchemy.exc import IntegrityError

    db_session.add(Mailbox(owner_user_id=None, provider="imap", status="active", address="a@b.it"))
    with pytest.raises(IntegrityError, match="ck_mailboxes_imap_holds_its_connection"):
        db_session.flush()


def test_bodystructure_reads_nested_parts_literals_and_a_language_that_is_not_a_disposition() -> (
    None
):
    raw = (
        b'(("TEXT" "PLAIN" ("CHARSET" "utf-8") NIL NIL "7BIT" 10 1 NIL NIL ("en" "it") NIL)'
        b'(("TEXT" "HTML" NIL NIL NIL "7BIT" 20 1 NIL NIL NIL NIL)'
        b'("IMAGE" "PNG" ("NAME" "l.png") "<cid1>" NIL "BASE64" 300 NIL '
        b'("INLINE" ("FILENAME" "l.png")) NIL)'
        b' "RELATED" ("BOUNDARY" "r") NIL NIL)'
        b'("APPLICATION" "PKCS7-MIME" ("NAME" {8}\r\nfatt.p7m) NIL NIL "BASE64" 9000 NIL '
        b'("ATTACHMENT" ("FILENAME" "fatt.p7m")) NIL)'
        b' "MIXED" ("BOUNDARY" "m") NIL NIL)'
    )

    parts = parse_bodystructure(raw)

    assert [(p.path, p.mime_type, p.filename, p.size, p.disposition) for p in parts] == [
        ("1", "text/plain", None, 10, None),
        ("2.1", "text/html", None, 20, None),
        ("2.2", "image/png", "l.png", 300, "inline"),
        ("3", "application/pkcs7-mime", "fatt.p7m", 9000, "attachment"),
    ]
    assert [p.inline for p in parts] == [False, False, True, False]


def test_a_malformed_structure_yields_what_could_be_read_and_never_raises() -> None:
    assert parse_bodystructure(b"") == []
    assert parse_bodystructure(b"(") == []


def test_a_reader_learns_nothing_about_a_mailbox_not_even_that_it_exists(
    db_session: Session, server: FakeImap
) -> None:
    service = _service(db_session, server)
    with pytest.raises(PermissionDenied):
        service.disconnect(_user(db_session, "readonly"), uuid4())
    with pytest.raises(PermissionDenied):
        service.change_password(_user(db_session, "readonly"), uuid4(), "x")


# --- what the review of REB-1015 found -------------------------------------------------------


def _read_all(server: FakeImap) -> list[MessageHead]:
    target = ImapTarget(HOST, server.port, "tls", server.state.username, PASSWORD)
    with ImapReader(target, resolve=loopback, context=server.client_context()) as reader:
        reader.uidvalidity = reader.select_inbox()
        return list(reader.heads(reader.uids_since(NOW - dt.timedelta(days=5))))


def test_a_name_the_server_sends_as_a_literal_does_not_cost_the_message_its_headers(
    server: FakeImap,
) -> None:
    server.state.messages = [
        Msg(
            1,
            NOW - dt.timedelta(days=1),
            subject="fattura",
            parts=(
                Part("text/plain"),
                Part("application/pkcs7-mime", "fattura è.p7m", 900, literal=True),
            ),
        ),
        Msg(2, NOW - dt.timedelta(hours=3), subject="seconda"),
    ]

    heads = _read_all(server)

    assert [(h.uid, h.subject, h.from_address) for h in heads] == [
        (1, "fattura", "fornitore@acme.test"),
        (2, "seconda", "fornitore@acme.test"),
    ]
    assert heads[0].message_id == "<1@acme.test>"
    assert [(p.mime_type, p.filename) for p in heads[0].parts] == [
        ("text/plain", None),
        ("application/pkcs7-mime", "fattura è.p7m"),
    ]


def test_an_attachment_with_a_content_id_is_not_inline_and_an_inline_image_is() -> None:
    parts = parse_bodystructure(
        b'(("APPLICATION" "PDF" ("NAME" "a.pdf") "<id1>" NIL "BASE64" 5 NIL '
        b'("ATTACHMENT" ("FILENAME" "a.pdf")) NIL)'
        b'("IMAGE" "PNG" NIL "<id2>" NIL "BASE64" 5 NIL NIL NIL)'
        b'("IMAGE" "PNG" NIL NIL NIL "BASE64" 5 NIL ("INLINE" NIL) NIL) "MIXED" NIL NIL NIL)'
    )
    assert [p.inline for p in parts] == [False, True, True]


def test_a_filename_in_rfc_2231_or_2047_is_decoded() -> None:
    parts = parse_bodystructure(
        b'(("APPLICATION" "PDF" NIL NIL NIL "BASE64" 5 NIL '
        b"""("ATTACHMENT" ("FILENAME*" "utf-8''fattura%20%C3%A8.pdf")) NIL)"""
        b'("APPLICATION" "XML" ("NAME" "=?utf-8?B?ZmF0dHVyYSDDqC54bWw=?=") NIL NIL "BASE64" 5 NIL '
        b'NIL NIL) "MIXED" NIL NIL NIL)'
    )
    assert [p.filename for p in parts] == ["fattura è.pdf", "fattura è.xml"]


@pytest.mark.parametrize(
    ("reply", "kind"),
    [
        ("NO [AUTHENTICATIONFAILED]", "auth"),
        ("NO [AUTHORIZATIONFAILED]", "auth"),
        ("NO login failed", "auth"),
        ("NO [LIMIT]", "transient"),
        ("NO [OVERQUOTA]", "transient"),
        ("NO [SERVERBUG]", "transient"),
        ("NO [UNAVAILABLE]", "transient"),
        ("NO [INUSE]", "transient"),
        ("BAD syntax", "transient"),
        ("BYE", "transient"),
    ],
)
def test_only_a_refusal_of_the_credentials_is_auth_and_everything_else_is_retried(
    server: FakeImap, reply: str, kind: str
) -> None:
    server.state.login_reply = reply
    target = ImapTarget(HOST, server.port, "tls", USERNAME, PASSWORD)

    with pytest.raises(ImapFailure) as failure:
        ImapReader(target, resolve=loopback, context=server.client_context()).__enter__()

    assert failure.value.kind == kind


def test_a_user_name_with_a_backslash_or_a_space_reaches_the_server_as_one_string(
    server: FakeImap,
) -> None:
    server.state.username = "DOMAIN\\fatture amministrazione"
    target = ImapTarget(HOST, server.port, "tls", "DOMAIN\\fatture amministrazione", PASSWORD)
    with ImapReader(target, resolve=loopback, context=server.client_context()) as reader:
        reader.select_inbox()
    assert server.state.logins == 1


@pytest.mark.parametrize("field", ["username", "password"])
@pytest.mark.parametrize("bad", ["a\r\nb DELETE INBOX", "a\nb", "a\0b"])
def test_a_line_break_or_a_nul_in_the_user_name_or_the_password_is_refused(
    field: str, bad: str
) -> None:
    body = {
        "address": "a@b.it",
        "host": "h.it",
        "port": 993,
        "security": "tls",
        "username": "u",
        "password": "p",
        "purposes": ["billing"],
        "space_owned": True,
        field: bad,
    }
    with pytest.raises(ValueError, match="single line"):
        ImapMailboxCreate(**body)  # type: ignore[arg-type]


def test_a_validation_error_does_not_echo_the_password() -> None:
    with pytest.raises(ValueError) as refused:
        ImapMailboxCreate(  # type: ignore[call-arg]
            address="a@b.it", username="u", password="hunter2-plain", purposes=["billing"]
        )
    assert "hunter2-plain" not in str(refused.value)


def test_a_response_that_announces_too_much_is_refused_not_buffered(server: FakeImap) -> None:
    server.state.messages = [Msg(1, NOW - dt.timedelta(hours=1))]
    server.state.fetch_raw = b"* 1 FETCH (UID 1 BODY[HEADER.FIELDS (FROM)] {99999999}\r\n"

    with pytest.raises(ImapFailure) as failure:
        _read_all(server)

    assert failure.value.kind == "transient"


def test_a_read_that_runs_past_its_time_is_stopped(server: FakeImap) -> None:
    server.state.messages = [Msg(1, NOW - dt.timedelta(hours=1))]
    target = ImapTarget(HOST, server.port, "tls", USERNAME, PASSWORD)
    with ImapReader(target, resolve=loopback, context=server.client_context()) as reader:
        reader.select_inbox()
        reader._deadline = 0.0
        with pytest.raises(ImapFailure, match="lento"):
            list(reader.heads([1]))


def test_a_key_that_no_longer_opens_the_password_makes_the_mailbox_auth_failed(
    db_session: Session, server: FakeImap
) -> None:
    mailbox_id = _service(db_session, server).connect(_user(db_session), _create(server)).id
    rotated = ImapSyncService(
        db_session,
        settings=_settings(mailbox_token_key=base64.b64encode(b"z" * 32).decode()),
        resolve=loopback,
        context=server.client_context(),
    )
    logins = server.state.logins

    report = rotated.sync(mailbox_id, now=NOW)

    mailbox = db_session.get(Mailbox, mailbox_id)
    assert mailbox is not None
    assert report.health == "auth_failed" and mailbox.status == "auth_failed"
    assert mailbox.last_error is not None and "password" in mailbox.last_error
    assert server.state.logins == logins


def test_two_connects_of_one_address_at_once_are_one_conflict_and_not_a_server_error(
    db_session: Session, server: FakeImap, monkeypatch: pytest.MonkeyPatch
) -> None:
    admin = _user(db_session)
    service = _service(db_session, server)
    service.connect(admin, _create(server))
    monkeypatch.setattr(service.repo, "imap_address_taken", lambda *_: False)

    with pytest.raises(Conflict, match="già collegata"):
        service.connect(admin, _create(server))
