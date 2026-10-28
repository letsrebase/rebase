"""0041 (REB-853): `card_batches`, `card_batch_items` and `ai_usage.batch`. The usage rows
that exist when it lands are synchronous calls (`batch` false); an item goes with its batch
and with its person; the checks hold the statuses; a retried deploy lands on the same
schema; the downgrade drops all three and the upgrade brings them back."""

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

REVISION = "0041"


def _parent(config: Config) -> str:
    parent = ScriptDirectory.from_config(config).get_revision(REVISION).down_revision
    assert isinstance(parent, str)
    return parent


def _tables(connection: Connection) -> set[str]:
    return set(
        connection.execute(
            text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_name IN ('card_batches', 'card_batch_items')"
            )
        ).scalars()
    )


def _has_batch_column(connection: Connection) -> bool:
    return bool(
        connection.execute(
            text(
                "SELECT count(*) FROM information_schema.columns "
                "WHERE table_name = 'ai_usage' AND column_name = 'batch'"
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


def test_upgrade_adds_the_tables_and_the_models_agree(hub_postgres: Any) -> None:
    with hub_postgres.fresh_container() as container:
        url = container.get_connection_url()
        upgrade_to_head(url)
        engine = create_engine(url, future=True)
        with engine.connect() as connection:
            assert _tables(connection) == {"card_batches", "card_batch_items"}
            assert _has_batch_column(connection)
            diff = compare_metadata(MigrationContext.configure(connection), Base.metadata)
            assert diff == [], diff
        engine.dispose()


def test_existing_usage_rows_are_synchronous_calls(hub_postgres: Any) -> None:
    with hub_postgres.fresh_container() as container:
        url = container.get_connection_url()
        config = Config(str(INI_PATH))
        config.set_main_option("sqlalchemy.url", url)
        upgrade_to_head(url)
        engine = create_engine(url, future=True)
        command.downgrade(config, _parent(config))
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO ai_usage (id, task, purpose, provider, model, priced) "
                    "VALUES (gen_random_uuid(), 'card', 'run', 'anthropic', 'm', false)"
                )
            )

        command.upgrade(config, "head")

        with engine.connect() as connection:
            assert connection.execute(text("SELECT batch FROM ai_usage")).all() == [(False,)]
        engine.dispose()


def _batch(connection: Connection, status: str = "in_progress") -> Any:
    return connection.execute(
        text(
            "INSERT INTO card_batches (id, provider, status, request_count) "
            "VALUES (gen_random_uuid(), 'anthropic', :s, 1) RETURNING id"
        ),
        {"s": status},
    ).scalar_one()


def _item(connection: Connection, batch: Any, person: Any, status: str = "pending") -> None:
    connection.execute(
        text(
            "INSERT INTO card_batch_items (batch_id, freelancer_id, cv_sha256, input_hashes, "
            "status) VALUES (:b, :f, repeat('a', 64), '{}'::jsonb, :s)"
        ),
        {"b": batch, "f": person, "s": status},
    )


def test_an_item_goes_with_its_batch_and_with_its_person(hub_postgres: Any) -> None:
    with hub_postgres.fresh_container() as container:
        url = container.get_connection_url()
        upgrade_to_head(url)
        engine = create_engine(url, future=True)
        person = _person(engine)
        with engine.begin() as connection:
            first = _batch(connection)
            _item(connection, first, person)
            second = _batch(connection, "applied")
            _item(connection, second, person, "written")

        with engine.begin() as connection:
            connection.execute(text("DELETE FROM card_batches WHERE id = :id"), {"id": first})
        with engine.connect() as connection:
            assert connection.execute(text("SELECT count(*) FROM card_batch_items")).scalar() == 1
        with engine.begin() as connection:
            connection.execute(text("DELETE FROM freelancers WHERE id = :id"), {"id": person})
        with engine.connect() as connection:
            assert connection.execute(text("SELECT count(*) FROM card_batch_items")).scalar() == 0
        engine.dispose()


def test_the_statuses_are_held_by_the_database(hub_postgres: Any) -> None:
    with hub_postgres.fresh_container() as container:
        url = container.get_connection_url()
        upgrade_to_head(url)
        engine = create_engine(url, future=True)
        person = _person(engine)
        with pytest.raises(IntegrityError), engine.begin() as connection:
            _batch(connection, "done")
        with pytest.raises(IntegrityError), engine.begin() as connection:
            _item(connection, _batch(connection), person, "applied")
        engine.dispose()


def test_a_provider_id_is_unique(hub_postgres: Any) -> None:
    with hub_postgres.fresh_container() as container:
        url = container.get_connection_url()
        upgrade_to_head(url)
        engine = create_engine(url, future=True)
        insert = text(
            "INSERT INTO card_batches (id, provider, provider_batch_id, request_count) "
            "VALUES (gen_random_uuid(), 'anthropic', 'msgbatch_1', 1)"
        )
        with engine.begin() as connection:
            connection.execute(insert)
        with pytest.raises(IntegrityError), engine.begin() as connection:
            connection.execute(insert)
        engine.dispose()


def test_upgrade_is_idempotent(hub_postgres: Any) -> None:
    with hub_postgres.fresh_container() as container:
        url = container.get_connection_url()
        upgrade_to_head(url)
        config = Config(str(INI_PATH))
        config.set_main_option("sqlalchemy.url", url)
        engine = create_engine(url, future=True)
        person = _person(engine)
        with engine.begin() as connection:
            _item(connection, _batch(connection), person)
            connection.execute(
                text("UPDATE alembic_version SET version_num = :parent"),
                {"parent": _parent(config)},
            )
        command.upgrade(config, "head")
        with engine.connect() as connection:
            assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar() == (
                head_revision()
            )
            assert connection.execute(text("SELECT count(*) FROM card_batch_items")).scalar() == 1
        engine.dispose()


def test_downgrade_drops_them_and_upgrade_brings_them_back(hub_postgres: Any) -> None:
    with hub_postgres.fresh_container() as container:
        url = container.get_connection_url()
        upgrade_to_head(url)
        config = Config(str(INI_PATH))
        config.set_main_option("sqlalchemy.url", url)
        engine = create_engine(url, future=True)
        command.downgrade(config, _parent(config))
        with engine.connect() as connection:
            assert _tables(connection) == set()
            assert not _has_batch_column(connection)
        command.upgrade(config, "head")
        with engine.connect() as connection:
            assert _tables(connection) == {"card_batches", "card_batch_items"}
            assert _has_batch_column(connection)
        engine.dispose()
