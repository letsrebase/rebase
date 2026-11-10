"""The only module in the mailbox slice that knows an IMAP socket exists (REB-1015, § 2.2 of
`2026-11-09-incoming-documents-inbox-design.md`).

**What it will and will not do.**

- TLS only. `tls` is TLS from the first byte (port 993); `starttls` upgrades a plain connection
  before anything is sent (port 143) and gives up if the server does not offer it, so a server
  cannot talk the client out of encrypting. There is no third choice, and the certificate and its
  name are always checked.
- The host is resolved **once**, every address must be global (`reminders/push.py::resolve_public`),
  and the socket is opened to the addresses that were checked: a name re-pointed at this network
  between the check and the connect is not followed. The TLS name stays the host's.
- It reads **headers and BODYSTRUCTURE, never a body**: `BODY.PEEK[HEADER.FIELDS (...)]` and
  `BODYSTRUCTURE`, on a mailbox selected read-only. It never sets a flag, moves, deletes or
  expunges (the spec's «never deletes or marks mail on the server»).
- A failure is one of two kinds, because the health states want two answers: `auth` (the server
  refused the user or the password: a person must act) and `transient` (anything else: retried).
  The sentence a person reads is written here, in Italian, and carries nothing the server sent,
  no address and no password.
"""

import contextlib
import imaplib
import re
import socket
import ssl
import time
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from email import message_from_bytes, policy
from email.header import decode_header, make_header
from email.utils import getaddresses
from typing import Final, Literal
from urllib.parse import unquote_to_bytes

from pigrocrm.core.mailboxes.models import IMAP_SECURITY
from pigrocrm.core.reminders.push import NotPublicAddress, resolve_public

Resolver = Callable[[str, int], Sequence[str]]

TLS_PORT: Final = 993
STARTTLS_PORT: Final = 143
CONNECT_TIMEOUT_SECONDS: Final = 20
# Headers are read in groups: one round trip per group, and a group a server cannot answer in one
# line stays small.
FETCH_GROUP: Final = 50
HEADER_FIELDS: Final = "FROM TO CC SUBJECT DATE MESSAGE-ID"
# What one response may weigh, and how long one read of a mailbox may take: a server of somebody's
# own choosing is reached from the cron, so neither is the server's to decide.
MAX_LITERAL_BYTES: Final = 16 * 1024 * 1024
READ_SECONDS: Final = 300
# The verdicts of a refused LOGIN that mean «the user or the password is wrong». Any other `NO`
# with a response code (`[LIMIT]`, `[OVERQUOTA]`, `[UNAVAILABLE]`, `[SERVERBUG]`...) is the server's
# trouble, not a reason to stop trying.
_AUTH_CODES: Final = frozenset(
    {"AUTHENTICATIONFAILED", "AUTHORIZATIONFAILED", "EXPIRED", "PRIVACYREQUIRED"}
)

_MONTHS: Final = (
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
)


class ImapFailure(Exception):
    """A connection or a login that did not work. `kind` is what the health state follows and
    `reason` is the sentence a person reads: Italian, and free of anything the server said."""

    def __init__(self, kind: Literal["auth", "transient"], reason: str) -> None:
        super().__init__(reason)
        self.kind = kind
        self.reason = reason


@dataclass(frozen=True)
class ImapTarget:
    """Where and how to connect, and as whom. `password` is plaintext for the length of one call
    and the caller keeps no copy; it is never in a `repr`."""

    host: str
    port: int
    security: str
    username: str
    password: str = field(repr=False)


@dataclass(frozen=True)
class BodyPart:
    """One leaf of a message's structure: what BODYSTRUCTURE says, which is all that is known
    without fetching the part. `path` is the IMAP section number (`1`, `2`, `2.1`)."""

    path: str
    mime_type: str
    filename: str | None
    size: int
    disposition: str | None
    content_id: str | None

    @property
    def inline(self) -> bool:
        # A Content-ID alone does not make a part inline: mail clients put one on real
        # attachments. The disposition decides, and the Content-ID only where there is none.
        return self.disposition == "inline" or (
            self.disposition is None and self.content_id is not None
        )


@dataclass(frozen=True)
class MessageHead:
    """A message as the reader knows it: its headers and its structure. No body, ever."""

    uid: int
    uidvalidity: int
    internal_date: datetime
    message_id: str
    from_address: str
    to_addresses: tuple[str, ...]
    cc_addresses: tuple[str, ...]
    subject: str
    parts: tuple[BodyPart, ...]


def validate_target(host: str, port: int, security: str) -> None:
    """The refusals that need no network, as `ValueError` with a sentence. A plaintext choice
    does not exist (`security` is `tls` or `starttls`), and a port that speaks the other one is
    a mistake that would otherwise surface as a timeout."""
    if security not in IMAP_SECURITY:
        raise ValueError("la connessione deve essere cifrata: scegli TLS o STARTTLS")
    if not 1 <= port <= 65535:
        raise ValueError("la porta non è valida")
    if security == "tls" and port == STARTTLS_PORT:
        raise ValueError("la porta 143 non è cifrata: usa 993 con TLS, oppure STARTTLS")
    if security == "starttls" and port == TLS_PORT:
        raise ValueError("la porta 993 è già cifrata: usa TLS")
    if not host or len(host) > 255 or not re.fullmatch(r"[A-Za-z0-9.-]+|[0-9A-Fa-f:]+", host):
        raise ValueError("l'indirizzo del server non è valido: scrivi solo il nome, senza https://")


class _Capped:
    """Refuses a response that announces more than `MAX_LITERAL_BYTES`: `imaplib` would buffer
    whatever the server said it was going to send."""

    def read(self, size: int) -> bytes:
        if size > MAX_LITERAL_BYTES:
            raise imaplib.IMAP4.abort("a response is too large")
        return super().read(size)  # type: ignore[misc, no-any-return]


class _Tls(_Capped, imaplib.IMAP4_SSL):
    def __init__(
        self, host: str, port: int, *, addresses: Sequence[str], context: ssl.SSLContext
    ) -> None:
        self._addresses = addresses
        super().__init__(host, port, ssl_context=context, timeout=CONNECT_TIMEOUT_SECONDS)

    def _create_socket(self, timeout: float | None) -> socket.socket:
        raw = _open(self._addresses, self.port, timeout)
        try:
            wrapped: socket.socket = self.ssl_context.wrap_socket(raw, server_hostname=self.host)
        except BaseException:
            raw.close()
            raise
        return wrapped


class _Plain(_Capped, imaplib.IMAP4):
    """Plain only until `starttls` runs; the caller never logs in before it has."""

    def __init__(self, host: str, port: int, *, addresses: Sequence[str]) -> None:
        self._addresses = addresses
        super().__init__(host, port, timeout=CONNECT_TIMEOUT_SECONDS)

    def _create_socket(self, timeout: float | None) -> socket.socket:
        return _open(self._addresses, self.port, timeout)


def _open(addresses: Sequence[str], port: int, timeout: float | None) -> socket.socket:
    """A socket to the first of the checked addresses that answers."""
    last: OSError | None = None
    for address in addresses:
        try:
            return socket.create_connection((address, port), timeout)
        except OSError as exc:
            last = exc
    raise last or OSError("no address")


def default_context() -> ssl.SSLContext:
    """Certificate and name verified, TLS 1.2 at least."""
    context = ssl.create_default_context()
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    return context


class ImapReader:
    """One connection to one mailbox. Use it as a context manager; it logs out on the way out
    and never raises from doing so."""

    def __init__(
        self,
        target: ImapTarget,
        *,
        resolve: Resolver = resolve_public,
        context: ssl.SSLContext | None = None,
    ) -> None:
        self.target = target
        self._resolve = resolve
        self._context = context or default_context()
        self._imap: imaplib.IMAP4 | None = None
        self.uidvalidity = 0
        self._deadline = time.monotonic() + READ_SECONDS

    def __enter__(self) -> "ImapReader":
        self._connect()
        return self

    def __exit__(self, *_exc: object) -> None:
        imap, self._imap = self._imap, None
        if imap is not None:
            with contextlib.suppress(imaplib.IMAP4.error, OSError, EOFError):
                imap.logout()

    # --- connecting -------------------------------------------------------------------

    def _connect(self) -> None:
        target = self.target
        try:
            validate_target(target.host, target.port, target.security)
        except ValueError as exc:
            raise ImapFailure("transient", str(exc)) from exc
        try:
            addresses = self._resolve(target.host, target.port)
        except NotPublicAddress as exc:
            raise ImapFailure(
                "transient", "il server non ha un indirizzo pubblico: PigroCRM non lo raggiunge"
            ) from exc
        except OSError as exc:
            raise ImapFailure("transient", "il nome del server non si risolve") from exc
        try:
            if target.security == "tls":
                imap: imaplib.IMAP4 = _Tls(
                    target.host, target.port, addresses=addresses, context=self._context
                )
            else:
                imap = _Plain(target.host, target.port, addresses=addresses)
                try:
                    imap.starttls(self._context)
                except imaplib.IMAP4.error as exc:
                    _close(imap)
                    raise ImapFailure(
                        "transient", "il server non offre STARTTLS: la connessione non è cifrata"
                    ) from exc
                except BaseException:
                    _close(imap)
                    raise
        except ImapFailure:
            raise
        except ssl.SSLCertVerificationError as exc:
            raise ImapFailure("transient", "il certificato del server non è valido") from exc
        except ssl.SSLError as exc:
            raise ImapFailure("transient", "il server non ha completato la cifratura TLS") from exc
        except TimeoutError as exc:
            raise ImapFailure("transient", "il server non risponde in tempo") from exc
        except (OSError, EOFError, imaplib.IMAP4.error) as exc:
            raise ImapFailure("transient", "non riesco a collegarmi al server") from exc
        self._imap = imap
        self._login(imap)

    def _login(self, imap: imaplib.IMAP4) -> None:
        username, password = self.target.username, self.target.password
        try:
            if username.isascii() and password.isascii():
                # `login` quotes the password and not the user name: a name with a space or a
                # backslash (`DOMAIN\\user`) must reach the server as one string.
                imap.login(_quote(username), password)
            else:
                # LOGIN carries ASCII only; PLAIN carries UTF-8.
                imap.authenticate("PLAIN", lambda _challenge: f"\0{username}\0{password}".encode())
        except imaplib.IMAP4.abort as exc:
            # The server hung up or said BYE: not a verdict on the password.
            _close(imap)
            self._imap = None
            raise ImapFailure("transient", "la connessione si è interrotta") from exc
        except imaplib.IMAP4.error as exc:
            _close(imap)
            self._imap = None
            if not _is_refusal_of_the_credentials(str(exc)):
                raise ImapFailure(
                    "transient", "il server non accetta l'accesso in questo momento"
                ) from exc
            raise ImapFailure(
                "auth", "il server non accetta l'utente o la password: aggiorna la password"
            ) from exc
        except (OSError, EOFError) as exc:
            self._imap = None
            raise ImapFailure("transient", "la connessione si è interrotta") from exc

    # --- reading ----------------------------------------------------------------------

    def _check_time(self) -> None:
        if time.monotonic() > self._deadline:
            raise ImapFailure("transient", "il server è troppo lento: riprovo al prossimo ciclo")

    def select_inbox(self) -> int:
        """Opens INBOX read-only and answers its UIDVALIDITY."""
        imap = self._need()
        try:
            status, _ = imap.select("INBOX", readonly=True)
            if status != "OK":
                raise ImapFailure("transient", "il server non apre la posta in arrivo")
            self.uidvalidity = _uidvalidity(imap)
        except (imaplib.IMAP4.error, OSError, EOFError) as exc:
            raise ImapFailure("transient", "la connessione si è interrotta") from exc
        return self.uidvalidity

    def uids_since(self, moment: datetime) -> list[int]:
        """The UIDs of the messages the server dates on or after `moment`'s day, in order. IMAP's
        `SINCE` is a date in the server's own zone, so the caller filters on the exact time."""
        imap = self._need()
        day = (moment - timedelta(days=1)).astimezone(UTC)
        since = f"{day.day:02d}-{_MONTHS[day.month - 1]}-{day.year}"
        try:
            status, data = imap.uid("SEARCH", "SINCE", since)
        except (imaplib.IMAP4.error, OSError, EOFError) as exc:
            raise ImapFailure("transient", "la connessione si è interrotta") from exc
        if status != "OK" or not data or data[0] is None:
            return []
        return sorted(int(token) for token in data[0].split())

    def heads(self, uids: Sequence[int]) -> Iterator[MessageHead]:
        """Headers and structure of `uids`, a group at a time. Never a body: `PEEK` fetches the
        named header fields only, and BODYSTRUCTURE is the server's own summary of the parts."""
        imap = self._need()
        for start in range(0, len(uids), FETCH_GROUP):
            self._check_time()
            group = ",".join(str(uid) for uid in uids[start : start + FETCH_GROUP])
            try:
                status, data = imap.uid(
                    "FETCH",
                    group,
                    f"(UID INTERNALDATE BODYSTRUCTURE BODY.PEEK[HEADER.FIELDS ({HEADER_FIELDS})])",
                )
            except (imaplib.IMAP4.error, OSError, EOFError) as exc:
                raise ImapFailure("transient", "la connessione si è interrotta") from exc
            if status != "OK":
                raise ImapFailure("transient", "il server non restituisce le intestazioni")
            for text, headers in _responses(data):
                head = _head(text, headers, self.uidvalidity)
                if head is not None:
                    yield head
            self._check_time()

    def _need(self) -> imaplib.IMAP4:
        if self._imap is None:
            raise ImapFailure("transient", "la connessione è chiusa")
        return self._imap


def _quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


_CODE = re.compile(r"""^B?['"]?\s*\[([A-Z-]+)[\] ]""")


def _is_refusal_of_the_credentials(message: str) -> bool:
    """Whether a failed LOGIN or AUTHENTICATE said «wrong user or password». `imaplib` raises
    the server's own text for a `NO` (so the answer is in its response code) and a
    `command error` sentence for a `BAD`, a command the server did not understand. A `NO` with no
    response code is the plain refusal, and so are the codes of `_AUTH_CODES`; any other code
    (`[LIMIT]`, `[OVERQUOTA]`, `[SERVERBUG]`) is the server's own trouble."""
    text = message.upper()
    if "COMMAND ERROR:" in text:
        return False
    code = _CODE.search(text)
    return code is None or code.group(1) in _AUTH_CODES


def _close(imap: imaplib.IMAP4) -> None:
    with contextlib.suppress(OSError, imaplib.IMAP4.error):
        imap.shutdown()


def _uidvalidity(imap: imaplib.IMAP4) -> int:
    _, data = imap.response("UIDVALIDITY")
    return int(data[0]) if data and data[0] else 0


# --- parsing what a FETCH answers ---------------------------------------------------------

_MESSAGE_START = re.compile(rb"^\d+ \(")
_HEADERS_LITERAL = re.compile(rb"HEADER\.FIELDS \([^)]*\)\]\s*\{\d+\}$")
_LITERAL = re.compile(rb"\{\d+\}$")


def _responses(data: Sequence[object]) -> Iterator[tuple[bytes, bytes]]:
    """One `(text, headers)` per message of a FETCH answer. `imaplib` cuts an answer at every
    literal, so a name the server sent as one (`"NAME" {8}`) splits the structure; every literal
    but the headers' is put back in the text, quoted, and the headers' is the one whose marker
    ends the header-fields item."""
    text, headers = b"", b""
    for item in data:
        first, literal = item if isinstance(item, tuple) else (item, None)
        if not isinstance(first, bytes):
            continue
        if _MESSAGE_START.match(first) and text:
            yield text, headers
            text, headers = b"", b""
        if literal is None:
            text += first
        elif _HEADERS_LITERAL.search(first):
            text += _LITERAL.sub(b"", first)
            headers = literal
        else:
            quoted = literal.replace(b"\\", b"\\\\").replace(b'"', b'\\"')
            text += _LITERAL.sub(b"", first) + b'"' + quoted + b'"'
    if text:
        yield text, headers


_UID = re.compile(rb"UID (\d+)")
_INTERNALDATE = re.compile(rb'INTERNALDATE "\s*([^"]+)"')
_STRUCTURE = re.compile(rb"BODYSTRUCTURE ")


def _head(prefix: bytes, headers: bytes, uidvalidity: int) -> MessageHead | None:
    uid = _UID.search(prefix)
    stamp = _INTERNALDATE.search(prefix)
    structure = _STRUCTURE.search(prefix)
    if uid is None or stamp is None or structure is None:
        return None
    try:
        moment = datetime.strptime(stamp.group(1).decode("ascii"), "%d-%b-%Y %H:%M:%S %z")
    except ValueError:
        return None
    parsed = message_from_bytes(headers, policy=policy.default)
    senders = _addresses(parsed.get_all("From"))
    return MessageHead(
        uid=int(uid.group(1)),
        uidvalidity=uidvalidity,
        internal_date=moment.astimezone(UTC),
        message_id=str(parsed.get("Message-ID", "")).strip(),
        from_address=senders[0] if senders else "",
        to_addresses=tuple(_addresses(parsed.get_all("To"))),
        cc_addresses=tuple(_addresses(parsed.get_all("Cc"))),
        subject=str(parsed.get("Subject", "")).strip(),
        parts=tuple(parse_bodystructure(prefix[structure.end() :])),
    )


def _addresses(values: Sequence[object] | None) -> list[str]:
    if not values:
        return []
    found = getaddresses([str(value) for value in values])
    return [address.lower() for _, address in found if address]


# --- BODYSTRUCTURE ----------------------------------------------------------------------

Node = bytes | None | list["Node"]


def _tokens(raw: bytes) -> Node:
    """The parenthesised list of an IMAP response, as nested lists of bytes. Strings are
    unquoted, `NIL` is `None`, a literal (`{n}` followed by `n` bytes) is its bytes."""
    position = 0
    length = len(raw)

    def atom() -> Node:
        nonlocal position
        while position < length and raw[position : position + 1] in (b" ", b"\r", b"\n"):
            position += 1
        char = raw[position : position + 1]
        if char == b"(":
            position += 1
            items: list[Node] = []
            while True:
                while position < length and raw[position : position + 1] in (b" ", b"\r", b"\n"):
                    position += 1
                if position >= length:
                    return items
                if raw[position : position + 1] == b")":
                    position += 1
                    return items
                items.append(atom())
        if char == b'"':
            position += 1
            out = bytearray()
            while position < length and raw[position : position + 1] != b'"':
                if raw[position : position + 1] == b"\\":
                    position += 1
                out += raw[position : position + 1]
                position += 1
            position += 1
            return bytes(out)
        if char == b"{":
            end = raw.index(b"}", position)
            size = int(raw[position + 1 : end])
            start = end + 3  # past `}\r\n`
            position = start + size
            return raw[start:position]
        start = position
        while position < length and raw[position : position + 1] not in (b" ", b")", b"(", b"\r"):
            position += 1
        word = raw[start:position]
        return None if word.upper() == b"NIL" else word

    return atom()


def _text(node: Node) -> str:
    return node.decode("utf-8", "replace") if isinstance(node, bytes) else ""


def _params(node: Node) -> dict[str, str]:
    """A parameter list as a dict with lower-case names. A name sent as RFC 2231 (`filename*`,
    `charset'language'%C3%A8`) or as an RFC 2047 encoded word is decoded."""
    if not isinstance(node, list):
        return {}
    found: dict[str, str] = {}
    for i in range(0, len(node) - 1, 2):
        key = node[i]
        if not isinstance(key, bytes):
            continue
        name, value = key.decode("ascii", "replace").lower(), _text(node[i + 1])
        if name.endswith("*"):
            name, value = name[:-1], _decode_2231(value)
        elif value.startswith("=?"):
            value = _decode_2047(value)
        found.setdefault(name, value)
    return found


def _decode_2231(value: str) -> str:
    charset, _, rest = value.partition("'")
    _, _, encoded = rest.partition("'")
    try:
        return unquote_to_bytes(encoded).decode(charset or "utf-8", "replace")
    except LookupError:
        return unquote_to_bytes(encoded).decode("utf-8", "replace")


def _decode_2047(value: str) -> str:
    try:
        return str(make_header(decode_header(value)))
    except (ValueError, LookupError):
        return value


def parse_bodystructure(raw: bytes) -> list[BodyPart]:
    """The leaves of a BODYSTRUCTURE, in order, with their section numbers. Only what the
    structure says: a part's name comes from its disposition or its parameters, its size is the
    server's, and nothing is fetched. A malformed structure yields what could be read."""
    tree = _tokens(raw)
    parts: list[BodyPart] = []
    if isinstance(tree, list):
        _walk(tree, "", parts, top=True)
    return parts


def _walk(node: list[Node], path: str, parts: list[BodyPart], *, top: bool = False) -> None:
    if node and isinstance(node[0], list):
        # multipart: the children, then the subtype and the extension data.
        index = 0
        while index < len(node) and isinstance(node[index], list):
            child = node[index]
            assert isinstance(child, list)
            number = str(index + 1)
            _walk(child, number if top else f"{path}.{number}", parts)
            index += 1
        return
    if len(node) < 7:
        return
    main, sub = _text(node[0]).lower(), _text(node[1]).lower()
    params = _params(node[2])
    content_id = _text(node[3]) or None
    size = int(node[6]) if isinstance(node[6], bytes) and node[6].isdigit() else 0
    disposition, filename = None, params.get("name")
    # Extension data follows the fixed fields: md5, then the disposition `("attachment" (...))`.
    for extra in node[7:]:
        # `("attachment" ("filename" "a.pdf"))`: two elements, the second a list or NIL, which
        # tells it from the language list (`("en")`, `("en" "it")`) that can follow it.
        if (
            isinstance(extra, list)
            and len(extra) == 2
            and isinstance(extra[0], bytes)
            and (extra[1] is None or isinstance(extra[1], list))
        ):
            disposition = _text(extra[0]).lower()
            filename = _params(extra[1]).get("filename", filename)
            break
    parts.append(
        BodyPart(
            path=path or "1",
            mime_type=f"{main}/{sub}",
            filename=filename,
            size=size,
            disposition=disposition,
            content_id=content_id,
        )
    )
