"""What leaves this process when a signup converts, and what deliberately does not.

The network is the only thing faked: the body, the URL, the header and the failure
classification all run for real, because those are the parts that are wrong in silence.
An event OpenAI refuses is not an exception anywhere -- it is a campaign whose numbers
are missing and nobody noticing -- so most of this file is about the exact shape of one
JSON object.
"""

import json
from typing import Any

import pytest

from rebase_core.config import Settings
from rebase_core.conversions import (
    CONVERSIONS_URL,
    NETWORK_ERROR_STATUS,
    USER_AGENT_MAX_LENGTH,
    ConversionsPixel,
    Visitor,
    hashed_email,
    pixel_from_settings,
)

PIXEL_ID = "9r6qrnPxBV8WDVGtpuaqxh"
KEY = "sk-svcacct-non-una-chiave-vera"
EVENT_ID = "8f14e45f-ceea-467a-9f36-dcd8b0eba0b1"
SIGNUP_URL = "https://letsrebase.com/"


class FakeHttp:
    """One recorded call and one canned answer. `raises` makes the socket itself fail,
    which is a different path from a status OpenAI chose to return."""

    def __init__(self, status: int = 200, body: bytes = b'{"ok":true}', raises: bool = False):
        self.status = status
        self.body = body
        self.raises = raises
        self.calls: list[tuple[str, str, dict[str, str], bytes]] = []

    def __call__(
        self, method: str, url: str, headers: dict[str, str], body: bytes
    ) -> tuple[int, bytes]:
        self.calls.append((method, url, headers, body))
        if self.raises:
            raise OSError("la rete non c'e'")
        return self.status, self.body

    @property
    def event(self) -> dict[str, Any]:
        sent = json.loads(self.calls[-1][3])
        events = sent["events"]
        assert len(events) == 1, "un'iscrizione e' un evento, non un batch"
        return dict(events[0])

    @property
    def sent(self) -> dict[str, Any]:
        return dict(json.loads(self.calls[-1][3]))


def _pixel(http: FakeHttp, **overrides: Any) -> ConversionsPixel:
    return ConversionsPixel(
        pixel_id=PIXEL_ID, api_key=KEY, http=http, _clock=lambda: 1_757_000_000.5, **overrides
    )


def _send(pixel: ConversionsPixel, **overrides: Any) -> Any:
    kwargs: dict[str, Any] = {"event_id": EVENT_ID, "source_url": SIGNUP_URL}
    kwargs.update(overrides)
    return pixel.send(**kwargs)


# --- the request ----------------------------------------------------------------------


def test_posts_to_the_documented_endpoint_with_the_pixel_id_in_the_query() -> None:
    http = FakeHttp()
    _send(_pixel(http))
    method, url, headers, _ = http.calls[0]
    assert method == "POST"
    assert url == f"{CONVERSIONS_URL}?pid={PIXEL_ID}"
    assert headers["Content-Type"] == "application/json"


def test_the_key_travels_only_in_the_authorization_header() -> None:
    """Not in the URL, where it would reach an access log, and not in the body."""
    http = FakeHttp()
    _send(_pixel(http))
    _, url, headers, body = http.calls[0]
    assert headers["Authorization"] == f"Bearer {KEY}"
    assert KEY not in url
    assert KEY.encode() not in body


def test_the_event_is_a_registration_completed_customer_action_on_the_web() -> None:
    http = FakeHttp()
    _send(_pixel(http))
    assert http.event["type"] == "registration_completed"
    assert http.event["data"] == {"type": "customer_action"}
    assert http.event["action_source"] == "web"
    assert http.event["source_url"] == SIGNUP_URL


def test_the_id_is_the_one_the_browser_used_so_the_two_are_one_conversion() -> None:
    """Deduplication is on (pixel id, event name, id). If this were generated here the
    same conversion would be counted twice -- once by the pixel, once by us."""
    http = FakeHttp()
    _send(_pixel(http))
    assert http.event["id"] == EVENT_ID


def test_the_timestamp_is_in_milliseconds() -> None:
    """Seconds would be a timestamp in 1970, which the API refuses as older than seven
    days -- and would refuse for every event, forever, with nothing else to notice."""
    http = FakeHttp()
    _send(_pixel(http))
    assert http.event["timestamp_ms"] == 1_757_000_000_500


def test_validate_only_is_false_unless_a_caller_asks() -> None:
    http = FakeHttp()
    _send(_pixel(http))
    assert http.sent["validate_only"] is False
    _send(_pixel(http), validate_only=True)
    assert http.sent["validate_only"] is True


# --- what identifies the visitor, and what does not ------------------------------------


def test_the_click_identifier_sits_at_the_top_level_unchanged() -> None:
    """`oppref` identifies the click, not the person, and OpenAI's instruction is to
    pass it as it arrived."""
    http = FakeHttp()
    _send(_pixel(http), visitor=Visitor(oppref="clic-{ABC}.123"))
    assert http.event["oppref"] == "clic-{ABC}.123"


def test_the_browser_reference_the_address_and_the_agent_go_under_user() -> None:
    http = FakeHttp()
    _send(
        _pixel(http),
        visitor=Visitor(obref="ob-1", ip_address="203.0.113.7", user_agent="Mozilla/5.0"),
    )
    assert http.event["user"] == {
        "obref": "ob-1",
        "ip_address": "203.0.113.7",
        "user_agent": "Mozilla/5.0",
    }


def test_a_visitor_we_know_nothing_about_has_no_user_key_at_all() -> None:
    """`"user": {}` and no `user` mean the same thing to the platform; the second is the
    honest shape."""
    http = FakeHttp()
    _send(_pixel(http), visitor=Visitor())
    assert "user" not in http.event
    assert "oppref" not in http.event


def test_the_address_is_not_sent_by_default_in_any_form() -> None:
    """The default matters more than the feature: an installation that pastes a pixel id
    must not start shipping its subscribers' addresses to an ad platform because of it.
    Neither the address nor its hash appears anywhere in the request."""
    http = FakeHttp()
    _send(_pixel(http), visitor=Visitor(email="ada@studio.it", ip_address="203.0.113.7"))
    body = http.calls[-1][3].decode()
    assert "ada@studio.it" not in body
    assert hashed_email("ada@studio.it") not in body
    assert "emails_sha256" not in body


def test_the_address_is_sent_hashed_only_when_the_installation_switched_it_on() -> None:
    http = FakeHttp()
    _send(_pixel(http, send_hashed_email=True), visitor=Visitor(email="  Ada@Studio.IT "))
    # Trimmed and lowercased before hashing, which is OpenAI's normalisation: a hash of
    # the raw string would match nothing and would look like it was working.
    assert http.event["user"]["emails_sha256"] == [hashed_email("ada@studio.it")]
    assert "ada@studio.it" not in http.calls[-1][3].decode()


def test_the_name_and_the_profile_never_travel() -> None:
    """There is no field for them, by construction: `Visitor` has nowhere to put a name
    or a LinkedIn URL, so no call site can decide to send one."""
    assert not {"first_name", "last_name", "linkedin_url", "name"} & set(
        Visitor.__dataclass_fields__
    )


def test_a_very_long_user_agent_is_cut_rather_than_dropping_the_event() -> None:
    http = FakeHttp()
    _send(_pixel(http), visitor=Visitor(user_agent="U" * 5_000))
    assert len(http.event["user"]["user_agent"]) == USER_AGENT_MAX_LENGTH


# --- failures -------------------------------------------------------------------------


def test_a_success_is_reported_as_sent() -> None:
    assert _send(_pixel(FakeHttp(status=202))).sent is True


@pytest.mark.parametrize("status", [400, 401, 403, 429, 500, 503])
def test_a_refusal_is_not_an_exception_and_says_which_status(status: int) -> None:
    """This runs in a background task after the response: raising would print a
    traceback carrying the bearer token and change nothing for the person who signed up."""
    outcome = _send(_pixel(FakeHttp(status=status, body=b'{"error":"nope"}')))
    assert outcome.sent is False
    assert outcome.status == status
    assert outcome.detail == '{"error":"nope"}'


def test_a_dead_network_is_a_599_and_not_a_raise() -> None:
    outcome = _send(_pixel(FakeHttp(raises=True)))
    assert outcome.sent is False
    assert outcome.status == NETWORK_ERROR_STATUS


def test_nothing_of_ours_is_kept_from_a_refusal() -> None:
    """Their words about our event, bounded. A body that echoed the request back would
    otherwise put an email hash into whatever the caller does with `detail`."""
    outcome = _send(_pixel(FakeHttp(status=400, body=b"x" * 5_000)))
    assert len(outcome.detail) == 200


# --- configuration --------------------------------------------------------------------


def test_an_installation_without_a_pixel_has_none() -> None:
    """`None` rather than a disabled object: not configured is not the same as failing,
    and a caller that must check for `None` cannot forget the difference."""
    assert pixel_from_settings(Settings(_env_file=None)) is None  # type: ignore[call-arg]


@pytest.mark.parametrize(
    ("pixel_id", "key"),
    [(PIXEL_ID, ""), ("", KEY)],
)
def test_half_a_configuration_is_no_configuration(pixel_id: str, key: str) -> None:
    """An id with no key cannot post; a key with no id has nowhere to post to."""
    settings = Settings(
        openai_pixel_id=pixel_id,
        openai_conversions_api_key=key,
        _env_file=None,  # type: ignore[call-arg]
    )
    assert pixel_from_settings(settings) is None


def test_a_configured_installation_carries_its_own_choice_about_the_address() -> None:
    settings = Settings(
        openai_pixel_id=PIXEL_ID,
        openai_conversions_api_key=KEY,
        openai_conversions_send_hashed_email=True,
        _env_file=None,  # type: ignore[call-arg]
    )
    pixel = pixel_from_settings(settings)
    assert pixel is not None
    assert pixel.pixel_id == PIXEL_ID
    assert pixel.send_hashed_email is True
