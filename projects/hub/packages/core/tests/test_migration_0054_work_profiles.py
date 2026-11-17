"""0054 (REB-1133, REB-1124): the two tables of the CLI's sign-in and the profile it sends. Both
come and go with the migration, a code is unique only while its row is pending, and a freelancer
has one profile."""

from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from rebase_core.migrate import INI_PATH, upgrade_to_head

TABLES = ("cli_authorizations", "work_profiles")


def _tables(connection: Any) -> set[str]:
    rows = connection.execute(
        text("SELECT table_name FROM information_schema.tables WHERE table_name = ANY(:names)"),
        {"names": list(TABLES)},
    )
    return {row[0] for row in rows}


def test_migration_0054_creates_and_drops_both_tables_and_can_run_again(hub_postgres: Any) -> None:
    with hub_postgres.fresh_container() as container:
        url = container.get_connection_url()
        upgrade_to_head(url)
        config = Config(str(INI_PATH))
        config.set_main_option("sqlalchemy.url", url)
        engine = create_engine(url, future=True)
        with engine.connect() as connection:
            assert _tables(connection) == set(TABLES)
        command.downgrade(config, "0053")
        with engine.connect() as connection:
            assert _tables(connection) == set()
        command.upgrade(config, "0054")
        command.downgrade(config, "0053")
        command.upgrade(config, "0054")
        with engine.connect() as connection:
            assert _tables(connection) == set(TABLES)
        engine.dispose()


def test_a_user_code_is_unique_only_while_its_row_is_pending(hub_postgres: Any) -> None:
    with hub_postgres.fresh_container() as container:
        url = container.get_connection_url()
        upgrade_to_head(url)
        engine = create_engine(url, future=True)

        def insert(connection: Any, n: int, state: str) -> None:
            connection.execute(
                text(
                    "INSERT INTO cli_authorizations (id, device_code_hash, user_code, scope, "
                    "state, expires_at) VALUES (gen_random_uuid(), :h, 'ABCD-EFGH', "
                    "'work_profile', :s, now())"
                ),
                {"h": f"hash-{n}", "s": state},
            )

        with engine.connect() as connection:
            outer = connection.begin()
            insert(connection, 1, "pending")
            insert(connection, 2, "denied")  # the same code, no longer pending
            with pytest.raises(IntegrityError), connection.begin_nested():
                insert(connection, 3, "pending")
            outer.rollback()
        engine.dispose()
