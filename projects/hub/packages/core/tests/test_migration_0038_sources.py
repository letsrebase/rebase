"""0038 (REB-837): `freelancer_sources`, `ai_sources` (seeded off) and the card's
provenance columns; a retried deploy lands on the same schema, a person's delete takes
their sources, and the downgrade drops what the revision added."""

from typing import Any

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Connection, create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import rebase_core.models  # noqa: F401
from rebase_core.db import Base
from rebase_core.migrate import INI_PATH, head_revision, upgrade_to_head
from rebase_core.models import Freelancer, User

REVISION = "0038"
CARD_COLUMNS = {
    "prompt_version": "integer",
    "provider": "character varying",
    "fonti": "jsonb",
    "source_hashes": "jsonb",
    "conflicts": "jsonb",
    "thinking_tokens": "integer",
}


def _parent(config: Config) -> str:
    """The revision this one revises, read from the script: the tests rewind to it, and a
    hard-coded number is the one that goes stale when another branch lands one first."""
    parent = ScriptDirectory.from_config(config).get_revision(REVISION).down_revision
    assert isinstance(parent, str)
    return parent


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


def _tables(connection: Connection) -> set[str]:
    return {
        name
        for (name,) in connection.execute(
            text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_name IN ('freelancer_sources', 'ai_sources')"
            )
        )
    }


def test_revision_follows_the_evaluation_set() -> None:
    config = Config(str(INI_PATH))
    assert _parent(config) == "0037"


def test_upgrade_creates_tables_columns_and_seed(hub_postgres: Any) -> None:
    with hub_postgres.fresh_container() as container:
        url = container.get_connection_url()
        upgrade_to_head(url)
        engine = create_engine(url, future=True)
        with engine.connect() as connection:
            assert _tables(connection) == {"freelancer_sources", "ai_sources"}
            assert set(_columns(connection, "freelancer_sources")) == {
                "id",
                "freelancer_id",
                "kind",
                "ref",
                "position",
                "text",
                "content_sha256",
                "status",
                "fetched_at",
            }
            assert set(_columns(connection, "ai_sources")) == {
                "kind",
                "enabled",
                "updated_by",
                "updated_at",
            }
            card = _columns(connection, "freelancer_cards")
            for name, data_type in CARD_COLUMNS.items():
                assert card[name] == (data_type, "YES"), name
            # Both switches exist, and both are off: nothing reads a third party's page
            # until an admin says so.
            assert connection.execute(
                text("SELECT kind, enabled FROM ai_sources ORDER BY kind")
            ).all() == [("github", False), ("links", False)]
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
            # An admin's switch survives the revision running again over its own tables.
            connection.execute(text("UPDATE ai_sources SET enabled = true WHERE kind = 'github'"))
            connection.execute(
                text("UPDATE alembic_version SET version_num = :parent"),
                {"parent": _parent(config)},
            )
        command.upgrade(config, "head")
        with engine.connect() as connection:
            assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar() == (
                head_revision()
            )
            assert connection.execute(
                text("SELECT kind, enabled FROM ai_sources ORDER BY kind")
            ).all() == [("github", True), ("links", False)]
        engine.dispose()


def test_downgrade_drops_what_it_added(hub_postgres: Any) -> None:
    with hub_postgres.fresh_container() as container:
        url = container.get_connection_url()
        upgrade_to_head(url)
        config = Config(str(INI_PATH))
        config.set_main_option("sqlalchemy.url", url)
        engine = create_engine(url, future=True)
        command.downgrade(config, _parent(config))
        with engine.connect() as connection:
            assert _tables(connection) == set()
            assert not set(CARD_COLUMNS) & set(_columns(connection, "freelancer_cards"))
            assert "cv_sha256" in _columns(connection, "freelancer_cards")
        command.upgrade(config, "head")
        with engine.connect() as connection:
            assert _tables(connection) == {"freelancer_sources", "ai_sources"}
            assert set(CARD_COLUMNS) <= set(_columns(connection, "freelancer_cards"))
        engine.dispose()


def test_constraints_hold(hub_postgres: Any) -> None:
    with hub_postgres.fresh_container() as container:
        url = container.get_connection_url()
        upgrade_to_head(url)
        engine = create_engine(url, future=True)
        with Session(engine) as session:
            user = User(email="a@x.it", nome="A", cognome="B")
            session.add(user)
            session.flush()
            row = Freelancer(user_id=user.id)
            session.add(row)
            session.commit()
            person = row.id
        insert = text(
            "INSERT INTO freelancer_sources (id, freelancer_id, kind, ref, position, status, "
            "fetched_at) VALUES (gen_random_uuid(), :f, :k, :r, 1, :s, now())"
        )
        with engine.begin() as connection:
            connection.execute(
                insert, {"f": person, "k": "link", "r": "https://a.example/", "s": "ok"}
            )
        for params in (
            {"f": person, "k": "link", "r": "https://a.example/", "s": "ok"},  # duplicate
            {"f": person, "k": "linkedin", "r": "https://b.example/", "s": "ok"},  # kind
            {"f": person, "k": "link", "r": "https://b.example/", "s": "weird"},  # status
        ):
            with pytest.raises(IntegrityError), engine.begin() as connection:
                connection.execute(insert, params)
        with engine.begin() as connection:
            connection.execute(text("DELETE FROM freelancers WHERE id = :f"), {"f": person})
            assert (
                connection.execute(text("SELECT count(*) FROM freelancer_sources")).scalar_one()
                == 0
            )
        engine.dispose()
