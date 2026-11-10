"""0048 (REB-889 review): which public door asked for a magic link."""

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

REVISION = "0048"


def _parent(config: Config) -> str:
    parent = ScriptDirectory.from_config(config).get_revision(REVISION).down_revision
    assert isinstance(parent, str)
    return parent


def _token(connection: Any, user_id: Any, asked_via: str | None) -> None:
    connection.execute(
        text(
            "INSERT INTO magic_link_tokens (id, user_id, token_hash, expires_at, asked_via) "
            "VALUES (:id, :u, :h, now() + interval '15 minutes', :via)"
        ),
        {"id": uuid4(), "u": user_id, "h": uuid4().hex * 2, "via": asked_via},
    )


def test_revision_follows_the_integrity_checks() -> None:
    assert _parent(Config(str(INI_PATH))) == "0047"


def test_a_token_names_its_door_or_none_and_nothing_else(hub_postgres: Any) -> None:
    with hub_postgres.fresh_container() as container:
        url = container.get_connection_url()
        upgrade_to_head(url)
        engine = create_engine(url, future=True)
        user_id = uuid4()
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO users (id, email, first_name, last_name, role, active) "
                    "VALUES (:id, 'a@acme.it', 'A', 'B', 'member', true)"
                ),
                {"id": user_id},
            )
            for door in ("login", "wizard", None):
                _token(connection, user_id, door)
        with (
            pytest.raises(IntegrityError, match="ck_magic_link_tokens_asked_via"),
            engine.begin() as c,
        ):
            _token(c, user_id, "altro")
        engine.dispose()


def test_a_token_written_before_the_migration_counts_for_no_door(hub_postgres: Any) -> None:
    with hub_postgres.fresh_container() as container:
        url = container.get_connection_url()
        config = Config(str(INI_PATH))
        config.set_main_option("sqlalchemy.url", url)
        upgrade_to_head(url)
        command.downgrade(config, _parent(config))
        engine = create_engine(url, future=True)
        user_id = uuid4()
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO users (id, email, nome, cognome, role, attivo) "
                    "VALUES (:id, 'a@acme.it', 'A', 'B', 'member', true)"
                ),
                {"id": user_id},
            )
            connection.execute(
                text(
                    "INSERT INTO magic_link_tokens (id, user_id, token_hash, expires_at) "
                    "VALUES (:id, :u, :h, now() + interval '15 minutes')"
                ),
                {"id": uuid4(), "u": user_id, "h": "h" * 64},
            )
        command.upgrade(config, "head")
        with engine.connect() as connection:
            assert connection.execute(
                text("SELECT asked_via FROM magic_link_tokens")
            ).scalars().all() == [None]
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
