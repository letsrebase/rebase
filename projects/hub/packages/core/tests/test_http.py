"""The one urllib call every outbound request goes through."""

import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

import pytest

import rebase_core.http as http_module
from rebase_core.http import (
    ENGAGEMENTS_TIMEOUT_SECONDS,
    HTTP_TIMEOUT_SECONDS,
    MAX_BODY_BYTES,
    MAX_DOWNLOAD_BYTES,
    USER_AGENT,
    urllib_call,
    urllib_download_call,
    urllib_engagements_call,
)


class _Response:
    status = 200

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self, amt: int = -1) -> bytes:
        return b"{}"


def test_every_call_names_itself_unless_the_caller_already_did(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cloudflare in front of Resend and of OpenAI's conversions endpoint answers
    `403 error code: 1010` to urllib's default signature: without a name of our own
    every login mail and every conversion is refused before reaching the API."""
    seen: list[Any] = []

    def fake_open(request: Any, timeout: float) -> _Response:
        seen.append(request)
        return _Response()

    monkeypatch.setattr(http_module._OPENER, "open", fake_open)
    urllib_call("POST", "https://api.example.test/x", {"Content-Type": "application/json"}, b"{}")
    urllib_call("GET", "https://api.example.test/y", {"User-Agent": "altro/1"}, b"")
    assert seen[0].get_header("User-agent") == USER_AGENT
    assert USER_AGENT.startswith("rebase-hub/")
    assert seen[1].get_header("User-agent") == "altro/1"
    assert seen[0].get_header("Content-type") == "application/json"
    # A GET with nothing to send carries no body at all: `data=b""` would make urllib
    # write `Content-Length: 0` and a form content type on a request some proxies then
    # refuse (the registry read of ORB-142 is such a GET).
    assert seen[1].data is None
    assert seen[0].data == b"{}"


def test_the_engagements_seam_waits_for_a_space_to_be_provisioned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The first link of a new freelancer makes the CRM provision a database for their
    space (REB-498): that one client waits 90 seconds, every other call still ten."""
    timeouts: list[float] = []

    def fake_open(request: Any, timeout: float) -> _Response:
        timeouts.append(timeout)
        return _Response()

    monkeypatch.setattr(http_module._OPENER, "open", fake_open)
    urllib_engagements_call("PUT", "https://pigro.example.test/x", {}, b"{}")
    urllib_call("GET", "https://api.example.test/y", {}, b"")
    urllib_download_call("GET", "https://api.example.test/z", {}, b"")
    assert timeouts == [ENGAGEMENTS_TIMEOUT_SECONDS, HTTP_TIMEOUT_SECONDS, HTTP_TIMEOUT_SECONDS]
    assert ENGAGEMENTS_TIMEOUT_SECONDS == 90


def test_an_http_error_is_a_status_not_an_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    import urllib.error

    def refuse(request: Any, timeout: float) -> Any:
        raise urllib.error.HTTPError(request.full_url, 403, "Forbidden", {}, None)  # type: ignore[arg-type]

    monkeypatch.setattr(http_module._OPENER, "open", refuse)
    status, _ = urllib_call("GET", "https://api.example.test/z", {}, b"")
    assert status == 403


class _Redirecting(BaseHTTPRequestHandler):
    """Answers `/lookup` with `code` (default 302) to `/altrove` on itself, and
    anything else with a plain 200. A client that followed the redirect would re-send
    whatever headers it carried, including a bearer, to wherever `Location` points."""

    seen: list[tuple[str, str, str | None]] = []
    code = 302

    def do_GET(self) -> None:  # noqa: N802 - the name http.server dispatches on
        _Redirecting.seen.append((self.command, self.path, self.headers.get("Authorization")))
        if self.path == "/lookup":
            self.send_response(_Redirecting.code)
            self.send_header("Location", "/altrove")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        answer = b'{"ok": true}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(answer)))
        self.end_headers()
        self.wfile.write(answer)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        return


@pytest.fixture
def redirecting_server() -> Iterator[str]:
    _Redirecting.seen = []
    _Redirecting.code = 302
    server = HTTPServer(("127.0.0.1", 0), _Redirecting)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.parametrize("code", [301, 302, 303, 307, 308])
def test_a_redirect_is_not_followed_and_the_bearer_stays_where_it_was_sent(
    redirecting_server: str, code: int
) -> None:
    """The registry read and the mail both carry a bearer no third party may see. Every
    redirect status `HTTPRedirectHandler` knows must come back as that status, not send
    the bearer on to whatever host `Location` names (REB-242): a fix that only covered
    302 would still leak it on a 301 or a 308."""
    _Redirecting.code = code
    status, _ = urllib_call(
        "GET", f"{redirecting_server}/lookup", {"Authorization": "Bearer secret"}, b""
    )
    assert status == code
    # Exactly one request reached the server; nothing went to `/altrove`.
    assert _Redirecting.seen == [("GET", "/lookup", "Bearer secret")]


class _TooBig(BaseHTTPRequestHandler):
    """Answers every request with a body well past the seam's cap."""

    def do_GET(self) -> None:  # noqa: N802
        payload = b"x" * (MAX_BODY_BYTES + 1000)
        self.send_response(200)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        return


@pytest.fixture
def oversized_server() -> Iterator[str]:
    server = HTTPServer(("127.0.0.1", 0), _TooBig)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


def test_the_body_is_capped_so_a_wrong_endpoint_cannot_choose_the_allocation(
    oversized_server: str,
) -> None:
    """A wrong `REBASE_PIGRO_API_URL` or a compromised Resend must not decide how many
    bytes this process reads into memory (REB-242). The cap is one byte past
    `MAX_BODY_BYTES`, so the caller can tell "too long" from "exactly the cap"."""
    status, body = urllib_call("GET", oversized_server, {}, b"")
    assert status == 200
    assert len(body) == MAX_BODY_BYTES + 1


def test_a_download_reads_past_the_json_cap_and_keeps_a_cap_of_its_own(
    oversized_server: str,
) -> None:
    """The sealed PDFs the Documenso client downloads (REB-387) are larger than any JSON
    the hub reads: the download seam takes the whole body the JSON seam would cut, and
    is still capped, one byte past its own limit."""
    status, body = urllib_download_call("GET", oversized_server, {}, b"")
    assert status == 200
    assert len(body) == MAX_BODY_BYTES + 1000
    assert MAX_DOWNLOAD_BYTES > MAX_BODY_BYTES


class _TooBigForDownload(BaseHTTPRequestHandler):
    """Answers every request with a body past the download seam's own cap."""

    def do_GET(self) -> None:  # noqa: N802
        payload = b"x" * (MAX_DOWNLOAD_BYTES + 1000)
        self.send_response(200)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        return


@pytest.fixture
def oversized_download_server() -> Iterator[str]:
    server = HTTPServer(("127.0.0.1", 0), _TooBigForDownload)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


def test_the_download_seam_stops_at_its_own_cap_past_the_json_ones(
    oversized_download_server: str,
) -> None:
    """The download seam's own cap is `MAX_DOWNLOAD_BYTES`, not the JSON seam's: a body
    past it is still read only one byte past `MAX_DOWNLOAD_BYTES` (correction 1)."""
    status, body = urllib_download_call("GET", oversized_download_server, {}, b"")
    assert status == 200
    assert len(body) == MAX_DOWNLOAD_BYTES + 1
