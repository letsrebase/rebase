"""The safe fetcher (REB-837): HTTPS only, public addresses only, checked again on every
redirect hop, `robots.txt` respected, bounded, text only. A fake transport and a fake
resolver stand in for the network in most tests; the last section runs the real
`pinned_transport` against a TLS server on loopback (a throw-away certificate)."""

import datetime
import socket
import ssl
import threading
import time
from collections.abc import Callable, Iterator, Mapping
from dataclasses import replace
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from rebase_core import web_fetch
from rebase_core.web_fetch import (
    FETCH_MAX_BYTES,
    FETCH_MAX_TEXT_CHARS,
    MAX_REDIRECTS,
    ConnectFailed,
    PinnedRequest,
    RawResponse,
    SafeFetcher,
    Transport,
    html_to_text,
    is_blocked_host,
    is_public_address,
    pinned_transport,
)

PUBLIC = "93.184.216.34"
PUBLIC_B = "151.101.1.69"

PAGE = (
    b"<html><head><title>Ada</title><style>p{color:red}</style></head><body>"
    b"<h1>Backend engineer</h1><p>Builds payment systems.</p>"
    b"<script>track()</script></body></html>"
)


def html(body: bytes = PAGE, content_type: str = "text/html; charset=utf-8") -> RawResponse:
    return RawResponse(200, {"content-type": content_type}, body)


def redirect(location: str, status: int = 302) -> RawResponse:
    return RawResponse(status, {"location": location}, b"")


class FakeNetwork:
    """A resolver and a transport over canned `(host, path)` answers; both record what
    they were asked, so a test can read what would have left."""

    def __init__(
        self,
        answers: Mapping[tuple[str, str], RawResponse],
        dns: Mapping[str, list[str]] | None = None,
    ) -> None:
        self.answers = dict(answers)
        self.dns = dict(dns or {})
        self.resolved: list[str] = []
        self.requests: list[PinnedRequest] = []

    def resolver(self, host: str) -> list[str]:
        self.resolved.append(host)
        return self.dns.get(host, [PUBLIC])

    def transport(self, request: PinnedRequest) -> RawResponse:
        self.requests.append(request)
        key = (request.host, request.target)
        if key in self.answers:
            return self.answers[key]
        if request.target == "/robots.txt":
            return RawResponse(404, {}, b"")
        raise OSError(f"unexpected {key}")

    def fetcher(self) -> SafeFetcher:
        return SafeFetcher(transport=self.transport, resolver=self.resolver)


def test_http_scheme_refused() -> None:
    network = FakeNetwork({})
    result = network.fetcher().fetch("http://example.com/")
    assert result.status == "blocked"
    assert network.requests == [] and network.resolved == []


@pytest.mark.parametrize(
    "url",
    [
        "ftp://example.com/",
        "https://user:secret@example.com/",
        "https://example.com:8443/",
        "https:///nohost",
        "not a url",
    ],
)
def test_other_schemes_credentials_ports_and_junk_refused(url: str) -> None:
    network = FakeNetwork({})
    assert network.fetcher().fetch(url).status == "blocked"
    assert network.requests == []


@pytest.mark.parametrize(
    "url",
    [
        "https://www.linkedin.com/in/someone",
        "https://it.linkedin.com/in/someone",
        "https://linkedin.com/in/someone",
        "https://LinkedIn.com./in/someone",
    ],
)
def test_linkedin_and_subdomain_blocked(url: str) -> None:
    network = FakeNetwork({})
    result = network.fetcher().fetch(url)
    assert result.status == "blocked"
    assert network.requests == [] and network.resolved == []


def test_is_blocked_host_does_not_catch_look_alikes() -> None:
    assert is_blocked_host("www.linkedin.com")
    assert not is_blocked_host("notlinkedin.com")
    assert not is_blocked_host("linkedin.com.example.org")


@pytest.mark.parametrize(
    "address",
    ["127.0.0.1", "10.0.0.5", "169.254.169.254", "::1", "fd00::1", "192.168.1.1",
     "172.16.0.1", "100.64.0.1", "0.0.0.0", "fe80::1", "::ffff:10.0.0.5", "224.0.0.1",
     "64:ff9b::a00:5", "2002:a00:5::1", "::127.0.0.1", "::7f00:1", "::10.0.0.5",
     "fec0::1", "feff::1", "not-an-ip"],
)  # fmt: skip
def test_private_loopback_linklocal_resolution_blocked(address: str) -> None:
    network = FakeNetwork({}, dns={"site.example": [address]})
    result = network.fetcher().fetch("https://site.example/")
    assert result.status == "blocked"
    assert network.requests == []


def test_one_private_answer_among_public_ones_refuses_the_host() -> None:
    network = FakeNetwork({}, dns={"site.example": [PUBLIC, "10.0.0.5"]})
    assert network.fetcher().fetch("https://site.example/").status == "blocked"
    assert network.requests == []


def test_ip_literal_in_url_is_checked_without_resolving() -> None:
    network = FakeNetwork({})
    assert network.fetcher().fetch("https://169.254.169.254/latest/meta-data").status == "blocked"
    assert network.resolved == [] and network.requests == []


def test_public_addresses_accepted() -> None:
    assert is_public_address(PUBLIC)
    assert is_public_address("2606:4700:4700::1111")


def test_redirect_to_private_address_blocked_on_second_hop() -> None:
    network = FakeNetwork(
        {("site.example", "/"): redirect("https://internal.example/admin")},
        dns={"internal.example": ["10.0.0.5"]},
    )
    result = network.fetcher().fetch("https://site.example/")
    assert result.status == "blocked"
    assert result.final_url == "https://internal.example/admin"
    assert all(request.host == "site.example" for request in network.requests)


@pytest.mark.parametrize(
    "location",
    [
        "http://169.254.169.254/latest/meta-data",
        "https://169.254.169.254/latest/meta-data",
        "https://localhost/",
        "http://example.org/",
        "https://www.linkedin.com/in/someone",
    ],
)
def test_redirect_to_forbidden_places_blocked(location: str) -> None:
    network = FakeNetwork(
        {("site.example", "/"): redirect(location)}, dns={"localhost": ["127.0.0.1"]}
    )
    assert network.fetcher().fetch("https://site.example/").status == "blocked"
    assert {request.host for request in network.requests} == {"site.example"}


def test_relative_redirect_is_followed_and_final_url_reported() -> None:
    network = FakeNetwork(
        {("site.example", "/"): redirect("/about"), ("site.example", "/about"): html()}
    )
    result = network.fetcher().fetch("https://site.example/")
    assert result.status == "ok"
    assert result.final_url == "https://site.example/about"


def test_redirect_limit() -> None:
    answers = {("site.example", f"/{n}"): redirect(f"/{n + 1}") for n in range(MAX_REDIRECTS + 3)}
    network = FakeNetwork(answers)
    result = network.fetcher().fetch("https://site.example/0")
    assert result.status == "error"
    pages = [r for r in network.requests if r.target != "/robots.txt"]
    assert len(pages) == MAX_REDIRECTS + 1


def test_redirect_without_location_is_an_error() -> None:
    network = FakeNetwork({("site.example", "/"): RawResponse(302, {}, b"")})
    assert network.fetcher().fetch("https://site.example/").status == "error"


def test_robots_disallow_respected() -> None:
    network = FakeNetwork(
        {
            ("site.example", "/robots.txt"): RawResponse(200, {}, b"User-agent: *\nDisallow: /\n"),
            ("site.example", "/"): html(),
        }
    )
    result = network.fetcher().fetch("https://site.example/")
    assert result.status == "robots"
    assert [r.target for r in network.requests] == ["/robots.txt"]


def test_robots_disallow_one_path_leaves_the_rest() -> None:
    robots = RawResponse(200, {}, b"User-agent: *\nDisallow: /private\n")
    network = FakeNetwork(
        {
            ("site.example", "/robots.txt"): robots,
            ("site.example", "/private/cv"): html(),
            ("site.example", "/public"): html(),
        }
    )
    assert network.fetcher().fetch("https://site.example/private/cv").status == "robots"
    assert network.fetcher().fetch("https://site.example/public").status == "ok"


def test_robots_missing_allows() -> None:
    network = FakeNetwork({("site.example", "/"): html()})
    assert network.fetcher().fetch("https://site.example/").status == "ok"


def test_robots_server_error_disallows() -> None:
    network = FakeNetwork(
        {("site.example", "/robots.txt"): RawResponse(503, {}, b""), ("site.example", "/"): html()}
    )
    assert network.fetcher().fetch("https://site.example/").status == "robots"


def test_robots_redirect_to_www_is_followed() -> None:
    network = FakeNetwork(
        {
            ("site.example", "/robots.txt"): redirect("https://www.site.example/robots.txt", 301),
            ("www.site.example", "/robots.txt"): RawResponse(
                200, {}, b"User-agent: rebase-hub\nDisallow: /\n"
            ),
            ("site.example", "/"): html(),
        }
    )
    assert network.fetcher().fetch("https://site.example/").status == "robots"


def test_robots_redirect_to_private_address_is_not_a_licence() -> None:
    network = FakeNetwork(
        {
            ("site.example", "/robots.txt"): redirect("https://internal.example/robots.txt"),
            ("site.example", "/"): html(),
        },
        dns={"internal.example": ["10.0.0.5"]},
    )
    assert network.fetcher().fetch("https://site.example/").status == "robots"
    assert {r.host for r in network.requests} == {"site.example"}


def test_robots_checked_again_on_the_host_a_redirect_lands_on() -> None:
    network = FakeNetwork(
        {
            ("a.example", "/"): redirect("https://b.example/x"),
            ("b.example", "/robots.txt"): RawResponse(200, {}, b"User-agent: *\nDisallow: /\n"),
            ("b.example", "/x"): html(),
        }
    )
    result = network.fetcher().fetch("https://a.example/")
    assert result.status == "robots"
    assert not any(r.host == "b.example" and r.target == "/x" for r in network.requests)


def test_body_over_limit_is_too_large() -> None:
    network = FakeNetwork({("site.example", "/"): html(b"<p>x</p>" * FETCH_MAX_BYTES)})
    # The real transport stops one byte past the cap; the fake hands the caller that much.
    network.answers[("site.example", "/")] = html(b"a" * (FETCH_MAX_BYTES + 1))
    assert network.fetcher().fetch("https://site.example/").status == "too_large"


def test_body_exactly_at_limit_is_read() -> None:
    body = b"<p>" + b"a" * (FETCH_MAX_BYTES - 7) + b"</p>"
    assert len(body) == FETCH_MAX_BYTES
    network = FakeNetwork({("site.example", "/"): html(body)})
    assert network.fetcher().fetch("https://site.example/").status == "ok"


def test_js_only_page_is_unreadable() -> None:
    page = (
        b"<html><body><div id=root></div><script src=app.js></script>"
        b"<script>boot()</script></body></html>"
    )
    network = FakeNetwork({("site.example", "/"): html(page)})
    result = network.fetcher().fetch("https://site.example/")
    assert result.status == "unreadable"
    assert result.text == ""


def test_non_text_content_is_unreadable() -> None:
    network = FakeNetwork(
        {("site.example", "/"): html(b"%PDF-1.7", content_type="application/pdf")}
    )
    assert network.fetcher().fetch("https://site.example/").status == "unreadable"


def test_http_error_status_is_an_error() -> None:
    network = FakeNetwork({("site.example", "/"): RawResponse(500, {}, b"boom")})
    assert network.fetcher().fetch("https://site.example/").status == "error"


@pytest.mark.parametrize("status", [404, 410])
def test_removed_page_is_unreadable_not_an_error(status: int) -> None:
    network = FakeNetwork({("site.example", "/"): RawResponse(status, {}, b"gone")})
    result = network.fetcher().fetch("https://site.example/")
    assert (result.status, result.text) == ("unreadable", "")


@pytest.mark.parametrize("status", [401, 403, 429, 500, 502, 503])
def test_other_error_statuses_stay_errors(status: int) -> None:
    network = FakeNetwork({("site.example", "/"): RawResponse(status, {}, b"no")})
    assert network.fetcher().fetch("https://site.example/").status == "error"


def test_redirect_to_a_removed_page_is_unreadable() -> None:
    network = FakeNetwork(
        {
            ("site.example", "/"): redirect("/old"),
            ("site.example", "/old"): RawResponse(404, {}, b""),
        }
    )
    result = network.fetcher().fetch("https://site.example/")
    assert (result.status, result.final_url) == ("unreadable", "https://site.example/old")


def test_network_failure_is_an_error_not_an_exception() -> None:
    network = FakeNetwork({})  # the page itself raises OSError in the fake
    assert network.fetcher().fetch("https://site.example/").status == "error"


def test_resolution_failure_is_an_error_not_an_exception() -> None:
    def failing(host: str) -> list[str]:
        raise OSError("no such host")

    fetcher = SafeFetcher(transport=FakeNetwork({}).transport, resolver=failing)
    assert fetcher.fetch("https://nowhere.example/").status == "error"


def test_text_extraction_drops_script_style_and_keeps_headings() -> None:
    network = FakeNetwork({("site.example", "/"): html()})
    result = network.fetcher().fetch("https://site.example/")
    assert result.status == "ok"
    assert "# Backend engineer" in result.text
    assert "Builds payment systems." in result.text
    assert "track()" not in result.text and "color:red" not in result.text
    assert result.text.splitlines()[0] == "Ada"


def test_head_left_open_does_not_swallow_the_page() -> None:
    text = html_to_text("<html><head><title>T</title><body><p>Hello there</p></body></html>")
    assert text is not None and "Hello there" in text


def test_plain_text_page_is_returned_as_text() -> None:
    network = FakeNetwork(
        {("site.example", "/"): html(b"line one\n\n  line two  \n", "text/plain; charset=utf-8")}
    )
    result = network.fetcher().fetch("https://site.example/")
    assert (result.status, result.text) == ("ok", "line one\nline two")


def test_charset_from_header_is_honoured() -> None:
    body = "<p>caff\u00e8</p>".encode("latin-1")
    network = FakeNetwork({("site.example", "/"): html(body, "text/html; charset=iso-8859-1")})
    assert "caff\u00e8" in network.fetcher().fetch("https://site.example/").text


def test_long_text_is_cut() -> None:
    body = b"<p>" + b"word " * FETCH_MAX_TEXT_CHARS + b"</p>"
    network = FakeNetwork({("site.example", "/"): html(body)})
    assert len(network.fetcher().fetch("https://site.example/").text) == FETCH_MAX_TEXT_CHARS


def test_dns_rebinding_guard() -> None:
    """The name is resolved once and the request goes to the address that was checked:
    a resolver that would answer a private address the second time is never asked."""

    class Rebinding(FakeNetwork):
        def resolver(self, host: str) -> list[str]:
            self.resolved.append(host)
            return [PUBLIC] if len(self.resolved) == 1 else ["10.0.0.5"]

    network = Rebinding({("site.example", "/"): html()})
    result = network.fetcher().fetch("https://site.example/")
    assert result.status == "ok"
    assert network.resolved == ["site.example"]
    assert {request.ip for request in network.requests} == {PUBLIC}
    assert {request.host for request in network.requests} == {"site.example"}
    assert [r.target for r in network.requests] == ["/robots.txt", "/"]


def test_each_hop_connects_to_its_own_checked_address() -> None:
    network = FakeNetwork(
        {("a.example", "/"): redirect("https://b.example/"), ("b.example", "/"): html()},
        dns={"a.example": [PUBLIC], "b.example": [PUBLIC_B]},
    )
    assert network.fetcher().fetch("https://a.example/").status == "ok"
    assert [(r.host, r.ip) for r in network.requests if r.target == "/"] == [
        ("a.example", PUBLIC),
        ("b.example", PUBLIC_B),
    ]


def test_request_is_bounded_and_unencoded() -> None:
    network = FakeNetwork({("site.example", "/p?x=1"): html()})
    network.fetcher().fetch("https://site.example/p?x=1")
    page = network.requests[-1]
    assert page.target == "/p?x=1"
    assert page.max_bytes == FETCH_MAX_BYTES
    assert 0 < page.timeout <= 10
    assert page.headers["Accept-Encoding"] == "identity"
    assert page.port == 443


# ---- robots.txt: wildcards, anchors, precedence, unavailable files ----------------------


def robots_network(robots: bytes, *pages: str) -> FakeNetwork:
    answers = {("site.example", "/robots.txt"): RawResponse(200, {}, robots)}
    answers.update({("site.example", page): html() for page in pages})
    return FakeNetwork(answers)


@pytest.mark.parametrize(
    ("robots", "path", "status"),
    [
        # `*` is a wildcard: `/*?` closes every URL with a query.
        ("User-agent: *\nDisallow: /*?\n", "/p?x=1", "robots"),
        ("User-agent: *\nDisallow: /*?\n", "/p", "ok"),
        ("User-agent: *\nDisallow: /a*/private\n", "/ab/private/x", "robots"),
        ("User-agent: *\nDisallow: /a*/private\n", "/ab/public/x", "ok"),
        # `$` anchors the end.
        ("User-agent: *\nDisallow: /*.pdf$\n", "/cv.pdf", "robots"),
        ("User-agent: *\nDisallow: /*.pdf$\n", "/cv.pdf?x=1", "ok"),
        ("User-agent: *\nDisallow: /*.pdf$\n", "/cv.pdfx", "ok"),
        ("User-agent: *\nDisallow: /$\n", "/", "robots"),
        ("User-agent: *\nDisallow: /$\n", "/about", "ok"),
        # The longest match wins, `Allow` wins a tie.
        ("User-agent: *\nDisallow: /a\nAllow: /a/b\n", "/a/b/c", "ok"),
        ("User-agent: *\nDisallow: /a\nAllow: /a/b\n", "/a/c", "robots"),
        ("User-agent: *\nAllow: /x\nDisallow: /x\n", "/x", "ok"),
        ("User-agent: *\nDisallow: /x\nAllow: /x\n", "/x", "ok"),
        ("User-agent: *\nDisallow: /*.html\nAllow: /pub/\n", "/pub/a.html", "robots"),
        ("User-agent: *\nDisallow: /*.html\nAllow: /public/\n", "/public/a.html", "ok"),
        # An empty `Disallow` allows everything; comments and case are ignored.
        ("User-agent: *\nDisallow:\n", "/", "ok"),
        ("USER-AGENT: *  # everyone\nDISALLOW: /private # no\n", "/private/x", "robots"),
        # A group that names us beats the `*` group, in either order.
        ("User-agent: rebase-hub\nAllow: /\n\nUser-agent: *\nDisallow: /\n", "/", "ok"),
        ("User-agent: *\nDisallow: /\n\nUser-agent: rebase-hub\nAllow: /\n", "/", "ok"),
        ("User-agent: other-bot\nDisallow: /\n", "/", "ok"),
        # Several `User-agent` lines share the rules under them.
        ("User-agent: other-bot\nUser-agent: *\nDisallow: /\n", "/", "robots"),
    ],
)  # fmt: skip
def test_robots_rules(robots: str, path: str, status: str) -> None:
    network = robots_network(robots.encode(), path)
    assert network.fetcher().fetch(f"https://site.example{path}").status == status


def test_hostile_robots_pattern_does_not_backtrack() -> None:
    pattern = "/" + "*a" * 40 + "b"
    network = robots_network(f"User-agent: *\nDisallow: {pattern}\n".encode(), "/" + "a" * 4000)
    started = time.monotonic()
    assert network.fetcher().fetch("https://site.example/" + "a" * 4000).status == "ok"
    assert time.monotonic() - started < 2


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
def test_robots_throttled_or_failing_disallows_for_this_pass(status: int) -> None:
    network = FakeNetwork(
        {
            ("site.example", "/robots.txt"): RawResponse(status, {}, b""),
            ("site.example", "/"): html(),
        }
    )
    assert network.fetcher().fetch("https://site.example/").status == "robots"
    assert [r.target for r in network.requests] == ["/robots.txt"]


@pytest.mark.parametrize("status", [401, 403, 404, 410])
def test_robots_missing_or_refused_to_us_allows(status: int) -> None:
    network = FakeNetwork(
        {
            ("site.example", "/robots.txt"): RawResponse(status, {}, b""),
            ("site.example", "/"): html(),
        }
    )
    assert network.fetcher().fetch("https://site.example/").status == "ok"


# ---- several addresses --------------------------------------------------------------


class FlakyNetwork(FakeNetwork):
    """The first address of a host refuses connections."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]
        self.down: set[str] = set()

    def transport(self, request: PinnedRequest) -> RawResponse:
        if request.ip in self.down:
            self.requests.append(request)
            raise ConnectFailed("connection refused")
        return super().transport(request)


def test_next_checked_address_is_tried_when_the_first_does_not_connect() -> None:
    network = FlakyNetwork(
        {("site.example", "/"): html()}, dns={"site.example": [PUBLIC, PUBLIC_B]}
    )
    network.down = {PUBLIC}
    assert network.fetcher().fetch("https://site.example/").status == "ok"
    assert network.resolved == ["site.example"]  # one resolution for the whole fetch
    # The robots file tried both; the page goes straight to the address that answered.
    assert [(r.target, r.ip) for r in network.requests] == [
        ("/robots.txt", PUBLIC),
        ("/robots.txt", PUBLIC_B),
        ("/", PUBLIC_B),
    ]


def test_every_address_down_is_an_error() -> None:
    network = FlakyNetwork({}, dns={"site.example": [PUBLIC, PUBLIC_B]})
    network.down = {PUBLIC, PUBLIC_B}
    # A host nobody can reach has no readable robots file: not a licence, but not a page.
    assert network.fetcher().fetch("https://site.example/").status == "robots"
    assert [r.ip for r in network.requests] == [PUBLIC, PUBLIC_B]


def test_a_failure_after_connecting_does_not_move_to_the_next_address() -> None:
    network = FakeNetwork({}, dns={"site.example": [PUBLIC, PUBLIC_B]})  # the page raises OSError
    assert network.fetcher().fetch("https://site.example/").status == "error"
    assert [(r.target, r.ip) for r in network.requests] == [("/robots.txt", PUBLIC), ("/", PUBLIC)]


def test_unchecked_address_is_never_tried_as_a_fallback() -> None:
    network = FlakyNetwork({}, dns={"site.example": [PUBLIC, "10.0.0.5"]})
    network.down = {PUBLIC}
    assert network.fetcher().fetch("https://site.example/").status == "blocked"
    assert network.requests == []


# ---- the wall-clock limit ----------------------------------------------------------


def test_a_resolver_that_never_answers_is_an_error_within_the_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(web_fetch, "FETCH_TIMEOUT_SECONDS", 0.3)
    release = threading.Event()

    def stuck(host: str) -> list[str]:
        release.wait(10)
        return [PUBLIC]

    started = time.monotonic()
    try:
        result = SafeFetcher(transport=FakeNetwork({}).transport, resolver=stuck).fetch(
            "https://site.example/"
        )
    finally:
        release.set()
    assert result.status == "error"
    assert time.monotonic() - started < 3


def test_a_spent_budget_stops_before_the_next_request(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(web_fetch, "FETCH_TIMEOUT_SECONDS", 0.2)

    class Slow(FakeNetwork):
        def transport(self, request: PinnedRequest) -> RawResponse:
            time.sleep(0.3)  # robots.txt alone uses the whole budget
            return super().transport(request)

    network = Slow({("site.example", "/"): html()})
    assert network.fetcher().fetch("https://site.example/").status == "error"
    assert [r.target for r in network.requests] == ["/robots.txt"]


# ---- the real transport against a TLS server on loopback --------------------------------

LOOPBACK = "127.0.0.1"
NAME = "site.example"  # never resolves anywhere: reaching the server proves the pin
BIG_LENGTH = 512 * 1024 * 1024


def _certificate(directory: Path) -> tuple[Path, Path]:
    """A self-signed certificate for `NAME`, usable as its own trust anchor."""
    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, NAME)])
    now = datetime.datetime.now(datetime.UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(NAME)]), critical=False)
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                key_cert_sign=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .sign(key, hashes.SHA256())
    )
    cert_path, key_path = directory / "cert.pem", directory / "key.pem"
    cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return cert_path, key_path


class TlsServer:
    """An HTTPS server on 127.0.0.1 with scripted misbehaviour, one thread per connection.
    It records the SNI name, the `Host` header and the paths it was asked for."""

    def __init__(self, cert_path: Path, key_path: Path) -> None:
        self.context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.context.load_cert_chain(cert_path, key_path)
        self.context.sni_callback = self._on_sni
        self.sni: list[str | None] = []
        self.hosts: list[str] = []
        self.paths: list[str] = []
        self.big_sent = 0
        self.big_done = threading.Event()
        self._stop = threading.Event()
        self._listener = socket.socket()
        self._listener.bind((LOOPBACK, 0))
        self._listener.listen(16)
        self.port: int = self._listener.getsockname()[1]
        threading.Thread(target=self._accept, daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
        self._listener.close()

    def _on_sni(self, _socket: ssl.SSLObject, name: str, _context: ssl.SSLContext) -> None:
        self.sni.append(name)

    def _accept(self) -> None:
        while not self._stop.is_set():
            try:
                connection, _peer = self._listener.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(connection,), daemon=True).start()

    def _serve(self, raw: socket.socket) -> None:
        try:
            with self.context.wrap_socket(raw, server_side=True) as connection:
                connection.settimeout(10)
                request = b""
                while b"\r\n\r\n" not in request:
                    data = connection.recv(4096)
                    if not data:
                        return
                    request += data
                lines = request.decode("latin-1").split("\r\n")
                path = lines[0].split()[1]
                self.hosts.extend(
                    line.split(":", 1)[1].strip()
                    for line in lines
                    if line.lower().startswith("host:")
                )
                self.paths.append(path)
                self._respond(connection, path)
        except OSError:
            pass  # the client gave up: that is what the tests want
        finally:
            raw.close()

    def _respond(self, connection: ssl.SSLSocket, path: str) -> None:
        if path == "/page":
            body = b"<html><body><h1>Hello from loopback</h1><p>Some text.</p></body></html>"
            connection.sendall(
                b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nConnection: close\r\n"
                + f"Content-Length: {len(body)}\r\n\r\n".encode()
                + body
            )
        elif path == "/body-drip":  # the headers are quick, the body is one byte per tick
            connection.sendall(
                b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nContent-Length: 100000\r\n\r\n"
            )
            while not self._stop.wait(0.2):
                connection.sendall(b"a")
        elif path == "/header-drip":  # the headers never end, one byte per tick
            connection.sendall(b"HTTP/1.1 200 OK\r\n")
            while not self._stop.wait(0.2):
                connection.sendall(b"X")
        elif path == "/big":
            connection.sendall(
                b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\n"
                + f"Content-Length: {BIG_LENGTH}\r\n\r\n".encode()
            )
            chunk = b"a" * 65_536
            try:
                while self.big_sent < BIG_LENGTH:
                    connection.sendall(chunk)
                    self.big_sent += len(chunk)
            finally:
                self.big_done.set()
        else:
            connection.sendall(b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\n\r\n")


@pytest.fixture(scope="module")
def certificate(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    return _certificate(tmp_path_factory.mktemp("tls"))


@pytest.fixture
def server(certificate: tuple[Path, Path]) -> Iterator[TlsServer]:
    tls_server = TlsServer(*certificate)
    yield tls_server
    tls_server.stop()


@pytest.fixture
def trusting(certificate: tuple[Path, Path]) -> ssl.SSLContext:
    """A client that trusts the throw-away certificate and nothing else."""
    return ssl.create_default_context(cafile=str(certificate[0]))


def loopback_transport(server: TlsServer, context: ssl.SSLContext | None) -> Transport:
    """The real `pinned_transport`, pointed at the test server's port (the fetcher only
    ever asks for 443). The context is the only thing a test changes about it."""

    def transport(request: PinnedRequest) -> RawResponse:
        return pinned_transport(replace(request, port=server.port), context=context)

    return transport


@pytest.fixture
def loopback_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Loopback counts as public in this test only: the production check is untouched
    for every other address."""
    real: Callable[[str], bool] = web_fetch.is_public_address
    monkeypatch.setattr(web_fetch, "is_public_address", lambda a: a == LOOPBACK or real(a))


def loopback_fetcher(server: TlsServer, context: ssl.SSLContext | None) -> SafeFetcher:
    return SafeFetcher(
        transport=loopback_transport(server, context), resolver=lambda host: [LOOPBACK]
    )


def raw_request(host: str, target: str, server: TlsServer, timeout: float = 5) -> PinnedRequest:
    return PinnedRequest(
        host=host,
        ip=LOOPBACK,
        port=server.port,
        target=target,
        headers={"User-Agent": "test", "Accept-Encoding": "identity"},
        timeout=timeout,
        max_bytes=FETCH_MAX_BYTES,
    )


def test_pinned_transport_reads_a_page_over_tls(
    server: TlsServer, trusting: ssl.SSLContext
) -> None:
    response = pinned_transport(raw_request(NAME, "/page", server), context=trusting)
    assert response.status == 200
    assert response.headers["content-type"] == "text/html"
    assert b"Hello from loopback" in response.body


def test_connection_goes_to_the_checked_address_with_the_name_in_sni_and_host(
    server: TlsServer, trusting: ssl.SSLContext, loopback_allowed: None
) -> None:
    """`site.example` resolves to nothing real: the request can only arrive because it
    went to the address the resolver returned, while SNI, `Host:` and the certificate
    check all carry the name."""
    asked: list[str] = []

    def resolver(host: str) -> list[str]:
        asked.append(host)
        return [LOOPBACK]

    fetcher = SafeFetcher(transport=loopback_transport(server, trusting), resolver=resolver)
    result = fetcher.fetch(f"https://{NAME}/page")
    assert (result.status, "Hello from loopback" in result.text) == ("ok", True)
    assert asked == [NAME]  # one resolution for robots.txt and the page
    assert server.paths == ["/robots.txt", "/page"]
    assert server.sni == [NAME, NAME]
    assert server.hosts == [f"{NAME}:{server.port}"] * 2  # http.client adds the port


def test_loopback_is_still_refused_without_the_test_allowance(
    server: TlsServer, trusting: ssl.SSLContext
) -> None:
    result = loopback_fetcher(server, trusting).fetch(f"https://{NAME}/page")
    assert result.status == "blocked"
    assert server.paths == []


def test_certificate_is_verified_against_the_hostname(
    server: TlsServer, trusting: ssl.SSLContext, loopback_allowed: None
) -> None:
    """The certificate is trusted but names `site.example`: the same server reached as
    another name must fail the handshake, with nothing sent."""
    with pytest.raises(ssl.SSLCertVerificationError):
        pinned_transport(raw_request("other.example", "/page", server), context=trusting)
    result = loopback_fetcher(server, trusting).fetch("https://other.example/page")
    assert result.status == "robots"  # robots.txt could not be fetched: nothing is read
    assert server.paths == []
    assert "other.example" in server.sni


def test_untrusted_certificate_is_refused(server: TlsServer, loopback_allowed: None) -> None:
    """Production context: the throw-away certificate is not in the system store."""
    with pytest.raises(ssl.SSLCertVerificationError):
        pinned_transport(raw_request(NAME, "/page", server))
    assert loopback_fetcher(server, None).fetch(f"https://{NAME}/page").status == "robots"
    assert server.paths == []


def test_connect_to_a_closed_port_is_a_connect_failure(trusting: ssl.SSLContext) -> None:
    with socket.socket() as probe:
        probe.bind((LOOPBACK, 0))
        port = probe.getsockname()[1]
    request = PinnedRequest(NAME, LOOPBACK, port, "/", {}, 5, FETCH_MAX_BYTES)
    with pytest.raises(ConnectFailed):
        pinned_transport(request, context=trusting)


@pytest.mark.parametrize("path", ["/body-drip", "/header-drip"])
def test_a_dripping_server_cannot_outlast_the_wall_clock_limit(
    path: str,
    server: TlsServer,
    trusting: ssl.SSLContext,
    loopback_allowed: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One byte every 0.2 s would take hours to finish, and no single read ever times out:
    only a deadline across the whole exchange ends it."""
    monkeypatch.setattr(web_fetch, "FETCH_TIMEOUT_SECONDS", 1.0)
    started = time.monotonic()
    result = loopback_fetcher(server, trusting).fetch(f"https://{NAME}{path}")
    elapsed = time.monotonic() - started
    assert result.status == "error"
    assert elapsed < 3
    assert server.paths == ["/robots.txt", path]


def test_drip_ends_the_transport_with_a_timeout(
    server: TlsServer, trusting: ssl.SSLContext
) -> None:
    started = time.monotonic()
    with pytest.raises(TimeoutError):
        pinned_transport(raw_request(NAME, "/body-drip", server, timeout=0.8), context=trusting)
    assert time.monotonic() - started < 3


def test_oversize_body_is_too_large_without_reading_it_all(
    server: TlsServer, trusting: ssl.SSLContext, loopback_allowed: None
) -> None:
    result = loopback_fetcher(server, trusting).fetch(f"https://{NAME}/big")
    assert result.status == "too_large"
    # The client closed after one byte past the cap: the server's writes fail long before
    # the 512 MiB it offered (a few MiB of socket buffers is all it can have pushed out).
    assert server.big_done.wait(10)
    assert 0 < server.big_sent < BIG_LENGTH // 8
