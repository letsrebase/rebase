"""Resend, one call per mail, keyed so a retry never sends twice."""

import json

from rebase_core.campaigns.render import RenderedMail
from rebase_core.campaigns.sender import (
    RecordingCampaignSender,
    ResendCampaignSender,
    SendOutcome,
    campaign_sender_from_settings,
)
from rebase_core.config import Settings
from rebase_core.mail import Mail

MAIL = RenderedMail(
    Mail(to="ada@studio.it", subject="S", text="T", html="<p>H</p>"),
    {"List-Unsubscribe": "<https://x/u>", "List-Unsubscribe-Post": "List-Unsubscribe=One-Click"},
    {"campaign": "c-1", "azione": "cv", "kind": "real", "r": "r-1"},
)


class FakeHttp:
    def __init__(self, status: int, body: bytes) -> None:
        self.status, self.body, self.calls = status, body, []

    def __call__(
        self, method: str, url: str, headers: dict[str, str], body: bytes
    ) -> tuple[int, bytes]:
        self.calls.append((method, url, headers, json.loads(body)))
        return self.status, self.body


def test_a_mail_goes_with_its_key_headers_and_tags() -> None:
    http = FakeHttp(200, b'{"id": "re_1"}')
    outcome = ResendCampaignSender("key", "Ivan di rebase <ciao@letsrebase.com>", http).send(
        MAIL, "row-1"
    )
    assert outcome == SendOutcome("accettata", "re_1")
    method, url, headers, body = http.calls[0]
    assert (method, url) == ("POST", "https://api.resend.com/emails")
    assert headers["Idempotency-Key"] == "row-1"
    assert body["from"] == "Ivan di rebase <ciao@letsrebase.com>" and body["to"] == [
        "ada@studio.it"
    ]
    assert body["headers"]["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
    assert {"name": "r", "value": "r-1"} in body["tags"]


def test_a_concurrent_key_a_rate_limit_and_a_network_error_are_retried_later() -> None:
    for status, raw in (
        (409, b'{"name": "concurrent_idempotent_requests"}'),
        (429, b"{}"),
        (503, b""),
        (599, b""),
    ):
        assert (
            ResendCampaignSender("k", "s", FakeHttp(status, raw)).send(MAIL, "x").esito == "riprova"
        )


def test_a_changed_payload_under_the_same_key_and_a_bad_request_are_refused() -> None:
    changed = FakeHttp(409, b'{"name": "invalid_idempotent_request"}')
    assert ResendCampaignSender("k", "s", changed).send(MAIL, "x").esito == "rifiutata"
    assert (
        ResendCampaignSender("k", "s", FakeHttp(422, b'{"name": "validation_error"}'))
        .send(MAIL, "x")
        .esito
        == "rifiutata"
    )


def test_the_seam_never_raises() -> None:
    def boom(*_: object) -> tuple[int, bytes]:
        raise OSError("down")

    assert ResendCampaignSender("k", "s", boom).send(MAIL, "x").esito == "riprova"


def test_recording_sender_answers_in_order_then_accepts() -> None:
    recording = RecordingCampaignSender([SendOutcome("riprova")])
    assert recording.send(MAIL, "a").esito == "riprova"
    assert recording.send(MAIL, "a").esito == "accettata"
    assert recording.keys == ["a", "a"]


def test_no_key_no_sender() -> None:
    assert campaign_sender_from_settings(Settings(_env_file=None)) is None  # type: ignore[call-arg]
