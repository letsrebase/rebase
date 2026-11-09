"""`pigrocrm ensure-space-defaults` gives every space its profile (REB-955, spec 2026-11-09
B7): `company` for rebase's own space, `solo` with `deals` on for a space a hub match
opened, nothing for the rest, written only where no profile is stored and never over a
module row that exists. Run for real against the container, like `test_tenants.py`."""

from collections.abc import Iterator
from uuid import uuid4

import pytest
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from pigrocrm.core import cli
from pigrocrm.core.actor import Actor
from pigrocrm.core.config import Settings
from pigrocrm.core.db import session_factory
from pigrocrm.core.db.sidecar import create_database_if_missing, drop_database
from pigrocrm.core.engagements.models import RebaseEngagement
from pigrocrm.core.modules.service import SpaceModulesService
from pigrocrm.core.tenants import (
    Tenant,
    TenantService,
    TenantSignup,
    ensure_tenants_database,
)
from pigrocrm.core.tenants.database import tenant_database_name, tenant_database_url
from pigrocrm.core.tenants.service import migrate_to_head

ALL_ON = {f"module_{key}": "true" for key in ("deals", "contracts", "time", "costs", "taxes")}
SOLO = {
    "space_profile": "solo",
    "module_deals": "true",
    "module_contracts": "true",
    "module_time": "false",
    "module_costs": "true",
    "module_taxes": "true",
}
SHAPE_SQL = text(
    "select key, value from space_settings where key = 'space_profile' or key like 'module\\_%'"
)


def _settings(db_engine: Engine, **extra: str) -> Settings:
    return Settings(
        database_url=db_engine.url.render_as_string(hide_password=False),
        _env_file=None,  # type: ignore[call-arg]
        **extra,
    )


@pytest.fixture
def registry(db_engine: Engine) -> Iterator[Engine]:
    engine = ensure_tenants_database(_settings(db_engine))
    yield engine
    engine.dispose()


@pytest.fixture
def spaces(db_engine: Engine, registry: Engine) -> Iterator[list[str]]:
    """The slugs a test opened: dropped, with their registry rows, on the way out."""
    slugs: list[str] = []
    yield slugs
    settings = _settings(db_engine)
    with registry.begin() as connection:
        connection.execute(text("delete from rebase_engagements"))
        connection.execute(text("delete from tenants where slug = any(:s)"), {"s": slugs})
    for slug in slugs:
        drop_database(settings, tenant_database_url(settings, tenant_database_name(slug)))


def _open(db_engine: Engine, registry: Engine, spaces: list[str], slug: str) -> None:
    spaces.append(slug)
    with session_factory(registry)() as session:
        TenantService(session, _settings(db_engine)).provision(
            TenantSignup(slug=slug, name="Ada", email=f"{slug}@studio.it")
        )


def _opened_by_a_match(registry: Engine, slug: str, *, created: bool = True) -> None:
    with session_factory(registry)() as session:
        tenant = session.query(Tenant).filter_by(slug=slug).one()
        session.add(RebaseEngagement(match_id=uuid4(), tenant_id=tenant.id, space_created=created))
        session.commit()


def _stored(db_engine: Engine, slug: str) -> dict[str, str]:
    settings = _settings(db_engine)
    engine = create_engine(tenant_database_url(settings, tenant_database_name(slug)), future=True)
    try:
        with engine.connect() as connection:
            return {row.key: row.value for row in connection.execute(SHAPE_SQL)}
    finally:
        engine.dispose()


def _boot(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    assert cli.main(["ensure-space-defaults"]) == 0


def test_rebases_own_space_reads_company_with_every_module_on(
    db_engine: Engine,
    registry: Engine,
    spaces: list[str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _open(db_engine, registry, spaces, "prova-rebase")
    _boot(_settings(db_engine, leads_space="prova-rebase"), monkeypatch)
    assert _stored(db_engine, "prova-rebase") == {"space_profile": "company", **ALL_ON}
    assert "prova-rebase: profile company inferred (rebase's own space)" in capsys.readouterr().out


def test_a_space_a_hub_match_opened_reads_solo_with_deals_kept_on(
    db_engine: Engine,
    registry: Engine,
    spaces: list[str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _open(db_engine, registry, spaces, "prova-freelance")
    _opened_by_a_match(registry, "prova-freelance")
    _boot(_settings(db_engine), monkeypatch)
    assert _stored(db_engine, "prova-freelance") == SOLO
    assert (
        "prova-freelance: profile solo inferred (opened by a hub match)" in capsys.readouterr().out
    )


def test_a_space_the_door_only_found_and_a_plain_signup_get_no_profile(
    db_engine: Engine,
    registry: Engine,
    spaces: list[str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _open(db_engine, registry, spaces, "prova-reuse")
    _open(db_engine, registry, spaces, "prova-signup")
    # A match that found a space the freelancer already owned: `space_created` false.
    _opened_by_a_match(registry, "prova-reuse", created=False)
    _boot(_settings(db_engine), monkeypatch)
    assert _stored(db_engine, "prova-reuse") == {}
    assert _stored(db_engine, "prova-signup") == {}
    assert "prova-signup: nothing inferred" in capsys.readouterr().out


def test_the_company_space_wins_over_a_match_that_also_opened_it(
    db_engine: Engine,
    registry: Engine,
    spaces: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _open(db_engine, registry, spaces, "prova-both")
    _opened_by_a_match(registry, "prova-both")
    _boot(_settings(db_engine, leads_space="prova-both"), monkeypatch)
    assert _stored(db_engine, "prova-both")["space_profile"] == "company"


def test_a_profile_or_a_module_switch_already_set_is_never_overwritten(
    db_engine: Engine,
    registry: Engine,
    spaces: list[str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _open(db_engine, registry, spaces, "prova-chosen")
    _open(db_engine, registry, spaces, "prova-switch")
    for slug in ("prova-chosen", "prova-switch"):
        _opened_by_a_match(registry, slug)
    settings = _settings(db_engine)
    # An admin picked `company` and switched `taxes` off; another space holds only a
    # switch (a profile never chosen).
    for slug, values in (
        ("prova-chosen", {"space_profile": "company", "module_taxes": "false"}),
        ("prova-switch", {"module_contracts": "false"}),
    ):
        engine = create_engine(
            tenant_database_url(settings, tenant_database_name(slug)), future=True
        )
        try:
            with engine.begin() as connection:
                for key, value in values.items():
                    connection.execute(
                        text("insert into space_settings (key, value) values (:k, :v)"),
                        {"k": key, "v": value},
                    )
        finally:
            engine.dispose()
    _boot(settings, monkeypatch)
    assert _stored(db_engine, "prova-chosen") == {
        "space_profile": "company",
        "module_taxes": "false",
    }
    assert "prova-chosen: profile already set" in capsys.readouterr().out
    # No profile, so it is given one, around the switch somebody wrote.
    assert _stored(db_engine, "prova-switch") == {**SOLO, "module_contracts": "false"}


def test_a_second_boot_changes_nothing(
    db_engine: Engine,
    registry: Engine,
    spaces: list[str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _open(db_engine, registry, spaces, "prova-again")
    _opened_by_a_match(registry, "prova-again")
    settings = _settings(db_engine)
    _boot(settings, monkeypatch)
    engine = create_engine(
        tenant_database_url(settings, tenant_database_name("prova-again")), future=True
    )
    try:
        with engine.connect() as connection:
            before = connection.execute(
                text("select key, value, updated_at from space_settings order by key")
            ).all()
            activities = connection.execute(text("select count(*) from activities")).scalar()
        capsys.readouterr()
        _boot(settings, monkeypatch)
        assert "prova-again: profile already set" in capsys.readouterr().out
        with engine.connect() as connection:
            assert (
                connection.execute(
                    text("select key, value, updated_at from space_settings order by key")
                ).all()
                == before
            )
            assert (
                connection.execute(text("select count(*) from activities")).scalar() == activities
            )
    finally:
        engine.dispose()


def test_the_root_that_is_rebases_own_space_reads_company(
    db_engine: Engine,
    registry: Engine,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """On a scratch root, so no profile leaks into the database the other tests share."""
    scratch = "prova_root_profile"
    base = _settings(db_engine)
    url = make_url(base.database_url).set(database=scratch)
    create_database_if_missing(base, url)
    try:
        migrate_to_head(base, url.render_as_string(hide_password=False))
        settings = _settings(db_engine, leads_space="rebase-root", root_slug="rebase-root")
        settings = settings.model_copy(
            update={"database_url": url.render_as_string(hide_password=False)}
        )
        _boot(settings, monkeypatch)
        assert "root: profile company inferred (rebase's own space)" in capsys.readouterr().out
        engine = create_engine(url, future=True)
        try:
            with engine.connect() as connection:
                assert {r.key: r.value for r in connection.execute(SHAPE_SQL)} == {
                    "space_profile": "company",
                    **ALL_ON,
                }
        finally:
            engine.dispose()
    finally:
        drop_database(base, url)


def test_a_space_that_cannot_be_written_is_one_line_and_the_boot_goes_on(
    db_engine: Engine,
    registry: Engine,
    spaces: list[str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _open(db_engine, registry, spaces, "prova-broken")
    _open(db_engine, registry, spaces, "prova-fine")
    for slug in ("prova-broken", "prova-fine"):
        _opened_by_a_match(registry, slug)

    real = SpaceModulesService.ensure_profile

    def broken(
        self: SpaceModulesService, profile: str, actor: Actor, **kwargs: object
    ) -> list[str]:
        if self.settings.base.public_url.endswith(
            "prova-broken"
        ) or self.settings.base.public_url.endswith("prova-broken/"):
            raise RuntimeError("postgresql://leak-marker")
        return real(self, profile, actor, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(SpaceModulesService, "ensure_profile", broken)
    _boot(_settings(db_engine, public_url="https://pigro.test"), monkeypatch)
    captured = capsys.readouterr()
    assert "prova-broken: profile not written (RuntimeError)" in captured.err
    assert "leak-marker" not in captured.err + captured.out
    assert _stored(db_engine, "prova-fine") == SOLO
    assert _stored(db_engine, "prova-broken") == {}


def test_ensure_profile_on_a_session_writes_once(db_session: Session) -> None:
    service = SpaceModulesService(db_session, Settings(_env_file=None))  # type: ignore[call-arg]
    first = service.ensure_profile("solo", Actor.rebase(), keep_on=("deals",))
    assert first == sorted(["space_profile", *ALL_ON])
    assert service.ensure_profile("company", Actor.rebase()) == []
    assert service.describe().profile == "solo"


def test_a_module_that_holds_records_stays_on_whatever_the_profile_says(
    db_session: Session, time_entry_factory: object
) -> None:
    from datetime import date

    time_entry_factory(date=date(2026, 3, 10))  # type: ignore[operator]
    service = SpaceModulesService(db_session, Settings(_env_file=None))  # type: ignore[call-arg]
    service.ensure_profile("solo", Actor.rebase())
    state = {m.key: m.stored for m in service.describe().modules}
    # Hours need their deal, which holds the entry's deal: both stay on under `solo`.
    assert state["time"] is True and state["deals"] is True


def test_the_same_space_without_entries_has_time_off(db_session: Session) -> None:
    service = SpaceModulesService(db_session, Settings(_env_file=None))  # type: ignore[call-arg]
    service.ensure_profile("solo", Actor.rebase(), keep_on=("deals",))
    state = {m.key: m.stored for m in service.describe().modules}
    assert state["time"] is False and state["deals"] is True
