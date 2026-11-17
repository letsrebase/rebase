"""The CLI's sign-in over HTTP (REB-1133): the device start, the token polling in RFC 8628's
shapes, the signed-in person's page calls, and the two limits."""

from typing import Any

import pytest
from contract_api_helpers import login_as
from fastapi.testclient import TestClient
from sqlalchemy import update
from sqlalchemy.orm import Session
from work_profile_api_helpers import apply_card

from rebase_api.deps import get_sender
from rebase_core.mail import RecordingSender
from rebase_core.models import CliAuthorization, User

CARD = "ada@studio.it"
NO_CARD = "someone@studio.it"


@pytest.fixture
def sender(client: TestClient) -> RecordingSender:
    recording = RecordingSender()
    client.app.dependency_overrides[get_sender] = lambda: recording  # type: ignore[attr-defined]
    return recording


def _start(client: TestClient) -> dict[str, Any]:
    response = client.post("/api/hub/cli/device", json={"scope": "work_profile"})
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def _poll(client: TestClient, api_session: Session, device_code: str) -> Any:
    """A poll the CLI would make `interval` seconds after its last one."""
    api_session.execute(update(CliAuthorization).values(last_polled_at=None))
    api_session.commit()
    return client.post("/api/hub/cli/token", json={"device_code": device_code})


def test_a_start_answers_the_rfc_8628_fields_at_the_top_level(client: TestClient) -> None:
    body = _start(client)

    assert set(body) == {
        "device_code",
        "user_code",
        "verification_uri",
        "verification_uri_complete",
        "expires_in",
        "interval",
    }
    assert body["verification_uri"] == "https://letsrebase.com/hub/cli"
    assert body["verification_uri_complete"].endswith(f"?code={body['user_code']}")
    assert (body["expires_in"], body["interval"]) == (600, 5)


def test_polling_answers_the_rfc_errors_as_400_json_until_the_person_decides(
    client: TestClient, api_session: Session
) -> None:
    device = _start(client)["device_code"]

    pending = _poll(client, api_session, device)
    assert (pending.status_code, pending.json()) == (400, {"error": "authorization_pending"})
    fast = client.post("/api/hub/cli/token", json={"device_code": device})
    assert fast.json() == {"error": "slow_down"}
    unknown = client.post("/api/hub/cli/token", json={"device_code": "x"})
    assert (unknown.status_code, unknown.json()) == (400, {"error": "expired_token"})


def test_the_signed_in_person_approves_and_the_cli_gets_a_bearer_token_once(
    client: TestClient, api_session: Session, sender: RecordingSender
) -> None:
    started = _start(client)
    apply_card(client)
    login_as(client, sender, CARD)

    shown = client.get(f"/api/hub/cli/authorizations/{started['user_code']}")
    assert shown.status_code == 200
    assert shown.json() == {
        "user_code": started["user_code"],
        "scope": "work_profile",
        "state": "pending",
        "can_authorize": True,
        "blocked_reason": None,
        "expires_at": shown.json()["expires_at"],
    }
    approved = client.post(f"/api/hub/cli/authorizations/{started['user_code']}/approve")
    assert approved.status_code == 200 and approved.json()["state"] == "approved"

    granted = _poll(client, api_session, started["device_code"])
    assert granted.status_code == 200
    assert set(granted.json()) == {"access_token", "token_type", "expires_in"}
    assert (granted.json()["token_type"], granted.json()["expires_in"]) == ("Bearer", 900)
    again = _poll(client, api_session, started["device_code"])
    assert again.json() == {"error": "expired_token"}


def test_cancelling_is_access_denied(
    client: TestClient, api_session: Session, sender: RecordingSender
) -> None:
    started = _start(client)
    apply_card(client)
    login_as(client, sender, CARD)

    denied = client.post(f"/api/hub/cli/authorizations/{started['user_code']}/deny")
    assert denied.json()["state"] == "denied"
    polled = _poll(client, api_session, started["device_code"])
    assert (polled.status_code, polled.json()) == (400, {"error": "access_denied"})


def test_somebody_with_no_card_sees_the_reason_and_cannot_authorize(
    client: TestClient, api_session: Session, sender: RecordingSender
) -> None:
    started = _start(client)
    api_session.add(User(email=NO_CARD, first_name="No", last_name="Card"))
    api_session.commit()
    login_as(client, sender, NO_CARD)

    shown = client.get(f"/api/hub/cli/authorizations/{started['user_code']}").json()
    assert shown["can_authorize"] is False and "scheda" in shown["blocked_reason"]
    refused = client.post(f"/api/hub/cli/authorizations/{started['user_code']}/approve")
    assert refused.status_code == 403 and "scheda" in refused.json()["detail"]


def test_the_page_calls_need_a_signed_in_person(client: TestClient) -> None:
    code = _start(client)["user_code"]
    for call in (
        client.get(f"/api/hub/cli/authorizations/{code}"),
        client.post(f"/api/hub/cli/authorizations/{code}/approve"),
        client.post(f"/api/hub/cli/authorizations/{code}/deny"),
    ):
        assert call.status_code == 401


def test_a_wrong_code_is_404_and_the_sixth_in_ten_minutes_is_429(
    client: TestClient, sender: RecordingSender
) -> None:
    apply_card(client)
    login_as(client, sender, CARD)

    for _ in range(5):
        wrong = client.get("/api/hub/cli/authorizations/ZZZZ-ZZZZ")
        assert wrong.status_code == 404 and "codice" in wrong.json()["detail"].lower()
    limited = client.get("/api/hub/cli/authorizations/ZZZZ-ZZZZ")
    assert limited.status_code == 429 and "Retry-After" in limited.headers
    assert "codici sbagliati" in limited.json()["detail"]


def test_the_eleventh_start_an_hour_from_one_address_is_429_and_an_ipv6_net_is_one_address(
    client: TestClient,
) -> None:
    for n in range(10):
        suffix = f"{n:x}"
        ok = client.post(
            "/api/hub/cli/device",
            json={},
            headers={"X-Real-IP": f"2001:db8:1:2::{suffix}"},
        )
        assert ok.status_code == 200
    blocked = client.post(
        "/api/hub/cli/device", json={}, headers={"X-Real-IP": "2001:db8:1:2:aaaa::1"}
    )
    assert blocked.status_code == 429 and "Retry-After" in blocked.headers
    other = client.post("/api/hub/cli/device", json={}, headers={"X-Real-IP": "203.0.113.9"})
    assert other.status_code == 200
