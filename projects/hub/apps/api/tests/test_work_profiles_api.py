"""`POST /api/hub/work-profiles` (REB-1124): the bearer, the one multipart part, the refusals and
the 201."""

import json
from typing import Any

import pytest
from contract_api_helpers import login_as
from fastapi.testclient import TestClient
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session
from work_profile_api_helpers import apply_card
from work_profile_helpers import sample

from rebase_api.deps import get_sender
from rebase_core.mail import RecordingSender
from rebase_core.models import CliAuthorization, WorkProfile

URL = "/api/hub/work-profiles"


@pytest.fixture
def token(client: TestClient, api_session: Session) -> str:
    """A bearer the whole sign-in produced, for the freelancer `apply_freelancer` makes."""
    sender = RecordingSender()
    client.app.dependency_overrides[get_sender] = lambda: sender  # type: ignore[attr-defined]
    started = client.post("/api/hub/cli/device", json={"scope": "work_profile"}).json()
    apply_card(client)
    login_as(client, sender, "ada@studio.it")
    client.post(f"/api/hub/cli/authorizations/{started['user_code']}/approve").raise_for_status()
    api_session.execute(update(CliAuthorization).values(last_polled_at=None))
    api_session.commit()
    polled = client.post("/api/hub/cli/token", json={"device_code": started["device_code"]})
    client.cookies.clear()  # the CLI has no cookie
    return str(polled.json()["access_token"])


def _send(client: TestClient, bearer: str | None, profile: Any = None, **extra: Any) -> Any:
    body = json.dumps(sample() if profile is None else profile).encode()
    files = {"profile": ("candidate.json", body, "application/json"), **extra}
    headers = {"Authorization": f"Bearer {bearer}"} if bearer else {}
    return client.post(URL, files=files, headers=headers)


def test_a_profile_is_stored_as_a_draft_and_answers_201(
    client: TestClient, api_session: Session, token: str
) -> None:
    response = _send(client, token)

    assert response.status_code == 201, response.text
    assert response.json() == {"state": "draft", "review_url": "https://letsrebase.com/hub/me"}
    row = api_session.scalars(select(WorkProfile)).one()
    assert row.state == "draft" and "contact" not in row.payload
    assert _send(client, token).status_code == 401  # the token went with the first profile


@pytest.mark.parametrize("bearer", [None, "nonsense"])
def test_without_a_live_token_it_is_401_before_the_body_matters(
    client: TestClient, bearer: str | None
) -> None:
    response = client.post(
        URL, content=b"garbage", headers={"Authorization": f"Bearer {bearer}"} if bearer else {}
    )
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_any_other_part_is_refused_with_the_sentence_of_the_spec(
    client: TestClient, api_session: Session, token: str
) -> None:
    response = _send(client, token, attachment=("a.zip", b"PK", "application/zip"))

    assert response.status_code == 422
    assert response.json()["detail"] == "gli allegati non sono accettati: indica un link"
    assert api_session.scalar(select(func.count()).select_from(WorkProfile)) == 0


def test_a_repo_label_is_a_422_and_the_token_is_still_good(client: TestClient, token: str) -> None:
    profile = sample()
    profile["projects"][0]["repoLabel"] = "acme"

    assert _send(client, token, profile).status_code == 422
    assert _send(client, token).status_code == 201


def test_a_missing_profile_part_or_a_plain_body_is_422(client: TestClient, token: str) -> None:
    assert (
        client.post(URL, json={}, headers={"Authorization": f"Bearer {token}"}).status_code == 422
    )
    empty = client.post(
        URL, files={"other": ("x", b"1")}, headers={"Authorization": f"Bearer {token}"}
    )
    assert empty.status_code == 422


def test_an_upload_far_over_512_kb_is_413(client: TestClient, token: str) -> None:
    big = client.post(
        URL,
        files={"profile": ("candidate.json", b" " * (600 * 1024), "application/json")},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert big.status_code == 413
