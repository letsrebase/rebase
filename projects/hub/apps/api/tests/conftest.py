from collections.abc import Iterator
from contextlib import nullcontext
from typing import Any
from uuid import UUID

import pytest
from fakes_cards import card_response
from fakes_crm_door import FakeCrmDoor
from fastapi.testclient import TestClient
from hub_tables import wipe_hub
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from rebase_api.deps import (
    get_card_batches,
    get_card_llm,
    get_crm_filing,
    get_crm_llm,
    get_cv_llm,
    get_freelancer_door,
    get_sender,
    get_session,
    get_session_opener,
    get_team_llm,
)
from rebase_api.main import create_app
from rebase_api.ratelimit import _limiter as _public_limiter
from rebase_api.ratelimit import reset_rate_limit
from rebase_core.config import Settings, get_settings
from rebase_core.db import create_engine_from_settings, session_factory
from rebase_core.llm import RecordingCall
from rebase_core.mail import RecordingSender
from rebase_core.team_slots import reset_proposal_slots


@pytest.fixture(scope="session")
def api_engine(hub_postgres: Any) -> Iterator[Engine]:
    """A clone of the worker's template database (`projects/hub/conftest.py`, REB-579)."""
    url = hub_postgres.clone("api")
    engine = create_engine_from_settings(Settings(database_url=url, _env_file=None))  # type: ignore[call-arg]
    yield engine
    engine.dispose()


@pytest.fixture
def api_session(api_engine: Engine) -> Iterator[Session]:
    session = session_factory(api_engine)()
    try:
        yield session
    finally:
        session.rollback()
        wipe_hub(session)
        session.close()


# Far above any test's number of public requests: the limiter is the floor under nginx, and
# most tests are not about it.
ROOMY_LIMIT = 10_000


@pytest.fixture
def strict_rate_limit() -> None:
    """Asked for by name by the test of a 429: it runs against the real limit
    (`SIGNUPS_PER_MINUTE` a minute) instead of the roomy one every other test gets."""


@pytest.fixture(autouse=True)
def fresh_process_state(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """The limiters count requests per process, so one test's posts would otherwise be spent
    out of the next test's budget, and a test would pass or fail by its place in the run.
    Every test starts with the public limiter and the team builder's per-client share empty,
    and a test of a 429 asks for `strict_rate_limit`; the others get a roomy limit, so a
    test that signs in four times beside a wizard's request is not about the speed bump."""
    reset_rate_limit()
    reset_proposal_slots()
    if "strict_rate_limit" not in request.fixturenames:
        monkeypatch.setattr(_public_limiter, "limit", ROOMY_LIMIT)


@pytest.fixture
def client(api_session: Session) -> Iterator[TestClient]:
    app = create_app()
    app.dependency_overrides[get_session] = lambda: api_session
    # Declared, never inherited from a developer's `.env`: no pixel unless a test says so.
    app.dependency_overrides[get_settings] = lambda: Settings(_env_file=None)  # type: ignore[call-arg]
    # No model configured unless a test hands one (`llm` here, `_llm` in the team tests):
    # the real dependencies read «Modelli AI» through the default database URL.
    app.dependency_overrides[get_card_llm] = lambda: None
    app.dependency_overrides[get_card_batches] = lambda: None
    app.dependency_overrides[get_team_llm] = lambda: None
    app.dependency_overrides[get_cv_llm] = lambda: None
    # The first attempt at the CRM (REB-868) is a background job: nothing runs it unless a
    # test asks to see it (`crm_filings`).
    app.dependency_overrides[get_crm_filing] = lambda: lambda company_id: None
    app.dependency_overrides[get_crm_llm] = lambda: lambda: None
    # The CRM's reads of a freelancer (REB-1107): a CRM that holds nothing and finds no conflict,
    # unless a test hands its own fake (`get_freelancer_door`).
    app.dependency_overrides[get_freelancer_door] = FakeCrmDoor().door
    with TestClient(app, base_url="https://testserver") as test_client:
        yield test_client


@pytest.fixture
def crm_filings(client: TestClient) -> list[UUID]:
    """The ids of the requests the API asked the CRM filing for, in order: what
    `BackgroundTasks` was handed after each response (REB-868)."""
    seen: list[UUID] = []
    client.app.dependency_overrides[get_crm_filing] = lambda: seen.append  # type: ignore[attr-defined]
    return seen


@pytest.fixture
def sender(client: TestClient) -> Iterator[RecordingSender]:
    """The one mailbox every module in this directory overrides `get_sender` with
    (REB-406): one fixture, so a test file names it as a parameter without also
    importing it (which ruff flags as a redefinition, `F811`)."""
    recording = RecordingSender()
    client.app.dependency_overrides[get_sender] = lambda: recording  # type: ignore[attr-defined]
    yield recording


@pytest.fixture
def llm(client: TestClient, api_session: Session) -> Iterator[RecordingCall]:
    """Claude, for every module here whose route writes an anonymous card (REB-510),
    overridden the way `sender` overrides the mailbox: a canned card for each call, and
    the background write's own session the test's, the way the Documenso webhook's
    tests hand its follow-up one, so the card lands where the test reads it."""
    recording = RecordingCall([card_response() for _ in range(4)])
    overrides = client.app.dependency_overrides  # type: ignore[attr-defined]
    overrides[get_card_llm] = lambda: recording
    overrides[get_session_opener] = lambda: lambda: nullcontext(api_session)
    yield recording
