"""The one HTTP seam of the clients that talk to Google and Slack.

Gmail, Drive and Slack each need the same four things from the network and used to carry
their own copy of them: a call that never raises for a failed request, a retry loop with
backoff that honours the server's `Retry-After`, one set of statuses worth retrying, and
one timeout. What a test replaces is the *network* (`tests/fakes/`), so URL construction,
bodies, error classification and the retry loop itself all run for real.

`HttpCall` answers `(status, body, response headers)`. Failure to get an answer at all
(DNS, a refused connection, a timeout) is the synthetic `NETWORK_ERROR_STATUS`, so that
"no response" travels through the one channel every other status uses instead of a second
failure path only the urllib adapter knows about.

Nothing here logs, and nothing that leaves here carries a request: the bearer token, the
refresh token and the client secret pass through, and none of them may reach an exception,
a `repr` or a traceback.
"""

import json
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Protocol

from pigrocrm.core.errors import Conflict

# One timeout for every client, overridable by a caller that wants another through
# `urllib_call(..., timeout=...)`.
DEFAULT_TIMEOUT_SECONDS = 30
# The "network connect timeout" status some proxies use for exactly this shape: no HTTP
# response was ever received.
NETWORK_ERROR_STATUS = 599
# 429 (rate limited) and 5xx (transient server-side failure) are worth retrying; anything
# else -- a 4xx other than 429 in particular -- is a client error that will not go away on
# its own, so retrying it only delays reporting a failure.
RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504, NETWORK_ERROR_STATUS})
# What is safe to retry for a call that may have had an effect (a message send, a Slack
# post): only a 429, which Google and Slack both answer *before* processing. A 502 or a
# 599 means no answer arrived, not that nothing happened.
RATE_LIMITED_ONLY = frozenset({429})
NEVER_RETRY: frozenset[int] = frozenset()
# First attempt plus up to three retries: 0.5s, 1s, 2s.
MAX_ATTEMPTS = 4
_RETRY_BASE_DELAY_SECONDS = 0.5
# A server-supplied Retry-After is honoured, but not unboundedly: a synchronous request
# holding open for an hour because a header said so is an outage with extra steps. Public
# because the test that proves the clamp must not restate the number.
MAX_HONOURED_RETRY_AFTER_SECONDS = 30.0

Response = tuple[int, bytes, dict[str, str]]
SleepFn = Callable[[float], None]


class HttpCall(Protocol):
    """`(method, url, headers, body)` -> `(status, body, response headers)`.

    `max_bytes` caps how much of the response body is read: the call reads at most
    `max_bytes + 1` bytes, so a caller that refuses anything over `max_bytes` can tell an
    oversized answer from one that fits without the whole of it ever being buffered.
    `None` reads it all.
    """

    def __call__(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        body: bytes | None,
        *,
        max_bytes: int | None = None,
    ) -> Response: ...


def urllib_call(
    method: str,
    url: str,
    headers: dict[str, str],
    body: bytes | None,
    *,
    max_bytes: int | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> Response:
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    limit = None if max_bytes is None else max_bytes + 1
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read() if limit is None else response.read(limit)
            return int(response.status), body, dict(response.headers.items())
    except urllib.error.HTTPError as exc:
        error_body = exc.read() if limit is None else exc.read(limit)
        return int(exc.code), error_body, dict(exc.headers.items()) if exc.headers else {}
    except (urllib.error.URLError, OSError) as exc:
        # `str(exc)` names the failure reason and possibly the target host -- always a
        # googleapis.com or slack.com address, never sensitive -- but never the request's
        # headers or body, so no bearer token or key material can reach it.
        return (
            NETWORK_ERROR_STATUS,
            json.dumps({"error": {"message": str(exc)}}).encode(),
            {},
        )


def retry_delay_seconds(attempt: int) -> float:
    # `1 << attempt`, not `2 ** attempt`: typeshed types `int.__pow__` as returning
    # `Any`, which would make this function's return type `Any` under mypy strict.
    return _RETRY_BASE_DELAY_SECONDS * (1 << attempt)


def parse_retry_after(headers: dict[str, str]) -> float | None:
    """Seconds only. An HTTP-date is legal and Google and Slack do not send one, but a
    parser that raises on a legal header turns a retry into an outage.

    The name is matched case-insensitively because HTTP field names are (RFC 9110 5.1)
    and no proxy in the path owes us a particular casing. Getting that wrong fails
    *quietly* -- straight back to the guessed schedule -- which is why it has a test of
    its own.
    """
    for name, value in headers.items():
        if name.lower() != "retry-after":
            continue
        try:
            seconds = float(value.strip())
        except ValueError:
            return None
        if seconds <= 0:
            return None
        return min(seconds, MAX_HONOURED_RETRY_AFTER_SECONDS)
    return None


def call_with_retry(
    http: HttpCall,
    sleep: SleepFn,
    method: str,
    url: str,
    headers: dict[str, str],
    body: bytes | None,
    *,
    retryable: frozenset[int] = RETRYABLE_STATUSES,
    max_bytes: int | None = None,
) -> Response:
    """Every request of these clients goes through here, so that retry-with-backoff
    applies uniformly instead of being reimplemented -- or forgotten -- at each call site.

    A status in `retryable` is retried up to `MAX_ATTEMPTS` attempts in all, sleeping the
    server's own `Retry-After` when it gave one and an exponential schedule when it did
    not; the last answer is returned whatever it is, so the caller's own error handling
    reports it. Anything *not* in the set is returned at once: a 4xx that is not a rate
    limit will not go away on its own, and `invalid_grant` arrives as a 400 for exactly
    that reason -- it is terminal and this loop must not soften it.

    `retryable` is how a caller says what a failed attempt is known not to have done:
    `RETRYABLE_STATUSES` for a read, `RATE_LIMITED_ONLY` or `NEVER_RETRY` for a call with
    an effect that offers no idempotency key (`users.messages.send` delivering a second
    copy of somebody's email is the failure `test_gmail_reconcile.py` reproduced).
    """
    status, payload, response_headers = http(method, url, headers, body, max_bytes=max_bytes)
    for attempt in range(MAX_ATTEMPTS - 1):
        if status not in retryable:
            break
        # The server's own hint wins over our guess when it gave one.
        delay = parse_retry_after(response_headers)
        sleep(delay if delay is not None else retry_delay_seconds(attempt))
        status, payload, response_headers = http(method, url, headers, body, max_bytes=max_bytes)
    return status, payload, response_headers


class UpstreamUnavailable(Conflict):
    """Transient, after every retry was spent: the service did not answer correctly.
    Distinct from a revoked credential precisely because the reaction differs -- wait and
    try again, versus re-consent or reinstall. One sentence for Gmail, Drive and Slack, so
    that it cannot drift between them."""

    def __init__(self, entity: str, service: str, status: int, *, what: str | None = None) -> None:
        sentence = f"{service} non ha risposto correttamente (codice {status}). Riprova più tardi"
        super().__init__(
            entity, f"{what}: {sentence}" if what else sentence, upstream_status=status
        )
