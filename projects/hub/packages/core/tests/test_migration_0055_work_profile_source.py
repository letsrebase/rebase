"""0055 (REB-1134): `ai_sources` takes a third switch, `work_profile`, seeded off. The column fits
the 12-character name, the check names the three kinds, a fourth is refused, and the migration
comes and goes."""

from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from rebase_core.migrate import INI_PATH, upgrade_to_head


def _switches(connection: Any) -> dict[str, bool]:
    rows = connection.execute(text("SELECT kind, enabled FROM ai_sources"))
    return {row[0]: row[1] for row in rows}


def test_the_work_profile_switch_is_seeded_off_and_the_migration_comes_and_goes(
    hub_postgres: Any,
) -> None:
    with hub_postgres.fresh_container() as container:
        url = container.get_connection_url()
        upgrade_to_head(url)
        config = Config(str(INI_PATH))
        config.set_main_option("sqlalchemy.url", url)
        engine = create_engine(url, future=True)
        with engine.connect() as connection:
            assert _switches(connection) == {"github": False, "links": False, "work_profile": False}
        command.downgrade(config, "0054")
        with engine.connect() as connection:
            assert _switches(connection) == {"github": False, "links": False}
        command.upgrade(config, "0055")
        command.downgrade(config, "0054")
        command.upgrade(config, "0055")
        with engine.connect() as connection:
            assert set(_switches(connection)) == {"github", "links", "work_profile"}
        engine.dispose()


def test_the_check_names_the_three_kinds_and_refuses_a_fourth(hub_postgres: Any) -> None:
    with hub_postgres.fresh_container() as container:
        url = container.get_connection_url()
        upgrade_to_head(url)
        engine = create_engine(url, future=True)
        with engine.begin() as connection:
            connection.execute(
                text("UPDATE ai_sources SET enabled = true WHERE kind = 'work_profile'")
            )
        with pytest.raises(IntegrityError), engine.begin() as connection:
            connection.execute(
                text("INSERT INTO ai_sources (kind, enabled) VALUES ('other', false)")
            )
        engine.dispose()
