"""Side databases: found or created on the CRM's own server, then given their schema.

Two things live beside the CRM database without being part of it -- the Orbiters signup
list and the registry of tenant spaces -- and both are shaped the same way: a database
whose name is derived from the CRM's URL, created idempotently the first time anyone
asks, with a `MetaData` of its own that `create_all` is the honest size of tool for.
`packages/core/migrations` describes the CRM schema and every installation runs it;
neither of these is the CRM schema.
"""

from sqlalchemy import Engine, MetaData, create_engine, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ProgrammingError

from pigrocrm.core.config import Settings


def sidecar_url(settings: Settings, explicit: str, default_name: str) -> URL:
    """`explicit` when set; otherwise the owner's URL with only the database name
    swapped, so the compose stack needs no second variable. The owner's, not the
    application role's (REB-634): a side database is created and given its tables by
    whoever connects to it, and `create_all` is the owner's to run."""
    if explicit:
        return make_url(explicit)
    return make_url(settings.owner_database_url).set(database=default_name)


def admin_url(settings: Settings, target: URL) -> URL:
    """Where to connect in order to run `CREATE DATABASE` for `target`.

    `CREATE DATABASE` needs a connection to *some other* database on the same server.
    When the target lives on the CRM's server, the CRM database is that other one and
    its credentials are known to work. An explicit URL pointing elsewhere falls back
    to Postgres's own maintenance database.
    """
    main = make_url(settings.owner_database_url)
    same_server = (main.host, main.port, main.username) == (
        target.host,
        target.port,
        target.username,
    )
    if same_server and main.database != target.database:
        return main
    return target.set(database="postgres")


def create_database_if_missing(settings: Settings, target: URL) -> bool:
    """Returns True when it created the database, False when it was already there."""
    if target.database is None:
        raise ValueError("the database URL names no database")
    admin = create_engine(admin_url(settings, target), isolation_level="AUTOCOMMIT", future=True)
    try:
        with admin.connect() as connection:
            exists = connection.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :name"), {"name": target.database}
            ).scalar()
            if exists is not None:
                return False
            quoted = admin.dialect.identifier_preparer.quote(target.database)
            try:
                connection.execute(text(f"CREATE DATABASE {quoted}"))
            except ProgrammingError as exc:
                # Two processes booting at once: one of them loses the race and finds
                # the database created a moment ago. Anything else is real.
                if "already exists" not in str(exc):
                    raise
                return False
            return True
    finally:
        admin.dispose()


def drop_database(settings: Settings, target: URL) -> None:
    """Best effort, for undoing a provisioning that failed halfway. Terminates the
    connections still open on it first, or Postgres refuses."""
    if target.database is None:
        return
    admin = create_engine(admin_url(settings, target), isolation_level="AUTOCOMMIT", future=True)
    try:
        with admin.connect() as connection:
            connection.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :name AND pid <> pg_backend_pid()"
                ),
                {"name": target.database},
            )
            quoted = admin.dialect.identifier_preparer.quote(target.database)
            connection.execute(text(f"DROP DATABASE IF EXISTS {quoted}"))
    finally:
        admin.dispose()


def ensure_sidecar_database(settings: Settings, target: URL, metadata: MetaData) -> Engine:
    """Idempotent. Returns an engine bound to a database that exists and has the tables."""
    create_database_if_missing(settings, target)
    engine = create_engine(target, pool_pre_ping=True, future=True)
    metadata.create_all(engine)
    return engine
