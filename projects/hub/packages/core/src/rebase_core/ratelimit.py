"""A small in-process sliding-window limiter, shared by every public door of the hub.

The API's per-client write limit, the MCP transport's failed-bearer limit and the team
builder's speed bump all need the same thing: "at most `limit` events per `window`
seconds for this key". It is deliberately tiny rather than a dependency, and it lives in
core so none of them has to reach into another adapter for it.

Per process, on purpose: with several workers the effective ceiling is `limit` times the
number of workers, which is the honest cost of not introducing shared state. A real cap
belongs at the reverse proxy (`limit_req`, see `projects/hub/AGENTS.md`); this is the
floor under it, not a substitute.
"""

import ipaddress
import threading
import time
from collections import OrderedDict, deque
from collections.abc import Callable

# What a caller whose address cannot be known is keyed on. One shared bucket for all
# of them: they are rare, and a bucket of their own is better than none.
UNKNOWN_CLIENT = "unknown"

# Bounds the table: an attacker who varies an address header on every request must not
# be able to grow it without limit. The least recently seen key goes first, and
# forgetting a client only hands it a fresh window, so an overflowing table makes the
# limiter too lenient and never locks anybody out.
DEFAULT_MAX_KEYS = 4096

# An IPv6 allocation to one subscriber is a /64 (RFC 6177 and the common practice since),
# so a single household or attacker owns 2^64 addresses. Keying on the full address would
# hand them a fresh window per request.
_IPV6_PREFIX_BITS = 64


def client_key(address: str) -> str:
    """The key a limiter should use for an address as the proxy observed it.

    An IPv4 address is its own key. An IPv6 address is keyed on its /64 prefix, and an
    IPv4-mapped IPv6 address (`::ffff:203.0.113.9`) is the IPv4 address it wraps, so one
    visitor is one key however the proxy happened to write the address. Anything that
    does not parse is kept as typed (the caller decided it was worth a bucket), and an
    empty one is `UNKNOWN_CLIENT`.
    """
    text = address.strip()
    if not text:
        return UNKNOWN_CLIENT
    try:
        parsed = ipaddress.ip_address(text)
    except ValueError:
        return text
    if isinstance(parsed, ipaddress.IPv6Address):
        if parsed.ipv4_mapped is not None:
            return str(parsed.ipv4_mapped)
        network = ipaddress.IPv6Network((parsed, _IPV6_PREFIX_BITS), strict=False)
        return str(network)
    return str(parsed)


class RateLimiter:
    """At most `limit` events per `window` seconds, per key (a sliding window).

    Thread-safe. `spend` records an event and says whether it fits; `has_room` only
    looks, for a caller that counts failures rather than attempts.

    Eviction is amortised O(1): keys are kept in least-recently-seen order, so a sweep
    only ever pops from the front, stops at the first key still inside its window, and
    the table is held to `max_keys` by dropping the front. Nothing sorts or scans the
    whole table on a request.
    """

    def __init__(
        self,
        limit: int,
        window: float,
        *,
        max_keys: int = DEFAULT_MAX_KEYS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if limit < 1 or window <= 0 or max_keys < 1:
            raise ValueError("limit and max_keys must be at least 1 and window positive")
        self.limit = limit
        self.window = window
        self.max_keys = max_keys
        self._clock = clock
        self._lock = threading.Lock()
        # key -> times of the events still inside the window, oldest first.
        self._events: OrderedDict[str, deque[float]] = OrderedDict()

    def reset(self) -> None:
        """Forgets every key. For tests, since the table lives as long as the process."""
        with self._lock:
            self._events.clear()

    def has_room(self, key: str) -> bool:
        """Whether one more event would fit, without recording it."""
        now = self._clock()
        with self._lock:
            return self._room(key, now)

    def spend(self, key: str) -> bool:
        """Records one event for `key` and returns whether it fits the window.

        A refused event is not recorded: hammering a closed door does not keep it closed.
        """
        now = self._clock()
        with self._lock:
            self._forget_expired(now)
            if not self._room(key, now):
                return False
            events = self._events.get(key)
            if events is None:
                events = self._events[key] = deque()
            events.append(now)
            self._events.move_to_end(key)
            while len(self._events) > self.max_keys:
                self._events.popitem(last=False)
            return True

    def _room(self, key: str, now: float) -> bool:
        events = self._events.get(key)
        if events is None:
            return True
        while events and now - events[0] >= self.window:
            events.popleft()
        return len(events) < self.limit

    def _forget_expired(self, now: float) -> None:
        """Pops the keys whose newest event has left the window, from the front only."""
        while self._events:
            key, events = next(iter(self._events.items()))
            if events and now - events[-1] < self.window:
                return
            del self._events[key]
