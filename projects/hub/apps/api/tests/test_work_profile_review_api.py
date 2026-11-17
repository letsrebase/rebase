"""The admin's «Profilo di lavoro» over HTTP (REB-1126): read, approve and reject, the 409 of a
second decision, and the «Talenti» filter."""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from contract_api_helpers import login_as
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session
from work_profile_helpers import sample

from rebase_api.deps import get_sender
from rebase_core.mail import RecordingSender
from rebase_core.models import AdminAction, Freelancer, User, WorkProfile

ADMIN_EMAIL = "ivan@rebase.it"


@pytest.fixture
def sender(client: TestClient) -> Iterator[RecordingSender]:
    recording = RecordingSender()
    client.app.dependency_overrides[get_sender] = lambda: recording  # type: ignore[attr-defined]
    yield recording


def _seed(session: Session, email: str = "ada@studio.it", *, expires_in_days: int = 300) -> str:
    user = User(email=email, first_name="Ada", last_name="Lovelace")
    session.add(user)
    session.flush()
    card = Freelancer(user_id=user.id)
    session.add(card)
    session.flush()
    now = datetime.now(UTC)
    payload = {key: value for key, value in sample().items() if key != "contact"}
    session.add(
        WorkProfile(
            freelancer_id=card.id,
            payload=payload,
            tool_version="1.0.0",
            groundedness=89,
            unsupported_anchors=[{"where": "summary", "anchor": "12 commits", "kind": "number"}],
            submitted_at=now - timedelta(days=5),
            expires_at=now + timedelta(days=expires_in_days),
        )
    )
    session.commit()
    return str(card.id)


@pytest.fixture
def admin(client: TestClient, sender: RecordingSender, api_session: Session) -> None:
    api_session.add(User(email=ADMIN_EMAIL, first_name="Ivan", last_name="", role="admin"))
    api_session.commit()
    login_as(client, sender, ADMIN_EMAIL)


def test_the_section_reads_the_state_the_recheck_and_the_rendered_profile(
    client: TestClient, api_session: Session, admin: None
) -> None:
    freelancer_id = _seed(api_session)

    body = client.get(f"/api/hub/freelancers/{freelancer_id}/work-profile").json()

    assert (body["state"], body["expired"], body["groundedness"]) == ("draft", False, 89)
    assert body["unsupported_anchors"] == [
        {"where": "summary", "anchor": "12 commits", "kind": "number"}
    ]
    assert body["profile"]["summary"] == sample()["summary"]
    assert body["profile"]["projects"][0]["id"] == "p1"


def test_approving_vets_and_a_second_decision_is_a_409(
    client: TestClient, api_session: Session, admin: None
) -> None:
    freelancer_id = _seed(api_session)
    url = f"/api/hub/freelancers/{freelancer_id}/work-profile"

    approved = client.post(f"{url}/approve", json={"note": "solido"})

    assert approved.status_code == 200, approved.text
    assert approved.json()["state"] == "approved" and approved.json()["decision_note"] == "solido"
    api_session.expire_all()
    assert api_session.get_one(Freelancer, freelancer_id).vetted_at is not None
    kinds = {a.kind for a in api_session.scalars(select(AdminAction))}
    assert {"vetted", "work_profile_decided"} <= kinds
    assert client.post(f"{url}/reject", json={"note": "no"}).status_code == 409
    assert client.post(f"{url}/approve", json={}).status_code == 409


def test_rejecting_needs_a_note_and_an_expired_profile_cannot_be_decided(
    client: TestClient, api_session: Session, admin: None
) -> None:
    freelancer_id = _seed(api_session)
    url = f"/api/hub/freelancers/{freelancer_id}/work-profile"
    assert client.post(f"{url}/reject", json={}).status_code == 422
    assert client.post(f"{url}/reject", json={"note": "  "}).status_code == 422

    rejected = client.post(f"{url}/reject", json={"note": "Manca un link."})
    assert rejected.json()["state"] == "rejected"
    api_session.expire_all()
    assert api_session.get_one(Freelancer, freelancer_id).vetted_at is None

    old = _seed(api_session, "old@studio.it", expires_in_days=-1)
    gone = client.get(f"/api/hub/freelancers/{old}/work-profile").json()
    assert gone["expired"] is True
    assert (
        client.post(f"/api/hub/freelancers/{old}/work-profile/approve", json={}).status_code == 409
    )


def test_a_person_with_no_profile_is_a_404(client: TestClient, admin: None) -> None:
    unknown = "00000000-0000-7000-8000-000000000009"
    assert client.get(f"/api/hub/freelancers/{unknown}/work-profile").status_code == 404


def test_the_talent_list_filters_by_review_state_and_says_it(
    client: TestClient, api_session: Session, admin: None
) -> None:
    waiting = _seed(api_session, "wait@studio.it")
    api_session.add(Freelancer(user_id=_user(api_session, "none@studio.it")))  # no profile
    api_session.commit()

    listed = client.get("/api/hub/talent", params={"work_profile": "draft"}).json()
    assert [(i["id"], i["work_profile_state"]) for i in listed["items"]] == [(waiting, "draft")]
    assert client.get("/api/hub/talent", params={"work_profile": "none"}).json()["total"] == 1
    assert client.get("/api/hub/talent", params={"work_profile": "maybe"}).status_code == 422


def _user(session: Session, email: str) -> UUID:
    user = User(email=email, first_name="No", last_name="Profile")
    session.add(user)
    session.flush()
    return user.id
