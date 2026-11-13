"""REB-392, spec § 4; REB-1107 over HTTP: `/me/contracts` answers the caller's own documents,
read from the CRM through its door by the caller's freelancer id, and someone else's document is
a 404, never a 403. An unreachable CRM is a 502 and an environment without the token a 503, which
the page shows as its error line, never as an empty list."""

from collections.abc import Iterator
from uuid import UUID

import pytest
from contract_flow import enter
from fakes_cards import text_pdf
from fakes_crm_door import FakeCrmDoor, crm_document
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from rebase_api.deps import get_freelancer_door
from rebase_core.mail import RecordingSender
from rebase_core.models import Freelancer
from rebase_core.pigro_door import FreelancerDoor

PDF = text_pdf("Ada Lovelace Backend developer")
SIGNED_PDF = b"%PDF-1.4 sealed by the signing site"
FRAMEWORK = UUID("0192e0a0-0000-7000-8000-0000000000f1")
LETTER = UUID("0192e0a0-0000-7000-8000-0000000000a1")
SIGNED = {
    "state": "signed",
    "signed_at": "2026-09-30T23:30:00Z",
    "signing_url": None,
    "has_signed_copy": True,
    "filename": "contratto-quadro-v1.0.pdf",
    "active": True,
    "renewal": "2027-10-01",
    "last_notice_day": "2027-09-01",
}


@pytest.fixture
def crm(client: TestClient) -> FakeCrmDoor:
    fake = FakeCrmDoor()
    client.app.dependency_overrides[get_freelancer_door] = fake.door  # type: ignore[attr-defined]
    return fake


@pytest.fixture(autouse=True)
def _clean(api_session: Session) -> Iterator[None]:
    yield
    api_session.rollback()
    for table in ("admin_actions", "notes", "freelancer_cards", "freelancers", "users", "signups"):
        api_session.execute(text(f"DELETE FROM {table}"))
    api_session.commit()


def _member(client: TestClient, sender: RecordingSender, api_session: Session, email: str) -> UUID:
    """A freelancer card for `email`, signed in; its id, which the CRM is asked by."""
    applied = client.post(
        "/api/hub/freelancers",
        data={
            "first_name": "Ada",
            "last_name": "Lovelace",
            "email": email,
            "daily_rate": "450",
            "job_title": "Backend developer",
            "remote": "remote",
        },
        files={"cv": ("Ada CV.pdf", PDF, "application/pdf")},
    )
    assert applied.status_code == 201, applied.text
    enter(client, sender, email)
    api_session.expire_all()
    return api_session.scalars(select(Freelancer.id).order_by(Freelancer.created_at.desc())).first()  # type: ignore[return-value]


def test_without_the_cookie_the_contracts_are_a_401(client: TestClient, crm: FakeCrmDoor) -> None:
    assert client.get("/api/hub/me/contracts").status_code == 401
    missing = "00000000-0000-7000-8000-000000000000"
    assert client.get(f"/api/hub/me/contracts/{missing}/pdf").status_code == 401
    assert crm.calls == []


def test_a_freelancer_reads_their_contracts_and_downloads_the_signed_copy(
    client: TestClient, crm: FakeCrmDoor, sender: RecordingSender, api_session: Session
) -> None:
    ada = _member(client, sender, api_session, "ada@studio.it")
    crm.documents[ada] = [
        crm_document(
            id=str(LETTER),
            kind="engagement_letter",
            number="2026-001",
            state="pending",
            customer="ACME S.r.l.",
            start="1° ottobre 2026",
            signing_url=None,
        ),
        crm_document(id=str(FRAMEWORK), **SIGNED),
    ]
    crm.pdfs[FRAMEWORK] = SIGNED_PDF

    mine = client.get("/api/hub/me/contracts")

    assert mine.status_code == 200, mine.text
    body = mine.json()
    assert (body["framework"]["id"], body["framework"]["status"]) == (str(FRAMEWORK), "signed")
    assert body["framework"]["regime"] == "vat_registered"
    assert [(letter["number"], letter["status"]) for letter in body["letters"]] == [
        ("2026-001", "pending")
    ]
    copy = client.get(f"/api/hub/me/contracts/{FRAMEWORK}/pdf")
    assert copy.status_code == 200
    assert copy.content == SIGNED_PDF
    assert copy.headers["content-type"].startswith("application/pdf")
    assert "contratto-quadro-v1.0.pdf" in copy.headers["content-disposition"]


def test_someone_elses_document_is_a_404_never_a_403(
    client: TestClient, crm: FakeCrmDoor, sender: RecordingSender, api_session: Session
) -> None:
    ada = UUID("0192e0a0-0000-7000-8000-0000000000aa")
    crm.documents[ada] = [crm_document(id=str(FRAMEWORK), **SIGNED)]
    crm.pdfs[FRAMEWORK] = SIGNED_PDF
    _member(client, sender, api_session, "grace@studio.it")

    assert client.get(f"/api/hub/me/contracts/{FRAMEWORK}/pdf").status_code == 404
    assert client.get("/api/hub/me/contracts").json() == {
        "framework": None,
        "occasional_framework": None,
        "previous_frameworks": [],
        "letters": [],
    }


def test_an_unreachable_crm_is_a_502_and_an_unconfigured_one_a_503(
    client: TestClient, crm: FakeCrmDoor, sender: RecordingSender, api_session: Session
) -> None:
    _member(client, sender, api_session, "ada@studio.it")

    crm.down(ConnectionRefusedError(), ConnectionRefusedError())
    down = client.get("/api/hub/me/contracts")
    assert down.status_code == 502, down.text
    assert down.json() == {"detail": "Pigro non risponde."}
    crm.down((500, b""))
    assert client.get(f"/api/hub/me/contracts/{FRAMEWORK}/pdf").status_code == 502

    def no_token() -> FreelancerDoor:
        return crm.door(token="")

    client.app.dependency_overrides[get_freelancer_door] = no_token  # type: ignore[attr-defined]
    unconfigured = client.get("/api/hub/me/contracts")
    assert unconfigured.status_code == 503, unconfigured.text
    assert "non è configurato" in unconfigured.json()["detail"]
