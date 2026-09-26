"""Spaces: the name rules, and a real provisioning against the test container.

`provision` is exercised for what it is -- CREATE DATABASE, the repository's own Alembic
migrations to head, the first admin -- not against a database somebody pre-created. The
container `db_engine` starts is the server; the registry and the space it creates are
two more databases on it.
"""

from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session

from pigrocrm.core.config import Settings
from pigrocrm.core.db import session_factory
from pigrocrm.core.errors import Conflict, ValidationFailed
from pigrocrm.core.tenants import (
    RESERVED_SLUGS,
    Tenant,
    TenantService,
    TenantSignup,
    ensure_tenants_database,
    slugify,
    validate_slug,
)
from pigrocrm.core.tenants.database import tenant_database_name, tenant_database_url
from pigrocrm.core.tenants.prefix import API_SEGMENTS, MCP_SEGMENTS, split_tenant_prefix


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


# --- the name --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("nome", "slug"),
    [
        ("Studio Rossi", "studio-rossi"),
        ("  Caffè & Co.  ", "caffe-co"),
        ("ÀÉÎÕÜ", "aeiou"),
        ("già-ok", "gia-ok"),
        ("x" * 40, "x" * 32),
    ],
)
def test_slugify_makes_a_url_segment_out_of_a_name(nome: str, slug: str) -> None:
    assert slugify(nome) == slug


@pytest.mark.parametrize("slug", ["studio-rossi", "abc", "a1-b2", "x" * 32])
def test_well_formed_slugs_pass(slug: str) -> None:
    assert validate_slug(slug) is None


@pytest.mark.parametrize(
    "slug",
    ["ab", "-abc", "abc-", "Studio", "studio rossi", "a_b_c", "x" * 33, *sorted(RESERVED_SLUGS)],
)
def test_malformed_and_reserved_slugs_are_refused_with_a_reason(slug: str) -> None:
    reason = validate_slug(slug)
    assert reason is not None and reason


def test_the_signup_schema_refuses_a_reserved_name_before_anything_else() -> None:
    with pytest.raises(ValueError, match="riservato"):
        TenantSignup(slug="app", nome="Ada", email="ada@studio.it")


def test_the_database_name_is_an_unquoted_identifier() -> None:
    assert tenant_database_name("studio-rossi") == "pigro_t_studio_rossi"


def test_the_prefix_splitter_recognises_the_api_segments_by_default() -> None:
    assert split_tenant_prefix("/studio/api/customers") == ("studio", "/api/customers")
    assert split_tenant_prefix("/studio/health") == ("studio", "/health")
    assert split_tenant_prefix("/api/customers") == (None, "/api/customers")
    assert split_tenant_prefix("/app/api/x") == (None, "/app/api/x")  # reserved word
    assert split_tenant_prefix("/studio/app/login") == (None, "/studio/app/login")
    assert split_tenant_prefix("/Studio/api/x") == (None, "/Studio/api/x")  # not a slug
    assert API_SEGMENTS == ("api", "health")


def test_the_prefix_splitter_serves_the_mcp_segment_when_asked() -> None:
    assert split_tenant_prefix("/studio/mcp", segments=MCP_SEGMENTS) == ("studio", "/mcp")
    assert split_tenant_prefix("/studio/mcp/", segments=MCP_SEGMENTS) == ("studio", "/mcp/")
    assert split_tenant_prefix("/mcp", segments=MCP_SEGMENTS) == (None, "/mcp")
    # The MCP splitter does not know the API's segments, and vice versa.
    assert split_tenant_prefix("/studio/api/x", segments=MCP_SEGMENTS) == (None, "/studio/api/x")
    assert split_tenant_prefix("/studio/mcp") == (None, "/studio/mcp")


def test_the_root_slug_is_stripped_but_stays_the_root_for_every_segment_set() -> None:
    assert split_tenant_prefix("/studiorossi/api/auth/me", "studiorossi") == (None, "/api/auth/me")
    assert split_tenant_prefix("/studiorossi/mcp", "studiorossi", MCP_SEGMENTS) == (None, "/mcp")
    assert split_tenant_prefix("/altro/mcp", "studiorossi", MCP_SEGMENTS) == ("altro", "/mcp")


def test_mcp_is_a_reserved_name() -> None:
    assert "mcp" in RESERVED_SLUGS
    assert validate_slug("mcp") == "questo nome è riservato"


# --- the provisioning --------------------------------------------------------------


def _drop(settings: Settings, slug: str) -> None:
    from pigrocrm.core.db.sidecar import drop_database

    drop_database(settings, tenant_database_url(settings, tenant_database_name(slug)))


def test_provisioning_creates_a_migrated_database_with_one_admin(
    settings: Settings, registry_session: Session
) -> None:
    service = TenantService(registry_session, settings)
    slug = "prova-spazio"
    try:
        created = service.provision(
            TenantSignup(slug=slug, nome="Ada Lovelace", email="Ada@Studio.it")
        )
        assert created.slug == slug
        assert created.owner_email == "ada@studio.it"
        assert service.availability(slug).disponibile is False

        space = create_engine(
            tenant_database_url(settings, tenant_database_name(slug)), future=True
        )
        try:
            with space.connect() as connection:
                # The same head the CRM itself is at: `env.py` ran, not `create_all`.
                head = connection.execute(text("select version_num from alembic_version")).scalar()
                assert head is not None and head >= "0027"
                users = connection.execute(text("select email, ruolo, attivo from users")).all()
                assert users == [("ada@studio.it", "admin", True)]
                assert connection.execute(text("select count(*) from customers")).scalar() == 0
                # Born ready (spec 2026-09-12 §6.5): the first deal, the first offer and
                # the first cost need nothing from Impostazioni.
                assert (
                    connection.execute(text("select count(*) from pipeline_stages")).scalar() == 6
                )
                names = (
                    connection.execute(text("select nome from templates order by nome"))
                    .scalars()
                    .all()
                )
                assert "Offerta" in names and len(names) == 3
                assert (
                    connection.execute(text("select count(*) from cost_categories")).scalar() == 5
                )
                # The emitter carries the name and nothing fiscal: that is the person's.
                emitter = connection.execute(
                    text("select ragione_sociale, partita_iva, codice_fiscale from emitter_profile")
                ).all()
                assert emitter == [("Ada Lovelace", None, None)]
        finally:
            space.dispose()
    finally:
        _drop(settings, slug)
        registry_session.execute(text("delete from tenants where slug = :s"), {"s": slug})
        registry_session.commit()


def test_the_same_name_twice_is_a_conflict_and_leaves_the_first_space_alone(
    settings: Settings, registry_session: Session
) -> None:
    service = TenantService(registry_session, settings)
    slug = "prova-doppio"
    try:
        service.provision(TenantSignup(slug=slug, nome="Ada", email="ada@studio.it"))
        with pytest.raises(Conflict):
            service.provision(TenantSignup(slug=slug, nome="Bob", email="bob@studio.it"))
        space = create_engine(
            tenant_database_url(settings, tenant_database_name(slug)), future=True
        )
        try:
            with space.connect() as connection:
                assert connection.execute(text("select email from users")).scalars().all() == [
                    "ada@studio.it"
                ]
        finally:
            space.dispose()
    finally:
        _drop(settings, slug)
        registry_session.execute(text("delete from tenants where slug = :s"), {"s": slug})
        registry_session.commit()


def test_a_refused_admin_provisions_nothing_and_frees_the_name(
    settings: Settings, registry_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A domain rule that refuses the first admin (the wizard sends no password, so today
    that is a hypothetical; the guarantee is not) leaves no database and no row."""
    import pigrocrm.core.tenants.service as tenants_service

    def refuse(self: object, data: object, actor: object) -> None:
        raise ValidationFailed("user", "email", "rifiutato")

    monkeypatch.setattr(tenants_service.UserService, "create", refuse)
    service = TenantService(registry_session, settings)
    slug = "prova-rifiuto"
    with pytest.raises(ValidationFailed):
        service.provision(TenantSignup(slug=slug, nome="Ada", email="ada@studio.it"))
    assert service.availability(slug).disponibile is True
    assert (
        registry_session.execute(
            text("select count(*) from tenants where slug = :s"), {"s": slug}
        ).scalar()
        == 0
    )
    with create_engine(settings.database_url, future=True).connect() as connection:
        exists = connection.execute(
            text("select 1 from pg_database where datname = :n"), {"n": tenant_database_name(slug)}
        ).scalar()
    assert exists is None


def test_the_signup_refuses_a_password_it_no_longer_asks_for() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        TenantSignup(slug="prova", nome="Ada", email="ada@studio.it", password="lunghissima1")  # type: ignore[call-arg]


def test_the_cli_furnishes_an_existing_space_that_has_nothing(
    settings: Settings,
    registry_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from pigrocrm.core import cli

    service = TenantService(registry_session, settings)
    slug = "prova-arredo"
    try:
        service.provision(TenantSignup(slug=slug, nome="Ada", email="ada@studio.it"))
        space = create_engine(
            tenant_database_url(settings, tenant_database_name(slug)), future=True
        )
        try:
            # The state the eight production spaces are in: born before the seeds.
            with space.begin() as connection:
                connection.execute(text("delete from pipeline_stages"))
                connection.execute(text("delete from cost_categories"))
            monkeypatch.setattr(cli, "get_settings", lambda: settings)
            assert cli.main(["ensure-space-defaults"]) == 0
            out = capsys.readouterr().out
            assert slug in out and "stati 6" in out and "categorie 5" in out
            with space.connect() as connection:
                assert (
                    connection.execute(text("select count(*) from pipeline_stages")).scalar() == 6
                )
                assert (
                    connection.execute(text("select count(*) from cost_categories")).scalar() == 5
                )
                # Templates were not empty and are untouched: still the three seeds.
                assert connection.execute(text("select count(*) from templates")).scalar() == 3
            # A second run has nothing to do and says so.
            assert cli.main(["ensure-space-defaults"]) == 0
            assert "già a posto" in capsys.readouterr().out
        finally:
            space.dispose()
    finally:
        _drop(settings, slug)
        registry_session.execute(text("delete from tenants where slug = :s"), {"s": slug})
        registry_session.commit()


def test_the_cli_skips_a_space_it_cannot_reach_and_still_furnishes_the_others(
    settings: Settings,
    registry_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from pigrocrm.core import cli
    from pigrocrm.core.tenants import Tenant

    service = TenantService(registry_session, settings)
    slug = "prova-vicino"
    ghost = "prova-fantasma"
    try:
        service.provision(TenantSignup(slug=slug, nome="Ada", email="ada@studio.it"))
        # A registry row whose database does not exist: the eight-spaces boot must not
        # stop here, and the failure must not name the URL.
        registry_session.add(
            Tenant(slug=ghost, db_name=tenant_database_name(ghost), owner_email="x@studio.it")
        )
        registry_session.commit()
        space = create_engine(
            tenant_database_url(settings, tenant_database_name(slug)), future=True
        )
        try:
            with space.begin() as connection:
                connection.execute(text("delete from pipeline_stages"))
            monkeypatch.setattr(cli, "get_settings", lambda: settings)
            assert cli.main(["ensure-space-defaults"]) == 0
            captured = capsys.readouterr()
            # Alembic logs the migrations it ran on stderr too; the line under test is the
            # CLI's own, and it must carry the exception type and nothing of the URL.
            ghost_lines = [line for line in captured.err.splitlines() if line.startswith(ghost)]
            # The migration comes first (ORB-189), so a database that does not exist
            # fails there, once, and is not furnished either.
            assert ghost_lines == [f"{ghost}: non migrato (OperationalError)"]
            assert f"{slug}: stati 6" in captured.out
            assert f"{slug}: schema già a" in captured.out
        finally:
            space.dispose()
    finally:
        _drop(settings, slug)
        registry_session.execute(
            text("delete from tenants where slug in (:a, :b)"), {"a": slug, "b": ghost}
        )
        registry_session.commit()


def test_the_cli_migrates_a_space_left_behind_before_furnishing_it(
    settings: Settings,
    registry_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The production state of 2026-09-12 (ORB-189): eight spaces provisioned at an
    older head, the image now carrying a migration every read of `users` depends on.
    The boot command must bring the space to head before it touches anything else."""
    from alembic import command
    from alembic.config import Config

    from pigrocrm.core import cli
    from pigrocrm.core.tenants.service import default_alembic_ini

    service = TenantService(registry_session, settings)
    slug = "prova-indietro"
    try:
        service.provision(TenantSignup(slug=slug, nome="Ada", email="ada@studio.it"))
        url = tenant_database_url(settings, tenant_database_name(slug))
        space = create_engine(url, future=True)
        try:
            with space.begin() as connection:
                head = connection.execute(text("select version_num from alembic_version")).scalar()
                # 0034's downgrade makes the password NOT NULL again, and the first admin
                # has none (ORB-176): the row goes first, the schema is what is under test.
                connection.execute(text("delete from users"))
                connection.execute(text("delete from pipeline_stages"))
            config = Config(str(default_alembic_ini()))
            config.set_main_option("sqlalchemy.url", url.render_as_string(hide_password=False))
            # To 0033 by name, not `-1`: the incident's revision, and the one where
            # `email_verificata_il` is absent whatever the head becomes later.
            command.downgrade(config, "0033")
            with space.connect() as connection:
                behind = connection.execute(
                    text("select version_num from alembic_version")
                ).scalar()
                assert behind != head
                columns = (
                    connection.execute(
                        text(
                            "select column_name from information_schema.columns "
                            "where table_name = 'users'"
                        )
                    )
                    .scalars()
                    .all()
                )
                assert "email_verificata_il" not in columns

            monkeypatch.setattr(cli, "get_settings", lambda: settings)
            assert cli.main(["ensure-space-defaults"]) == 0
            out = capsys.readouterr().out
            assert f"{slug}: schema migrato da {behind} a {head}" in out
            assert f"{slug}: stati 6" in out
            with space.connect() as connection:
                assert (
                    connection.execute(text("select version_num from alembic_version")).scalar()
                    == head
                )
                columns = (
                    connection.execute(
                        text(
                            "select column_name from information_schema.columns "
                            "where table_name = 'users'"
                        )
                    )
                    .scalars()
                    .all()
                )
                assert "email_verificata_il" in columns
                assert (
                    connection.execute(text("select count(*) from pipeline_stages")).scalar() == 6
                )
            # The next boot has nothing to migrate and says so.
            assert cli.main(["ensure-space-defaults"]) == 0
            assert f"{slug}: schema già a {head}" in capsys.readouterr().out
        finally:
            space.dispose()
    finally:
        _drop(settings, slug)
        registry_session.execute(text("delete from tenants where slug = :s"), {"s": slug})
        registry_session.commit()


def test_the_cli_never_fails_the_boot_when_the_registry_is_unreachable(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from pigrocrm.core import cli

    unreachable = Settings(
        database_url="postgresql+psycopg://x:y@127.0.0.1:9/nulla",
        _env_file=None,  # type: ignore[call-arg]
    )
    monkeypatch.setattr(cli, "get_settings", lambda: unreachable)
    assert cli.main(["ensure-space-defaults"]) == 0
    err = capsys.readouterr().err
    assert "registro degli spazi non raggiungibile (" in err and "x:y" not in err


def test_a_failing_seed_undoes_the_space_and_frees_the_name(
    settings: Settings, registry_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    import pigrocrm.core.tenants.service as tenants_service

    def boom(session: Session) -> None:
        raise RuntimeError("seed rotto")

    monkeypatch.setattr(tenants_service, "ensure_defaults", boom)
    service = TenantService(registry_session, settings)
    slug = "prova-seme"
    with pytest.raises(RuntimeError):
        service.provision(TenantSignup(slug=slug, nome="Ada", email="ada@studio.it"))
    assert service.availability(slug).disponibile is True
    with create_engine(settings.database_url, future=True).connect() as connection:
        exists = connection.execute(
            text("select 1 from pg_database where datname = :n"), {"n": tenant_database_name(slug)}
        ).scalar()
    assert exists is None


# --- REB-230: a failed drop must not free the slug -----------------------------------


def test_a_failed_drop_keeps_the_registry_row_and_the_exception_propagates(
    settings: Settings, registry_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The old `_undo` deleted the registry row in a `finally`, whether or not
    `drop_database` succeeded. `DROP DATABASE` raises `ObjectInUse` when a connection
    appears between its own `pg_terminate_backend` and the drop, which a concurrent
    request for the same slug can cause, and the old code freed the name onto a
    database that survived that failure, already migrated, already holding whatever
    the failed attempt wrote. The row must stay, and the failure must not be
    swallowed."""
    from psycopg.errors import ObjectInUse

    import pigrocrm.core.tenants.service as tenants_service

    def fake_drop(settings: Settings, url: object) -> None:
        raise ObjectInUse('database "pigro_t_prova_undo_fail" is being accessed by other users')

    monkeypatch.setattr(tenants_service, "drop_database", fake_drop)
    slug = "prova-undo-fail"
    tenant = Tenant(slug=slug, db_name=tenant_database_name(slug), owner_email="ada@studio.it")
    registry_session.add(tenant)
    registry_session.commit()

    service = TenantService(registry_session, settings)
    with pytest.raises(ObjectInUse):
        service._undo(tenant, tenant_database_url(settings, tenant.db_name))
    assert (
        registry_session.execute(
            text("select count(*) from tenants where slug = :s"), {"s": slug}
        ).scalar()
        == 1
    )
    registry_session.execute(text("delete from tenants where slug = :s"), {"s": slug})
    registry_session.commit()


# --- A3: the welcome step, shared with the engagements door (REB-492) ---------------


def test_welcome_answers_the_entering_link_and_none_without_a_public_origin(
    settings: Settings, registry_session: Session
) -> None:
    from pigrocrm.core.mail import RecordingSender
    from pigrocrm.core.tenants.welcome import welcome

    service = TenantService(registry_session, settings)
    slug = "prova-welcome"
    try:
        tenant = service.provision(TenantSignup(slug=slug, nome="Ada", email="ada@studio.it"))
        space_engine = create_engine(
            tenant_database_url(settings, tenant_database_name(slug)), future=True
        )
        try:
            with session_factory(space_engine)() as space:
                with_origin = settings.model_copy(update={"public_url": "https://pigro.test"})
                mail = welcome(
                    space, with_origin, RecordingSender(), tenant.owner_email, slug, membro=False
                )
                assert mail is not None
                assert mail.html is not None
                assert f"/{slug}/app/verify?t=" in mail.html

                without_origin = settings.model_copy(update={"public_url": ""})
                assert (
                    welcome(
                        space,
                        without_origin,
                        RecordingSender(),
                        tenant.owner_email,
                        slug,
                        membro=False,
                    )
                    is None
                )
        finally:
            space_engine.dispose()
    finally:
        _drop(settings, slug)
        registry_session.execute(text("delete from tenants where slug = :s"), {"s": slug})
        registry_session.commit()


def test_a_successful_drop_deletes_the_registry_row(
    settings: Settings, registry_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The counterpart: once the database is actually gone, the row goes with it, as
    before."""
    import pigrocrm.core.tenants.service as tenants_service

    dropped: list[str] = []

    def fake_drop(settings: Settings, url: object) -> None:
        dropped.append(str(url))

    monkeypatch.setattr(tenants_service, "drop_database", fake_drop)
    slug = "prova-undo-ok"
    tenant = Tenant(slug=slug, db_name=tenant_database_name(slug), owner_email="ada@studio.it")
    registry_session.add(tenant)
    registry_session.commit()

    service = TenantService(registry_session, settings)
    service._undo(tenant, tenant_database_url(settings, tenant.db_name))
    assert dropped
    assert (
        registry_session.execute(
            text("select count(*) from tenants where slug = :s"), {"s": slug}
        ).scalar()
        == 0
    )
