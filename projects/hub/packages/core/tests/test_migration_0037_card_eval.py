"""0037 (REB-835): `card_eval_profiles`, `card_eval_runs` and `card_eval_results`; a
retried deploy lands on the same schema, the downgrade drops the three, and a person's
removal takes their expected card and every result written for them."""

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
from sqlalchemy.orm import Session

import rebase_core.models  # noqa: F401
from rebase_core.db import Base
from rebase_core.migrate import INI_PATH, head_revision, upgrade_to_head
from rebase_core.models import Freelancer, User

REVISION = "0037"
TABLES = {"card_eval_profiles", "card_eval_runs", "card_eval_results"}


def _parent(config: Config) -> str:
    """The revision this one revises, read from the script: the tests rewind to it, and a
    hard-coded number is the one that goes stale when another branch lands a revision
    first."""
    parent = ScriptDirectory.from_config(config).get_revision(REVISION).down_revision
    assert isinstance(parent, str)
    return parent


def _tables(connection: Connection) -> set[str]:
    return {
        name
        for (name,) in connection.execute(
            text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_name LIKE 'card\\_eval\\_%'"
            )
        )
    }


def _columns(connection: Connection, table: str) -> set[str]:
    return {
        name
        for (name,) in connection.execute(
            text("SELECT column_name FROM information_schema.columns WHERE table_name = :t"),
            {"t": table},
        )
    }


def test_migration_creates_tables_idempotently(hub_postgres: Any) -> None:
    with hub_postgres.fresh_container() as container:
        url = container.get_connection_url()
        upgrade_to_head(url)
        config = Config(str(INI_PATH))
        config.set_main_option("sqlalchemy.url", url)
        engine = create_engine(url, future=True)
        with engine.connect() as connection:
            assert _tables(connection) == TABLES
            assert _columns(connection, "card_eval_profiles") == {
                "freelancer_id",
                "label",
                "expected_card",
                "status",
                "corrected_by",
                "created_at",
                "updated_at",
            }
            assert _columns(connection, "card_eval_runs") == {
                "id",
                "task",
                "provider",
                "model",
                "effort",
                "prompt_version",
                "summary",
                "created_by",
                "created_at",
            }
            assert _columns(connection, "card_eval_results") == {
                "run_id",
                "freelancer_id",
                "label",
                "anni_ok",
                "seniority_ok",
                "guard_clean",
                "failure",
                "latency_ms",
                "cost_eur",
                "card",
                "ratings",
            }
            diff = compare_metadata(MigrationContext.configure(connection), Base.metadata)
            assert diff == [], diff
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
            assert _tables(connection) == TABLES
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


def test_an_accepted_profile_needs_a_card_and_a_person_takes_everything_with_them(
    hub_postgres: Any,
) -> None:
    with hub_postgres.fresh_container() as container:
        url = container.get_connection_url()
        upgrade_to_head(url)
        engine = create_engine(url, future=True)
        run_id = uuid4()
        with Session(engine) as session:
            user = User(email="a@b.it", nome="A", cognome="B", role="member")
            session.add(user)
            session.flush()
            freelancer = Freelancer(user_id=user.id, posizione="dev")
            session.add(freelancer)
            session.commit()
            freelancer_id = freelancer.id
        with pytest.raises(IntegrityError), engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO card_eval_profiles (freelancer_id, label, status) "
                    "VALUES (:f, 'lead', 'accettata')"
                ),
                {"f": freelancer_id},
            )
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO card_eval_profiles (freelancer_id, label, status, expected_card) "
                    "VALUES (:f, 'lead', 'accettata', '{}')"
                ),
                {"f": freelancer_id},
            )
            connection.execute(
                text(
                    "INSERT INTO card_eval_runs (id, task, provider, model, prompt_version, "
                    "summary) VALUES (:r, 'card', 'anthropic', 'claude-opus-5', 1, '{}')"
                ),
                {"r": run_id},
            )
            connection.execute(
                text(
                    "INSERT INTO card_eval_results (run_id, freelancer_id, label, card) "
                    "VALUES (:r, :f, 'lead', '{}')"
                ),
                {"r": run_id, "f": freelancer_id},
            )
        with engine.begin() as connection:
            connection.execute(text("DELETE FROM freelancers WHERE id = :f"), {"f": freelancer_id})
        with engine.connect() as connection:
            assert (
                connection.execute(text("SELECT count(*) FROM card_eval_profiles")).scalar_one()
                == 0
            )
            assert (
                connection.execute(text("SELECT count(*) FROM card_eval_results")).scalar_one() == 0
            )
            # The run itself, which holds only totals, stays.
            assert connection.execute(text("SELECT count(*) FROM card_eval_runs")).scalar_one() == 1
        engine.dispose()
