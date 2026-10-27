"""0036 (REB-830): `ai_providers`, `ai_tasks`, `ai_prices` and `ai_usage`, seeded with the
three providers and the two tasks and no key; a retried deploy lands on the same schema,
and the downgrade drops the four."""

from typing import Any

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
from rebase_core.migrate import INI_PATH, head_revision, upgrade_to_head

REVISION = "0036"


def _parent(config: Config) -> str:
    """The revision this one revises, read from the script: the tests rewind to it, and a
    hard-coded number is the one that goes stale when another branch lands a revision
    first (REB-800 did)."""
    parent = ScriptDirectory.from_config(config).get_revision(REVISION).down_revision
    assert isinstance(parent, str)
    return parent


TABLES = {"ai_providers", "ai_tasks", "ai_prices", "ai_usage"}


def _tables(connection: Connection) -> set[str]:
    return {
        name
        for (name,) in connection.execute(
            text("SELECT table_name FROM information_schema.tables WHERE table_name LIKE 'ai\\_%'")
        )
    }


def _columns(connection: Connection, table: str) -> dict[str, tuple[str, str]]:
    return {
        name: (data_type, nullable)
        for name, data_type, nullable in connection.execute(
            text(
                "SELECT column_name, data_type, is_nullable FROM information_schema.columns "
                "WHERE table_name = :t"
            ),
            {"t": table},
        )
    }


def test_upgrade_creates_four_tables_and_columns(hub_postgres: Any) -> None:
    with hub_postgres.fresh_container() as container:
        url = container.get_connection_url()
        upgrade_to_head(url)
        engine = create_engine(url, future=True)
        with engine.connect() as connection:
            assert _tables(connection) == TABLES
            assert set(_columns(connection, "ai_providers")) == {
                "provider",
                "key_ciphertext",
                "key_nonce",
                "key_last4",
                "saved_by",
                "saved_at",
                "enabled",
            }
            assert set(_columns(connection, "ai_tasks")) == {
                "task",
                "provider",
                "model",
                "effort",
                "max_output_tokens",
                "daily_cap_eur",
                "enabled",
                "updated_at",
            }
            assert set(_columns(connection, "ai_prices")) == {
                "provider",
                "model",
                "input_eur_per_mtok",
                "output_eur_per_mtok",
                "thinking_eur_per_mtok",
                "cache_read_eur_per_mtok",
            }
            assert set(_columns(connection, "ai_usage")) == {
                "id",
                "at",
                "task",
                "purpose",
                "provider",
                "model",
                "input_tokens",
                "output_tokens",
                "thinking_tokens",
                "cache_read_tokens",
                "cost_eur",
                "priced",
                "freelancer_id",
            }
            index = connection.execute(
                text("SELECT indexdef FROM pg_indexes WHERE indexname = 'ix_ai_usage_task_at'")
            ).scalar_one()
            assert "(task, at)" in index
            # The seed: three providers, off, no key; two tasks on Anthropic.
            assert connection.execute(
                text(
                    "SELECT provider, enabled, key_ciphertext IS NULL FROM ai_providers "
                    "ORDER BY provider"
                )
            ).all() == [
                ("anthropic", False, True),
                ("google", False, True),
                ("openai", False, True),
            ]
            assert connection.execute(
                text(
                    "SELECT task, provider, model, effort, max_output_tokens, daily_cap_eur, "
                    "enabled FROM ai_tasks ORDER BY task"
                )
            ).all() == [
                ("card", "anthropic", "claude-opus-5", "medium", 2000, None, True),
                ("team_proposal", "anthropic", "claude-opus-5", "medium", 8000, None, True),
            ]
            assert connection.execute(text("SELECT count(*) FROM ai_prices")).scalar_one() == 0
            diff = compare_metadata(MigrationContext.configure(connection), Base.metadata)
            assert diff == [], diff
        engine.dispose()


def test_upgrade_is_idempotent(hub_postgres: Any) -> None:
    with hub_postgres.fresh_container() as container:
        url = container.get_connection_url()
        upgrade_to_head(url)
        config = Config(str(INI_PATH))
        config.set_main_option("sqlalchemy.url", url)
        engine = create_engine(url, future=True)
        with engine.begin() as connection:
            # An admin's change survives the revision running again over its own tables.
            connection.execute(text("UPDATE ai_tasks SET model = 'gpt-5' WHERE task = 'card'"))
            connection.execute(
                text("UPDATE alembic_version SET version_num = :parent"),
                {"parent": _parent(config)},
            )
        command.upgrade(config, "head")
        with engine.connect() as connection:
            assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar() == (
                head_revision()
            )
            assert connection.execute(text("SELECT count(*) FROM ai_providers")).scalar_one() == 3
            assert (
                connection.execute(
                    text("SELECT model FROM ai_tasks WHERE task = 'card'")
                ).scalar_one()
                == "gpt-5"
            )
        engine.dispose()


def test_downgrade_drops_them(hub_postgres: Any) -> None:
    with hub_postgres.fresh_container() as container:
        url = container.get_connection_url()
        upgrade_to_head(url)
        config = Config(str(INI_PATH))
        config.set_main_option("sqlalchemy.url", url)
        engine = create_engine(url, future=True)
        command.downgrade(config, _parent(config))
        with engine.connect() as connection:
            assert _tables(connection) == set()
        command.upgrade(config, "head")
        with engine.connect() as connection:
            assert _tables(connection) == TABLES
        engine.dispose()


def test_no_key_column_is_plain_text(hub_postgres: Any) -> None:
    with hub_postgres.fresh_container() as container:
        url = container.get_connection_url()
        upgrade_to_head(url)
        engine = create_engine(url, future=True)
        with engine.connect() as connection:
            columns = _columns(connection, "ai_providers")
            textual = {
                name
                for name, (data_type, _) in columns.items()
                if data_type in {"text", "character varying"}
            }
            # What is left as text holds the provider's name and the last four characters.
            assert textual == {"provider", "key_last4"}
            assert columns["key_ciphertext"][0] == "bytea"
            assert columns["key_nonce"][0] == "bytea"
            length = connection.execute(
                text(
                    "SELECT character_maximum_length FROM information_schema.columns "
                    "WHERE table_name = 'ai_providers' AND column_name = 'key_last4'"
                )
            ).scalar_one()
            assert length == 4
        engine.dispose()


def test_the_checks_refuse_what_the_code_never_writes(hub_postgres: Any) -> None:
    with hub_postgres.fresh_container() as container:
        url = container.get_connection_url()
        upgrade_to_head(url)
        engine = create_engine(url, future=True)
        with engine.begin() as connection:
            with pytest.raises(IntegrityError), connection.begin_nested():
                connection.execute(text("INSERT INTO ai_providers (provider) VALUES ('mistral')"))
            with pytest.raises(IntegrityError), connection.begin_nested():
                connection.execute(
                    text(
                        "UPDATE ai_providers SET key_ciphertext = '\\x00' WHERE provider = 'google'"
                    )
                )
            with pytest.raises(IntegrityError), connection.begin_nested():
                connection.execute(
                    text(
                        "INSERT INTO ai_usage (id, task, provider, model, priced, cost_eur) "
                        "VALUES (gen_random_uuid(), 'card', 'anthropic', 'm', true, NULL)"
                    )
                )
            with pytest.raises(IntegrityError), connection.begin_nested():
                connection.execute(
                    text(
                        "INSERT INTO ai_usage (id, task, provider, model, priced) "
                        "VALUES (gen_random_uuid(), 'eval', 'anthropic', 'm', false)"
                    )
                )
            with pytest.raises(IntegrityError), connection.begin_nested():
                connection.execute(text("UPDATE ai_tasks SET daily_cap_eur = -1"))
        engine.dispose()
