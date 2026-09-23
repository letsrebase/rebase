"""Cross-space identity: passwordless entry into the registry (design 2026-09-23,
REB-345/376). `registry_session` is a real database on the test container, the same
shape `test_tenants.py` uses for the sidecar registry -- `identities`,
`identity_link_tokens` and `identity_sessions` need no Alembic revision (§6), so
`ensure_tenants_database`'s own `create_all` is what this file actually exercises.
"""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from pigrocrm.core.auth.tokens import decode_token, issue_access_token
from pigrocrm.core.config import Settings
from pigrocrm.core.db import session_factory
from pigrocrm.core.errors import ValidationFailed
from pigrocrm.core.identity import Identity, IdentityLinkToken, IdentityService, IdentitySession
from pigrocrm.core.identity.service import _hash
from pigrocrm.core.tenants import ensure_tenants_database


def _settings_for(engine: Engine) -> Settings:
    return Settings(
        database_url=engine.url.render_as_string(hide_password=False),
        _env_file=None,  # type: ignore[call-arg]
    )


@pytest.fixture(scope="module")
def settings(db_engine: Engine) -> Settings:
    return _settings_for(db_engine)


@pytest.fixture(scope="module")
def registry(settings: Settings) -> Iterator[Engine]:
    engine = ensure_tenants_database(settings)
    yield engine
    engine.dispose()


@pytest.fixture
def registry_session(registry: Engine) -> Iterator[Session]:
    session = session_factory(registry)()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def _identity_of(session: Session, email: str) -> Identity:
    identity = session.scalar(select(Identity).where(Identity.email == email))
    assert identity is not None
    return identity


# --- upsert_and_issue: the side effect the three entry points ride on ---------------


def test_upsert_and_issue_creates_one_identity_row_with_a_live_unrevoked_session(
    settings: Settings, registry_session: Session
) -> None:
    email = f"ada-{uuid4()}@example.it"
    token = IdentityService(registry_session, settings).upsert_and_issue(email)
    assert token
    identity = _identity_of(registry_session, email)
    sessions = list(
        registry_session.scalars(
            select(IdentitySession).where(IdentitySession.identity_id == identity.id)
        )
    )
    assert len(sessions) == 1 and sessions[0].revoked_at is None
    payload = decode_token(token, settings, expected_type="identity")
    assert payload.sub == identity.id
    assert payload.jti == sessions[0].jti


def test_upsert_and_issue_normalises_case_and_whitespace_before_writing(
    settings: Settings, registry_session: Session
) -> None:
    unique = uuid4()
    token = IdentityService(registry_session, settings).upsert_and_issue(
        f"  ADA-{unique}@Example.IT  "
    )
    assert token
    identity = _identity_of(registry_session, f"ada-{unique}@example.it")
    assert identity.email == f"ada-{unique}@example.it"


def test_upsert_and_issue_is_idempotent_on_the_row_but_mints_a_fresh_session_each_time(
    settings: Settings, registry_session: Session
) -> None:
    email = f"twice-{uuid4()}@example.it"
    service = IdentityService(registry_session, settings)
    first = service.upsert_and_issue(email)
    second = service.upsert_and_issue(email)
    assert first and second and first != second
    identities = list(registry_session.scalars(select(Identity).where(Identity.email == email)))
    assert len(identities) == 1
    sessions = list(
        registry_session.scalars(
            select(IdentitySession).where(IdentitySession.identity_id == identities[0].id)
        )
    )
    assert len(sessions) == 2 and all(row.revoked_at is None for row in sessions)


def test_upsert_and_issue_recovers_from_a_racing_insert_on_the_same_address(
    settings: Settings, registry: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two first logins for the same brand-new address, racing: the get-or-create's
    `SELECT` then `INSERT` is not atomic on its own. Simulated here by forcing the
    first `_get_by_email` call to answer `None` even though a second session has
    already committed the winning row -- the exact shape a real race produces --
    and pinning the recovery this method's `IntegrityError` branch performs, rather
    than losing the request's own identity cookie over a collision it caused
    itself."""
    email = f"racing-{uuid4()}@example.it"
    winner = session_factory(registry)()
    try:
        winner.add(Identity(email=email))
        winner.commit()
        identity_id = winner.scalar(select(Identity.id).where(Identity.email == email))
    finally:
        winner.close()

    loser = session_factory(registry)()
    try:
        service = IdentityService(loser, settings)
        real_get_by_email = IdentityService._get_by_email
        calls = {"n": 0}

        def _racy_get_by_email(self: IdentityService, normalized: str) -> Identity | None:
            calls["n"] += 1
            if calls["n"] == 1:
                return None  # the moment the real race was already lost against
            return real_get_by_email(self, normalized)

        monkeypatch.setattr(IdentityService, "_get_by_email", _racy_get_by_email)
        token = service.upsert_and_issue(email)
        assert token
        payload = decode_token(token, settings, expected_type="identity")
        assert payload.sub == identity_id
        rows = list(loser.scalars(select(Identity).where(Identity.email == email)))
        assert len(rows) == 1
    finally:
        loser.close()


def test_upsert_and_issue_refuses_an_address_that_normalises_to_nothing(
    settings: Settings, registry_session: Session
) -> None:
    assert IdentityService(registry_session, settings).upsert_and_issue("   ") is None


def test_an_identity_token_is_refused_as_an_access_token_and_vice_versa(
    settings: Settings, registry_session: Session
) -> None:
    """The whole point of widening `TokenType` rather than reusing "access": a token
    minted for one purpose must never be replayed for another (design §2)."""
    identity_token = IdentityService(registry_session, settings).upsert_and_issue(
        f"cross-{uuid4()}@example.it"
    )
    assert identity_token
    with pytest.raises(ValidationFailed):
        decode_token(identity_token, settings, expected_type="access")
    access_token = issue_access_token(uuid4(), "admin", settings)
    with pytest.raises(ValidationFailed):
        decode_token(access_token, settings, expected_type="identity")


def test_the_functional_index_refuses_two_rows_for_the_same_address_in_different_case(
    registry_session: Session,
) -> None:
    email = f"case-{uuid4()}@example.it"
    registry_session.add(Identity(email=email))
    registry_session.commit()
    registry_session.add(Identity(email=email.upper()))
    with pytest.raises(IntegrityError):
        registry_session.commit()
    registry_session.rollback()


# --- revoke_all: what POST /api/identity/logout rides on ---------------------------


def test_revoke_all_kills_every_live_session_not_only_the_one_presented(
    settings: Settings, registry_session: Session
) -> None:
    email = f"revoked-{uuid4()}@example.it"
    service = IdentityService(registry_session, settings)
    service.upsert_and_issue(email)
    service.upsert_and_issue(email)
    identity = _identity_of(registry_session, email)
    service.revoke_all(identity.id)
    sessions = list(
        registry_session.scalars(
            select(IdentitySession).where(IdentitySession.identity_id == identity.id)
        )
    )
    assert len(sessions) == 2 and all(row.revoked_at is not None for row in sessions)


def test_revoke_all_is_idempotent_on_an_identity_with_nothing_live_left(
    settings: Settings, registry_session: Session
) -> None:
    email = f"idempotent-{uuid4()}@example.it"
    service = IdentityService(registry_session, settings)
    service.upsert_and_issue(email)
    identity = _identity_of(registry_session, email)
    service.revoke_all(identity.id)
    service.revoke_all(identity.id)  # nothing left to revoke: must not raise
    sessions = list(
        registry_session.scalars(
            select(IdentitySession).where(IdentitySession.identity_id == identity.id)
        )
    )
    assert all(row.revoked_at is not None for row in sessions)


def test_revoke_all_on_an_unknown_identity_touches_nothing_and_raises_nothing(
    settings: Settings, registry_session: Session
) -> None:
    IdentityService(registry_session, settings).revoke_all(uuid4())


# --- request/enter: proving an address nobody has vouched for yet ------------------


def test_request_answers_none_for_an_unknown_address_and_a_token_for_an_existing_identity(
    settings: Settings, registry_session: Session
) -> None:
    service = IdentityService(registry_session, settings)
    email = f"linked-{uuid4()}@example.it"
    assert service.request(email) is None  # no identity yet: nothing to send
    service.upsert_and_issue(email)
    raw = service.request(email)
    assert raw
    identity = _identity_of(registry_session, email)
    row = registry_session.scalar(
        select(IdentityLinkToken).where(IdentityLinkToken.token_hash == _hash(raw))
    )
    assert row is not None and row.used_at is None and row.identity_id == identity.id
    assert row.expires_at > datetime.now(UTC) + timedelta(minutes=settings.magic_link_minutes - 1)


def test_enter_spends_the_token_once_and_answers_the_identity(
    settings: Settings, registry_session: Session
) -> None:
    service = IdentityService(registry_session, settings)
    email = f"entering-{uuid4()}@example.it"
    service.upsert_and_issue(email)
    raw = service.request(email)
    assert raw
    identity = service.enter(raw)
    assert identity is not None and identity.email == email
    assert service.enter(raw) is None  # spent


def test_an_empty_unknown_or_expired_link_answers_none(
    settings: Settings, registry_session: Session
) -> None:
    service = IdentityService(registry_session, settings)
    assert service.enter("") is None
    assert service.enter("this-token-was-never-minted") is None

    email = f"expiring-{uuid4()}@example.it"
    service.upsert_and_issue(email)
    raw = service.request(email)
    assert raw
    identity = _identity_of(registry_session, email)
    row = registry_session.scalar(
        select(IdentityLinkToken).where(IdentityLinkToken.identity_id == identity.id)
    )
    assert row is not None
    row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    registry_session.commit()
    assert service.enter(raw) is None


def test_request_sweeps_the_spent_and_expired_rows_of_that_identity(
    settings: Settings, registry_session: Session
) -> None:
    service = IdentityService(registry_session, settings)
    email = f"sweeping-{uuid4()}@example.it"
    service.upsert_and_issue(email)
    raw1 = service.request(email)
    assert raw1 and service.enter(raw1) is not None
    service.request(email)  # a second request sweeps the now-spent first row
    identity = _identity_of(registry_session, email)
    rows = list(
        registry_session.scalars(
            select(IdentityLinkToken).where(IdentityLinkToken.identity_id == identity.id)
        )
    )
    assert len(rows) == 1 and rows[0].used_at is None


# --- ensure_tenants_database's create_all must see every one of these tables -------


def test_the_cli_boot_import_path_alone_registers_every_identity_table() -> None:
    """The exact bug a reviewer flagged on the first pass of this PR: `Tenant` lives
    in the same module as `TenantsBase`, so importing one always defines the other,
    but `Identity`/`IdentityLinkToken`/`IdentitySession` are a sibling package with
    no reason to be on any particular caller's own import path. The production boot
    step that actually calls `ensure_tenants_database` in a fresh process
    (`pigrocrm ensure-space-defaults`) imports only `pigrocrm.core.tenants` -- so a
    subprocess mirroring exactly that import, and nothing more, is what proves
    `TenantsBase.metadata` is complete without relying on whatever this test file
    happened to import first."""
    import ast
    import subprocess
    import sys

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from pigrocrm.core.tenants import Tenant, ensure_defaults, ensure_tenants_database\n"
            "from pigrocrm.core.tenants.models import TenantsBase\n"
            "print(sorted(TenantsBase.metadata.tables))",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    tables = ast.literal_eval(result.stdout.strip())
    assert tables == ["identities", "identity_link_tokens", "identity_sessions", "tenants"]
