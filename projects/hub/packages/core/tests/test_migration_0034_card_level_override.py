"""0034 (REB-799): `freelancers.seniority_admin` and `anni_admin`, nullable, pinned to the
card's own words and to 0..60; a retried deploy lands on the same schema, and the
downgrade drops them."""

from typing import Any

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import Connection, create_engine, text
from sqlalchemy.exc import IntegrityError

import rebase_core.models  # noqa: F401
from rebase_core.db import Base
from rebase_core.migrate import INI_PATH, head_revision, upgrade_to_head


def _columns(connection: Connection) -> set[str]:
    return {
        column
        for (column,) in connection.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'freelancers' "
                "AND column_name IN ('seniority_admin', 'anni_admin')"
            )
        )
    }


def _freelancer(connection: Connection) -> str:
    user = connection.execute(
        text(
            "INSERT INTO users (id, email, nome, cognome, role, attivo, created_at, "
            "updated_at) VALUES (gen_random_uuid(), 'level@studio.it', 'A', 'B', 'member', "
            "true, now(), now()) RETURNING id"
        )
    ).scalar_one()
    return str(
        connection.execute(
            text(
                "INSERT INTO freelancers (id, user_id, stato, links, compilata_da, "
                "created_at, updated_at) VALUES (gen_random_uuid(), :u, 'nuovo', '[]', "
                "'persona', now(), now()) RETURNING id"
            ),
            {"u": user},
        ).scalar_one()
    )


def test_migration_0034_adds_the_admins_level_pinned_to_the_cards_words(
    hub_postgres: Any,
) -> None:
    with hub_postgres.fresh_container() as container:
        url = container.get_connection_url()
        upgrade_to_head(url)
        config = Config(str(INI_PATH))
        config.set_main_option("sqlalchemy.url", url)
        engine = create_engine(url, future=True)
        command.downgrade(config, "0033")
        with engine.begin() as connection:
            assert _columns(connection) == set()
            freelancer = _freelancer(connection)
        for _ in range(2):  # a retried deploy runs the same revision again
            command.upgrade(config, "head")
            with engine.begin() as connection:
                assert _columns(connection) == {"seniority_admin", "anni_admin"}
                assert connection.execute(
                    text("SELECT seniority_admin, anni_admin FROM freelancers WHERE id = :i"),
                    {"i": freelancer},
                ).one() == (None, None)
                connection.execute(
                    text("UPDATE freelancers SET seniority_admin = 'lead', anni_admin = 60")
                )
                with pytest.raises(IntegrityError), connection.begin_nested():
                    connection.execute(text("UPDATE freelancers SET seniority_admin = 'guru'"))
                with pytest.raises(IntegrityError), connection.begin_nested():
                    connection.execute(text("UPDATE freelancers SET anni_admin = 61"))
                with pytest.raises(IntegrityError), connection.begin_nested():
                    connection.execute(text("UPDATE freelancers SET anni_admin = -1"))
            command.downgrade(config, "0033")
            with engine.connect() as connection:
                assert _columns(connection) == set()
        command.upgrade(config, "head")
        with engine.begin() as connection:
            # The revision again over the columns it already added.
            connection.execute(text("UPDATE alembic_version SET version_num = '0033'"))
        command.upgrade(config, "head")
        with engine.connect() as connection:
            assert (
                connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
                == head_revision()
            )
            diff = compare_metadata(MigrationContext.configure(connection), Base.metadata)
            assert diff == [], diff
        engine.dispose()
