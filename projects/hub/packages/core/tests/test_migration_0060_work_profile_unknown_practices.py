"""0060 (REB-1144): the count of practices items dropped at intake."""

from typing import Any

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect

import rebase_core.models  # noqa: F401
from rebase_core.db import Base
from rebase_core.migrate import INI_PATH, upgrade_to_head

REVISION = "0060"


def _parent(config: Config) -> str:
    parent = ScriptDirectory.from_config(config).get_revision(REVISION).down_revision
    assert isinstance(parent, str)
    return parent


def _columns(engine: Any) -> set[str]:
    return {c["name"] for c in inspect(engine).get_columns("work_profiles")}


def test_revision_follows_the_unknown_technologies_count() -> None:
    assert _parent(Config(str(INI_PATH))) == "0059"


def test_the_column_is_added_with_a_zero_default_and_dropped_on_downgrade(
    hub_postgres: Any,
) -> None:
    with hub_postgres.fresh_container() as container:
        url = container.get_connection_url()
        config = Config(str(INI_PATH))
        config.set_main_option("sqlalchemy.url", url)
        upgrade_to_head(url)
        engine = create_engine(url, future=True)
        assert "unknown_practices" in _columns(engine)
        column = next(
            c
            for c in inspect(engine).get_columns("work_profiles")
            if c["name"] == "unknown_practices"
        )
        assert column["nullable"] is False
        assert str(column["default"]) == "0"
        command.downgrade(config, _parent(config))
        assert "unknown_practices" not in _columns(engine)
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
