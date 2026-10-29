"""0043 (REB-855): `freelancer_cards.persona`, a nullable JSONB beside the card; a card
written before it reads `NULL`, a retried deploy lands on the same schema, and the
downgrade drops the column and nothing else."""

from typing import Any

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Connection, create_engine, text
from sqlalchemy.orm import Session

import rebase_core.models  # noqa: F401
from rebase_core.db import Base
from rebase_core.migrate import INI_PATH, head_revision, upgrade_to_head
from rebase_core.models import Freelancer, FreelancerCard, User

REVISION = "0043"


def _parent(config: Config) -> str:
    parent = ScriptDirectory.from_config(config).get_revision(REVISION).down_revision
    assert isinstance(parent, str)
    return parent


def _persona_column(connection: Connection) -> tuple[str, str] | None:
    row = connection.execute(
        text(
            "SELECT data_type, is_nullable FROM information_schema.columns "
            "WHERE table_name = 'freelancer_cards' AND column_name = 'persona'"
        )
    ).one_or_none()
    return (row[0], row[1]) if row is not None else None


def test_revision_follows_the_notes() -> None:
    assert _parent(Config(str(INI_PATH))) == "0042"


def test_upgrade_adds_a_nullable_jsonb_and_matches_the_models(hub_postgres: Any) -> None:
    with hub_postgres.fresh_container() as container:
        url = container.get_connection_url()
        upgrade_to_head(url)
        engine = create_engine(url, future=True)
        with engine.connect() as connection:
            assert _persona_column(connection) == ("jsonb", "YES")
            diff = compare_metadata(MigrationContext.configure(connection), Base.metadata)
            assert diff == [], diff
        engine.dispose()


def test_upgrade_is_idempotent_and_keeps_what_the_column_holds(hub_postgres: Any) -> None:
    with hub_postgres.fresh_container() as container:
        url = container.get_connection_url()
        upgrade_to_head(url)
        config = Config(str(INI_PATH))
        config.set_main_option("sqlalchemy.url", url)
        engine = create_engine(url, future=True)
        with Session(engine) as session:
            user = User(email="a@x.it", nome="A", cognome="B")
            session.add(user)
            session.flush()
            row = Freelancer(user_id=user.id)
            session.add(row)
            session.flush()
            session.add(FreelancerCard(freelancer_id=row.id, persona={"email": "ada@example.org"}))
            session.commit()
        with engine.begin() as connection:
            connection.execute(
                text("UPDATE alembic_version SET version_num = :parent"),
                {"parent": _parent(config)},
            )
        command.upgrade(config, "head")
        with engine.connect() as connection:
            assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar() == (
                head_revision()
            )
            assert (
                connection.execute(
                    text("SELECT persona->>'email' FROM freelancer_cards")
                ).scalar_one()
                == "ada@example.org"
            )
        engine.dispose()


def test_downgrade_drops_only_the_column(hub_postgres: Any) -> None:
    with hub_postgres.fresh_container() as container:
        url = container.get_connection_url()
        upgrade_to_head(url)
        config = Config(str(INI_PATH))
        config.set_main_option("sqlalchemy.url", url)
        engine = create_engine(url, future=True)
        command.downgrade(config, _parent(config))
        with engine.connect() as connection:
            assert _persona_column(connection) is None
            assert (
                connection.execute(
                    text(
                        "SELECT count(*) FROM information_schema.columns "
                        "WHERE table_name = 'freelancer_cards' AND column_name = 'prompt_version'"
                    )
                ).scalar_one()
                == 1
            )
        command.upgrade(config, "head")
        with engine.connect() as connection:
            assert _persona_column(connection) == ("jsonb", "YES")
        engine.dispose()
