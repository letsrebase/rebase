"""0044 (REB-862): the `cv_anonimo` task, seeded from the `card` row, and `anonymous_cvs`;
a downgrade takes both away and puts the two CHECKs back."""

from typing import Any
from uuid import uuid4

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Connection, create_engine, text
from sqlalchemy.exc import IntegrityError

import rebase_core.models  # noqa: F401
from rebase_core.db import Base
from rebase_core.migrate import INI_PATH, upgrade_to_head

REVISION = "0044"

COLUMNS = {
    "freelancer_id": ("uuid", "NO"),
    "cv_sha256": ("character varying", "NO"),
    "prompt_version": ("integer", "NO"),
    "body": ("jsonb", "NO"),
    "provider": ("character varying", "NO"),
    "model": ("character varying", "NO"),
    "input_tokens": ("integer", "NO"),
    "output_tokens": ("integer", "NO"),
    "thinking_tokens": ("integer", "NO"),
    "generated_at": ("timestamp with time zone", "NO"),
}


def _parent(config: Config) -> str:
    parent = ScriptDirectory.from_config(config).get_revision(REVISION).down_revision
    assert isinstance(parent, str)
    return parent


def _usage(connection: Connection, task: str) -> None:
    connection.execute(
        text(
            "INSERT INTO ai_usage (id, task, provider, model, priced) "
            "VALUES (:id, :task, 'anthropic', 'm', false)"
        ),
        {"id": uuid4(), "task": task},
    )


def _table_exists(connection: Connection) -> bool:
    return connection.execute(text("SELECT to_regclass('anonymous_cvs')")).scalar_one() is not None


def test_revision_follows_the_card_persona() -> None:
    assert _parent(Config(str(INI_PATH))) == "0043"


def test_upgrade_creates_the_table_and_the_task(hub_postgres: Any) -> None:
    with hub_postgres.fresh_container() as container:
        url = container.get_connection_url()
        upgrade_to_head(url)
        engine = create_engine(url, future=True)
        with engine.begin() as connection:
            columns = {
                name: (data_type, nullable)
                for name, data_type, nullable in connection.execute(
                    text(
                        "SELECT column_name, data_type, is_nullable "
                        "FROM information_schema.columns WHERE table_name = 'anonymous_cvs'"
                    )
                )
            }
            assert columns == COLUMNS
            card, cv = (
                connection.execute(
                    text(
                        "SELECT provider, model, effort, enabled, max_output_tokens, "
                        "daily_cap_eur FROM ai_tasks WHERE task = :t"
                    ),
                    {"t": task},
                ).one()
                for task in ("card", "cv_anonimo")
            )
            assert cv[:4] == card[:4]
            assert cv.max_output_tokens == 16000
            assert cv.daily_cap_eur is None
            _usage(connection, "cv_anonimo")
        with pytest.raises(IntegrityError, match="ck_ai_usage_task"), engine.begin() as connection:
            _usage(connection, "altro")
        engine.dispose()


def test_upgrade_copies_a_changed_card_row(hub_postgres: Any) -> None:
    with hub_postgres.fresh_container() as container:
        url = container.get_connection_url()
        config = Config(str(INI_PATH))
        config.set_main_option("sqlalchemy.url", url)
        upgrade_to_head(url)
        command.downgrade(config, _parent(config))
        engine = create_engine(url, future=True)
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE ai_tasks SET model = 'claude-sonnet-5', effort = 'high', "
                    "enabled = true WHERE task = 'card'"
                )
            )
        command.upgrade(config, "head")
        with engine.connect() as connection:
            row = connection.execute(
                text("SELECT model, effort, enabled FROM ai_tasks WHERE task = 'cv_anonimo'")
            ).one()
            assert tuple(row) == ("claude-sonnet-5", "high", True)
        engine.dispose()


def test_upgrade_without_a_card_row_seeds_anthropic_defaults(hub_postgres: Any) -> None:
    with hub_postgres.fresh_container() as container:
        url = container.get_connection_url()
        config = Config(str(INI_PATH))
        config.set_main_option("sqlalchemy.url", url)
        upgrade_to_head(url)
        command.downgrade(config, _parent(config))
        engine = create_engine(url, future=True)
        with engine.begin() as connection:
            connection.execute(text("DELETE FROM ai_tasks WHERE task = 'card'"))
        command.upgrade(config, "head")
        with engine.connect() as connection:
            row = connection.execute(
                text(
                    "SELECT provider, model, effort, enabled, max_output_tokens "
                    "FROM ai_tasks WHERE task = 'cv_anonimo'"
                )
            ).one()
            assert tuple(row) == ("anthropic", "claude-opus-5", "medium", False, 16000)
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


def test_downgrade_drops_table_and_row(hub_postgres: Any) -> None:
    with hub_postgres.fresh_container() as container:
        url = container.get_connection_url()
        upgrade_to_head(url)
        config = Config(str(INI_PATH))
        config.set_main_option("sqlalchemy.url", url)
        engine = create_engine(url, future=True)
        with engine.begin() as connection:
            _usage(connection, "cv_anonimo")
            _usage(connection, "card")
        command.downgrade(config, _parent(config))
        with engine.connect() as connection:
            assert not _table_exists(connection)
            assert (
                connection.execute(
                    text("SELECT count(*) FROM ai_tasks WHERE task = 'cv_anonimo'")
                ).scalar_one()
                == 0
            )
            assert connection.execute(text("SELECT task FROM ai_usage")).scalars().all() == ["card"]
        for table, constraint in (
            ("ai_tasks", "ck_ai_tasks_task"),
            ("ai_usage", "ck_ai_usage_task"),
        ):
            with pytest.raises(IntegrityError, match=constraint), engine.begin() as connection:
                if table == "ai_tasks":
                    connection.execute(
                        text(
                            "INSERT INTO ai_tasks (task, provider, model, max_output_tokens) "
                            "VALUES ('cv_anonimo', 'anthropic', 'm', 1)"
                        )
                    )
                else:
                    _usage(connection, "cv_anonimo")
        command.upgrade(config, "head")
        with engine.connect() as connection:
            assert _table_exists(connection)
        engine.dispose()
