"""The per-client speed bump shared by every public write of the hub.

There is no other limiter in this API (nothing else is unauthenticated), so this is the
one that exists; the mechanism is `rebase_core.ratelimit.RateLimiter`, and this module is
the FastAPI side of it: who the visitor is, and the 429 they get. It came with the signup
route from PigroCRM on 2026-09-09 and grew more callers.

It is the floor, not the cap: the real cap is nginx's `limit_req`, and the zones to commit
on the host vhost are in `projects/hub/AGENTS.md`.
"""

from fastapi import HTTPException, Request, status

from rebase_core.ratelimit import UNKNOWN_CLIENT, RateLimiter
from rebase_core.ratelimit import client_key as address_key

# A person filling in a form needs two or three attempts, not five; five a minute
# leaves room for a double click, a reload on a slow connection, and a household
# sharing one address, while making a scripted sweep of a mailing list pointless.
SIGNUPS_PER_MINUTE = 5
RETRY_AFTER_SECONDS = 60

_limiter = RateLimiter(SIGNUPS_PER_MINUTE, RETRY_AFTER_SECONDS)


def reset_rate_limit() -> None:
    """Forgets every client. For tests, which would otherwise spend one test's budget
    on the next one, since the table lives for the life of the process."""
    _limiter.reset()


def observed_address(request: Request) -> str:
    """The visitor's address as the proxy saw it, as written, or `UNKNOWN_CLIENT`.

    nginx sets `X-Real-IP` and appends to `X-Forwarded-For` for this API, which otherwise
    sees only the proxy's own address, so without reading a header every visitor would
    share one bucket and five signups a minute would be the whole world's budget.
    `X-Real-IP` is what nginx observed; in `X-Forwarded-For` only the LAST hop is: the
    first is whatever the visitor typed, and keying on it let a script mint a fresh
    bucket per request by changing the header.
    """
    real_ip = request.headers.get("x-real-ip", "").strip()
    if real_ip:
        return real_ip
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[-1].strip()
    return request.client.host if request.client else UNKNOWN_CLIENT


def client_key(request: Request) -> str:
    """The limiter's key for the visitor: their address, an IPv6 one as its /64."""
    return address_key(observed_address(request))


def spend_one(request: Request) -> None:
    """Charges the visitor one event and answers 429 when their minute is used up."""
    if not _limiter.spend(client_key(request)):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Troppe richieste da qui. Riprova tra un minuto.",
            headers={"Retry-After": str(RETRY_AFTER_SECONDS)},
        )
