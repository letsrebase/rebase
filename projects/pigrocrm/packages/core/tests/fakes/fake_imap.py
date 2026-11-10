"""A fake IMAP server on the loopback interface, for the mailbox tests (REB-1015).

What is faked is the *server*, not the client: the real `ImapReader` runs, over a real socket, a
real TLS handshake (a self-signed certificate for `imap.test`, trusted by the test's context) and
real `imaplib`. No test touches a real account. The server records every command it received, so
a test can say what the reader asked for: headers and BODYSTRUCTURE, and nothing else.

Two modes, as production has: `tls` (implicit TLS) and `starttls` (plain until STARTTLS, which
`offer_starttls=False` withholds to prove the client gives up instead of logging in plain).
"""

import base64
import datetime as dt
import re
import socket
import socketserver
import ssl
import tempfile
import threading
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

HOST = "imap.test"
USERNAME = "fatture@studio.test"
PASSWORD = "s3cr3t-password"


@dataclass(frozen=True)
class Part:
    mime: str
    filename: str | None = None
    size: int = 100
    inline: bool = False
    content_id: str | None = None
    # Sent as an IMAP literal (`{n}` and the bytes) rather than a quoted string, as a server does
    # for a name with a byte outside ASCII.
    literal: bool = False
    # What the structure says it is, when a test wants an attachment that has a Content-ID.
    disposition: str | None = None


@dataclass
class Msg:
    uid: int
    date: dt.datetime
    sender: str = "fornitore@acme.test"
    to: str = "fatture@studio.test"
    subject: str = "Fattura 12"
    message_id: str = ""
    parts: Sequence[Part] = (Part("text/plain"), Part("application/pdf", "fattura.pdf", 5000))

    def headers(self) -> bytes:
        mid = self.message_id or f"<{self.uid}@acme.test>"
        return (
            f"From: {self.sender}\r\nTo: {self.to}\r\nSubject: {self.subject}\r\n"
            f"Message-ID: {mid}\r\n\r\n"
        ).encode()

    def structure(self) -> str:
        leaves = []
        for part in self.parts:
            main, sub = part.mime.upper().split("/")
            quoted = _string(part.filename, literal=part.literal) if part.filename else None
            params = f'("NAME" {quoted})' if quoted else "NIL"
            cid = f'"{part.content_id}"' if part.content_id else "NIL"
            kind = part.disposition or ("inline" if part.inline else None)
            if kind is None and part.filename:
                kind = "attachment"
            if kind is None:
                extra = "NIL"
            elif quoted:
                extra = f'("{kind.upper()}" ("FILENAME" {quoted}))'
            else:
                extra = f'("{kind.upper()}" NIL)'
            if main == "TEXT":
                leaves.append(
                    f'("{main}" "{sub}" ("CHARSET" "utf-8") {cid} NIL "7BIT" {part.size} 4 NIL '
                    f"{extra} NIL)"
                )
            else:
                leaves.append(
                    f'("{main}" "{sub}" {params} {cid} NIL "BASE64" {part.size} NIL {extra} NIL)'
                )
        if len(leaves) == 1:
            return leaves[0]
        return "(" + "".join(leaves) + ' "MIXED" ("BOUNDARY" "b") NIL NIL)'


def _string(value: str, *, literal: bool) -> str:
    if literal:
        return f"{{{len(value.encode())}}}\r\n{value}"
    return f'"{value}"'


@dataclass
class ServerState:
    mode: str = "tls"
    offer_starttls: bool = True
    password: str = PASSWORD
    username: str = USERNAME
    # `OK`, or what follows the tag: `NO [AUTHENTICATIONFAILED]`, `NO [LIMIT]`, `BAD`, or `BYE`.
    login_reply: str = "OK"
    # When set, the server answers every FETCH with exactly these bytes (a response too large).
    fetch_raw: bytes | None = None
    messages: list[Msg] = field(default_factory=list)
    commands: list[str] = field(default_factory=list)
    logins: int = 0
    uidvalidity: int = 7


def _certificate() -> tuple[bytes, bytes]:
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, HOST)])
    now = dt.datetime.now(dt.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(days=1))
        .not_valid_after(now + dt.timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(HOST)]), critical=False)
        .sign(key, hashes.SHA256())
    )
    return (
        cert.public_bytes(serialization.Encoding.PEM),
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ),
    )


class _Handler(socketserver.StreamRequestHandler):
    server: "FakeImap"

    def handle(self) -> None:
        state = self.server.state
        conn: socket.socket | ssl.SSLSocket = self.request
        if state.mode == "tls":
            conn = self.server.context.wrap_socket(conn, server_side=True)
        self.rfile = conn.makefile("rb")
        self.wfile = conn.makefile("wb")
        self.conn = conn
        self._say("* OK fake IMAP ready")
        selected = False
        while True:
            line = self.rfile.readline()
            if not line:
                return
            text = line.decode().rstrip("\r\n")
            tag, _, rest = text.partition(" ")
            command, _, args = rest.partition(" ")
            state.commands.append(rest if command.upper() != "LOGIN" else "LOGIN <redacted>")
            verb = command.upper()
            if verb == "CAPABILITY":
                caps = "IMAP4rev1 AUTH=PLAIN"
                if (
                    state.mode == "starttls"
                    and state.offer_starttls
                    and not isinstance(conn, ssl.SSLSocket)
                ):
                    caps += " STARTTLS"
                self._say(f"* CAPABILITY {caps}")
                self._say(f"{tag} OK done")
            elif verb == "STARTTLS":
                if not (state.mode == "starttls" and state.offer_starttls):
                    self._say(f"{tag} BAD no")
                    continue
                self._say(f"{tag} OK begin TLS")
                conn = self.server.context.wrap_socket(self.request, server_side=True)
                self.rfile = conn.makefile("rb")
                self.wfile = conn.makefile("wb")
                self.conn = conn
            elif verb == "LOGIN":
                found = re.findall(r'"((?:[^"\\]|\\.)*)"|(\S+)', args)
                user, password = [
                    (quoted or bare).replace('\\"', '"').replace("\\\\", "\\")
                    for quoted, bare in found[:2]
                ]
                self._login(tag, user, password)
            elif verb == "AUTHENTICATE":
                self._say("+ ")
                blob = base64.b64decode(self.rfile.readline().strip())
                _, user, password = blob.decode().split("\0")
                self._login(tag, user, password)
            elif verb in ("SELECT", "EXAMINE"):
                selected = True
                self._say(f"* {len(state.messages)} EXISTS")
                self._say(f"* OK [UIDVALIDITY {state.uidvalidity}] ok")
                self._say(f"{tag} OK [READ-ONLY] done")
            elif verb == "UID" and selected:
                sub, _, tail = args.partition(" ")
                if sub.upper() == "SEARCH":
                    uids = " ".join(str(m.uid) for m in state.messages)
                    self._say(f"* SEARCH {uids}")
                    self._say(f"{tag} OK done")
                elif sub.upper() == "FETCH":
                    self._fetch(tag, tail)
                else:
                    self._say(f"{tag} BAD unsupported")
            elif verb == "LOGOUT":
                self._say("* BYE bye")
                self._say(f"{tag} OK done")
                return
            else:
                self._say(f"{tag} BAD unsupported")

    def _login(self, tag: str, user: str, password: str) -> None:
        state = self.server.state
        state.logins += 1
        if state.login_reply == "BYE":
            self._say("* BYE going away")
            return
        if state.login_reply != "OK":
            self._say(f"{tag} {state.login_reply} refused")
        elif user == state.username and password == state.password:
            self._say(f"{tag} OK logged in")
        else:
            self._say(f"{tag} NO [AUTHENTICATIONFAILED] refused")

    def _fetch(self, tag: str, tail: str) -> None:
        state = self.server.state
        if state.fetch_raw is not None:
            self.wfile.write(state.fetch_raw)
            self.wfile.flush()
            return
        wanted_set, _, _items = tail.partition(" ")
        wanted = {int(u) for u in wanted_set.split(",")}
        for index, msg in enumerate(state.messages, start=1):
            if msg.uid not in wanted:
                continue
            stamp = msg.date.strftime("%d-%b-%Y %H:%M:%S +0000")
            headers = msg.headers()
            self.wfile.write(
                (
                    f'* {index} FETCH (UID {msg.uid} INTERNALDATE "{stamp}" '
                    f"BODYSTRUCTURE {msg.structure()} "
                    f"BODY[HEADER.FIELDS (FROM TO CC SUBJECT DATE MESSAGE-ID)] "
                    f"{{{len(headers)}}}\r\n"
                ).encode()
                + headers
                + b")\r\n"
            )
        self.wfile.flush()
        self._say(f"{tag} OK done")

    def _say(self, line: str) -> None:
        self.wfile.write(line.encode() + b"\r\n")
        self.wfile.flush()


class FakeImap(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, state: ServerState) -> None:
        cert, key = _certificate()
        self.cert_pem = cert
        directory = Path(tempfile.mkdtemp(prefix="fake-imap-"))
        (directory / "c.pem").write_bytes(cert)
        (directory / "k.pem").write_bytes(key)
        self.context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.context.load_cert_chain(directory / "c.pem", directory / "k.pem")
        self.state = state
        super().__init__(("127.0.0.1", 0), _Handler)

    @property
    def port(self) -> int:
        return int(self.server_address[1])

    def client_context(self) -> ssl.SSLContext:
        """What the reader under test trusts: this server's own certificate, nothing else."""
        context = ssl.create_default_context(cadata=self.cert_pem.decode())
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        return context

    def start(self) -> None:
        threading.Thread(target=self.serve_forever, daemon=True).start()

    def stop(self) -> None:
        self.shutdown()
        self.server_close()


def loopback(_host: str, _port: int) -> list[str]:
    """The resolver a test hands the reader: the fake server's name is the loopback."""
    return ["127.0.0.1"]
