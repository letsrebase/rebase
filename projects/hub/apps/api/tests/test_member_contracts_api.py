"""REB-392, spec § 4 over HTTP: `/me/contracts` answers the caller's own documents from
the session, and someone else's document is a 404, never a 403."""

import json
from collections.abc import Iterator
from contextlib import nullcontext
from datetime import UTC, datetime

import pytest
from contract_flow import ADMIN_EMAIL, FREELANCER_EMAIL, PDF, SIGNER, TABLES, draft_match, enter
from fakes_contracts import FakeRenderer
from fakes_documenso import SIGNING_HOST, FakeDocumenso
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from rebase_api.deps import get_documenso, get_renderer, get_session_opener
from rebase_api.ratelimit import reset_rate_limit
from rebase_core.config import Settings, get_settings
from rebase_core.mail import RecordingSender
from rebase_core.models import ContractDocument, User

SECRET = "segreto-del-webhook-di-prova"
SIGNED_AT = datetime(2026, 9, 30, 23, 30, tzinfo=UTC)


@pytest.fixture
def documenso(client: TestClient, api_session: Session) -> Iterator[FakeDocumenso]:
    fake = FakeDocumenso()
    renderer = FakeRenderer(draft=False)
    overrides = client.app.dependency_overrides  # type: ignore[attr-defined]
    overrides[get_documenso] = fake.client
    overrides[get_renderer] = lambda: renderer
    overrides[get_session_opener] = lambda: lambda: nullcontext(api_session)
    yield fake


@pytest.fixture
def admin(client: TestClient, api_session: Session) -> Iterator[None]:
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None, signer_json=json.dumps(SIGNER), documenso_webhook_secret=SECRET
    )
    client.app.dependency_overrides[get_settings] = lambda: settings  # type: ignore[attr-defined]
    api_session.add(User(email=ADMIN_EMAIL, nome="Ivan", cognome="", role="admin"))
    api_session.commit()
    yield
    api_session.rollback()
    for table in TABLES:
        api_session.execute(text(f"DELETE FROM {table}"))
    api_session.commit()


def _quadro(session: Session) -> ContractDocument:
    session.expire_all()
    return session.scalars(select(ContractDocument).where(ContractDocument.kind == "quadro")).one()


def test_without_the_cookie_the_contracts_are_a_401(client: TestClient, admin: None) -> None:
    assert client.get("/api/hub/me/contracts").status_code == 401
    missing = "00000000-0000-7000-8000-000000000000"
    assert client.get(f"/api/hub/me/contracts/{missing}/pdf").status_code == 401


def test_a_freelancer_reads_their_contracts_and_downloads_the_signed_copy(
    client: TestClient,
    admin: None,
    sender: RecordingSender,
    documenso: FakeDocumenso,
    api_session: Session,
) -> None:
    match = draft_match(client, sender)
    assert client.post(f"/api/hub/matches/{match['id']}/send").status_code == 200
    reset_rate_limit()
    enter(client, sender, FREELANCER_EMAIL)

    mine = client.get("/api/hub/me/contracts")

    assert mine.status_code == 200, mine.text
    body = mine.json()
    assert body["quadro"]["stato"] == "inviato"
    assert body["quadro"]["signing_url"].startswith(f"{SIGNING_HOST}/sign/")
    assert [lettera["stato"] for lettera in body["lettere"]] == ["in_attesa"]
    assert "777.77" not in mine.text

    envelope = _quadro(api_session).documenso_id
    assert envelope is not None
    documenso.sign(envelope, SIGNED_AT)
    delivered = client.post(
        "/api/hub/documenso/webhook",
        json=documenso.webhook(envelope, "DOCUMENT_COMPLETED"),
        headers={"X-Documenso-Secret": SECRET},
    )
    assert delivered.status_code == 200
    quadro_id = body["quadro"]["id"]
    copy = client.get(f"/api/hub/me/contracts/{quadro_id}/pdf")
    assert copy.status_code == 200
    assert copy.content == documenso.signed_pdf(envelope)
    assert copy.headers["content-type"].startswith("application/pdf")
    assert client.get("/api/hub/me/contracts").json()["quadro"]["signing_url"] is None


def test_someone_elses_document_is_a_404_never_a_403(
    client: TestClient,
    admin: None,
    sender: RecordingSender,
    documenso: FakeDocumenso,
    api_session: Session,
) -> None:
    match = draft_match(client, sender)
    assert client.post(f"/api/hub/matches/{match['id']}/send").status_code == 200
    ada_quadro = _quadro(api_session)
    reset_rate_limit()
    applied = client.post(
        "/api/hub/freelancers",
        data={
            "nome": "Grace",
            "cognome": "Hopper",
            "email": "grace@studio.it",
            "tariffa_giornaliera": "500",
            "posizione": "Backend developer",
            "remoto": "remoto",
        },
        files={"cv": ("Grace CV.pdf", PDF, "application/pdf")},
    )
    assert applied.status_code == 201, applied.text
    enter(client, sender, "grace@studio.it")

    assert client.get(f"/api/hub/me/contracts/{ada_quadro.id}/pdf").status_code == 404
    assert client.get("/api/hub/me/contracts").json() == {"quadro": None, "lettere": []}
