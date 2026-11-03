"""The application role the boot creates (REB-634, spec 2026-10-03 §4 «The role»).

The boundary itself is `test_azienda_scope.py`, which connects as this role. This file
is about the helper: what one URL means, what two mean, and that the grants reach a
table created after the role was granted, which is what every later migration is.
"""

from __future__ import annotations

import pytest
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import DBAPIError

from pigrocrm.core.config import Settings
from pigrocrm.core.db.role import DEFINER_FUNCTIONS, application_role, ensure_application_role

ROLE = "pigrocrm_app_role_test"


def test_one_url_means_no_role_and_the_owner_url_defaults_to_it() -> None:
    settings = Settings(database_url="postgresql+psycopg://u:p@h/db", _env_file=None)  # type: ignore[call-arg]
    assert settings.owner_database_url == settings.database_url
    assert application_role(settings.owner_database_url, settings.database_url) is None
    assert ensure_application_role(settings.database_url, settings.database_url) is False
    two = Settings(
        database_url="postgresql+psycopg://pigrocrm_app:s3cret@h/db",
        admin_database_url="postgresql+psycopg://u:p@h/db",
        _env_file=None,  # type: ignore[call-arg]
    )
    assert two.owner_database_url == "postgresql+psycopg://u:p@h/db"
    assert application_role(two.owner_database_url, two.database_url) == ("pigrocrm_app", "s3cret")


def test_the_role_is_created_once_granted_every_time_and_reaches_later_tables(
    db_engine: Engine,
) -> None:
    app_url = db_engine.url.set(username=ROLE, password="first")
    assert ensure_application_role(db_engine.url, app_url) is True
    with db_engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT rolsuper, rolbypassrls, rolcreatedb, rolcanlogin FROM pg_roles "
                "WHERE rolname = :r"
            ),
            {"r": ROLE},
        ).one()
    assert tuple(row) == (False, False, False, True)
    # A second boot with a rotated password: the same role, the new password.
    rotated = app_url.set(password="second")
    assert ensure_application_role(db_engine.url, rotated) is True
    with db_engine.begin() as connection:
        connection.execute(text("CREATE TABLE IF NOT EXISTS role_test_later (id serial, n int)"))
    app = create_engine(rotated, future=True)
    try:
        with app.begin() as connection:
            # Default privileges: a table the owner created after the grant is granted.
            connection.execute(text("INSERT INTO role_test_later (n) VALUES (1)"))
            assert connection.execute(text("SELECT count(*) FROM users")).scalar_one() >= 0
            try:
                connection.execute(text("CREATE TABLE role_test_forbidden (id int)"))
            except DBAPIError as exc:
                assert getattr(exc.orig, "sqlstate", None) == "42501"
            else:  # pragma: no cover - the assertion is the point
                raise AssertionError("the application role could create a table")
    finally:
        app.dispose()
        with db_engine.begin() as connection:
            connection.execute(text("DROP TABLE IF EXISTS role_test_later"))


def test_a_role_that_bypasses_the_policies_is_refused_not_granted(db_engine: Engine) -> None:
    """The attributes go on CREATE and never on ALTER (a CREATEROLE owner may not say
    SUPERUSER or BYPASSRLS at all), so a role somebody made a superuser by hand is
    caught by the check instead, and the boot stops rather than serving through it."""
    with db_engine.begin() as connection:
        connection.execute(text(f"DROP ROLE IF EXISTS {ROLE}_super"))
        connection.execute(text(f"CREATE ROLE {ROLE}_super LOGIN BYPASSRLS"))
    try:
        with pytest.raises(DBAPIError, match="bypasses RLS"):
            ensure_application_role(db_engine.url, db_engine.url.set(username=f"{ROLE}_super"))
    finally:
        with db_engine.begin() as connection:
            connection.execute(text(f"DROP ROLE IF EXISTS {ROLE}_super"))


def test_the_definer_helpers_pin_a_closed_search_path_and_are_closed_to_public(
    db_engine: Engine,
) -> None:
    """B-09 (migration 0066): the helpers that read past the policies resolve names in the
    catalog first, and only the roles `ensure_application_role` names may run them."""
    with db_engine.connect() as connection:
        for function in DEFINER_FUNCTIONS:
            config, public = connection.execute(
                text(
                    "SELECT proconfig, has_function_privilege('public', oid, 'EXECUTE') "
                    "FROM pg_proc WHERE oid = to_regprocedure(:f)"
                ),
                {"f": function},
            ).one()
            assert "search_path=pg_catalog, public, pg_temp" in config
            assert public is False, f"{function} is executable by PUBLIC"


def test_the_application_role_runs_the_definer_helpers_but_cannot_move_the_schema_version(
    db_engine: Engine,
) -> None:
    app_url = db_engine.url.set(username=f"{ROLE}_defn", password="x")
    assert ensure_application_role(db_engine.url, app_url) is True
    with db_engine.begin() as connection:
        outsider = f"{ROLE}_outsider"
        connection.execute(text(f"DROP ROLE IF EXISTS {outsider}"))
        connection.execute(text(f"CREATE ROLE {outsider} LOGIN PASSWORD 'x'"))
        connection.execute(
            text(f"GRANT CONNECT ON DATABASE {db_engine.url.database} TO {outsider}")
        )
        connection.execute(text(f"GRANT USAGE ON SCHEMA public TO {outsider}"))
    app = create_engine(app_url, future=True)
    outside = create_engine(db_engine.url.set(username=outsider, password="x"), future=True)
    try:
        with app.begin() as connection:
            assert (
                connection.execute(
                    text("SELECT cliente_archiviato(gen_random_uuid())")
                ).scalar_one()
                is None
            )
            with pytest.raises(DBAPIError):
                connection.execute(text("UPDATE alembic_version SET version_num = version_num"))
        with outside.connect() as connection, pytest.raises(DBAPIError):
            connection.execute(text("SELECT cliente_archiviato(gen_random_uuid())"))
    finally:
        app.dispose()
        outside.dispose()
