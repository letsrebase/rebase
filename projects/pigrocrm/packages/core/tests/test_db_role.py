"""The application role the boot creates (REB-634, spec 2026-10-03 §4 «The role»).

The boundary itself is `test_azienda_scope.py`, which connects as this role. This file
is about the helper: what one URL means, what two mean, and that the grants reach a
table created after the role was granted, which is what every later migration is.
"""

from __future__ import annotations

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import DBAPIError

from pigrocrm.core.config import Settings
from pigrocrm.core.db.role import application_role, ensure_application_role

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
