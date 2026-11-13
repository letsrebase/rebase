"""What PigroCRM tells the hub about its engagements and asks it, over HTTP (REB-1106): the
notice route under its own bearer, idempotent and ignoring an older sequence, the reward a signed
notice writes once, and the referrer's name, answered only for a verified referral."""

from collections.abc import Iterator
from decimal import Decimal
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from rebase_core.config import Settings, get_settings
from rebase_core.freelancer_schemas import FreelancerCreate
from rebase_core.freelancers import FreelancerService
from rebase_core.models import HubEngagement, ReferralReward, User
from rebase_core.referrals import ReferralService

TOKEN = "un-token-lungo-per-gli-avvisi-del-crm"
NOTICES = "/api/hub/engagement-notices"
REFERRER = "/api/hub/referrer-name"
PDF = b"%PDF-1.7\n1 0 obj<<>>endobj\n%%EOF\n"
SIGNED_AT = "2026-10-20T10:00:00+00:00"


@pytest.fixture
def notices_token(client: TestClient) -> None:
    """The installation with `REBASE_PIGRO_NOTICES_TOKEN` set, declared and never inherited
    from a developer's `.env`."""
    client.app.dependency_overrides[get_settings] = lambda: Settings(  # type: ignore[attr-defined]
        pigro_notices_token=TOKEN,
        _env_file=None,  # type: ignore[call-arg]
    )


@pytest.fixture
def clean(api_session: Session) -> Iterator[None]:
    yield
    api_session.rollback()
    for table in (
        "referral_rewards",
        "hub_engagements",
        "referrals",
        "referral_settings",
        "freelancers",
        "users",
    ):
        api_session.execute(text(f"DELETE FROM {table}"))
    api_session.commit()


def _bearer(token: str = TOKEN) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _referred_freelancer(session: Session) -> UUID:
    """A freelancer card brought in by a member's code: a referral still to verify."""
    member = User(email="mario@community.it", first_name="Mario", last_name="Rossi", role="member")
    session.add(member)
    session.commit()
    code = ReferralService(session).code_for(member.id)
    session.commit()
    card, _ = FreelancerService(session).apply(
        FreelancerCreate(
            first_name="Ada",
            last_name="Lovelace",
            email="ada@studio.it",
            daily_rate=Decimal("450"),
            job_title="Backend developer",
            remote="remote",
            ref=code,
        ),
        PDF,
        "cv.pdf",
        "application/pdf",
    )
    return card.id


def _body(freelancer_id: UUID, **change: object) -> dict[str, object]:
    body: dict[str, object] = {
        "sequence": 1,
        "kind": "created",
        "engagement_id": str(uuid4()),
        "hub_freelancer_id": str(freelancer_id),
        "client_name": "ACME S.r.l.",
        "mode": "day",
        "fee": "450.00",
        "expected_days": "20.00",
        "role": "Backend developer",
        "starts_on": "2026-10-01",
        "state": "draft",
    }
    body.update(change)
    return body


def _post(client: TestClient, path: str, body: dict[str, object], **headers: str) -> httpx.Response:
    return client.post(path, json=body, headers=headers)


def test_without_the_token_neither_route_exists(client: TestClient) -> None:
    """A 404 whatever the caller presents, not a 401 that says «there is a door»."""
    assert _post(client, NOTICES, {}, **_bearer()).status_code == 404
    assert _post(client, REFERRER, {}, **_bearer()).status_code == 404


def test_only_the_notices_token_opens_them(client: TestClient, notices_token: None) -> None:
    for path in (NOTICES, REFERRER):
        assert _post(client, path, {}).status_code == 401
        assert _post(client, path, {}, **_bearer("sbagliato")).status_code == 401
        # Starlette decodes headers as latin-1: a stray byte is a 401, never a 500.
        odd = client.post(path, json={}, headers={"Authorization": b"Bearer t\xe9ken"})  # type: ignore[dict-item]
        assert odd.status_code == 401, odd.text


def test_a_notice_is_applied_once_and_an_older_one_is_not(
    client: TestClient, notices_token: None, api_session: Session, clean: None
) -> None:
    freelancer_id = _referred_freelancer(api_session)
    first = _body(freelancer_id, sequence=2, state="awaiting_signature", kind="sent")

    assert _post(client, NOTICES, first, **_bearer()).json() == {"applied": True}
    # The same notice again (a retry after a lost answer) and an older one change nothing.
    assert _post(client, NOTICES, first, **_bearer()).json() == {"applied": False}
    older = _body(freelancer_id, engagement_id=first["engagement_id"], sequence=1)
    assert _post(client, NOTICES, older, **_bearer()).json() == {"applied": False}

    api_session.expire_all()
    (row,) = api_session.scalars(select(HubEngagement)).all()
    assert (row.state, row.sequence) == ("awaiting_signature", 2)


def test_a_notice_for_an_unknown_freelancer_or_a_bad_body_is_a_422(
    client: TestClient, notices_token: None, clean: None
) -> None:
    unknown = _body(uuid4())
    assert _post(client, NOTICES, unknown, **_bearer()).status_code == 422
    assert _post(client, NOTICES, {"sequence": 0}, **_bearer()).status_code == 422


def test_a_signed_notice_writes_the_reward_once_and_verifies_the_referral(
    client: TestClient, notices_token: None, api_session: Session, clean: None
) -> None:
    freelancer_id = _referred_freelancer(api_session)
    engagement_id = str(uuid4())
    signed = _body(
        freelancer_id,
        engagement_id=engagement_id,
        sequence=1,
        kind="signed",
        state="active",
        signed_at=SIGNED_AT,
    )

    assert _post(client, NOTICES, signed, **_bearer()).status_code == 200
    assert _post(client, NOTICES, signed, **_bearer()).status_code == 200

    api_session.expire_all()
    # The engagement names no company, so no budget was snapshotted: an admin prices it by hand.
    (reward,) = api_session.scalars(select(ReferralReward)).all()
    assert reward.base_amount is None


def test_the_referrers_name_is_answered_only_for_a_verified_referral(
    client: TestClient, notices_token: None, api_session: Session, clean: None
) -> None:
    freelancer_id = _referred_freelancer(api_session)
    ask = {"hub_freelancer_id": str(freelancer_id), "hub_company_id": None}

    # Not verified yet: a squatted referral must not be printed on a signed document.
    assert _post(client, REFERRER, ask, **_bearer()).json() == {
        "freelancer": None,
        "company": None,
    }

    # The freelancer's own signature verifies them, and then the name is answered.
    signed = _body(freelancer_id, sequence=1, kind="signed", state="active", signed_at=SIGNED_AT)
    assert _post(client, NOTICES, signed, **_bearer()).status_code == 200
    assert _post(client, REFERRER, ask, **_bearer()).json() == {
        "freelancer": "Mario Rossi",
        "company": None,
    }
    # Somebody nobody referred, and a request with no ids at all, are «nobody», never an error.
    assert _post(client, REFERRER, {"hub_freelancer_id": str(uuid4())}, **_bearer()).json() == {
        "freelancer": None,
        "company": None,
    }
    assert _post(client, REFERRER, {}, **_bearer()).json() == {"freelancer": None, "company": None}
