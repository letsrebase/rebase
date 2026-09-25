"""The hub client never raises on the network and never reaches a hub outside this
machine (ORB-173).

Every path ends in a `MemberLookup`: the member, the non-member, a refused token, a hub
that is down, a body that is not the shape or is too long, an installation with no
token at all, and a redirect. The HTTP seam is a fake that records the one request that
would have left; two tests use the real `urllib` transport against loopback, one on a
closed port and one on a tiny server that redirects."""

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from pigrocrm.core.config import Settings
from pigrocrm.core.tenants.hub import LOOKUP_PATH, MAX_BODY_BYTES, MemberLookup, lookup_member

TOKEN = "un-token-lungo-condiviso-con-l-hub"


def test_engagements_token_defaults_empty_and_hides_from_repr() -> None:
    """Not a hub-lookup test: `engagements_token` guards a door the hub reaches
    through, same as `registry_token` above, and this is the file `registry_token`
    already lives in (REB-490)."""
    settings = Settings(_env_file=None)
    assert settings.engagements_token == ""
    assert "engagements_token" not in repr(Settings(engagements_token="x", _env_file=None))


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "jwt_secret": "test-secret-for-the-core-test-suite-only",
        "registry_token": TOKEN,
        "hub_url": "https://letsrebase.com/",
        "_env_file": None,
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


class FakeHub:
    """Answers what the test says and keeps the request it received."""

    def __init__(self, status: int = 200, body: object = None) -> None:
        self.status = status
        self.body = json.dumps(body).encode() if body is not None else b""
        self.calls: list[tuple[str, str, dict[str, str], bytes]] = []
        self.raises: Exception | None = None

    def __call__(
        self, method: str, url: str, headers: dict[str, str], body: bytes
    ) -> tuple[int, bytes]:
        self.calls.append((method, url, headers, body))
        if self.raises is not None:
            raise self.raises
        return self.status, self.body


def test_a_member_comes_back_with_the_two_names_and_one_post_left_with_the_bearer() -> None:
    hub = FakeHub(body={"membro": True, "nome": "Ada", "cognome": "Lovelace"})
    found = lookup_member(_settings(), "  Ada@Studio.it ", http=hub)
    assert found == MemberLookup(membro=True, nome="Ada", cognome="Lovelace")
    # The address is in the body and nowhere in the URL: no access log keeps it.
    assert [(m, u) for m, u, _, _ in hub.calls] == [
        ("POST", f"https://letsrebase.com{LOOKUP_PATH}")
    ]
    method, url, headers, body = hub.calls[0]
    assert json.loads(body) == {"email": "ada@studio.it"}
    assert headers["Authorization"] == f"Bearer {TOKEN}"
    assert headers["Content-Type"] == "application/json"


def test_a_non_member_is_a_plain_false() -> None:
    hub = FakeHub(body={"membro": False, "nome": None, "cognome": None})
    assert lookup_member(_settings(), "nessuno@example.org", http=hub) == MemberLookup(membro=False)


def test_a_refused_token_is_not_a_member_and_not_an_error() -> None:
    hub = FakeHub(status=401, body={"detail": "token non valido"})
    assert lookup_member(_settings(), "ada@studio.it", http=hub) == MemberLookup(membro=False)


def test_a_hub_that_is_down_is_not_a_member_and_not_an_error() -> None:
    hub = FakeHub()
    hub.raises = OSError("connection refused")
    assert lookup_member(_settings(), "ada@studio.it", http=hub) == MemberLookup(membro=False)


def test_only_the_network_and_the_parse_are_swallowed() -> None:
    """The suite's network guard (`projects/pigrocrm/conftest.py`) raises `AssertionError`
    on a socket to anything but this machine; a client that ate it would turn «you
    reached production» into «not a member». So would a programming error."""
    hub = FakeHub()
    hub.raises = AssertionError("a socket left the machine")
    with pytest.raises(AssertionError):
        lookup_member(_settings(), "ada@studio.it", http=hub)


def test_a_body_that_is_not_the_shape_or_is_too_long_is_not_a_member() -> None:
    garbled = FakeHub()
    garbled.body = b"<html>not json</html>"
    assert lookup_member(_settings(), "ada@studio.it", http=garbled) == MemberLookup(membro=False)
    wrong_shape = FakeHub(body={"membro": "forse"})
    assert lookup_member(_settings(), "ada@studio.it", http=wrong_shape) == MemberLookup(
        membro=False
    )
    too_long = FakeHub(body={"membro": True, "nome": "A" * MAX_BODY_BYTES, "cognome": "L"})
    assert len(too_long.body) > MAX_BODY_BYTES
    assert lookup_member(_settings(), "ada@studio.it", http=too_long) == MemberLookup(membro=False)


def test_without_a_token_or_a_url_or_an_address_the_hub_is_never_asked() -> None:
    hub = FakeHub(body={"membro": True, "nome": "Ada", "cognome": "Lovelace"})
    assert lookup_member(_settings(registry_token=""), "ada@studio.it", http=hub) == MemberLookup(
        membro=False
    )
    assert lookup_member(_settings(hub_url=""), "ada@studio.it", http=hub) == MemberLookup(
        membro=False
    )
    assert lookup_member(_settings(), "   ", http=hub) == MemberLookup(membro=False)
    assert hub.calls == []


def test_the_real_transport_treats_a_closed_port_as_not_a_member() -> None:
    """No fake: the default `urllib` call against a loopback port nobody listens on.
    Refused at once, and the answer is the same `false` every other failure gives."""
    assert lookup_member(_settings(hub_url="http://127.0.0.1:9"), "ada@studio.it") == (
        MemberLookup(membro=False)
    )


class _Redirecting(BaseHTTPRequestHandler):
    """Answers the lookup with a 302 to `/altrove` on itself, and `/altrove` with a
    member. A client that followed the redirect would come back with `membro: true` and
    would have re-sent the bearer to wherever `Location` pointed."""

    seen: list[tuple[str, str, str | None, bytes]] = []

    def do_POST(self) -> None:  # noqa: N802 - the name http.server dispatches on
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length)
        _Redirecting.seen.append((self.command, self.path, self.headers.get("Authorization"), body))
        if self.path == LOOKUP_PATH:
            self.send_response(302)
            self.send_header("Location", "/altrove")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        answer = json.dumps({"membro": True, "nome": "Ada", "cognome": "Lovelace"}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(answer)))
        self.end_headers()
        self.wfile.write(answer)

    def do_GET(self) -> None:  # noqa: N802
        self.do_POST()

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        return


@pytest.fixture
def redirecting_hub() -> Iterator[str]:
    _Redirecting.seen = []
    server = HTTPServer(("127.0.0.1", 0), _Redirecting)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


def test_a_redirect_is_not_followed_and_the_bearer_stays_where_it_was_sent(
    redirecting_hub: str,
) -> None:
    found = lookup_member(_settings(hub_url=redirecting_hub), "ada@studio.it")
    assert found == MemberLookup(membro=False)
    # Exactly one request reached the server, the POST to the lookup with the bearer and
    # the address in the body; nothing went to `/altrove`.
    assert [(m, p) for m, p, _, _ in _Redirecting.seen] == [("POST", LOOKUP_PATH)]
    assert _Redirecting.seen[0][2] == f"Bearer {TOKEN}"
    assert json.loads(_Redirecting.seen[0][3]) == {"email": "ada@studio.it"}
