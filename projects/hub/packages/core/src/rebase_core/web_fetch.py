"""The safe fetcher: one HTTPS page of a person's own links, read as plain text.

REB-837 (design 2026-10-27, § Sources): the card may be written from the links a person
gave, so the hub reads a personal site or a portfolio page. A link is a URL somebody else
chose, which makes the fetch the one place the hub is asked to open an address on a
stranger's say-so. What this module refuses, in order:

* anything but `https://` on the default port, and a URL with credentials in it;
* **LinkedIn**, in every form (`linkedin.com` and any subdomain): never fetched, never
  stored, whatever the caller passes (Lorenzo's decision, 2026-10-27);
* a host that resolves to **any** address that is not a public one (private, loopback,
  link-local, unspecified, multicast, reserved, carrier-grade NAT, and an IPv4 address
  wearing an IPv6 coat). One bad answer among several refuses the host;
* a redirect to such a place: every hop is resolved and checked again from the start, and
  at most `MAX_REDIRECTS` hops are followed;
* a page its host's `robots.txt` disallows for us (a robots file that cannot be read
  because the host errs is read as «disallow», RFC 9309);
* a body over `FETCH_MAX_BYTES`, a call that outlasts `FETCH_TIMEOUT_SECONDS`, and a page
  with no text in it (a page that only renders with JavaScript is `unreadable`: no
  headless browser here).

The connection is made to the address that was checked, with the host name kept for the
TLS handshake and the `Host` header, so a name that answers differently the second time
cannot walk the request to a private address after the check (DNS rebinding). If the
first address does not accept the connection, the next checked one is tried. The two
seams, `Transport` and `Resolver`, are what the tests replace.

`FETCH_TIMEOUT_SECONDS` is one wall-clock budget for a whole `fetch()`: name resolution,
`robots.txt`, every hop, the TLS handshake, the headers and the body. Every blocking read
is given only what is left of it, so a server that answers one byte at a time cannot
outlast it.

`rebase_core.http` is not reused because its seam follows no redirect at all and sends to
whatever host the URL names; this one must stop at every hop and look again.
"""

import http.client
import ipaddress
import re
import socket
import ssl
import threading
import time
from collections.abc import Buffer, Callable, Mapping, Sequence
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Literal, cast
from urllib.parse import urljoin, urlsplit

from rebase_core.http import USER_AGENT

FETCH_MAX_BYTES = 1_048_576
FETCH_TIMEOUT_SECONDS = 10
# One address that does not answer must not eat the whole budget before the next is tried.
CONNECT_TIMEOUT_SECONDS = 5
MAX_LINKS_PER_PERSON = 8
MAX_REDIRECTS = 3
# A page's text is cut here, after extraction: a megabyte of HTML can hold a megabyte of
# prose, and the card reads a handful of links, not a book.
FETCH_MAX_TEXT_CHARS = 20_000
# The product token `robots.txt` groups are matched against.
ROBOTS_AGENT = "rebase-hub"

FetchStatus = Literal["ok", "unreadable", "blocked", "robots", "too_large", "error"]

_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_GONE_STATUSES = frozenset({404, 410})
_TEXT_TYPES = ("text/html", "application/xhtml+xml", "text/plain")


@dataclass(frozen=True)
class FetchResult:
    status: FetchStatus
    text: str = ""
    final_url: str | None = None


@dataclass(frozen=True)
class PinnedRequest:
    """One GET to an address that was already checked: connect to `ip`, talk to `host`."""

    host: str
    ip: str
    port: int
    target: str
    headers: Mapping[str, str]
    timeout: float
    max_bytes: int


@dataclass(frozen=True)
class RawResponse:
    status: int
    headers: Mapping[str, str]  # lower-case names
    body: bytes  # at most `max_bytes + 1` long, so the caller can tell «too long»


Transport = Callable[[PinnedRequest], RawResponse]
Resolver = Callable[[str], Sequence[str]]


class ConnectFailed(OSError):
    """The checked address did not accept the connection: the next one may be tried."""


def is_blocked_host(host: str) -> bool:
    """LinkedIn (`linkedin.com` and every subdomain) is out of card generation."""
    name = host.strip().rstrip(".").lower()
    return name == "linkedin.com" or name.endswith(".linkedin.com")


def is_public_address(address: str) -> bool:
    """True only for an address a stranger's page may be fetched from."""
    try:
        ip = ipaddress.ip_address(address.split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            return is_public_address(str(ip.ipv4_mapped))
        packed = ip.packed
        # IPv4-compatible (::/96, deprecated: `::127.0.0.1`) and site-local (fec0::/10,
        # deprecated) addresses are never a place to fetch from.
        if packed[:12] == bytes(12) or (packed[0] == 0xFE and packed[1] & 0xC0 == 0xC0):
            return False
        # NAT64 (64:ff9b::/96) and 6to4 (2002::/16) carry an IPv4 address inside.
        if packed[:12] == bytes.fromhex("0064ff9b0000000000000000"):
            return is_public_address(str(ipaddress.IPv4Address(packed[12:])))
        if packed[:2] == bytes.fromhex("2002"):
            return is_public_address(str(ipaddress.IPv4Address(packed[2:6])))
    return ip.is_global and not ip.is_multicast


def socket_resolver(host: str) -> list[str]:
    """Every address the system resolver gives for `host` (port 443). It has no timeout
    of its own: `SafeFetcher` waits for it only as long as the fetch has left."""
    infos = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    return list(dict.fromkeys(str(info[4][0]) for info in infos))


def _remaining(deadline: float) -> float:
    """What is left of the budget, or a `TimeoutError` when it is spent."""
    left = deadline - time.monotonic()
    if left <= 0:
        raise TimeoutError("fetch deadline")
    return left


class _DeadlineSSLSocket(ssl.SSLSocket):
    """A TLS socket whose every receive is given only what is left of the budget, so
    the headers and the body cannot be dripped past it."""

    deadline = float("inf")

    def recv_into(self, buffer: Buffer, nbytes: int | None = None, flags: int = 0) -> int:
        self.settimeout(_remaining(self.deadline))
        return super().recv_into(buffer, nbytes, flags)


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """An HTTPS connection to a fixed address that still verifies the certificate for,
    and sends `Host:` as, the name the URL carried, all within `deadline`."""

    def __init__(
        self, host: str, ip: str, port: int, deadline: float, context: ssl.SSLContext
    ) -> None:
        context.sslsocket_class = _DeadlineSSLSocket
        super().__init__(host, port, context=context)
        self._ip = ip
        self._deadline = deadline

    def connect(self) -> None:
        try:
            sock = socket.create_connection(
                (self._ip, self.port),
                min(_remaining(self._deadline), CONNECT_TIMEOUT_SECONDS),
            )
        except OSError as failure:
            raise ConnectFailed(str(failure)) from failure
        # The handshake and the request are one call each, bounded by what is left.
        sock.settimeout(_remaining(self._deadline))
        tls = cast(
            _DeadlineSSLSocket,
            self._context.wrap_socket(sock, server_hostname=self.host),  # type: ignore[attr-defined]
        )
        tls.deadline = self._deadline
        self.sock = tls


def pinned_transport(
    request: PinnedRequest, *, context: ssl.SSLContext | None = None
) -> RawResponse:
    """The production `Transport`: HTTPS to the checked address, the whole exchange (connect,
    handshake, headers, body) bounded by `request.timeout` and the body by
    `request.max_bytes`. `context` is for tests that trust a throw-away certificate;
    production uses the system trust store."""
    deadline = time.monotonic() + request.timeout
    connection = _PinnedHTTPSConnection(
        request.host,
        request.ip,
        request.port,
        deadline,
        context or ssl.create_default_context(),
    )
    try:
        connection.request("GET", request.target, headers=dict(request.headers))
        response = connection.getresponse()
        chunks: list[bytes] = []
        size = 0
        while size <= request.max_bytes:
            _remaining(deadline)
            chunk = response.read1(min(65_536, request.max_bytes + 1 - size))
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
        headers = {name.lower(): value for name, value in response.getheaders()}
        return RawResponse(response.status, headers, b"".join(chunks))
    finally:
        connection.close()


_BLOCK_TAGS = frozenset(
    {
        "p", "div", "section", "article", "main", "header", "footer", "nav", "aside",
        "ul", "ol", "li", "table", "tr", "br", "hr", "blockquote", "pre", "figure",
        "figcaption", "dl", "dt", "dd", "form", "address",
    }
)  # fmt: skip
_HEADINGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})
_SKIPPED = frozenset({"script", "style", "noscript", "template", "svg", "head", "iframe"})


class _TextExtractor(HTMLParser):
    """Visible text, one line per block; headings kept as `# ` lines, scripts, styles
    and the `<head>` dropped. `<title>` is kept: a page's title is often its one line of
    description."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self._in_title = False
        self._heading: str | None = None
        self.title = ""
        self.lines: list[str] = []
        self._current: list[str] = []

    def _flush(self) -> None:
        line = " ".join("".join(self._current).split())
        self._current = []
        if line:
            self.lines.append(f"# {line}" if self._heading else line)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "body":
            # `</head>` may be left out, and a head never closed would swallow the page.
            self._skip_depth = 0
            self._in_title = False
        elif tag in _SKIPPED:
            self._skip_depth += 1
        elif tag == "title" and self._skip_depth > 0:
            self._in_title = True
        elif self._skip_depth == 0:
            if tag in _HEADINGS:
                self._flush()
                self._heading = tag
            elif tag in _BLOCK_TAGS:
                self._flush()

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIPPED:
            self._skip_depth = max(0, self._skip_depth - 1)
            self._in_title = False
        elif tag == "title":
            self._in_title = False
        elif self._skip_depth == 0:
            if tag in _HEADINGS:
                self._flush()
                self._heading = None
            elif tag in _BLOCK_TAGS:
                self._flush()

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
        elif self._skip_depth == 0:
            self._current.append(data)

    def text(self) -> str:
        self._flush()
        return "\n".join(self.lines)


def html_to_text(html: str) -> str | None:
    """The readable text of a page, or `None` when it has none (a JavaScript-only page:
    an empty `<div id=root>` and a script bundle)."""
    parser = _TextExtractor()
    parser.feed(html)
    parser.close()
    body = parser.text()
    if not body:
        return None
    title = " ".join(parser.title.split())
    return f"{title}\n{body}" if title else body


_CHARSET = re.compile(r"charset=([\w.:-]+)", re.IGNORECASE)
_META_CHARSET = re.compile(rb"<meta[^>]+charset=[\"']?([\w.:-]+)", re.IGNORECASE)


def _decode(body: bytes, content_type: str) -> str:
    match = _CHARSET.search(content_type)
    name = match.group(1) if match else None
    if name is None:
        meta = _META_CHARSET.search(body[:4096])
        name = meta.group(1).decode("ascii", "ignore") if meta else "utf-8"
    try:
        return body.decode(name, errors="replace")
    except LookupError:
        return body.decode("utf-8", errors="replace")


class _Refused(Exception):
    def __init__(self, status: FetchStatus) -> None:
        self.status: FetchStatus = status


class _RobotsRules:
    """The rules of a `robots.txt` that apply to us, read per RFC 9309: the group naming
    our product token, else the `*` group; `*` is a wildcard and a trailing `$` an end
    anchor; the longest matching rule wins and `Allow` beats `Disallow` on a tie. The
    standard library's parser knows neither wildcards nor anchors."""

    def __init__(self, text: str) -> None:
        groups: list[tuple[list[str], list[tuple[bool, str, bool, int]]]] = []
        reading_agents = False
        for raw in text.splitlines():
            field, separator, value = raw.split("#", 1)[0].partition(":")
            if not separator:
                continue
            field, value = field.strip().lower(), value.strip()
            if field == "user-agent":
                if not reading_agents:
                    groups.append(([], []))
                groups[-1][0].append(value.lower())
                reading_agents = True
            elif field in ("allow", "disallow"):
                reading_agents = False
                if groups and value:  # an empty rule says nothing
                    anchored = value.endswith("$")
                    pattern = value[:-1] if anchored else value
                    groups[-1][1].append((field == "allow", pattern, anchored, len(value)))
        token = ROBOTS_AGENT.lower()
        named = [g for g in groups if any(a and a != "*" and a in token for a in g[0])]
        chosen = named or [g for g in groups if "*" in g[0]]
        self._rules = [rule for _agents, rules in chosen for rule in rules]

    def allows(self, target: str) -> bool:
        best: tuple[int, bool] | None = None
        for allow, pattern, anchored, length in self._rules:
            if _robots_match(pattern, anchored, target) and (
                best is None or (length, allow) > best
            ):
                best = (length, allow)
        return best is None or best[1]


def _robots_match(pattern: str, anchored: bool, target: str) -> bool:
    """`pattern` (`*` is any run of characters) at the start of `target`, and at its end
    too when `anchored`. Greedy, so linear-ish: a hostile file cannot make it backtrack."""
    parts = pattern.split("*")
    if not target.startswith(parts[0]):
        return False
    position = len(parts[0])
    if len(parts) == 1:
        return not anchored or len(target) == position
    for part in parts[1:-1]:
        found = target.find(part, position)
        if found < 0:
            return False
        position = found + len(part)
    last = parts[-1]
    if anchored:
        return len(target) - len(last) >= position and target.endswith(last)
    return target.find(last, position) >= 0


class SafeFetcher:
    def __init__(
        self,
        transport: Transport = pinned_transport,
        resolver: Resolver = socket_resolver,
    ) -> None:
        self._transport = transport
        self._resolver = resolver

    def fetch(self, url: str) -> FetchResult:
        """One page as text, or why it is not. Never raises for a bad link or a bad
        host: the outcome is the `status`. The whole call, robots file and every hop
        included, is bounded by `FETCH_TIMEOUT_SECONDS`; running out is `error`."""
        deadline = time.monotonic() + FETCH_TIMEOUT_SECONDS
        resolved: dict[str, list[str]] = {}
        robots: dict[str, _RobotsRules | _Refused] = {}
        current = url.strip()
        try:
            for _hop in range(MAX_REDIRECTS + 1):
                host, target = self._check(current)
                addresses = self._pin(host, resolved, deadline)
                self._allowed_by_robots(host, target, addresses, robots, resolved, deadline)
                response = self._get(host, addresses, target, deadline)
                if response.status in _REDIRECT_STATUSES:
                    location = response.headers.get("location")
                    if not location:
                        return FetchResult("error", final_url=current)
                    current = urljoin(current, location)
                    continue
                return self._read(response, current)
            return FetchResult("error", final_url=current)  # MAX_REDIRECTS exceeded
        except _Refused as refused:
            return FetchResult(refused.status, final_url=current)
        except (OSError, http.client.HTTPException, ValueError):
            return FetchResult("error", final_url=current)

    def _check(self, url: str) -> tuple[str, str]:
        """The host and request target of a URL the fetcher may open, or a refusal."""
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        if parts.scheme != "https" or not host or parts.username or parts.password:
            raise _Refused("blocked")
        if parts.port not in (None, 443):
            raise _Refused("blocked")
        if is_blocked_host(host):
            raise _Refused("blocked")
        target = parts.path or "/"
        if parts.query:
            target = f"{target}?{parts.query}"
        return host, target

    def _resolve(self, host: str, deadline: float) -> Sequence[str]:
        """The resolver's answer, waited for only as long as the fetch has left: the
        system resolver has no timeout, so it runs on a daemon thread that is abandoned
        when the budget is spent."""
        outcome: list[Sequence[str] | Exception] = []

        def work() -> None:
            try:
                outcome.append(self._resolver(host))
            except Exception as failure:
                outcome.append(failure)

        thread = threading.Thread(target=work, daemon=True)
        thread.start()
        thread.join(max(deadline - time.monotonic(), 0))
        if not outcome:
            raise _Refused("error")
        answer = outcome[0]
        if isinstance(answer, Exception):
            raise answer
        return answer

    def _pin(self, host: str, resolved: dict[str, list[str]], deadline: float) -> list[str]:
        """The checked addresses this fetch will use for `host`: every address the name
        resolves to must be public, and the same answer serves the robots file and the
        page (the first that accepts a connection is tried first from then on)."""
        if host in resolved:
            return resolved[host]
        try:
            ipaddress.ip_address(host)
            addresses: Sequence[str] = [host]
        except ValueError:
            addresses = self._resolve(host, deadline)
        if not addresses or not all(is_public_address(address) for address in addresses):
            raise _Refused("blocked")
        resolved[host] = list(addresses)
        return resolved[host]

    def _get(self, host: str, addresses: list[str], target: str, deadline: float) -> RawResponse:
        """One GET to the first checked address that accepts a connection. Only a failed
        connect moves on to the next address; a failure after that is the answer."""
        failure: OSError = ConnectFailed("no address")
        for position, ip in enumerate(addresses):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise _Refused("error")
            try:
                response = self._transport(
                    PinnedRequest(
                        host=host,
                        ip=ip,
                        port=443,
                        target=target,
                        headers={
                            "User-Agent": USER_AGENT,
                            "Accept": "text/html,text/plain;q=0.9",
                            # No compressed body: its size would be known only after the bomb.
                            "Accept-Encoding": "identity",
                        },
                        timeout=min(remaining, FETCH_TIMEOUT_SECONDS),
                        max_bytes=FETCH_MAX_BYTES,
                    )
                )
            except ConnectFailed as refused_connection:
                failure = refused_connection
                continue
            if position:
                addresses.insert(0, addresses.pop(position))
            return response
        raise failure

    def _allowed_by_robots(
        self,
        host: str,
        target: str,
        addresses: list[str],
        cache: dict[str, _RobotsRules | _Refused],
        resolved: dict[str, list[str]],
        deadline: float,
    ) -> None:
        if host not in cache:
            cache[host] = self._read_robots(host, addresses, resolved, deadline)
        entry = cache[host]
        if isinstance(entry, _Refused):
            raise entry
        if not entry.allows(target):
            raise _Refused("robots")

    def _read_robots(
        self, host: str, addresses: list[str], resolved: dict[str, list[str]], deadline: float
    ) -> _RobotsRules | _Refused:
        """`robots.txt` at the host's root. A redirect (`example.com` to `www.`) is followed
        through the same checks as a page; a missing file (404, 410, any other 4xx but 429)
        allows everything; one the host fails to serve (429, 5xx, a network error, a loop)
        disallows everything for this pass, as RFC 9309 asks. A spent budget is `error`."""
        current = f"https://{host}/robots.txt"
        try:
            response = self._get(host, addresses, "/robots.txt", deadline)
            for _hop in range(MAX_REDIRECTS):
                if response.status not in _REDIRECT_STATUSES:
                    break
                location = response.headers.get("location")
                if not location:
                    return _Refused("robots")
                current = urljoin(current, location)
                next_host, next_target = self._check(current)
                next_addresses = self._pin(next_host, resolved, deadline)
                response = self._get(next_host, next_addresses, next_target, deadline)
        except (OSError, http.client.HTTPException) as failure:
            spent = isinstance(failure, TimeoutError) or time.monotonic() >= deadline
            return _Refused("error" if spent else "robots")
        except _Refused as refused:
            # A robots file that redirects somewhere forbidden is not a licence to fetch.
            return _Refused("robots" if refused.status == "blocked" else refused.status)
        if 400 <= response.status < 500 and response.status != 429:
            return _RobotsRules("")
        if response.status != 200 or len(response.body) > FETCH_MAX_BYTES:
            return _Refused("robots")
        return _RobotsRules(response.body.decode("utf-8", errors="replace"))

    def _read(self, response: RawResponse, url: str) -> FetchResult:
        if response.status in _GONE_STATUSES:
            # The page is not there any more: «unreadable», so what was stored for it is
            # replaced instead of kept for ever. A 5xx or a network error stays `error`.
            return FetchResult("unreadable", final_url=url)
        if response.status != 200:
            return FetchResult("error", final_url=url)
        if len(response.body) > FETCH_MAX_BYTES:
            return FetchResult("too_large", final_url=url)
        content_type = response.headers.get("content-type", "text/html")
        kind = content_type.split(";", 1)[0].strip().lower()
        if kind not in _TEXT_TYPES:
            return FetchResult("unreadable", final_url=url)
        decoded = _decode(response.body, content_type)
        if kind == "text/plain":
            text = "\n".join(line.strip() for line in decoded.splitlines() if line.strip())
        else:
            text = html_to_text(decoded) or ""
        if not text.strip():
            return FetchResult("unreadable", final_url=url)
        return FetchResult("ok", text[:FETCH_MAX_TEXT_CHARS], url)
