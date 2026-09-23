"""Migration 0001 adopts the table PigroCRM's sidecar left behind, rows included."""

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import IntegrityError
from testcontainers.community.postgres import PostgresContainer

import rebase_core.models  # noqa: F401
from rebase_core.db import Base
from rebase_core.migrate import head_revision, upgrade_to_head


def test_the_migrations_produce_exactly_the_models_schema(hub_engine: Engine) -> None:
    with hub_engine.connect() as connection:
        context = MigrationContext.configure(connection)
        diff = compare_metadata(context, Base.metadata)
    assert diff == [], diff


def test_the_company_check_constraints_are_installed(hub_engine: Engine) -> None:
    """REB-380: `ck_companies_giorni_presenza_range`, `ck_companies_giorni_presenza_together`
    and `ck_companies_numero_risorse_positive` are real database constraints, not only a
    Pydantic rule -- proven with a raw `INSERT` that bypasses the schema entirely. Every
    attempt runs inside one savepoint of a transaction rolled back at the end, so nothing
    here leaves a row behind for another test."""
    with hub_engine.connect() as connection:
        outer = connection.begin()
        user_id = connection.execute(
            text(
                "INSERT INTO users (id, email, nome, cognome, role, attivo) "
                "VALUES (gen_random_uuid(), 'ck-companies@studio.it', 'A', 'B', "
                "'member', true) RETURNING id"
            )
        ).scalar()

        def _insert(remoto: str, giorni_presenza: str, numero_risorse: int) -> None:
            connection.execute(
                text(
                    "INSERT INTO companies (id, user_id, nome_azienda, figura_richiesta, "
                    "progetto, periodo_da, durata, budget_giornaliero, remoto, "
                    "giorni_presenza, numero_risorse, stato) VALUES (gen_random_uuid(), "
                    f":user_id, 'ACME', 'Dev', 'Un progetto', '2026-10-01', '3 mesi', "
                    f"500, :remoto, {giorni_presenza}, :numero_risorse, 'nuovo')"
                ),
                {"user_id": user_id, "remoto": remoto, "numero_risorse": numero_risorse},
            )

        with pytest.raises(IntegrityError), connection.begin_nested():  # ibrido, no days
            _insert("ibrido", "NULL", 1)

        with pytest.raises(IntegrityError), connection.begin_nested():  # not ibrido, days set
            _insert("remoto", "2", 1)

        with pytest.raises(IntegrityError), connection.begin_nested():  # days outside 1-4
            _insert("ibrido", "5", 1)

        with pytest.raises(IntegrityError), connection.begin_nested():  # fewer than one person
            _insert("remoto", "NULL", 0)

        with connection.begin_nested():  # the same shape, valid, must be accepted
            _insert("ibrido", "3", 2)

        outer.rollback()


def test_the_production_table_is_adopted_with_its_rows() -> None:
    """The shape `create_all` gave the table on 2026-09-07 plus the columns two
    `ADD COLUMN IF NOT EXISTS` rounds added later, with a row in it: after `upgrade`
    the row is still there, the late columns exist, and the version table is at head."""
    with PostgresContainer("postgres:17-alpine", driver="psycopg") as container:
        url = container.get_connection_url()
        engine = create_engine(url, future=True)
        with engine.begin() as connection:
            connection.execute(
                text(
                    "CREATE TABLE signups (id UUID NOT NULL, email VARCHAR(320) NOT NULL, "
                    "created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, "
                    "utm_source VARCHAR(200), PRIMARY KEY (id))"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO signups (id, email) "
                    "VALUES (gen_random_uuid(), 'vecchia@studio.it')"
                )
            )
        upgrade_to_head(url)
        with engine.connect() as connection:
            assert connection.execute(text("SELECT count(*) FROM signups")).scalar() == 1
            columns = set(
                connection.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_name = 'signups'"
                    )
                ).scalars()
            )
            assert {"nome", "cognome", "linkedin_url", "utm_id"} <= columns
            version = connection.execute(text("SELECT version_num FROM alembic_version"))
            assert version.scalar() == head_revision()
            # And the result is the models' schema, on the adopted table as on a fresh one.
            diff = compare_metadata(MigrationContext.configure(connection), Base.metadata)
            assert diff == [], diff
        engine.dispose()
