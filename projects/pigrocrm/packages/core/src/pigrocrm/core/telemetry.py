"""The digest's own event, sent by the server: `digest_sent` (REB-221).

A space's browser reports very little of the CRM back to PostHog -- most of a working
day happens in Gmail, in a video call, in a PDF someone reads offline -- so the weekly
digest is one of the few signals the product gets about whether a space is actually
opening what it is mailed. It has to come from the server: the mail is generated and
sent by a background job with no browser attached to hand the event to, and a person
who never opens the mail leaves no client behind that could have sent it anyway.

**One person, one id.** This event is captured under `str(user_id)`, the same id the
browser uses once that person signs in (`shared/analytics`), so a digest sent and the
session it eventually leads to line up on one person rather than two. Unlike the hub's
`signup_completed` (`projects/hub/packages/core/src/rebase_core/analytics.py`),
which anonymizes an applicant who may never become a person PostHog has profiled, a
digest recipient is always an existing, already-identified CRM user -- so this event
carries no `$process_person_profile: false`. Setting it here would suppress the very
profile the browser half is building.

**Nothing here can break the digest run.** `digest_sent` is called once the mail has
already been handed to the sender, a capture that raises answers `False`, and an empty
`PIGROCRM_POSTHOG_KEY` builds no client at all -- the same three guarantees
`pigrocrm_mcp.analytics` gives its own callers, and the same shape the hub's
`analytics.py` gives `signup_completed`.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from uuid import UUID

from pigrocrm.core.config import Settings

if TYPE_CHECKING:
    from posthog import Posthog

DIGEST_SENT = "digest_sent"

# What the SDK's `capture` looks like, so a test can hand one that records.
Capture = Callable[..., Any]

_client: Posthog | None = None
_client_key: tuple[str, str] | None = None
_lock = threading.Lock()


@dataclass(frozen=True)
class Tracker:
    capture: Capture

    def digest_sent(self, user_id: UUID, *, week: str, sections: int) -> bool:
        """One `digest_sent`; answers whether the SDK took it."""
        properties: dict[str, Any] = {
            "week": week,
            "sections": sections,
            "via": "server",
        }
        try:
            self.capture(
                DIGEST_SENT,
                distinct_id=str(user_id),
                properties=properties,
            )
        except Exception:  # noqa: BLE001 -- the SDK's failure is not the digest's
            return False
        return True


def build_client(settings: Settings) -> Posthog | None:
    """The process's one client when the installation has a key, `None` when it has not.

    One per process, keyed by key and host so a test that changes settings gets a fresh
    one: the SDK runs a consumer thread per client, and one is enough.
    """
    global _client, _client_key
    if not settings.posthog_key:
        return None
    wanted = (settings.posthog_key, settings.posthog_host)
    with _lock:
        if _client is None or _client_key != wanted:
            from posthog import Posthog

            _client = Posthog(settings.posthog_key, host=settings.posthog_host)
            _client_key = wanted
        return _client


def shutdown() -> None:
    """Flush and stop the process's client, if one was built. Safe to call twice."""
    global _client, _client_key
    with _lock:
        client, _client, _client_key = _client, None, None
    if client is not None:
        client.shutdown()


def tracker_from_settings(settings: Settings) -> Tracker | None:
    client = build_client(settings)
    return None if client is None else Tracker(client.capture)
