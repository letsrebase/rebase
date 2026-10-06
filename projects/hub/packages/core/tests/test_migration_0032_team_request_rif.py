"""0032 (REB-600): `team_requests.rif` is added empty, survives a retried deploy, and leaves the
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
                "WHERE table_name = 'team_requests' AND column_name = 'rif'"
            )
        )
    ]


def test_migration_0032_adds_an_empty_column_and_runs_again() -> None:
    with PostgresContainer("postgres:17-alpine", driver="psycopg") as container:
        url = container.get_connection_url()
        upgrade_to_head(url)
        config = Config(str(INI_PATH))
        config.set_main_option("sqlalchemy.url", url)
        engine = create_engine(url, future=True)
        command.downgrade(config, "0031")
        with engine.begin() as connection:
            assert _columns(connection) == []
            connection.execute(
                text(
                    "INSERT INTO team_requests "
                    "(id, origine, azienda, email, stato, created_at, updated_at) VALUES "
                    "(gen_random_uuid(), 'pubblico', 'Old Srl', 'old@acme.it', "
                    "'nuova', now(), now())"
                )
            )
        for _ in range(2):
            command.upgrade(config, "head")
            with engine.begin() as connection:
                assert _columns(connection) == ["rif"]
                row = connection.execute(
                    text("SELECT email, azienda, rif FROM team_requests")
                ).one()
                assert tuple(row) == ("old@acme.it", "Old Srl", None)
                connection.execute(text("UPDATE team_requests SET rif = 'ABCDEFGH23'"))
            command.downgrade(config, "0031")
            with engine.connect() as connection:
                assert _columns(connection) == []
        command.upgrade(config, "head")
        with engine.begin() as connection:
            # The revision again over the column it already added.
            connection.execute(text("UPDATE alembic_version SET version_num = '0031'"))
        command.upgrade(config, "head")
        with engine.connect() as connection:
            assert (
                connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
                == head_revision()
            )
            diff = compare_metadata(MigrationContext.configure(connection), Base.metadata)
            assert diff == [], diff
        engine.dispose()
