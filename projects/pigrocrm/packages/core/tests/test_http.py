"""The one HTTP seam of the Gmail, Drive and Slack clients (`core/http.py`).

The retry loop is exercised through each client's own tests; what is here is the part
no client reaches with a fake: the urllib adapter itself (every other test injects a
fake and never gets to it) and the loop's contract with its callers.
"""

import io
import urllib.error
from collections.abc import Callable

import pytest

from pigrocrm.core.http import (
    DEFAULT_TIMEOUT_SECONDS,
    MAX_ATTEMPTS,
    NETWORK_ERROR_STATUS,
    NEVER_RETRY,
    RATE_LIMITED_ONLY,
    RETRYABLE_STATUSES,
    Response,
    call_with_retry,
    urllib_call,
)

URL = "https://www.googleapis.com/drive/v3/files"


class _Answer:
    """What `urllib.request.urlopen` returns, as a context manager."""

    def __init__(self, body: bytes) -> None:
        self.status = 200
        self.headers = {"Retry-After": "3"}
        self._body = body
        self.asked: list[int | None] = []

    def __enter__(self) -> "_Answer":
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def read(self, limit: int | None = None) -> bytes:
        self.asked.append(limit)
        return self._body if limit is None else self._body[:limit]


class _Headers(dict[str, str]):
    """`http.client.HTTPMessage` has `.items()`, which is all the adapter reads."""


# --- the urllib adapter ---------------------------------------------------------------


def test_a_connection_failure_becomes_a_synthetic_status(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(request: object, timeout: float) -> None:
        raise urllib.error.URLError(ConnectionRefusedError("connection refused"))

    monkeypatch.setattr("urllib.request.urlopen", refuse)

    status, payload, headers = urllib_call("GET", URL, {}, None)

    assert status == NETWORK_ERROR_STATUS
    assert b"refused" in payload
    assert headers == {}


def test_a_timeout_becomes_a_synthetic_status(monkeypatch: pytest.MonkeyPatch) -> None:
    def time_out(request: object, timeout: float) -> None:
        raise TimeoutError("timed out")

    monkeypatch.setattr("urllib.request.urlopen", time_out)

    status, payload, _ = urllib_call("GET", URL, {}, None)

    assert status == NETWORK_ERROR_STATUS
    assert b"timed out" in payload


def test_a_real_http_error_keeps_its_own_status_and_headers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A real HTTP error response (as opposed to no response at all) must surface its own
    status, not the synthetic network-failure one, and its headers: `Retry-After` is how
    the retry loop hears the server."""

    def refuse(request: object, timeout: float) -> None:
        raise urllib.error.HTTPError(
            URL,
            429,
            "Too Many Requests",
            _Headers({"Retry-After": "7"}),  # type: ignore[arg-type]
            io.BytesIO(b'{"error": {"message": "slow down"}}'),
        )

    monkeypatch.setattr("urllib.request.urlopen", refuse)

    status, _, headers = urllib_call("GET", URL, {}, None)

    assert status == 429
    assert headers == {"Retry-After": "7"}


def test_the_body_is_read_at_most_one_byte_past_the_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    answer = _Answer(b"x" * 500)
    monkeypatch.setattr("urllib.request.urlopen", lambda request, timeout: answer)

    _, payload, _ = urllib_call("GET", URL, {}, None, max_bytes=100)

    assert len(payload) == 101
    assert answer.asked == [101]


def test_without_a_cap_the_body_is_read_whole(monkeypatch: pytest.MonkeyPatch) -> None:
    answer = _Answer(b"x" * 500)
    monkeypatch.setattr("urllib.request.urlopen", lambda request, timeout: answer)

    _, payload, _ = urllib_call("GET", URL, {}, None)

    assert len(payload) == 500
    assert answer.asked == [None]


def test_the_timeout_has_one_default_and_a_caller_can_override_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[float] = []

    def record(request: object, timeout: float) -> _Answer:
        seen.append(timeout)
        return _Answer(b"{}")

    monkeypatch.setattr("urllib.request.urlopen", record)

    urllib_call("GET", URL, {}, None)
    urllib_call("GET", URL, {}, None, timeout=3)

    assert seen == [DEFAULT_TIMEOUT_SECONDS, 3]


# --- the retry loop's contract --------------------------------------------------------


def _script(*statuses: int) -> tuple[Callable[..., Response], list[int | None]]:
    """A seam that answers `statuses` in order, then 200, recording the cap it was asked."""
    queue = list(statuses)
    caps: list[int | None] = []

    def http(
        method: str,
        url: str,
        headers: dict[str, str],
        body: bytes | None,
        *,
        max_bytes: int | None = None,
    ) -> Response:
        caps.append(max_bytes)
        return (queue.pop(0) if queue else 200), b"{}", {}

    return http, caps


def _retry(*statuses: int, retryable: frozenset[int] = RETRYABLE_STATUSES) -> tuple[int, int]:
    """(the status the caller finally sees, how many attempts it took)."""
    http, caps = _script(*statuses)
    status, _, _ = call_with_retry(
        http, lambda _: None, "GET", URL, {}, None, retryable=retryable, max_bytes=10
    )
    assert set(caps) == {10}, "every attempt carries the same cap"
    return status, len(caps)


def test_a_transient_status_is_retried_until_it_clears() -> None:
    assert _retry(503, NETWORK_ERROR_STATUS, 429) == (200, 4)


def test_a_status_that_will_not_heal_is_returned_at_once() -> None:
    assert _retry(403) == (403, 1)


def test_the_loop_gives_up_after_the_attempt_ceiling_and_returns_the_last_answer() -> None:
    assert _retry(*[502] * 10) == (502, MAX_ATTEMPTS)


def test_a_call_with_an_effect_is_not_retried_at_all_when_the_caller_says_so() -> None:
    assert _retry(503, retryable=NEVER_RETRY) == (503, 1)
    assert _retry(429, retryable=NEVER_RETRY) == (429, 1)


def test_a_rate_limit_is_the_one_status_safe_to_retry_for_a_call_with_an_effect() -> None:
    assert _retry(429, retryable=RATE_LIMITED_ONLY) == (200, 2)
    assert _retry(503, retryable=RATE_LIMITED_ONLY) == (503, 1)


def test_the_servers_retry_after_outranks_the_schedule() -> None:
    slept: list[float] = []
    answers: list[Response] = [(429, b"{}", {"retry-after": "2"}), (200, b"{}", {})]

    def http(
        method: str,
        url: str,
        headers: dict[str, str],
        body: bytes | None,
        *,
        max_bytes: int | None = None,
    ) -> Response:
        return answers.pop(0)

    call_with_retry(http, slept.append, "GET", URL, {}, None)

    assert slept == [2.0]
