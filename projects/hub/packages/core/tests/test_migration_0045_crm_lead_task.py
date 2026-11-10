"""0045 (REB-867): the `crm_lead` task, seeded from the `team_proposal` row and switched off;
a downgrade takes it away and puts the two CHECKs back."""

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

REVISION = "0045"


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


def test_revision_follows_the_anonymous_cvs() -> None:
    assert _parent(Config(str(INI_PATH))) == "0044"


def test_upgrade_seeds_the_task_off(hub_postgres: Any) -> None:
    with hub_postgres.fresh_container() as container:
        url = container.get_connection_url()
        upgrade_to_head(url)
        engine = create_engine(url, future=True)
        with engine.begin() as connection:
            team, lead = (
                connection.execute(
                    text(
                        "SELECT provider, model, effort, enabled, max_output_tokens, "
                        "daily_cap_eur FROM ai_tasks WHERE task = :t"
                    ),
                    {"t": task},
                ).one()
                for task in ("team_proposal", "crm_lead")
            )
            assert lead[:2] == team[:2]
            assert (lead.effort, lead.enabled, lead.max_output_tokens) == ("low", False, 1500)
            assert lead.daily_cap_eur == 1
            _usage(connection, "crm_lead")
        with pytest.raises(IntegrityError, match="ck_ai_usage_task"), engine.begin() as connection:
            _usage(connection, "altro")
        engine.dispose()


def test_upgrade_copies_a_changed_team_proposal_row(hub_postgres: Any) -> None:
    with hub_postgres.fresh_container() as container:
        url = container.get_connection_url()
        config = Config(str(INI_PATH))
        config.set_main_option("sqlalchemy.url", url)
        upgrade_to_head(url)
        command.downgrade(config, _parent(config))
        engine = create_engine(url, future=True)
        with engine.begin() as connection:
            connection.execute(
                text("UPDATE ai_tasks SET model = 'claude-sonnet-5' WHERE task = 'team_proposal'")
            )
        command.upgrade(config, "head")
        with engine.connect() as connection:
            row = connection.execute(
                text("SELECT model, enabled FROM ai_tasks WHERE task = 'crm_lead'")
            ).one()
            assert tuple(row) == ("claude-sonnet-5", False)
        engine.dispose()


def test_upgrade_without_a_team_proposal_row_seeds_anthropic_defaults(hub_postgres: Any) -> None:
    with hub_postgres.fresh_container() as container:
        url = container.get_connection_url()
        config = Config(str(INI_PATH))
        config.set_main_option("sqlalchemy.url", url)
        upgrade_to_head(url)
        command.downgrade(config, _parent(config))
        engine = create_engine(url, future=True)
        with engine.begin() as connection:
            connection.execute(text("DELETE FROM ai_tasks WHERE task = 'team_proposal'"))
        command.upgrade(config, "head")
        with engine.connect() as connection:
            row = connection.execute(
                text(
                    "SELECT provider, model, effort, enabled FROM ai_tasks WHERE task = 'crm_lead'"
                )
            ).one()
            assert tuple(row) == ("anthropic", "claude-opus-5", "low", False)
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


def test_downgrade_drops_the_row_and_its_usage(hub_postgres: Any) -> None:
    with hub_postgres.fresh_container() as container:
        url = container.get_connection_url()
        upgrade_to_head(url)
        config = Config(str(INI_PATH))
        config.set_main_option("sqlalchemy.url", url)
        engine = create_engine(url, future=True)
        with engine.begin() as connection:
            _usage(connection, "crm_lead")
            _usage(connection, "card")
        command.downgrade(config, _parent(config))
        with engine.connect() as connection:
            assert (
                connection.execute(
                    text("SELECT count(*) FROM ai_tasks WHERE task = 'crm_lead'")
                ).scalar_one()
                == 0
            )
            assert connection.execute(text("SELECT task FROM ai_usage")).scalars().all() == ["card"]
        with pytest.raises(IntegrityError, match="ck_ai_tasks_task"), engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO ai_tasks (task, provider, model, max_output_tokens) "
                    "VALUES ('crm_lead', 'anthropic', 'm', 1)"
                )
            )
        command.upgrade(config, "head")
        engine.dispose()
