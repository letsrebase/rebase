"""Resend's webhook over HTTP (spec § 6.1)."""

import base64
import hashlib
import hmac
import json
import time
from collections.abc import Iterator

import pytest
from campaign_api_flow import tidy  # noqa: F401  (fixture)
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from rebase_core.config import Settings, get_settings

SECRET = "whsec_" + base64.b64encode(b"s" * 24).decode()


@pytest.fixture
def armed(client: TestClient) -> Iterator[TestClient]:
    client.app.dependency_overrides[get_settings] = lambda: Settings(
        _env_file=None, resend_webhook_secret=SECRET
    )  # type: ignore[attr-defined,call-arg]
    yield client


def post_body(client: TestClient, body: bytes, *, secret: str = SECRET) -> int:
    stamp = str(int(time.time()))
    mac = hmac.new(
        base64.b64decode(secret.removeprefix("whsec_")),
        f"msg_1.{stamp}.".encode() + body,
        hashlib.sha256,
    )
    headers = {
        "svix-id": "msg_1",
        "svix-timestamp": stamp,
        "svix-signature": "v1," + base64.b64encode(mac.digest()).decode(),
    }
    return client.post("/api/hub/webhooks/resend", content=body, headers=headers).status_code


def post(client: TestClient, payload: dict[str, object], *, secret: str = SECRET) -> int:
    return post_body(client, json.dumps(payload).encode(), secret=secret)


def test_without_the_secret_the_webhook_is_off(client: TestClient) -> None:
    assert client.post("/api/hub/webhooks/resend", content=b"{}").status_code == 503


def test_a_bad_signature_is_refused(armed: TestClient) -> None:
    other = "whsec_" + base64.b64encode(b"x" * 24).decode()
    assert post(armed, {"type": "email.delivered", "data": {}}, secret=other) == 401


def test_missing_svix_headers_are_refused(armed: TestClient) -> None:
    assert armed.post("/api/hub/webhooks/resend", content=b"{}").status_code == 401


def test_a_correctly_signed_non_json_body_is_refused(armed: TestClient) -> None:
    assert post_body(armed, b"not json") == 400


def test_a_correctly_signed_json_array_is_refused_not_crashed(armed: TestClient) -> None:
    assert post_body(armed, b"[]") == 400


def test_a_stranger_is_acknowledged_and_an_early_tagged_event_is_retried(
    armed: TestClient,
    tidy: Session,  # noqa: F811  (fixture)
) -> None:
    assert post(armed, {"type": "email.delivered", "data": {"email_id": "re_x", "tags": {}}}) == 200
    early = {
        "type": "email.delivered",
        "data": {
            "email_id": "re_y",
            "tags": {"campaign": "c-n", "r": "00000000-0000-0000-0000-000000000000"},
        },
    }
    assert post(armed, early) == 503
