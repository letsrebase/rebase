import json
from urllib.parse import parse_qs

import pytest
from fakes.fake_gmail import FakeGmail, FakeMessage

from pigrocrm.core.gmail.errors import GoogleCallFailed
from pigrocrm.core.gmail.transport import GmailTransport
from pigrocrm.core.http import (
    MAX_ATTEMPTS,
    MAX_HONOURED_RETRY_AFTER_SECONDS,
    NETWORK_ERROR_STATUS,
    HttpCall,
)

TOKEN = "ya29.a0-ACCESS-TOKEN-VALUE"
URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages"
TOKEN_URL = "https://oauth2.googleapis.com/token"

Response = tuple[int, bytes, dict[str, str]]
Call = tuple[str, str, dict[str, str], bytes | None]


def _responder(*responses: Response) -> tuple[HttpCall, list[Call]]:
    queue = list(responses)
    calls: list[Call] = []

    def http(
        method: str,
        url: str,
        headers: dict[str, str],
        body: bytes | None,
        *,
        max_bytes: int | None = None,
    ) -> Response:
        calls.append((method, url, headers, body))
        return queue.pop(0) if queue else (200, b"{}", {})

    return http, calls


def test_a_successful_call_returns_parsed_json_and_carries_the_bearer() -> None:
    http, calls = _responder((200, json.dumps({"messages": []}).encode(), {}))
    result = GmailTransport(http=http).json("GET", URL, token=TOKEN, what="elenco messaggi")
    assert result == {"messages": []}
    assert calls[0][2]["Authorization"] == f"Bearer {TOKEN}"


def test_a_body_is_sent_as_json_with_its_content_type() -> None:
    """The Gmail send endpoint is the only caller that posts a body, and it posts JSON.
    A missing `Content-Type` makes Google answer 400 for a request that is otherwise
    correct, which is the kind of defect that only ever shows up in production."""
    http, calls = _responder((200, b"{}", {}))
    GmailTransport(http=http).json("POST", URL, token=TOKEN, body={"raw": "encoded"}, what="invio")
    method, _, headers, body = calls[0]
    assert method == "POST"
    assert headers["Content-Type"] == "application/json"
    assert body is not None
    assert json.loads(body.decode()) == {"raw": "encoded"}


def test_a_call_without_a_body_sends_none_and_declares_no_content_type() -> None:
    http, calls = _responder((200, b"{}", {}))
    GmailTransport(http=http).json("GET", URL, token=TOKEN, what="elenco")
    _, _, headers, body = calls[0]
    assert body is None
    assert "Content-Type" not in headers


def test_an_empty_success_body_parses_as_an_empty_object() -> None:
    """A 204 from `messages.trash` carries no body at all. `json.loads(b"")` raises,
    so the empty case has to be handled rather than discovered."""
    http, _ = _responder((204, b"", {}))
    assert GmailTransport(http=http).json("POST", URL, token=TOKEN, what="cestino") == {}


def test_a_429_is_retried_and_honours_the_servers_own_retry_after() -> None:
    """gdrive.py's seam cannot read response headers, so its backoff is a fixed
    exponential schedule and it says so in a comment. This seam is widened to carry
    them, because a 429 from Gmail names the delay and guessing is worse."""
    slept: list[float] = []
    http, calls = _responder(
        (
            429,
            json.dumps({"error": {"status": "RESOURCE_EXHAUSTED"}}).encode(),
            {"Retry-After": "3"},
        ),
        (200, b"{}", {}),
    )
    GmailTransport(http=http, sleep=slept.append).json("GET", URL, token=TOKEN, what="elenco")
    assert slept == [3.0]
    assert len(calls) == 2


def test_retry_after_is_read_whatever_case_the_server_spelled_it_in() -> None:
    """HTTP header names are case-insensitive (RFC 9110 5.1) and the casing a proxy
    hands back is not ours to predict. A lookup that only matches `Retry-After`
    silently degrades to the guessed schedule, which is exactly the bug this seam was
    widened to remove -- and it degrades *quietly*, so nothing would report it."""
    slept: list[float] = []
    http, _ = _responder((429, b"{}", {"retry-after": "7"}), (200, b"{}", {}))
    GmailTransport(http=http, sleep=slept.append).json("GET", URL, token=TOKEN, what="elenco")
    assert slept == [7.0]


def test_an_absurd_retry_after_is_clamped_rather_than_obeyed() -> None:
    """A synchronous sync holding a request open for an hour because a header said so
    is an outage with extra steps. The hint is honoured, but bounded."""
    slept: list[float] = []
    http, _ = _responder((503, b"{}", {"Retry-After": "3600"}), (200, b"{}", {}))
    GmailTransport(http=http, sleep=slept.append).json("GET", URL, token=TOKEN, what="elenco")
    assert slept == [MAX_HONOURED_RETRY_AFTER_SECONDS]


def test_a_non_positive_retry_after_falls_back_to_the_schedule() -> None:
    """`Retry-After: 0` would busy-loop against a server that has just asked for room."""
    slept: list[float] = []
    http, _ = _responder((429, b"{}", {"Retry-After": "0"}), (200, b"{}", {}))
    GmailTransport(http=http, sleep=slept.append).json("GET", URL, token=TOKEN, what="elenco")
    assert slept == [0.5]


def test_a_401_is_not_retried_because_it_will_not_heal() -> None:
    body = json.dumps(
        {"error": {"code": 401, "status": "UNAUTHENTICATED", "message": "Invalid Credentials"}}
    ).encode()
    http, calls = _responder((401, body, {}))
    with pytest.raises(GoogleCallFailed) as caught:
        GmailTransport(http=http, sleep=lambda _: None).json("GET", URL, token=TOKEN, what="elenco")
    assert len(calls) == 1
    assert caught.value.failure.status == 401
    assert caught.value.failure.error_code == "UNAUTHENTICATED"


def test_the_older_errors_reason_dialect_is_read_as_well() -> None:
    """Google has never fully retired the v1 shape: `{"error": {"errors": [{"reason":
    "rateLimitExceeded"}]}}` still comes back from the Gmail API, and it carries no
    `status` at all. A parser that reads only `status` loses the code on exactly the
    responses whose code decides whether to retry."""
    body = json.dumps(
        {"error": {"code": 403, "errors": [{"reason": "rateLimitExceeded"}]}}
    ).encode()
    http, _ = _responder((403, body, {}))
    with pytest.raises(GoogleCallFailed) as caught:
        GmailTransport(http=http, sleep=lambda _: None).json("GET", URL, token=TOKEN, what="elenco")
    assert caught.value.failure.error_code == "rateLimitExceeded"


def test_a_body_that_is_not_json_leaves_the_code_empty_rather_than_crashing() -> None:
    """A 502 from a load balancer in front of Google is an HTML page. Failing to parse
    it must not replace the upstream failure with a `JSONDecodeError` from our own
    code -- the operator would then be debugging the wrong program."""
    http, calls = _responder(*[(502, b"<html>Bad Gateway</html>", {})] * MAX_ATTEMPTS)
    with pytest.raises(GoogleCallFailed) as caught:
        GmailTransport(http=http, sleep=lambda _: None).json("GET", URL, token=TOKEN, what="elenco")
    assert caught.value.failure.status == 502
    assert caught.value.failure.error_code == ""
    assert len(calls) == MAX_ATTEMPTS


def test_a_network_failure_becomes_a_synthetic_status_and_is_retried() -> None:
    slept: list[float] = []
    http, calls = _responder(
        (NETWORK_ERROR_STATUS, b'{"error":{"message":"timed out"}}', {}),
        (200, b"{}", {}),
    )
    GmailTransport(http=http, sleep=slept.append).json("GET", URL, token=TOKEN, what="elenco")
    assert len(calls) == 2
    assert slept == [0.5]


def test_it_gives_up_after_four_attempts_rather_than_forever() -> None:
    slept: list[float] = []
    http, calls = _responder(*[(503, b"{}", {})] * 6)
    with pytest.raises(GoogleCallFailed):
        GmailTransport(http=http, sleep=slept.append).json("GET", URL, token=TOKEN, what="elenco")
    assert len(calls) == 4
    assert slept == [0.5, 1.0, 2.0]


def test_the_token_endpoint_surfaces_invalid_grant_as_a_machine_readable_code() -> None:
    """The previous system's parseGoogleError truncated the message to 400 characters and returned
    500, so a revoked token and a flaky network produced the same screen and therefore
    the same wrong reaction: retry. The code is what makes them distinguishable."""
    body = json.dumps(
        {"error": "invalid_grant", "error_description": "Token has been expired or revoked."}
    ).encode()
    http, calls = _responder((400, body, {}))
    with pytest.raises(GoogleCallFailed) as caught:
        GmailTransport(http=http, sleep=lambda _: None).form(
            TOKEN_URL, {"grant_type": "refresh_token"}, what="refresh"
        )
    assert caught.value.failure.error_code == "invalid_grant"
    assert len(calls) == 1, "invalid_grant is terminal: retrying it is a bug"


def test_the_form_post_is_urlencoded_and_carries_no_bearer() -> None:
    """This is the one call that carries the client secret, and it carries it in the
    body because that is what the OAuth token endpoint takes. An `Authorization`
    header here would be a second place a credential lives for no reason."""
    http, calls = _responder((200, json.dumps({"access_token": "x"}).encode(), {}))
    GmailTransport(http=http).form(
        TOKEN_URL,
        {"grant_type": "refresh_token", "refresh_token": "1//0secret", "client_secret": "shh"},
        what="refresh",
    )
    method, url, headers, body = calls[0]
    assert (method, url) == ("POST", TOKEN_URL)
    assert headers == {"Content-Type": "application/x-www-form-urlencoded"}
    assert "Authorization" not in headers
    assert body is not None
    assert parse_qs(body.decode()) == {
        "grant_type": ["refresh_token"],
        "refresh_token": ["1//0secret"],
        "client_secret": ["shh"],
    }


def test_no_failure_path_puts_the_token_in_the_exception() -> None:
    http, _ = _responder((403, b'{"error":{"status":"PERMISSION_DENIED"}}', {}))
    with pytest.raises(GoogleCallFailed) as caught:
        GmailTransport(http=http, sleep=lambda _: None).json("GET", URL, token=TOKEN, what="elenco")
    assert TOKEN not in str(caught.value)
    assert TOKEN not in repr(caught.value.failure)
    assert TOKEN not in repr(caught.value)
    assert TOKEN not in str(caught.value.args)


def test_no_failure_path_of_the_token_endpoint_puts_the_secrets_in_the_exception() -> None:
    """The `form` call carries the refresh token *and* the client secret in its body,
    so it is the one request whose failure would be most expensive to log. The seam
    keeps the request out of the failure entirely: an `UpstreamFailure` holds a status,
    a code from Google's published vocabulary, and a number."""
    secret_body = {"refresh_token": "1//0-THE-REFRESH-TOKEN", "client_secret": "GOCSPX-shh"}
    http, _ = _responder((400, b'{"error":"invalid_grant"}', {}))
    with pytest.raises(GoogleCallFailed) as caught:
        GmailTransport(http=http, sleep=lambda _: None).form(TOKEN_URL, secret_body, what="refresh")
    rendered = f"{caught.value} {caught.value!r} {caught.value.args} {caught.value.failure!r}"
    for secret in secret_body.values():
        assert secret not in rendered


def test_a_malformed_retry_after_does_not_crash_the_backoff() -> None:
    slept: list[float] = []
    http, _ = _responder(
        (429, b"{}", {"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}), (200, b"{}", {})
    )
    GmailTransport(http=http, sleep=slept.append).json("GET", URL, token=TOKEN, what="elenco")
    # An HTTP-date Retry-After is legal and Gmail does not send it, but a parser that
    # crashes on a legal header is a parser that turns a retry into an outage.
    assert slept == [0.5]


# --- FakeGmail, driven through the real seam ----------------------------------------
#
# The fake is a test double the whole rest of the slice depends on, so it is exercised
# here rather than trusted. Driving it *through* `GmailTransport` rather than calling
# it directly is the point: it proves the fake satisfies `HttpCall` exactly, which is
# the only thing that makes B1-7's request assertions mean anything.


def _message(identifier: str, sender: str, recipient: str, when_ms: int) -> FakeMessage:
    return FakeMessage(
        id=identifier,
        thread_id=f"t-{identifier}",
        headers={
            "From": sender,
            "To": recipient,
            "Subject": "Preventivo",
            "Message-ID": f"<{identifier}@mail.example>",
        },
        body_text="Buongiorno",
        internal_date_ms=when_ms,
    )


def _mailbox() -> FakeGmail:
    return FakeGmail(
        messages={
            "m1": _message("m1", "anna@cliente.it", "io@studio.it", 1_700_000_000_000),
            "m2": _message("m2", "io@studio.it", "bruno@altro.it", 1_700_000_100_000),
        }
    )


def test_the_fake_records_what_was_asked_of_google_not_just_what_came_back() -> None:
    fake = _mailbox()
    GmailTransport(http=fake).json(
        "GET",
        f"{URL}?q=from%3Aanna%40cliente.it&maxResults=50",
        token=TOKEN,
        what="elenco",
    )
    recorded = fake.requests[0]
    assert recorded.is_messages_list
    assert recorded.host == "gmail.googleapis.com"
    assert recorded.q == "from:anna@cliente.it"
    assert recorded.query["maxResults"] == ["50"]


def test_the_fake_actually_filters_on_the_query_instead_of_returning_everything() -> None:
    """A fake that ignores `q` makes every relevance test in this slice vacuous: the
    production code could send a listing with no filter at all and still see exactly
    the messages the test seeded."""
    fake = _mailbox()
    result = GmailTransport(http=fake).json(
        "GET", f"{URL}?q=from%3Aanna%40cliente.it", token=TOKEN, what="elenco"
    )
    assert [m["id"] for m in result["messages"]] == ["m1"]


def test_the_fake_ors_within_a_group_and_ands_across_them() -> None:
    fake = _mailbox()
    both = GmailTransport(http=fake).json(
        "GET",
        f"{URL}?q=%28from%3Aanna%40cliente.it+OR+to%3Abruno%40altro.it%29",
        token=TOKEN,
        what="elenco",
    )
    assert {m["id"] for m in both["messages"]} == {"m1", "m2"}

    narrowed = GmailTransport(http=fake).json(
        "GET",
        f"{URL}?q=%28from%3Aanna%40cliente.it+OR+to%3Abruno%40altro.it%29+after%3A1700000050",
        token=TOKEN,
        what="elenco",
    )
    assert {m["id"] for m in narrowed["messages"]} == {"m2"}


def test_the_fake_refuses_a_listing_with_no_query_at_all() -> None:
    """Spec 4.1: a `messages.list` without an address filter is a bug, not a broad
    search. The fake refuses it loudly so that the bug is a red test rather than a
    mailbox-wide read in production."""
    fake = _mailbox()
    with pytest.raises(AssertionError, match="empty q"):
        GmailTransport(http=fake).json("GET", URL, token=TOKEN, what="elenco")


def test_the_fake_refuses_an_operator_it_does_not_implement() -> None:
    """Silently ignoring `newer_than:` would turn a passing test into a false
    statement about a query nobody actually evaluated."""
    fake = _mailbox()
    with pytest.raises(AssertionError, match="does not implement"):
        GmailTransport(http=fake).json(
            "GET", f"{URL}?q=newer_than%3A7d", token=TOKEN, what="elenco"
        )


def test_the_fake_serves_a_message_in_gmails_own_shape() -> None:
    fake = FakeGmail(
        messages={
            "m1": FakeMessage(
                id="m1",
                thread_id="t1",
                headers={"From": "anna@cliente.it", "Subject": "Preventivo"},
                body_text="Buongiorno",
                body_html="<p>Buongiorno</p>",
                internal_date_ms=1_700_000_000_000,
            )
        }
    )
    payload = GmailTransport(http=fake).json(
        "GET", f"{URL}/m1?format=full", token=TOKEN, what="messaggio"
    )
    assert payload["id"] == "m1"
    assert payload["internalDate"] == "1700000000000"
    assert payload["payload"]["mimeType"] == "multipart/alternative"
    mime_types = [part["mimeType"] for part in payload["payload"]["parts"]]
    assert mime_types == ["text/plain", "text/html"]
    assert {header["name"] for header in payload["payload"]["headers"]} == {"From", "Subject"}


def test_the_fake_serves_an_unknown_message_as_a_real_404() -> None:
    fake = _mailbox()
    with pytest.raises(GoogleCallFailed) as caught:
        GmailTransport(http=fake, sleep=lambda _: None).json(
            "GET", f"{URL}/nope", token=TOKEN, what="messaggio"
        )
    assert caught.value.failure.status == 404
    assert caught.value.failure.error_code == "NOT_FOUND"


def test_the_fakes_revoked_grant_never_heals() -> None:
    """A revoked refresh token is terminal. The fake answers the real 400 body every
    time rather than once, because a fake that heals on the second call would make a
    retry loop look correct."""
    fake = FakeGmail(revoked=True)
    transport = GmailTransport(http=fake, sleep=lambda _: None)
    for _ in range(2):
        with pytest.raises(GoogleCallFailed) as caught:
            transport.form(TOKEN_URL, {"grant_type": "refresh_token"}, what="refresh")
        assert caught.value.failure.error_code == "invalid_grant"
    assert fake.token_requests == 2


def test_the_fakes_queued_failure_is_consumed_once_and_then_the_call_succeeds() -> None:
    """`fail_with` is how every downstream test stages a transient upstream failure.
    If it were not consumed, a single queued 503 would fail all four attempts and the
    retry tests above would be testing nothing."""
    fake = FakeGmail(fail_with=[(503, b"{}", {})])
    slept: list[float] = []
    payload = GmailTransport(http=fake, sleep=slept.append).form(
        TOKEN_URL, {"grant_type": "refresh_token"}, what="refresh"
    )
    assert payload["access_token"] == fake.access_token
    assert slept == [0.5]
    assert len(fake.requests) == 2


def test_the_fakes_send_timeout_is_the_unknown_outcome_and_not_a_failure() -> None:
    """Spec 6.3(b): a send whose response never arrived may or may not have been
    delivered. The fake reproduces it as the same synthetic status `urllib_call`
    produces, so the code under test cannot tell it apart from a real one."""
    fake = FakeGmail(timeout_on_send=True)
    with pytest.raises(GoogleCallFailed) as caught:
        GmailTransport(http=fake, sleep=lambda _: None).json(
            "POST",
            "https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
            token=TOKEN,
            body={"raw": "..."},
            what="invio",
        )
    assert caught.value.failure.status == NETWORK_ERROR_STATUS
    assert len(fake.requests) == MAX_ATTEMPTS
