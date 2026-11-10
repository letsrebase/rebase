"""The API boots with no Documenso settings, and every signing route says so (REB-1019).

The hub keeps signing in production until a separate cutover, so the CRM's own path is off unless
`PIGROCRM_DOCUMENSO_URL` and `PIGROCRM_DOCUMENSO_API_TOKEN` are set: an installation that never
heard of Documenso must boot as before, with the webhook switched off (503) and the routes that
need a Documenso answering 503 `signing_unavailable` instead of failing deep inside a call.

The values are the ones `docker-compose.yml` gives the three variables, read out of its
`x-api-environment` block, so a default that stops being empty there fails here.
"""

import re
from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.auth.schemas import UserCreate
from pigrocrm.core.auth.service import UserService
from pigrocrm.core.config import Settings, get_settings
from pigrocrm.core.storage import LocalFileStorage
from pigrocrm_api.deps import get_session, get_storage
from pigrocrm_api.main import create_app
from pigrocrm_api.ratelimit import reset_rate_limit

COMPOSE = Path(__file__).resolve().parents[3] / "docker-compose.yml"
VARIABLES = (
    "PIGROCRM_DOCUMENSO_URL",
    "PIGROCRM_DOCUMENSO_API_TOKEN",
    "PIGROCRM_DOCUMENSO_WEBHOOK_SECRET",
)
JWT_SECRET = "a-real-signing-key-for-the-boot-test-0123456789"
ADMIN_EMAIL = "boot@pigro.it"
ADMIN_PASSWORD = "supersegreta1"
SECRET = "whsec_" + "0123456789abcdef" * 3
UNKNOWN_ENVELOPE = {
    "event": "DOCUMENT_COMPLETED",
    "payload": {"envelopeId": "envelope_0001", "externalId": None, "status": "COMPLETED"},
}


def compose_defaults() -> dict[str, str]:
    """What the compose file gives each variable when the `.env` leaves it out
    (`NAME: ${NAME:-default}`); a variable the file does not forward at all is left unset, which
    reads as empty."""
    text = COMPOSE.read_text(encoding="utf-8")
    defaults = {}
    for name in VARIABLES:
        found = re.search(rf"^\s+{name}:\s*\$\{{{name}:-(.*)\}}\s*$", text, re.MULTILINE)
        defaults[name] = found.group(1) if found else ""
    return defaults


def _booted(
    monkeypatch: pytest.MonkeyPatch, api_session: Session, tmp_path: Path, env: dict[str, str]
) -> Iterator[TestClient]:
    monkeypatch.setenv("PIGROCRM_JWT_SECRET", JWT_SECRET)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    get_settings.cache_clear()
    reset_rate_limit()
    try:
        app = create_app()
        settings = Settings(_env_file=None, module_collaborators=True)  # type: ignore[call-arg]
        app.dependency_overrides[get_settings] = lambda: settings
        app.dependency_overrides[get_session] = lambda: api_session
        app.dependency_overrides[get_storage] = lambda: LocalFileStorage(tmp_path)
        with TestClient(app, base_url="https://testserver") as client:
            yield client
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()


@pytest.fixture
def without_documenso(
    monkeypatch: pytest.MonkeyPatch, api_session: Session, tmp_path: Path
) -> Iterator[TestClient]:
    yield from _booted(monkeypatch, api_session, tmp_path, compose_defaults())


@pytest.fixture
def with_documenso(
    monkeypatch: pytest.MonkeyPatch, api_session: Session, tmp_path: Path
) -> Iterator[TestClient]:
    yield from _booted(
        monkeypatch,
        api_session,
        tmp_path,
        {
            "PIGROCRM_DOCUMENSO_URL": "https://documenso.example.test",
            "PIGROCRM_DOCUMENSO_API_TOKEN": "api_fake0123456789",
            "PIGROCRM_DOCUMENSO_WEBHOOK_SECRET": SECRET,
        },
    )


def _log_in(client: TestClient, api_session: Session) -> None:
    UserService(api_session).create(
        UserCreate(email=ADMIN_EMAIL, password=ADMIN_PASSWORD, name="Boot", role="admin"),
        Actor.system(),
    )
    response = client.post(
        "/api/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
    )
    assert response.status_code == 200, response.text


def test_the_compose_file_gives_all_three_settings_an_empty_default() -> None:
    assert compose_defaults() == dict.fromkeys(VARIABLES, "")


def test_with_no_documenso_settings_the_app_boots_and_the_webhook_is_off(
    without_documenso: TestClient,
) -> None:
    settings = get_settings()
    assert (settings.documenso_url, settings.documenso_api_token) == ("", "")
    assert settings.documenso_webhook_secret == ""

    assert without_documenso.get("/health").json() == {"status": "ok"}
    for headers in ({}, {"X-Documenso-Secret": SECRET}):
        refused = without_documenso.post(
            "/api/documenso/webhook", json=UNKNOWN_ENVELOPE, headers=headers
        )
        assert refused.status_code == 503, refused.text
        assert refused.json() == {"detail": "webhook_disabled"}


def test_with_no_documenso_settings_a_signing_route_says_signing_is_unavailable(
    without_documenso: TestClient, api_session: Session
) -> None:
    _log_in(without_documenso, api_session)

    unavailable = without_documenso.post(f"/api/engagements/{uuid4()}/send")
    assert unavailable.status_code == 503, unavailable.text
    assert unavailable.json()["code"] == "signing_unavailable"
    # The mail needs a provider as well: this one is refused before the document is even read.
    resend = without_documenso.post(f"/api/documents/{uuid4()}/signature/resend-mail")
    assert resend.status_code == 503, resend.text
    assert resend.json()["code"] == "signing_unavailable"

    # Every route that never needed a Documenso works as it did.
    listed = without_documenso.get("/api/engagements")
    assert listed.status_code == 200, listed.text
    assert listed.json()["items"] == []
    missing = without_documenso.get(f"/api/documents/{uuid4()}/signature")
    assert missing.status_code == 404, missing.text


def test_with_the_url_the_token_and_a_secret_the_webhook_works(
    with_documenso: TestClient,
) -> None:
    settings = get_settings()
    assert settings.documenso_url == "https://documenso.example.test"
    assert settings.documenso_webhook_secret == SECRET

    wrong = with_documenso.post(
        "/api/documenso/webhook", json=UNKNOWN_ENVELOPE, headers={"X-Documenso-Secret": "no"}
    )
    assert wrong.status_code == 401, wrong.text
    # An envelope that is not this installation's is acknowledged and left alone.
    accepted = with_documenso.post(
        "/api/documenso/webhook", json=UNKNOWN_ENVELOPE, headers={"X-Documenso-Secret": SECRET}
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json() == {"ok": True}
