"""The completion event the API sends on its own (REB-215): what it carries, whose it
is, and that nothing in it can fail an application.

The SDK is the only thing faked -- `Tracker` takes the `capture` callable -- because
the shape of the event is the part that is wrong in silence: a property misnamed here
is a funnel that quietly stops matching the browser's half.
"""

from typing import Any

import pytest

from rebase_core.analytics import (
    APPLICATION_COMPLETED,
    Tracker,
    build_client,
    shutdown,
    tracker_from_settings,
)
from rebase_core.config import Settings
from rebase_core.signup_schemas import SignupUtm


class FakeCapture:
    def __init__(self, raises: bool = False) -> None:
        self.raises = raises
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def __call__(self, event: str, *, distinct_id: str, properties: dict[str, Any]) -> None:
        if self.raises:
            raise RuntimeError("la rete non c'e'")
        self.calls.append((event, distinct_id, properties))


def test_a_freelancer_application_is_one_event_on_the_browsers_person() -> None:
    capture = FakeCapture()
    tracker = Tracker(capture)
    assert tracker.application("freelance", "anon-1", cv=True, utm=None) is True
    assert capture.calls == [
        (
            APPLICATION_COMPLETED,
            "anon-1",
            # No person profile: the browser keeps anonymous visitors anonymous
            # (`person_profiles: 'identified_only'`), and a server event must not turn
            # one into a profile behind its back.
            {"kind": "freelance", "via": "server", "cv": True, "$process_person_profile": False},
        )
    ]


def test_a_company_request_carries_no_cv_flag_and_the_attribution_it_came_with() -> None:
    capture = FakeCapture()
    utm = SignupUtm(utm_source="linkedin", utm_campaign="orbita", origin="home")
    Tracker(capture).application("company", "anon-2", utm=utm)
    ((_, _, properties),) = capture.calls
    assert "cv" not in properties
    assert properties["kind"] == "company"
    assert properties["utm_source"] == "linkedin"
    assert properties["utm_campaign"] == "orbita"
    assert properties["origin"] == "home"
    # Empty attribution keys are left out rather than sent as nulls.
    assert "utm_medium" not in properties


def test_a_browser_that_sent_no_id_still_counts_once() -> None:
    """An ad blocker that ate the SDK is the whole reason this event exists: the server
    invents an id, so the completion is counted, on a person of its own."""
    capture = FakeCapture()
    tracker = Tracker(capture)
    tracker.application("freelance", None, cv=False)
    tracker.application("freelance", "", cv=False)
    ids = [distinct_id for _, distinct_id, _ in capture.calls]
    assert len(ids) == 2 and all(len(i) == 32 for i in ids) and ids[0] != ids[1]


def test_an_sdk_that_raises_cannot_fail_the_application() -> None:
    assert Tracker(FakeCapture(raises=True)).application("freelance", "anon-1", cv=True) is False


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
