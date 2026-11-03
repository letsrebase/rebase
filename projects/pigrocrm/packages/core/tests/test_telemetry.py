"""The weekly digest's own event (REB-221): what it carries, whose it is, and that
nothing in it can fail the mail run.

The SDK is the only thing faked -- `Tracker` takes the `capture` callable -- because
the shape of the event is the part that is wrong in silence: a property misnamed here
is a metric that quietly stops meaning what the dashboard says it means.
"""

from typing import Any
from uuid import UUID

import pytest

from pigrocrm.core.config import Settings
from pigrocrm.core.telemetry import (
    DIGEST_SENT,
    Tracker,
    build_client,
    shutdown,
    tracker_from_settings,
)


class FakeCapture:
    def __init__(self, raises: bool = False) -> None:
        self.raises = raises
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def __call__(self, event: str, *, distinct_id: str, properties: dict[str, Any]) -> None:
        if self.raises:
            raise RuntimeError("la rete non c'e'")
        self.calls.append((event, distinct_id, properties))


USER_ID = UUID("11111111-2222-3333-4444-555555555555")


def test_a_digest_sent_is_one_event_on_the_recipients_own_id() -> None:
    capture = FakeCapture()
    tracker = Tracker(capture)
    assert tracker.digest_sent(USER_ID, week="2026-W38", sections=4) is True
    assert capture.calls == [
        (
            DIGEST_SENT,
            str(USER_ID),
            # No `$process_person_profile` flag: CRM users are already identified by
            # the browser under this same id, so the profile is wanted here.
            {"week": "2026-W38", "sections": 4, "via": "server"},
        )
    ]


def test_an_sdk_that_raises_cannot_fail_the_digest_run() -> None:
    assert (
        Tracker(FakeCapture(raises=True)).digest_sent(USER_ID, week="2026-W38", sections=4) is False
    )


def test_no_key_means_no_client_and_no_tracker() -> None:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert build_client(settings) is None
    assert tracker_from_settings(settings) is None


@pytest.fixture
def _shutdown_after() -> Any:
    yield
    shutdown()


@pytest.mark.usefixtures("_shutdown_after")
def test_a_key_builds_one_client_per_process_on_the_eu_host() -> None:
    settings = Settings(posthog_key="phc_test", _env_file=None)  # type: ignore[call-arg]
    first = build_client(settings)
    assert first is not None
    assert first.api_key == "phc_test"
    assert first.host == "https://eu.i.posthog.com"
    assert build_client(settings) is first
    tracker = tracker_from_settings(settings)
    assert tracker is not None and tracker.capture == first.capture
