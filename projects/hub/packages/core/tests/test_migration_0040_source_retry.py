"""0040 (REB-844): `freelancer_sources.attempts`. The rows that were already `error` when it
lands start at 1, so the first pass after the deploy retries them; every other row starts
at 0; a retried deploy lands on the same schema; the downgrade drops the column."""

from typing import Any

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Connection, Engine, create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import rebase_core.models  # noqa: F401
from rebase_core.db import Base
from rebase_core.migrate import INI_PATH, head_revision, upgrade_to_head
from rebase_core.models import Freelancer, User

REVISION = "0040"


def _parent(config: Config) -> str:
    """The revision this one revises, read from the script: the tests rewind to it, and a
    hard-coded number is the one that goes stale when another branch lands one first."""
    parent = ScriptDirectory.from_config(config).get_revision(REVISION).down_revision
    assert isinstance(parent, str)
    return parent


def _has_attempts(connection: Connection) -> bool:
    return bool(
        connection.execute(
            text(
                "SELECT count(*) FROM information_schema.columns "
                "WHERE table_name = 'freelancer_sources' AND column_name = 'attempts'"
            )
        ).scalar_one()
    )


def _person(engine: Engine) -> Any:
    with Session(engine) as session:
        user = User(email="a@x.it", nome="A", cognome="B")
        session.add(user)
        session.flush()
        row = Freelancer(user_id=user.id)
        session.add(row)
        session.commit()
        return row.id


def test_revision_follows_the_automatic_evaluation() -> None:
    assert _parent(Config(str(INI_PATH))) == "0039"


def test_upgrade_adds_the_column_and_the_models_agree(hub_postgres: Any) -> None:
    with hub_postgres.fresh_container() as container:
        url = container.get_connection_url()
        upgrade_to_head(url)
        engine = create_engine(url, future=True)
        with engine.connect() as connection:
            assert connection.execute(
                text(
                    "SELECT data_type, is_nullable, column_default FROM "
                    "information_schema.columns WHERE table_name = 'freelancer_sources' "
                    "AND column_name = 'attempts'"
                )
            ).one() == ("integer", "NO", "0")
            diff = compare_metadata(MigrationContext.configure(connection), Base.metadata)
            assert diff == [], diff
        engine.dispose()


def test_rows_already_in_error_start_at_one_and_the_rest_at_zero(hub_postgres: Any) -> None:
    with hub_postgres.fresh_container() as container:
        url = container.get_connection_url()
        config = Config(str(INI_PATH))
        config.set_main_option("sqlalchemy.url", url)
        upgrade_to_head(url)
        engine = create_engine(url, future=True)
        person = _person(engine)
        command.downgrade(config, _parent(config))  # production's state before the deploy
        insert = text(
            "INSERT INTO freelancer_sources (id, freelancer_id, kind, ref, position, status, "
            "fetched_at) VALUES (gen_random_uuid(), :f, 'link', :r, 1, :s, now())"
        )
        with engine.begin() as connection:
            for ref, status in (("a", "error"), ("b", "ok"), ("c", "unreadable"), ("d", "error")):
                connection.execute(insert, {"f": person, "r": ref, "s": status})

        command.upgrade(config, "head")

        with engine.connect() as connection:
            assert connection.execute(
                text("SELECT ref, attempts FROM freelancer_sources ORDER BY ref")
            ).all() == [("a", 1), ("b", 0), ("c", 0), ("d", 1)]
        engine.dispose()


def test_upgrade_is_idempotent_and_keeps_the_counts(hub_postgres: Any) -> None:
    with hub_postgres.fresh_container() as container:
        url = container.get_connection_url()
        upgrade_to_head(url)
        config = Config(str(INI_PATH))
        config.set_main_option("sqlalchemy.url", url)
        engine = create_engine(url, future=True)
        person = _person(engine)
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO freelancer_sources (id, freelancer_id, kind, ref, position, "
                    "status, fetched_at, attempts) VALUES (gen_random_uuid(), :f, 'link', 'a', "
                    "1, 'error', now(), 4)"
                ),
                {"f": person},
            )
            connection.execute(
                text("UPDATE alembic_version SET version_num = :parent"),
                {"parent": _parent(config)},
            )
        command.upgrade(config, "head")
        with engine.connect() as connection:
            assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar() == (
                head_revision()
            )
            assert connection.execute(text("SELECT attempts FROM freelancer_sources")).all() == [
                (4,)
            ]
        engine.dispose()


def test_a_negative_count_is_refused(hub_postgres: Any) -> None:
    with hub_postgres.fresh_container() as container:
        url = container.get_connection_url()
        upgrade_to_head(url)
        engine = create_engine(url, future=True)
        person = _person(engine)
        with pytest.raises(IntegrityError), engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO freelancer_sources (id, freelancer_id, kind, ref, position, "
                    "status, fetched_at, attempts) VALUES (gen_random_uuid(), :f, 'link', 'a', "
                    "1, 'error', now(), -1)"
                ),
                {"f": person},
            )
        engine.dispose()


def test_downgrade_drops_the_column_and_upgrade_brings_it_back(hub_postgres: Any) -> None:
    with hub_postgres.fresh_container() as container:
        url = container.get_connection_url()
        upgrade_to_head(url)
        config = Config(str(INI_PATH))
        config.set_main_option("sqlalchemy.url", url)
        engine = create_engine(url, future=True)
        command.downgrade(config, _parent(config))
        with engine.connect() as connection:
            assert not _has_attempts(connection)
        command.upgrade(config, "head")
        with engine.connect() as connection:
            assert _has_attempts(connection)
        engine.dispose()
