"""0031 (REB-554): `signups.rif` is added empty, survives a retried deploy, and leaves the
rows that were already there untouched."""

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, text
from testcontainers.community.postgres import PostgresContainer

from rebase_core.db import Base
from rebase_core.migrate import INI_PATH, head_revision, upgrade_to_head


def _columns(connection) -> list[str]:  # type: ignore[no-untyped-def]
    return [
        row[0]
        for row in connection.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'signups' AND column_name = 'rif'"
            )
        )
    ]


def test_migration_0031_adds_an_empty_column_and_runs_again() -> None:
    with PostgresContainer("postgres:17-alpine", driver="psycopg") as container:
        url = container.get_connection_url()
        upgrade_to_head(url)
        config = Config(str(INI_PATH))
        config.set_main_option("sqlalchemy.url", url)
        engine = create_engine(url, future=True)
        command.downgrade(config, "0030")
        with engine.begin() as connection:
            assert _columns(connection) == []
            connection.execute(
                text(
                    "INSERT INTO signups (id, email, nome, cognome, created_at) VALUES "
                    "(gen_random_uuid(), 'old@acme.it', 'Old', 'Row', now())"
                )
            )
        for _ in range(2):
            command.upgrade(config, "head")
            with engine.begin() as connection:
                assert _columns(connection) == ["rif"]
                row = connection.execute(text("SELECT email, nome, rif FROM signups")).one()
                assert tuple(row) == ("old@acme.it", "Old", None)
                connection.execute(text("UPDATE signups SET rif = 'ABCDEFGH23'"))
            command.downgrade(config, "0030")
            with engine.connect() as connection:
                assert _columns(connection) == []
        command.upgrade(config, "head")
        with engine.begin() as connection:
            # The revision again over the column it already added.
            connection.execute(text("UPDATE alembic_version SET version_num = '0030'"))
        command.upgrade(config, "head")
        with engine.connect() as connection:
            assert (
                connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
                == head_revision()
            )
            diff = compare_metadata(MigrationContext.configure(connection), Base.metadata)
            assert diff == [], diff
        engine.dispose()
