"""0039 (REB-842): the evaluation set keeps only who is in it and why, a result keeps what the
checks found instead of an admin's ratings; the expected cards go, the people and every
earlier result stay, and a retried deploy lands on the same schema."""

from typing import Any
from uuid import uuid4

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
from rebase_core.models import Freelancer, User

REVISION = "0039"


def _parent(config: Config) -> str:
    """The revision this one revises, read from the script: the tests rewind to it, and a
    hard-coded number is the one that goes stale when another branch lands a revision
    first."""
    parent = ScriptDirectory.from_config(config).get_revision(REVISION).down_revision
    assert isinstance(parent, str)
    return parent


def _columns(connection: Connection, table: str) -> set[str]:
    return {
        name
        for (name,) in connection.execute(
            text("SELECT column_name FROM information_schema.columns WHERE table_name = :t"),
            {"t": table},
        )
    }


PROFILE_COLUMNS = {"freelancer_id", "label", "created_at"}
RESULT_COLUMNS = {
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
    "checks",
    "judgement",
    "judge_latency_ms",
    "judge_cost_eur",
}


def test_the_schema_matches_the_models_and_a_retried_deploy_lands_on_it(hub_postgres: Any) -> None:
    with hub_postgres.fresh_container() as container:
        url = container.get_connection_url()
        upgrade_to_head(url)
        config = Config(str(INI_PATH))
        config.set_main_option("sqlalchemy.url", url)
        engine = create_engine(url, future=True)
        with engine.connect() as connection:
            assert _columns(connection, "card_eval_profiles") == PROFILE_COLUMNS
            assert _columns(connection, "card_eval_results") == RESULT_COLUMNS
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
            assert _columns(connection, "card_eval_profiles") == PROFILE_COLUMNS
            assert _columns(connection, "card_eval_results") == RESULT_COLUMNS
        engine.dispose()


def test_the_people_and_the_earlier_results_stay_and_the_expected_cards_go(
    hub_postgres: Any,
) -> None:
    with hub_postgres.fresh_container() as container:
        url = container.get_connection_url()
        config = Config(str(INI_PATH))
        config.set_main_option("sqlalchemy.url", url)
        command.upgrade(config, _parent(config))
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
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO card_eval_profiles (freelancer_id, label, status, expected_card) "
                    "VALUES (:f, 'senza date', 'accettata', '{\"anni\": 9}')"
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
                    "INSERT INTO card_eval_results (run_id, freelancer_id, label, anni_ok, "
                    "cost_eur, card, ratings) VALUES (:r, :f, 'senza date', true, 0.05, '{}', "
                    "'{\"ruolo\": 3}')"
                ),
                {"r": run_id, "f": freelancer_id},
            )

        command.upgrade(config, "head")

        with engine.connect() as connection:
            profile = connection.execute(
                text("SELECT freelancer_id, label FROM card_eval_profiles")
            ).one()
            assert (profile.freelancer_id, profile.label) == (freelancer_id, "senza date")
            result = connection.execute(
                text(
                    "SELECT anni_ok, cost_eur, checks, judgement, judge_cost_eur "
                    "FROM card_eval_results"
                )
            ).one()
            assert (result.anni_ok, float(result.cost_eur)) == (True, 0.05)
            assert (result.checks, result.judgement, result.judge_cost_eur) == (None, None, None)
            assert connection.execute(text("SELECT count(*) FROM card_eval_runs")).scalar() == 1
        engine.dispose()


def test_downgrade_puts_the_columns_back_empty(hub_postgres: Any) -> None:
    with hub_postgres.fresh_container() as container:
        url = container.get_connection_url()
        upgrade_to_head(url)
        config = Config(str(INI_PATH))
        config.set_main_option("sqlalchemy.url", url)
        engine = create_engine(url, future=True)
        command.downgrade(config, _parent(config))
        with engine.connect() as connection:
            assert _columns(connection, "card_eval_profiles") == {
                "freelancer_id",
                "label",
                "expected_card",
                "status",
                "corrected_by",
                "created_at",
                "updated_at",
            }
            assert "ratings" in _columns(connection, "card_eval_results")
            assert "judgement" not in _columns(connection, "card_eval_results")
        command.upgrade(config, "head")
        with engine.connect() as connection:
            assert _columns(connection, "card_eval_profiles") == PROFILE_COLUMNS
        engine.dispose()
