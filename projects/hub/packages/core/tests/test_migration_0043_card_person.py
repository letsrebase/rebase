"""0043 (REB-855): `freelancer_cards.person`, a nullable JSONB beside the card; a card
written before it reads `NULL`, a retried deploy lands on the same schema, and the
downgrade drops the column and nothing else."""

from typing import Any
from uuid import uuid4

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Connection, create_engine, text

import rebase_core.models  # noqa: F401
from rebase_core.db import Base
from rebase_core.migrate import INI_PATH, head_revision, upgrade_to_head

REVISION = "0043"


def _parent(config: Config) -> str:
    parent = ScriptDirectory.from_config(config).get_revision(REVISION).down_revision
    assert isinstance(parent, str)
    return parent


def _person_column(connection: Connection, name: str) -> tuple[str, str] | None:
    row = connection.execute(
        text(
            "SELECT data_type, is_nullable FROM information_schema.columns "
            "WHERE table_name = 'freelancer_cards' AND column_name = :c"
        ),
        {"c": name},
    ).one_or_none()
    return (row[0], row[1]) if row is not None else None


def test_revision_follows_the_notes() -> None:
    assert _parent(Config(str(INI_PATH))) == "0042"


def test_upgrade_adds_a_nullable_jsonb_and_matches_the_models(hub_postgres: Any) -> None:
    with hub_postgres.fresh_container() as container:
        url = container.get_connection_url()
        upgrade_to_head(url)
        engine = create_engine(url, future=True)
        with engine.connect() as connection:
            assert _person_column(connection, "person") == ("jsonb", "YES")
            diff = compare_metadata(MigrationContext.configure(connection), Base.metadata)
            assert diff == [], diff
        engine.dispose()


def test_upgrade_is_idempotent_and_keeps_what_the_column_holds(hub_postgres: Any) -> None:
    with hub_postgres.fresh_container() as container:
        url = container.get_connection_url()
        upgrade_to_head(url)
        config = Config(str(INI_PATH))
        config.set_main_option("sqlalchemy.url", url)
        engine = create_engine(url, future=True)
        command.downgrade(config, REVISION)  # 0043's own schema, 0049's English names undone
        user_id, freelancer_id = uuid4(), uuid4()
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO users (id, email, nome, cognome, role, attivo) "
                    "VALUES (:id, 'a@x.it', 'A', 'B', 'member', true)"
                ),
                {"id": user_id},
            )
            connection.execute(
                text(
                    "INSERT INTO freelancers (id, user_id, posizione, links, stato, compilata_da) "
                    "VALUES (:id, :u, 'dev', '[]', 'nuovo', 'persona')"
                ),
                {"id": freelancer_id, "u": user_id},
            )
            connection.execute(
                text(
                    "INSERT INTO freelancer_cards (freelancer_id, persona) "
                    'VALUES (:f, \'{"email": "ada@example.org"}\'::jsonb)'
                ),
                {"f": freelancer_id},
            )
        with engine.begin() as connection:
            connection.execute(
                text("UPDATE alembic_version SET version_num = :parent"),
                {"parent": _parent(config)},
            )
        command.upgrade(config, "head")
        with engine.connect() as connection:
            assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar() == (
                head_revision()
            )
            assert (
                connection.execute(
                    text("SELECT person->>'email' FROM freelancer_cards")
                ).scalar_one()
                == "ada@example.org"
            )
        engine.dispose()


def test_downgrade_drops_only_the_column(hub_postgres: Any) -> None:
    with hub_postgres.fresh_container() as container:
        url = container.get_connection_url()
        upgrade_to_head(url)
        config = Config(str(INI_PATH))
        config.set_main_option("sqlalchemy.url", url)
        engine = create_engine(url, future=True)
        command.downgrade(config, _parent(config))
        with engine.connect() as connection:
            assert _person_column(connection, "persona") is None
            assert (
                connection.execute(
                    text(
                        "SELECT count(*) FROM information_schema.columns "
                        "WHERE table_name = 'freelancer_cards' AND column_name = 'prompt_version'"
                    )
                ).scalar_one()
                == 1
            )
        command.upgrade(config, "head")
        with engine.connect() as connection:
            assert _person_column(connection, "person") == ("jsonb", "YES")
        engine.dispose()
