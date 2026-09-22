"""One small token bucket per client, for the public routes of the signup.

There was no limiter anywhere in this API until ORB-173: everything else is behind a
session or a token. `POST /api/tenants/member` is unauthenticated by design and relays
each question to the hub, so without a bucket anyone could sweep a mailing list through
it. The shape is the hub's `rebase_api.ratelimit`, which itself came from PigroCRM's
old signup route on 2026-09-09; the two products share no code (`AGENTS.md`), so this
is a copy on purpose and deliberately tiny rather than a dependency.
"""

import threading
import time
from typing import Any

from fastapi import HTTPException, Request, status

# A person filling in a form needs two or three attempts, not five; five a minute
# leaves room for a double click, a reload on a slow connection, and a household
# sharing one address, while making a scripted sweep of a mailing list pointless.
REQUESTS_PER_MINUTE = 5
# `GET /api/tenants/{slug}/disponibile` is a typeahead, not a submit: registrati.tsx
# asks it once per 350ms pause while a person is still deciding a name, so sharing
# `REQUESTS_PER_MINUTE`'s budget with `POST /api/tenants/member` and
# `POST /api/tenants/` would let ordinary typing starve the tokens the actual signup
# needs (REB-228) -- five hesitations while naming a business is not a scripted sweep.
# A generous ceiling on a read-only lookup that sends no mail and provisions nothing.
DISPONIBILE_REQUESTS_PER_MINUTE = 30
# `POST /api/auth/login` gets its own budget rather than sharing `REQUESTS_PER_MINUTE`:
# it is the one route whose cost per failed attempt is not just wasted mail or a
# wasted provision but a full argon2id verify at the library's own defaults (64 MiB,
# time cost 3, `auth/passwords.py`), so tying it to the signup routes' bucket would
# let a script guessing a password also burn the tokens a person mistyping a signup
# field needs, and vice versa. Ten, not five: a person locked out by a typo gets two
# tries at recovering their own password before the wait, still nowhere near enough
# attempts a minute to make guessing worthwhile (REB-270).
LOGIN_REQUESTS_PER_MINUTE = 10
# `GET /api/auth/invite` (REB-290) reads an invitation without spending it, and the
# acceptance page calls it on mount: a reload, the browser's own retry, or the
# person clicking the link twice must not lock them out of seeing who invited them.
# The `disponibile` reasoning -- a generous ceiling on a read that sends no mail and
# provisions nothing -- applies unchanged; separate scope so it cannot starve the
# signup typeahead's budget either.
INVITE_PEEK_REQUESTS_PER_MINUTE = DISPONIBILE_REQUESTS_PER_MINUTE
RETRY_AFTER_SECONDS = 60
# Bounds the table: an attacker who varies `X-Forwarded-For` on every request must not
# be able to grow it without limit. Full buckets (clients that have gone quiet) are
# dropped first, and forgetting a client only hands it a fresh bucket, so an
# overflowing table makes the limiter too lenient and never locks anybody out.
MAX_TRACKED_CLIENTS = 4096

# client key -> (tokens left, when they were last counted, that key's own per-minute
# cap). The cap travels with the bucket, rather than living in a single module
# constant, because REB-228 gave `disponibile` its own larger budget (see `spend_one`'s
# `scope` below): without it, `_forget_the_quiet_ones` would test every bucket's fill
# against whichever cap the *current* caller happened to pass, which is wrong for every
# other scope's buckets sharing this same table. Per process, on purpose: with several
# workers the effective ceiling is a cap times the number of workers, which is the
# honest cost of not introducing shared state for a signup form. A real cap belongs at
# the reverse proxy (`limit_req`), and this is the floor under it, not a substitute.
_buckets: dict[str, tuple[float, float, float]] = {}
_buckets_lock = threading.Lock()


def reset_rate_limit() -> None:
    """Forgets every client. For tests, which would otherwise spend one test's budget
    on the next one, since the table lives for the life of the process."""
    with _buckets_lock:
        _buckets.clear()


def client_key(request: Request) -> str:
    """The visitor's address as far as it can be known. nginx sets `X-Real-IP` and
    appends to `X-Forwarded-For`, and the API sees the proxy's own address, so without a
    header every visitor would share one bucket. In `X-Forwarded-For` only the LAST hop
    is trusted: the first is whatever the visitor typed, and keying on it lets a script
    mint a fresh bucket per request. A speed bump against floods and sweeps, not a
    security control: a real cap is nginx's `limit_req`."""
    real_ip = request.headers.get("x-real-ip", "").strip()
    if real_ip:
        return real_ip
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[-1].strip()
    return request.client.host if request.client else "sconosciuto"


def _refilled(tokens: float, last_seen: float, now: float, per_minute: float) -> float:
    return min(per_minute, tokens + (now - last_seen) * per_minute / 60.0)


def _forget_the_quiet_ones(now: float) -> None:
    for key in [
        key
        for key, (tokens, last_seen, per_minute) in _buckets.items()
        if _refilled(tokens, last_seen, now, per_minute) >= per_minute
    ]:
        del _buckets[key]
    if len(_buckets) > MAX_TRACKED_CLIENTS:
        # Still full of active clients: keep the ones seen most recently.
        for key in sorted(_buckets, key=lambda key: _buckets[key][1])[:-MAX_TRACKED_CLIENTS]:
            del _buckets[key]


def spend_one(request: Request, *, scope: str = "", per_minute: int = REQUESTS_PER_MINUTE) -> None:
    """A token bucket per client, refilling at `per_minute`. `scope` namespaces the
    bucket away from the default one: REB-228 gave `GET /api/tenants/{slug}/disponibile`
    its own (`scope="disponibile"`, a higher `per_minute`) precisely so a typeahead
    probing a name has its own budget rather than starving `POST /api/tenants/member`
    and `POST /api/tenants/` -- the actual submit actions -- of the tokens a person
    needs to finish signing up."""
    key = f"{scope}:{client_key(request)}" if scope else client_key(request)
    now = time.monotonic()
    with _buckets_lock:
        tokens, last_seen, cap = _buckets.get(key, (float(per_minute), now, float(per_minute)))
        tokens = _refilled(tokens, last_seen, now, cap)
        if tokens < 1.0:
            _buckets[key] = (tokens, now, cap)
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Troppe richieste da qui. Riprova tra un minuto.",
                headers={"Retry-After": str(RETRY_AFTER_SECONDS)},
            )
        _buckets[key] = (tokens - 1.0, now, cap)
        if len(_buckets) > MAX_TRACKED_CLIENTS:
            _forget_the_quiet_ones(now)


# Documents the 429 `spend_one` raises above, for the routes that call it
# (`POST /api/auth/link`, `GET /api/tenants/{slug}/disponibile`, REB-228;
# `POST /api/auth/login`, REB-270): a plain
# `HTTPException`, `application/json`, never `application/problem+json`
# (`pigrocrm_api.errors.PROBLEM_RESPONSES` would misdocument the content type -- the
# same reason auth.py's own `_UNAUTHENTICATED_RESPONSE` cannot reuse it for 401).
# Attach with `responses={429: TOO_MANY_REQUESTS_RESPONSE}` on each route's own
# decorator; FastAPI merges a route's `responses=` with its router's.
TOO_MANY_REQUESTS_RESPONSE: dict[str, Any] = {
    "description": (
        "Troppe richieste da questo indirizzo nell'ultimo minuto (il bucket è per "
        "client, non per rotta): il client deve attendere `Retry-After` secondi "
        "prima di riprovare."
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
