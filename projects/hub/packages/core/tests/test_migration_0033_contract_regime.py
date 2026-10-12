"""0033 (REB-687): every contract document says its regime, backfilled to `partita_iva`,
and tax data may have no partita IVA. The downgrade refuses while either would be lost,
and a retried deploy lands on the same schema."""

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


def _user(connection: Connection) -> str:
    return str(
        connection.execute(
            text(
                "INSERT INTO users (id, email, nome, cognome, role, attivo, created_at, "
                "updated_at) VALUES (gen_random_uuid(), 'ck-regime@studio.it', 'A', 'B', "
                "'admin', true, now(), now()) RETURNING id"
            )
        ).scalar_one()
    )


def _freelancer(connection: Connection, user_id: str) -> str:
    return str(
        connection.execute(
            text(
                "INSERT INTO freelancers (id, user_id, stato, links, compilata_da, "
                "created_at, updated_at) VALUES (gen_random_uuid(), :u, 'nuovo', '[]', "
                "'persona', now(), now()) RETURNING id"
            ),
            {"u": user_id},
        ).scalar_one()
    )


def _quadro(connection: Connection, freelancer_id: str, user_id: str) -> str:
    return str(
        connection.execute(
            text(
                "INSERT INTO contract_documents (id, kind, freelancer_id, text_version, "
                "testo_bozza, data, pdf, stato, created_by, created_at, updated_at) VALUES "
                "(gen_random_uuid(), 'quadro', :f, '1.0', false, '{}', '\\x00', 'generato', "
                ":u, now(), now()) RETURNING id"
            ),
            {"f": freelancer_id, "u": user_id},
        ).scalar_one()
    )


def _fiscal(connection: Connection, freelancer_id: str, user_id: str) -> None:
    connection.execute(
        text(
            "INSERT INTO freelancer_fiscal (id, freelancer_id, codice_fiscale, partita_iva, "
            "domicilio, updated_by, created_at, updated_at) VALUES (gen_random_uuid(), :f, "
            "'LVLDAA85T50H501Z', '01234567890', 'Milano', :u, now(), now())"
        ),
        {"f": freelancer_id, "u": user_id},
    )


def _columns(connection: Connection) -> dict[str, str]:
    """`table.column: is_nullable` for the two columns this revision touches."""
    return {
        f"{table}.{column}": nullable
        for table, column, nullable in connection.execute(
            text(
                "SELECT table_name, column_name, is_nullable FROM information_schema.columns "
                "WHERE (table_name = 'contract_documents' AND column_name = 'regime') "
                "OR (table_name = 'freelancer_fiscal' AND column_name = 'partita_iva')"
            )
        )
    }


AFTER = {"contract_documents.regime": "NO", "freelancer_fiscal.partita_iva": "YES"}
BEFORE = {"freelancer_fiscal.partita_iva": "NO"}


def test_migration_0033_backfills_the_regime_frees_the_partita_iva_and_guards_the_downgrade(
    hub_postgres: Any,
) -> None:
    with hub_postgres.fresh_container() as container:
        url = container.get_connection_url()
        upgrade_to_head(url)
        config = Config(str(INI_PATH))
        config.set_main_option("sqlalchemy.url", url)
        engine = create_engine(url, future=True)
        command.downgrade(config, "0032")
        with engine.begin() as connection:
            assert _columns(connection) == BEFORE
            user = _user(connection)
            freelancer = _freelancer(connection, user)
            quadro = _quadro(connection, freelancer, user)
            _fiscal(connection, freelancer, user)
        for _ in range(2):  # a retried deploy runs the same revision again
            command.upgrade(config, "head")
            with engine.begin() as connection:
                assert _columns(connection) == AFTER
                assert (
                    connection.execute(
                        text("SELECT regime FROM contract_documents WHERE id = :i"),
                        {"i": quadro},
                    ).scalar_one()
                    == "partita_iva"
                )
                with pytest.raises(IntegrityError), connection.begin_nested():
                    connection.execute(text("UPDATE contract_documents SET regime = 'forfettario'"))
                connection.execute(text("UPDATE freelancer_fiscal SET partita_iva = NULL"))
                connection.execute(text("UPDATE contract_documents SET regime = 'occasionale'"))
            # An occasional document would be typeset again with the ordinary text.
            with pytest.raises(RuntimeError, match="occasionale"):
                command.downgrade(config, "0032")
            with engine.begin() as connection:
                connection.execute(text("UPDATE contract_documents SET regime = 'partita_iva'"))
            # NOT NULL cannot come back over tax data without a partita IVA.
            with pytest.raises(RuntimeError, match="partita IVA"):
                command.downgrade(config, "0032")
            with engine.begin() as connection:
                connection.execute(text("UPDATE freelancer_fiscal SET partita_iva = '01234567890'"))
            command.downgrade(config, "0032")
            with engine.connect() as connection:
                assert _columns(connection) == BEFORE
        command.upgrade(config, "head")
        with engine.begin() as connection:
            # The revision again over the column it already added.
            connection.execute(text("UPDATE alembic_version SET version_num = '0032'"))
        command.upgrade(config, "head")
        with engine.connect() as connection:
            assert (
                connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
                == head_revision()
            )
            diff = compare_metadata(MigrationContext.configure(connection), Base.metadata)
            assert diff == [], diff
        engine.dispose()
