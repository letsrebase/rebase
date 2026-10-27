"""The safe fetcher (REB-837): HTTPS only, public addresses only, checked again on every
redirect hop, `robots.txt` respected, bounded, text only. A fake transport and a fake
resolver stand in for the network: no test here opens a socket."""

from collections.abc import Mapping

import pytest

from rebase_core.web_fetch import (
    FETCH_MAX_BYTES,
    FETCH_MAX_TEXT_CHARS,
    MAX_REDIRECTS,
    PinnedRequest,
    RawResponse,
    SafeFetcher,
    html_to_text,
    is_blocked_host,
    is_public_address,
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
     "64:ff9b::a00:5", "2002:a00:5::1", "not-an-ip"],
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
