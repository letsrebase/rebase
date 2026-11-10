"""REB-738 over HTTP: whether push is available, and a person's browsers."""

import base64
import os

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient

from pigrocrm.core.config import Settings, get_settings
from pigrocrm.core.reminders.push import generate_vapid_keys


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _subscription(endpoint: str = "https://fcm.googleapis.com/fcm/send/abc") -> dict[str, str]:
    public = (
        ec.generate_private_key(ec.SECP256R1())
        .public_key()
        .public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    )
    return {"endpoint": endpoint, "p256dh": _b64url(public), "auth": _b64url(os.urandom(16))}


def test_push_is_not_available_until_the_installation_has_a_key(logged_in: TestClient) -> None:
    response = logged_in.get("/api/push/config")

    assert response.status_code == 200
    assert response.json() == {"enabled": False, "public_key": None}


def test_a_configured_installation_hands_the_browser_the_key_to_subscribe_with(
    logged_in: TestClient,
) -> None:
    private, public = generate_vapid_keys()
    logged_in.app.dependency_overrides[get_settings] = lambda: Settings(  # type: ignore[attr-defined,call-arg]
        vapid_private_key=private, _env_file=None
    )

    response = logged_in.get("/api/push/config")

    assert response.json() == {"enabled": True, "public_key": public}
    assert private not in response.text, "the private half never leaves the server"


def test_a_person_subscribes_lists_and_unsubscribes_a_browser(logged_in: TestClient) -> None:
    body = _subscription()

    saved = logged_in.post("/api/push/subscribe", json=body, headers={"User-Agent": "Chrome/130"})

    assert saved.status_code == 200, saved.text
    assert saved.json()["user_agent"] == "Chrome/130"
    listed = logged_in.get("/api/push/subscriptions").json()
    assert [row["endpoint"] for row in listed] == [body["endpoint"]]
    assert "p256dh" not in listed[0] and "auth" not in listed[0], "keys are never read back"

    gone = logged_in.post("/api/push/unsubscribe", json={"endpoint": body["endpoint"]})
    assert gone.status_code == 204
    assert logged_in.get("/api/push/subscriptions").json() == []
    again = logged_in.post("/api/push/unsubscribe", json={"endpoint": body["endpoint"]})
    assert again.status_code == 204, "twice is the same state"


@pytest.mark.parametrize("client_name", ["readonly_client", "collaborator_client"])
def test_every_role_has_its_own_browsers(client_name: str, request: pytest.FixtureRequest) -> None:
    client: TestClient = request.getfixturevalue(client_name)

    assert client.post("/api/push/subscribe", json=_subscription()).status_code == 200
    assert len(client.get("/api/push/subscriptions").json()) == 1


def test_an_endpoint_that_is_not_a_push_service_is_a_422(logged_in: TestClient) -> None:
    response = logged_in.post(
        "/api/push/subscribe", json=_subscription("https://169.254.169.254/latest/meta-data")
    )

    assert response.status_code == 422
    assert "servizio push" in response.text


def test_nothing_is_open_without_a_session(client: TestClient) -> None:
    assert client.get("/api/push/config").status_code == 401
    assert client.post("/api/push/subscribe", json=_subscription()).status_code == 401
    assert (
        client.post("/api/push/unsubscribe", json={"endpoint": "https://x.example"}).status_code
        == 401
    )
