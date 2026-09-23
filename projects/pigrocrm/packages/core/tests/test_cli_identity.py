"""`pigrocrm rebuild-identity-index`: the day-one backfill for an installation that
wants "my spaces" complete immediately (design 2026-09-23 §6, §8; REB-379). Not a
correctness requirement -- `identities` already grows to completeness on its own as
people log back in (`test_identity.py`'s own `upsert_and_issue` coverage) -- so what
this file actually exercises is the shape `ensure_space_defaults` already set for a
CLI that visits every space in the registry: real provisioning against the test
container, one unreachable space costing a line on stderr and never the whole
command, and idempotence on a second run.
"""

from collections.abc import Iterator
from uuid import uuid4

import pytest
from sqlalchemy import Engine, create_engine, select, text
from sqlalchemy.orm import Session

from pigrocrm.core import cli
from pigrocrm.core.actor import Actor
from pigrocrm.core.auth.schemas import UserCreate
from pigrocrm.core.auth.service import UserService
from pigrocrm.core.config import Settings
from pigrocrm.core.db import session_factory
from pigrocrm.core.identity.models import Identity
from pigrocrm.core.tenants import Tenant, TenantService, TenantSignup, ensure_tenants_database
from pigrocrm.core.tenants.database import tenant_database_name, tenant_database_url


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


def _drop(settings: Settings, slug: str) -> None:
    from pigrocrm.core.db.sidecar import drop_database

    drop_database(settings, tenant_database_url(settings, tenant_database_name(slug)))


def _identities(registry_session: Session, emails: set[str]) -> set[str]:
    return set(
        registry_session.scalars(select(Identity.email).where(Identity.email.in_(emails))).all()
    )


def test_the_cli_indexes_every_users_row_not_only_the_owner_and_is_idempotent(
    settings: Settings,
    registry_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The whole point of REB-379 over what a plain login already does: a space
    invited someone after it was created, and that person's address must be indexed
    from `users`, never only `Tenant.owner_email`. A second run finds nothing new."""
    service = TenantService(registry_session, settings)
    slug = f"prova-index-{uuid4().hex[:8]}"
    owner = f"owner-{uuid4()}@studio.it"
    invited = f"invited-{uuid4()}@studio.it"
    try:
        service.provision(TenantSignup(slug=slug, nome="Ada", email=owner))
        space = create_engine(
            tenant_database_url(settings, tenant_database_name(slug)), future=True
        )
        try:
            with session_factory(space)() as space_session:
                UserService(space_session).create(
                    UserCreate(email=invited, password=None, nome="Bea", ruolo="collaboratore"),
                    Actor.system(),
                )
        finally:
            space.dispose()

        monkeypatch.setattr(cli, "get_settings", lambda: settings)
        assert cli.main(["rebuild-identity-index"]) == 0
        out = capsys.readouterr().out
        assert f"{slug}: 2 indirizzi, 2 nuove identità" in out
        assert _identities(registry_session, {owner, invited}) == {owner, invited}

        # A second run has nothing new to add and says so.
        assert cli.main(["rebuild-identity-index"]) == 0
        assert f"{slug}: 2 indirizzi, già indicizzati" in capsys.readouterr().out
        assert _identities(registry_session, {owner, invited}) == {owner, invited}
        assert (
            registry_session.scalar(select(Identity.id).where(Identity.email.in_({owner, invited})))
            is not None
        )
    finally:
        _drop(settings, slug)
        registry_session.execute(
            text("delete from identities where email = any(:e)"), {"e": [owner, invited]}
        )
        registry_session.execute(text("delete from tenants where slug = :s"), {"s": slug})
        registry_session.commit()


def test_overlapping_owner_emails_across_spaces_produce_one_identity_row_per_address(
    settings: Settings,
    registry_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Two spaces opened by the same address (the exact scenario the issue names)
    must not produce two `identities` rows -- the functional unique index on
    `lower(email)` and the get-or-create it backs must fold them into one."""
    service = TenantService(registry_session, settings)
    shared = f"shared-{uuid4()}@studio.it"
    slug_a = f"prova-overlap-a-{uuid4().hex[:8]}"
    slug_b = f"prova-overlap-b-{uuid4().hex[:8]}"
    try:
        service.provision(TenantSignup(slug=slug_a, nome="Ada", email=shared))
        service.provision(TenantSignup(slug=slug_b, nome="Ada", email=shared))

        monkeypatch.setattr(cli, "get_settings", lambda: settings)
        assert cli.main(["rebuild-identity-index"]) == 0

        rows = registry_session.scalars(select(Identity).where(Identity.email == shared)).all()
        assert len(rows) == 1
    finally:
        _drop(settings, slug_a)
        _drop(settings, slug_b)
        registry_session.execute(text("delete from identities where email = :e"), {"e": shared})
        registry_session.execute(
            text("delete from tenants where slug in (:a, :b)"), {"a": slug_a, "b": slug_b}
        )
        registry_session.commit()


def test_the_cli_skips_a_space_it_cannot_reach_and_still_indexes_the_others(
    settings: Settings,
    registry_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A registry row whose database does not exist (the eight-spaces-in-production
    shape `ensure_space_defaults`'s own equivalent test guards) must not stop the
    command -- it costs one line on stderr and the reachable space is still indexed."""
    service = TenantService(registry_session, settings)
    slug = f"prova-vicino-{uuid4().hex[:8]}"
    ghost = f"prova-fantasma-{uuid4().hex[:8]}"
    owner = f"reachable-{uuid4()}@studio.it"
    try:
        service.provision(TenantSignup(slug=slug, nome="Ada", email=owner))
        registry_session.add(
            Tenant(slug=ghost, db_name=tenant_database_name(ghost), owner_email="x@studio.it")
        )
        registry_session.commit()

        monkeypatch.setattr(cli, "get_settings", lambda: settings)
        assert cli.main(["rebuild-identity-index"]) == 0
        captured = capsys.readouterr()
        ghost_lines = [line for line in captured.err.splitlines() if line.startswith(ghost)]
        assert len(ghost_lines) == 1
        assert ghost_lines[0].startswith(f"{ghost}: non raggiungibile (")
        assert "x:y" not in captured.err and settings.database_url not in captured.err
        assert f"{slug}: 1 indirizzi, 1 nuove identità" in captured.out
        assert _identities(registry_session, {owner}) == {owner}
    finally:
        _drop(settings, slug)
        registry_session.execute(text("delete from identities where email = :e"), {"e": owner})
        registry_session.execute(
            text("delete from tenants where slug in (:a, :b)"), {"a": slug, "b": ghost}
        )
        registry_session.commit()


def test_the_cli_never_fails_when_the_registry_is_unreachable(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    unreachable = Settings(
        database_url="postgresql+psycopg://x:y@127.0.0.1:9/nulla",
        _env_file=None,  # type: ignore[call-arg]
    )
    monkeypatch.setattr(cli, "get_settings", lambda: unreachable)
    assert cli.main(["rebuild-identity-index"]) == 0
    err = capsys.readouterr().err
    assert "registro degli spazi non raggiungibile (" in err and "x:y" not in err


def test_a_space_with_no_users_is_reported_and_touches_no_identity(
    settings: Settings,
    registry_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`provision` always seeds a first admin, so a truly empty `users` table only
    happens for a space whose admin was later deactivated and deleted by hand -- the
    CLI must report it plainly rather than crash on an empty distinct-email set."""
    service = TenantService(registry_session, settings)
    slug = f"prova-vuoto-{uuid4().hex[:8]}"
    try:
        service.provision(TenantSignup(slug=slug, nome="Ada", email=f"solo-{uuid4()}@studio.it"))
        space = create_engine(
            tenant_database_url(settings, tenant_database_name(slug)), future=True
        )
        try:
            with space.begin() as connection:
                connection.execute(text("delete from users"))
        finally:
            space.dispose()

        monkeypatch.setattr(cli, "get_settings", lambda: settings)
        assert cli.main(["rebuild-identity-index"]) == 0
        assert f"{slug}: nessun utente" in capsys.readouterr().out
    finally:
        _drop(settings, slug)
        registry_session.execute(text("delete from tenants where slug = :s"), {"s": slug})
        registry_session.commit()
