"""In-process throttles for the unauthenticated routes of the API: token buckets per
client, per recipient and global, and a failure counter with backoff for credentials.

Everything here is per process and in memory, on purpose: one uvicorn worker today, no
shared state to introduce for a signup form. With several workers each ceiling is
multiplied by the worker count. This is the floor under nginx's `limit_req`
(`deploy/nginx`), not a substitute for it. The shape is the hub's `rebase_api.ratelimit`;
the two products share no code (`AGENTS.md`), so it is a copy on purpose.

Proxy-only invariant: `client_key` trusts `X-Real-IP`, which nginx sets from the TCP
peer. That is sound only while nothing but nginx can reach the API's port; a deployment
that exposes port 8000 directly lets any caller mint a fresh bucket per request by
typing a header. Compose publishes no API port; keep it so.

Rejections are logged on `pigrocrm_api.security` with the scope and the client address
and never with an email, a token or a password.
"""

import logging
import threading
import time
from typing import Any

from fastapi import HTTPException, Request, status

logger = logging.getLogger("pigrocrm_api.security")

# A person filling in a form needs two or three attempts, not five; five a minute leaves
# room for a double click, a reload on a slow connection and a household sharing one
# address, while making a scripted sweep of a mailing list pointless.
REQUESTS_PER_MINUTE = 5
# `GET /api/tenants/{slug}/disponibile` is a typeahead (one call per 350 ms pause while a
# person names a business), not a submit: its own, larger budget, so ordinary typing
# never starves the tokens the signup itself needs (REB-228).
AVAILABLE_REQUESTS_BY_MINUTE = 30
# `GET /api/auth/invite` is read on mount of the acceptance page: a reload or a double
# click must not lock the invitee out of seeing who invited them (REB-290).
INVITE_PEEK_REQUESTS_PER_MINUTE = AVAILABLE_REQUESTS_BY_MINUTE
# The routes that spend a credential rather than prove one. Generous, since a legitimate
# client calls them a few times per session (the SPA renews on every 401, from every open
# tab), yet bounded so that guessing a link or a refresh token is not free.
VERIFY_REQUESTS_PER_MINUTE = 20
REFRESH_REQUESTS_PER_MINUTE = 60
IDENTITY_ENTER_REQUESTS_PER_MINUTE = 30
RETRY_AFTER_SECONDS = 60

# Mails to one recipient: a burst of five, then one every twelve minutes. Keyed by the
# normalised address and checked on every route that mails somebody who is not logged in,
# so a script rotating its source address cannot mail-bomb one inbox.
MAIL_BURST_PER_RECIPIENT = 5
MAIL_WINDOW_SECONDS = 3600

# Wrong credentials: this many failures on a key are free, each further one doubles the
# wait (capped), and a success forgets the key. A key is silent for `FAILURE_MEMORY_SECONDS`
# before its count is dropped. A login key is one client and one account, so four typos
# are plenty; a bad bearer is counted per client address, which may be an office full of
# agents behind one NAT, so it is given more room.
FREE_FAILURES = 4
FREE_BEARER_FAILURES = 20
BACKOFF_BASE_SECONDS = 2
BACKOFF_MAX_SECONDS = 15 * 60
FAILURE_MEMORY_SECONDS = 15 * 60

# Bounds both tables: an attacker who varies `X-Forwarded-For` on every request must not
# be able to grow them without limit. Forgetting a key only hands it a fresh budget, so an
# overflowing table makes the limiter more lenient and never locks anybody out.
MAX_TRACKED_KEYS = 4096

# key -> (tokens left, when they were last counted, the bucket's capacity, the seconds it
# takes to refill from empty). The shape travels with the bucket because several scopes
# with different budgets share this one table.
_buckets: dict[str, tuple[float, float, float, float]] = {}
# key -> (failures so far, last failure, no attempt allowed before). Monotonic clock.
_failures: dict[str, tuple[int, float, float]] = {}
_lock = threading.Lock()


def reset_rate_limit() -> None:
    """Forgets every key. For tests: the tables live for the life of the process."""
    with _lock:
        _buckets.clear()
        _failures.clear()


def client_key(request: Request) -> str:
    """The visitor's address as far as it can be known. nginx sets `X-Real-IP` and
    appends to `X-Forwarded-For`, and the API sees the proxy's own address, so without a
    header every visitor would share one bucket. In `X-Forwarded-For` only the LAST hop is
    trusted: the first is whatever the visitor typed. See the module docstring for the
    invariant that makes `X-Real-IP` safe."""
    real_ip = request.headers.get("x-real-ip", "").strip()
    if real_ip:
        return real_ip
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[-1].strip()
    return request.client.host if request.client else "unknown"


def normalise_address(email: str) -> str:
    return email.strip().lower()


def _refilled(bucket: tuple[float, float, float, float], now: float) -> float:
    tokens, last_seen, capacity, window = bucket
    return min(capacity, tokens + (now - last_seen) * capacity / window)


def _forget_the_quiet_ones(now: float) -> None:
    for key in [key for key, bucket in _buckets.items() if _refilled(bucket, now) >= bucket[2]]:
        del _buckets[key]
    for key in [
        key
        for key, (_, last_failure, locked_until) in _failures.items()
        if now - last_failure > FAILURE_MEMORY_SECONDS and now >= locked_until
    ]:
        del _failures[key]
    for table in (_buckets, _failures):
        if len(table) > MAX_TRACKED_KEYS:
            # Still full of active keys: keep the ones seen most recently.
            for key in sorted(table, key=lambda key: table[key][1])[:-MAX_TRACKED_KEYS]:
                del table[key]


def _refuse(scope: str, request: Request | None, retry_after: int, detail: str) -> HTTPException:
    logger.warning(
        "throttled scope=%s client=%s retry_after=%ds",
        scope,
        client_key(request) if request is not None else "-",
        retry_after,
    )
    return HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail=detail,
        headers={"Retry-After": str(retry_after)},
    )


def _spend(
    key: str, *, scope: str, request: Request | None, capacity: int, window_seconds: float
) -> None:
    now = time.monotonic()
    with _lock:
        bucket = _buckets.get(key, (float(capacity), now, float(capacity), window_seconds))
        tokens = _refilled(bucket, now)
        if tokens < 1.0:
            _buckets[key] = (tokens, now, bucket[2], bucket[3])
            raise _refuse(
                scope,
                request,
                RETRY_AFTER_SECONDS,
                "Troppe richieste da qui. Riprova tra un minuto.",
            )
        _buckets[key] = (tokens - 1.0, now, bucket[2], bucket[3])
        if len(_buckets) > MAX_TRACKED_KEYS:
            _forget_the_quiet_ones(now)


def spend_one(request: Request, *, scope: str = "", per_minute: int = REQUESTS_PER_MINUTE) -> None:
    """A token bucket per client, refilling at `per_minute`. `scope` namespaces the bucket
    away from the default one, so a route with a larger budget (the typeahead) never
    starves the actual submit actions of their tokens."""
    key = f"{scope}:{client_key(request)}" if scope else client_key(request)
    _spend(key, scope=scope or "default", request=request, capacity=per_minute, window_seconds=60)


def spend_global(scope: str, *, per_minute: int) -> None:
    """One bucket for the whole process, whoever asks: the ceiling under the per-client
    ones, for the work an anonymous caller can make expensive (a `CREATE DATABASE`)."""
    _spend(
        f"global:{scope}",
        scope=f"global:{scope}",
        request=None,
        capacity=per_minute,
        window_seconds=60,
    )


def spend_for_recipient(email: str) -> bool:
    """Whether one more mail to this address is within its budget, spending a token if so.
    A bool and not a 429: each caller decides what a refused mail means to its own answer
    (the link request says so, the signup simply sends none)."""
    try:
        _spend(
            f"mail:{normalise_address(email)}",
            scope="mail",
            request=None,
            capacity=MAIL_BURST_PER_RECIPIENT,
            window_seconds=MAIL_WINDOW_SECONDS,
        )
    except HTTPException:
        return False
    return True


def too_many_mails() -> HTTPException:
    """The refusal for a recipient that has had its share of mails."""
    return _refuse(
        "mail",
        None,
        MAIL_WINDOW_SECONDS // MAIL_BURST_PER_RECIPIENT,
        "Hai già chiesto diversi link per questo indirizzo. Riprova tra qualche minuto.",
    )


def refuse_if_locked(request: Request, key: str) -> None:
    """429 while this key is inside the backoff its failures earned. Call before doing the
    work a wrong credential would make the server pay for (the argon2 verify)."""
    now = time.monotonic()
    with _lock:
        entry = _failures.get(key)
        locked_until = entry[2] if entry is not None else 0.0
    if now < locked_until:
        raise _refuse(
            key.split(":", 1)[0],
            request,
            int(locked_until - now) + 1,
            "Troppi tentativi non riusciti. Riprova più tardi.",
        )


def record_failure(key: str, *, free: int = FREE_FAILURES) -> None:
    now = time.monotonic()
    with _lock:
        count, last_failure, _ = _failures.get(key, (0, now, 0.0))
        if now - last_failure > FAILURE_MEMORY_SECONDS:
            count = 0
        count += 1
        excess = count - free
        wait = (
            min(BACKOFF_MAX_SECONDS, BACKOFF_BASE_SECONDS ** min(excess, 20)) if excess > 0 else 0
        )
        _failures[key] = (count, now, now + wait)
        if len(_failures) > MAX_TRACKED_KEYS:
            _forget_the_quiet_ones(now)


def log_unauthorized(request: Request, scope: str) -> None:
    """A refused credential, for whoever watches the log: where from and on which door."""
    logger.warning("unauthorized scope=%s client=%s", scope, client_key(request))


def forget_failures(key: str) -> None:
    with _lock:
        _failures.pop(key, None)


def login_key(request: Request, email: str) -> str:
    """The key login failures are counted on: this client trying this account."""
    return f"login:{client_key(request)}:{normalise_address(email)}"


def bearer_key(request: Request) -> str:
    return f"bearer:{client_key(request)}"


# Documents the 429 this module raises, for the routes that call it: a plain
# `HTTPException`, `application/json`, never `application/problem+json`
# (`pigrocrm_api.errors.PROBLEM_RESPONSES` would misdocument the content type, the same
# reason auth.py's own 401 entry cannot reuse it). Attach with
# `responses={429: TOO_MANY_REQUESTS_RESPONSE}` on the route's own decorator; FastAPI
# merges a route's `responses=` with its router's.
TOO_MANY_REQUESTS_RESPONSE: dict[str, Any] = {
    "description": (
        "Too many requests from this address in the last minute (the bucket is kept for "
        "each client, not each route): the client must wait `Retry-After` seconds before "
        "retrying."
    ),
    "content": {
        "application/json": {
            "schema": {
                "type": "object",
                "properties": {"detail": {"type": "string"}},
                "required": ["detail"],
            }
        }
    },
    "headers": {
        "Retry-After": {
            "description": "Secondi da attendere prima di riprovare.",
            "schema": {"type": "integer"},
        }
    },
}
