"""The registry table `rebase_engagements`: one row per hub match (spec 2026-09-25
§ 2.2). `ensure_tenants_database` only creates the tables of classes Python has
actually imported by the time it runs (`test_tenants.py`'s own registry fixture is
the model this follows), which is why this table's existence is the thing under
test, not its columns individually -- A4's `EngagementService` exercises those.
"""

from sqlalchemy import Engine, inspect

from pigrocrm.core.config import Settings


def _settings_for(engine: Engine) -> Settings:
    return Settings(
        database_url=engine.url.render_as_string(hide_password=False),
        _env_file=None,  # type: ignore[call-arg]
    )


def test_engagements_package_exports_nothing() -> None:
    """The import trap the task brief warns about: the package `__init__` holds a
    docstring and no import, so `tenants/database.py` importing
    `pigrocrm.core.engagements.models` can never pull in `service` (A4's own) and
    chain back into `tenants.service` -> `tenants.database` while that module is
    still half-initialised.

    This has to run, and collect, before anything else in the process imports
    `pigrocrm.core.engagements.models`: Python binds an imported submodule onto its
    parent package object as a side effect, for the life of the process, whatever
    `__init__.py` itself does -- which is exactly why this module imports
    `pigrocrm.core.tenants` nowhere at module scope, and why this test is the first
    one defined here."""
    import pigrocrm.core.engagements as p

    assert not [n for n in dir(p) if not n.startswith("_")]


def test_registry_has_the_engagements_table(db_engine: Engine) -> None:
    from pigrocrm.core.tenants import ensure_tenants_database

    settings = _settings_for(db_engine)
    engine = ensure_tenants_database(settings)
    try:
        assert inspect(engine).has_table("rebase_engagements")
    finally:
        engine.dispose()
