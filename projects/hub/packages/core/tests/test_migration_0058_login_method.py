"""0058 (REB-1137): how a person got in, written on the login."""

from typing import Any
from uuid import uuid4

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

import rebase_core.models  # noqa: F401
from rebase_core.db import Base
from rebase_core.migrate import INI_PATH, upgrade_to_head

REVISION = "0058"


def _parent(config: Config) -> str:
    parent = ScriptDirectory.from_config(config).get_revision(REVISION).down_revision
    assert isinstance(parent, str)
    return parent


def _user(connection: Any) -> Any:
    user_id = uuid4()
    connection.execute(
        text(
            "INSERT INTO users (id, email, first_name, last_name, role, active) "
            "VALUES (:id, 'a@acme.it', 'A', 'B', 'member', true)"
        ),
        {"id": user_id},
    )
    return user_id


def test_revision_follows_the_work_profile_campaign_action() -> None:
    assert _parent(Config(str(INI_PATH))) == "0057"


def test_a_login_names_its_method_and_nothing_else(hub_postgres: Any) -> None:
    with hub_postgres.fresh_container() as container:
        url = container.get_connection_url()
        upgrade_to_head(url)
        engine = create_engine(url, future=True)
        with engine.begin() as connection:
            user_id = _user(connection)
            for method in ("link", "github", "google"):
                connection.execute(
                    text("INSERT INTO logins (id, user_id, method) VALUES (:id, :u, :m)"),
                    {"id": uuid4(), "u": user_id, "m": method},
                )
        with (
            pytest.raises(IntegrityError, match="ck_logins_method"),
            engine.begin() as c,
        ):
            c.execute(
                text("INSERT INTO logins (id, user_id, method) VALUES (:id, :u, 'apple')"),
                {"id": uuid4(), "u": user_id},
            )
        engine.dispose()


def test_a_login_written_before_the_migration_was_a_magic_link(hub_postgres: Any) -> None:
    with hub_postgres.fresh_container() as container:
        url = container.get_connection_url()
        config = Config(str(INI_PATH))
        config.set_main_option("sqlalchemy.url", url)
        upgrade_to_head(url)
        command.downgrade(config, _parent(config))
        engine = create_engine(url, future=True)
        with engine.begin() as connection:
            user_id = _user(connection)
            connection.execute(
                text("INSERT INTO logins (id, user_id) VALUES (:id, :u)"),
                {"id": uuid4(), "u": user_id},
            )
        command.upgrade(config, "head")
        with engine.connect() as connection:
            assert connection.execute(text("SELECT method FROM logins")).scalars().all() == ["link"]
        engine.dispose()


def test_metadata_matches_the_schema(hub_postgres: Any) -> None:
    with hub_postgres.fresh_container() as container:
        url = container.get_connection_url()
        upgrade_to_head(url)
        engine = create_engine(url, future=True)
        with engine.connect() as connection:
            diff = compare_metadata(MigrationContext.configure(connection), Base.metadata)
            assert diff == [], diff
        engine.dispose()
