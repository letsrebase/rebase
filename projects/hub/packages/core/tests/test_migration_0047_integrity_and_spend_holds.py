"""0047 (REB-889/891/892): spend holds, the stored CV hash, token expiry and the `CHECK`s on
the closed value sets that only the schemas guarded."""

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

REVISION = "0047"


def _parent(config: Config) -> str:
    parent = ScriptDirectory.from_config(config).get_revision(REVISION).down_revision
    assert isinstance(parent, str)
    return parent


def _user(connection: Any, *, legacy: bool = False) -> Any:
    user_id = uuid4()
    columns = "nome, cognome, attivo" if legacy else "first_name, last_name, active"
    connection.execute(
        text(
            f"INSERT INTO users (id, email, {columns}, role) "
            "VALUES (:id, :e, 'A', 'B', true, 'member')"
        ),
        {"id": user_id, "e": f"{user_id}@acme.it"},
    )
    return user_id


def _freelancer(
    connection: Any, *, status: str = "new", cv: bytes | None = None, legacy: bool = False
) -> Any:
    """`legacy` writes the row with the Italian names of the schema before 0049."""
    freelancer_id = uuid4()
    columns, filled_by = (
        ("stato, compilata_da", "persona") if legacy else ("status, filled_by", "person")
    )
    connection.execute(
        text(
            f"INSERT INTO freelancers (id, user_id, {columns}, links, cv_bytes) "
            f"VALUES (:id, :u, :s, '{filled_by}', '[]', :cv)"
        ),
        {"id": freelancer_id, "u": _user(connection, legacy=legacy), "s": status, "cv": cv},
    )
    return freelancer_id


def test_revision_follows_the_company_crm_link() -> None:
    assert _parent(Config(str(INI_PATH))) == "0046"


def test_the_cv_hash_is_stored_by_the_database_and_follows_the_bytes(hub_postgres: Any) -> None:
    with hub_postgres.fresh_container() as container:
        url = container.get_connection_url()
        upgrade_to_head(url)
        engine = create_engine(url, future=True)
        with engine.begin() as connection:
            with_cv = _freelancer(connection, cv=b"%PDF-a")
            without = _freelancer(connection)
            connection.execute(
                text("UPDATE freelancers SET cv_bytes = :b WHERE id = :id"),
                {"b": b"%PDF-b", "id": with_cv},
            )
            digests = dict(connection.execute(text("SELECT id, cv_sha256 FROM freelancers")).all())
            expected = connection.execute(
                text("SELECT encode(sha256(:b), 'hex')"), {"b": b"%PDF-b"}
            ).scalar_one()
        assert digests == {with_cv: expected, without: None}
        engine.dispose()


def test_a_value_outside_its_closed_set_is_refused(hub_postgres: Any) -> None:
    with hub_postgres.fresh_container() as container:
        url = container.get_connection_url()
        upgrade_to_head(url)
        engine = create_engine(url, future=True)
        with engine.begin() as connection:
            _freelancer(connection, status="active")
        with pytest.raises(IntegrityError, match="ck_freelancers_status"), engine.begin() as c:
            _freelancer(c, status="altro")
        engine.dispose()


def test_a_legacy_row_outside_the_set_does_not_stop_the_upgrade(hub_postgres: Any) -> None:
    """Upgraded as far as 0048: 0049 renames the CHECK and validates it, which a row outside
    the set (never converted, since 0049 only maps the known values) would stop."""
    with hub_postgres.fresh_container() as container:
        url = container.get_connection_url()
        config = Config(str(INI_PATH))
        config.set_main_option("sqlalchemy.url", url)
        upgrade_to_head(url)
        command.downgrade(config, _parent(config))
        engine = create_engine(url, future=True)
        with engine.begin() as connection:
            _freelancer(connection, status="vecchio", legacy=True)
        command.upgrade(config, "0048")
        with engine.begin() as connection:
            enforced = connection.execute(
                text(
                    "SELECT convalidated FROM pg_constraint WHERE conname = 'ck_freelancers_stato'"
                )
            ).scalar_one()
        assert enforced is False
        with pytest.raises(IntegrityError, match="ck_freelancers_stato"), engine.begin() as c:
            _freelancer(c, status="altro", legacy=True)
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


def test_downgrade_takes_the_additions_away(hub_postgres: Any) -> None:
    with hub_postgres.fresh_container() as container:
        url = container.get_connection_url()
        upgrade_to_head(url)
        config = Config(str(INI_PATH))
        config.set_main_option("sqlalchemy.url", url)
        command.downgrade(config, _parent(config))
        engine = create_engine(url, future=True)
        with engine.connect() as connection:
            columns = set(
                connection.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns WHERE "
                        "(table_name, column_name) IN (('freelancers', 'cv_sha256'), "
                        "('admin_tokens', 'expires_at'), ('ai_usage', 'reserved_until'))"
                    )
                ).scalars()
            )
            constraints = connection.execute(
                text("SELECT count(*) FROM pg_constraint WHERE conname = 'ck_freelancers_stato'")
            ).scalar_one()
        assert columns == set()
        assert constraints == 0
        engine.dispose()


def test_downgrade_does_not_turn_a_spend_hold_into_spend(hub_postgres: Any) -> None:
    """A hold is a reservation: the older code would count the surviving row as a real call
    for the rest of the day."""
    with hub_postgres.fresh_container() as container:
        url = container.get_connection_url()
        upgrade_to_head(url)
        engine = create_engine(url, future=True)
        with engine.begin() as connection:
            for held in ("now() + interval '5 minutes'", "NULL"):
                connection.execute(
                    text(
                        "INSERT INTO ai_usage (id, task, purpose, provider, model, priced, "
                        f"cost_eur, reserved_until) VALUES (:id, 'card', 'run', 'anthropic', "
                        f"'m', true, 5, {held})"
                    ),
                    {"id": uuid4()},
                )
        config = Config(str(INI_PATH))
        config.set_main_option("sqlalchemy.url", url)
        command.downgrade(config, _parent(config))
        with engine.connect() as connection:
            assert connection.execute(text("SELECT count(*) FROM ai_usage")).scalar_one() == 1
        engine.dispose()
