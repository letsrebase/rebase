"""0046 (REB-868): the CRM link columns on `companies`, with no backfill: a request written
before the migration keeps a `NULL` state, so the sweep never picks it up."""

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

REVISION = "0046"

COLUMNS = {
    "crm_stato": ("character varying", "YES"),
    "crm_customer_id": ("uuid", "YES"),
    "crm_deal_id": ("uuid", "YES"),
    "crm_deal_url": ("text", "YES"),
    "crm_errore": ("text", "YES"),
    "crm_attempted_at": ("timestamp with time zone", "YES"),
    "crm_bozza": ("jsonb", "YES"),
}


def _parent(config: Config) -> str:
    parent = ScriptDirectory.from_config(config).get_revision(REVISION).down_revision
    assert isinstance(parent, str)
    return parent


def _columns(connection: Any) -> dict[str, tuple[str, str]]:
    return {
        name: (data_type, nullable)
        for name, data_type, nullable in connection.execute(
            text(
                "SELECT column_name, data_type, is_nullable FROM information_schema.columns "
                "WHERE table_name = 'companies' AND column_name LIKE 'crm\\_%'"
            )
        )
    }


def _company(connection: Any, crm_stato: str | None) -> None:
    user_id = uuid4()
    connection.execute(
        text(
            "INSERT INTO users (id, email, nome, cognome, role, attivo) "
            "VALUES (:id, :e, 'A', 'B', 'member', true)"
        ),
        {"id": user_id, "e": f"{user_id}@acme.it"},
    )
    connection.execute(
        text(
            "INSERT INTO companies (id, user_id, nome_azienda, figura_richiesta, progetto, "
            "periodo_da, durata, budget_giornaliero, remoto, numero_risorse, stato, crm_stato) "
            "VALUES (:id, :u, 'Acme', 'Dev', 'p', '2026-12-01', '3 mesi', 100, 'remoto', 1, "
            "'nuovo', :s)"
        ),
        {"id": uuid4(), "u": user_id, "s": crm_stato},
    )


def test_revision_follows_the_crm_lead_task() -> None:
    assert _parent(Config(str(INI_PATH))) == "0045"


def test_upgrade_adds_the_columns_and_the_check_and_leaves_old_rows_null(
    hub_postgres: Any,
) -> None:
    with hub_postgres.fresh_container() as container:
        url = container.get_connection_url()
        config = Config(str(INI_PATH))
        config.set_main_option("sqlalchemy.url", url)
        upgrade_to_head(url)
        command.downgrade(config, _parent(config))
        engine = create_engine(url, future=True)
        with engine.begin() as connection:
            assert _columns(connection) == {}
        command.upgrade(config, "head")
        with engine.begin() as connection:
            assert _columns(connection) == COLUMNS
            _company(connection, "da_collegare")
            _company(connection, None)
        with pytest.raises(IntegrityError, match="ck_companies_crm_stato"), engine.begin() as c:
            _company(c, "altro")
        engine.dispose()


def test_a_request_written_before_the_migration_is_not_backfilled(hub_postgres: Any) -> None:
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
                    "INSERT INTO companies (id, user_id, nome_azienda, figura_richiesta, "
                    "progetto, periodo_da, durata, budget_giornaliero, remoto, numero_risorse, "
                    "stato) "
                    "VALUES (:id, :u, 'Acme', 'Dev', 'p', '2026-12-01', '3 mesi', 100, "
                    "'remoto', 1, 'nuovo')"
                ),
                {"id": uuid4(), "u": user_id},
            )
        command.upgrade(config, "head")
        with engine.connect() as connection:
            assert connection.execute(text("SELECT crm_stato FROM companies")).scalars().all() == [
                None
            ]
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


def test_downgrade_takes_the_columns_away(hub_postgres: Any) -> None:
    with hub_postgres.fresh_container() as container:
        url = container.get_connection_url()
        upgrade_to_head(url)
        config = Config(str(INI_PATH))
        config.set_main_option("sqlalchemy.url", url)
        command.downgrade(config, _parent(config))
        engine = create_engine(url, future=True)
        with engine.connect() as connection:
            assert _columns(connection) == {}
        command.upgrade(config, "head")
        with engine.connect() as connection:
            assert _columns(connection) == COLUMNS
        engine.dispose()
