"""The events the server sends itself: the completion, `iscrizione_completata` (REB-215),
and the team builder's (REB-511, spec § 6), `team_proposta_generata` among them.

The browsers already report the wizards to PostHog, step by step (`shared/analytics`,
ORB-185). This exists because the browser's completion event is the one that gets lost:
on 2026-09-14 and 15, three of the seven freelancer profiles the hub received arrived
with no PostHog event at all -- an ad blocker, a tracking protection, a tab closed before
the SDK flushed. A count that misses four in ten is not a count, and the funnel in
PostHog read as worse than it was. `conversions.py` solved the same problem for the ad
platform; this is the same shape for the product analytics.

**The two halves are one person.** The browser sends the id PostHog gave it
(`distinct_id`, a form field of the application) and the event is captured on that id,
so the steps and the completion line up on one person in a funnel. Without one -- the
SDK was blocked, or a client that is not the wizard posted -- an id is invented, which is
right: there is no browser half to pair it with, and the completion still counts.

**No person profile.** The browsers keep anonymous visitors anonymous
(`person_profiles: 'identified_only'`); a server event on an anonymous id would create
a profile behind that policy's back, so every event carries
`$process_person_profile: false`. What travels is the kind of application, the
attribution it came with, and whether a CV was attached: never a name, an address or a
rate.

**Nothing here can break an application.** The row is committed before this is reached,
the call runs in a background task after the response has been sent, and a capture that
raises answers `False`. An empty `REBASE_POSTHOG_KEY` builds no client at all, which is
what the tests and a stack that measures nothing want.

**The team builder's events have no browser half.** A proposal, a request and a
talent's answer are counted where they happen, on the server, each on an id of its own
and with no person profile, carrying counts and words of ours (the origin, how many
people, the tokens, the answer) and never a description, a company or an address.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal
from uuid import uuid4

from rebase_core.config import Settings
from rebase_core.schemas import SignupUtm

if TYPE_CHECKING:
    from posthog import Posthog

APPLICATION_COMPLETED = "iscrizione_completata"
TEAM_PROPOSAL_GENERATED = "team_proposta_generata"

Kind = Literal["freelance", "azienda"]

# What the SDK's `capture` looks like, so a test can hand one that records.
Capture = Callable[..., Any]

_client: Posthog | None = None
_client_key: tuple[str, str] | None = None
_lock = threading.Lock()


@dataclass(frozen=True)
class Tracker:
    capture: Capture

    def application(
        self,
        kind: Kind,
        distinct_id: str | None,
        *,
        cv: bool | None = None,
        utm: SignupUtm | None = None,
    ) -> bool:
        """One `iscrizione_completata`; answers whether the SDK took it."""
        properties: dict[str, Any] = {"tipo": kind, "via": "server"}
        if cv is not None:
            properties["cv"] = cv
        if utm is not None:
            properties.update({key: value for key, value in utm.model_dump().items() if value})
        properties["$process_person_profile"] = False
        try:
            self.capture(
                APPLICATION_COMPLETED,
                distinct_id=distinct_id or uuid4().hex,
                properties=properties,
            )
        except Exception:  # noqa: BLE001 -- the SDK's failure is not the applicant's
            return False
        return True

    def team_event(self, name: str, properties: dict[str, Any]) -> bool:
        """One team builder event (`team_proposta_generata` and the two that follow it,
        spec § 6) on an id of its own: no browser sends a half of it, so there is no
        person to pair it with. Answers whether the SDK took it."""
        try:
            self.capture(
                name,
                distinct_id=uuid4().hex,
                properties={**properties, "$process_person_profile": False},
            )
        except Exception:  # noqa: BLE001 -- the SDK's failure is not the visitor's
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
