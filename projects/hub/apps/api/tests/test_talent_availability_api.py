"""`GET /api/hub/talent/availability` (REB-1120): who is committed, as the CRM says, for an admin's
«Talenti» and team request pages, in one read of the CRM, and the CRM's outage as the door's own
502 or 503 rather than a broken page."""

import re
from collections.abc import Iterator
from uuid import uuid4

import pytest
from fakes_crm_door import FakeCrmDoor
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from rebase_api.deps import get_freelancer_door
from rebase_core.mail import RecordingSender
from rebase_core.models import User

ADMIN_EMAIL = "ivan@rebase.it"
URL = "/api/hub/talent/availability"
WEEK = {
    "week": "2026-W47",
    "starts_on": "2026-11-16",
    "ends_on": "2026-11-22",
    "status": "committed",
    "label": "impegnata",
    "committed_days": "3.0",
    "free_days": "2.0",
    "engagement_ids": [],
}


@pytest.fixture
def admin(api_session: Session) -> Iterator[User]:
    row = User(email=ADMIN_EMAIL, first_name="Ivan", last_name="", role="admin")
    api_session.add(row)
    api_session.commit()
    yield row
    api_session.rollback()
    for table in ("logins", "sessions", "magic_link_tokens", "users"):
        api_session.execute(text(f"DELETE FROM {table}"))
    api_session.commit()


@pytest.fixture
def crm(client: TestClient) -> FakeCrmDoor:
    fake = FakeCrmDoor()
    client.app.dependency_overrides[get_freelancer_door] = fake.door  # type: ignore[attr-defined]
    return fake


def _login(client: TestClient, sender: RecordingSender) -> None:
    assert client.post("/api/hub/auth/link", json={"email": ADMIN_EMAIL}).status_code == 202
    match = re.search(r"/verify\?t=([A-Za-z0-9_-]+)", sender.sent[-1].text)
    assert match
    assert client.post("/api/hub/auth/enter", json={"token": match.group(1)}).status_code == 200


def test_an_admin_reads_every_committed_freelancer_in_one_call(
    client: TestClient, sender: RecordingSender, admin: User, crm: FakeCrmDoor
) -> None:
    busy = uuid4()
    crm.committed[busy] = [
        WEEK,
        {
            **WEEK,
            "week": "2026-W48",
            "starts_on": "2026-11-23",
            "ends_on": "2026-11-29",
            "status": "free",
            "committed_days": "0",
        },
    ]
    _login(client, sender)

    answer = client.get(URL)

    assert answer.status_code == 200, answer.text
    assert answer.json() == {
        "date_from": "2026-11-16",
        "date_to": "2026-11-29",
        "freelancers": {
            str(busy): {
                "status": "committed",
                "committed_days": "3.0",
                "committed_from": None,
                "free_from": "2026-11-23",
                "until": "2026-11-29",
            }
        },
    }
    assert crm.calls == [("GET", "/api/rebase/freelancers/commitments")]


def test_a_crm_that_does_not_answer_is_a_502_and_one_not_configured_a_503(
    client: TestClient, sender: RecordingSender, admin: User, crm: FakeCrmDoor
) -> None:
    _login(client, sender)
    crm.down((500, b"{}"))

    down = client.get(URL)
    assert down.status_code == 502, down.text
    assert "La disponibilità non si legge senza il CRM." in down.json()["detail"]

    overrides = client.app.dependency_overrides  # type: ignore[attr-defined]
    overrides[get_freelancer_door] = lambda: crm.door(token="")
    assert client.get(URL).status_code == 503
