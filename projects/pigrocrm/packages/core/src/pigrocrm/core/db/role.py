"""The application's own database role, created and granted at boot (REB-634, spec
2026-10-03 §4 «The role», §10 decision 1).

A superuser is never subject to a row-level policy and a table's owner only under
`FORCE ROW LEVEL SECURITY`, so the API connects as a role that is neither:
`PIGROCRM_DATABASE_URL` names it, `PIGROCRM_ADMIN_DATABASE_URL` keeps the owner for
Alembic, `ensure-space-defaults` and `CREATE DATABASE`. The role's name and password
are read from the application URL itself, so there is one place to rotate them: the
boot creates the role if it is missing and sets the password it finds either way.

The grants are per database and `ALTER DEFAULT PRIVILEGES` is per grantor, so this runs
on the admin URL of the root and of every space, after each one's migration, and again
for a space the moment it is provisioned. Everything is idempotent and the whole thing
is one transaction. `docker-entrypoint-initdb.d` could not do it: that runs only on an
empty data directory, and the production and preview clusters are not empty.
"""

from __future__ import annotations

from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, make_url

# The role name and the password travel as transaction-local settings into one plpgsql
# block, which quotes them with `format('%I')` and `format('%L')`: a role or a password
# with a quote in it is then an identifier and a literal, never a piece of SQL.
_ENSURE_ROLE = """
DO $$
DECLARE
    ruolo text := current_setting('pigrocrm.app_role');
    parola text := current_setting('pigrocrm.app_password');
BEGIN
    BEGIN
        IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = ruolo) THEN
            -- The attributes go on CREATE, which a CREATEROLE owner may say; an ALTER
            -- that mentions SUPERUSER or BYPASSRLS, even to deny them, is refused to
            -- anybody but a superuser, and a managed Postgres hands out no superuser.
            EXECUTE format(
                'CREATE ROLE %I LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE', ruolo
            );
        END IF;
    EXCEPTION WHEN duplicate_object THEN
        NULL;  -- two boots at once: the other one made it a moment ago
    END;
    IF parola = '' THEN
        EXECUTE format('ALTER ROLE %I WITH LOGIN NOCREATEDB NOCREATEROLE', ruolo);
    ELSE
        EXECUTE format(
            'ALTER ROLE %I WITH LOGIN NOCREATEDB NOCREATEROLE PASSWORD %L', ruolo, parola
        );
    END IF;
    -- A role somebody made a superuser by hand would bypass every policy: refused,
    -- loudly, rather than granted and served.
    IF EXISTS (
        SELECT 1 FROM pg_roles WHERE rolname = ruolo AND (rolsuper OR rolbypassrls)
    ) THEN
        RAISE EXCEPTION 'the application role % is a superuser or bypasses RLS', ruolo
            USING ERRCODE = 'invalid_parameter_value';
    END IF;
    EXECUTE format('GRANT CONNECT ON DATABASE %I TO %I', current_database(), ruolo);
    EXECUTE format('GRANT USAGE ON SCHEMA public TO %I', ruolo);
    EXECUTE format(
        'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO %I', ruolo
    );
    EXECUTE format('GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO %I', ruolo);
    EXECUTE format(
        'ALTER DEFAULT PRIVILEGES IN SCHEMA public '
        'GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO %I',
        ruolo
    );
    EXECUTE format(
        'ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO %I',
        ruolo
    );
END $$
"""


def application_role(owner_url: str | URL, app_url: str | URL) -> tuple[str, str] | None:
    """The role `app_url` names and its password, or `None` when both URLs log in as the
    same role: one URL, as a development checkout and the test suite run, means no
    second role exists and nothing is created."""
    owner, app = make_url(owner_url), make_url(app_url)
    if not app.username or app.username == owner.username:
        return None
    return app.username, app.password or ""


def ensure_application_role(owner_url: str | URL, app_url: str | URL) -> bool:
    """Create the application role if it is missing and grant it the database `owner_url`
    names. Answers whether anything was done: `False` when the two URLs are one role.

    Connects as the owner on that database, so the grants land where the tables are and
    the default privileges are the owner's, which is the grantor every later migration
    runs as."""
    role = application_role(owner_url, app_url)
    if role is None:
        return False
    name, password = role
    engine = create_engine(make_url(owner_url), future=True)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "SELECT set_config('pigrocrm.app_role', :name, true), "
                    "set_config('pigrocrm.app_password', :password, true)"
                ),
                {"name": name, "password": password},
            )
            connection.execute(text(_ENSURE_ROLE))
    finally:
        engine.dispose()
    return True


__all__ = ["application_role", "ensure_application_role"]
