"""Migration 0001 adopts the table PigroCRM's sidecar left behind, rows included."""

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import Connection, Engine, create_engine, text
from sqlalchemy.exc import IntegrityError
from testcontainers.community.postgres import PostgresContainer

import rebase_core.models  # noqa: F401
from rebase_core.db import Base
from rebase_core.migrate import INI_PATH, head_revision, upgrade_to_head


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


def test_the_contract_constraints_are_installed(hub_engine: Engine) -> None:
    """REB-387: a framework agreement never hangs on a match and never takes a letter
    number, a letter always does both, a number is taken once, and a notice is a
    framework agreement's state alone. Real constraints, proven with raw inserts inside
    one transaction rolled back at the end."""
    with hub_engine.connect() as connection:
        outer = connection.begin()
        user_id = connection.execute(
            text(
                "INSERT INTO users (id, email, nome, cognome, role, attivo) VALUES "
                "(gen_random_uuid(), 'ck-contracts@studio.it', 'A', 'B', 'admin', true) "
                "RETURNING id"
            )
        ).scalar()
        freelancer_id = connection.execute(
            text(
                "INSERT INTO freelancers (id, user_id, links, stato, compilata_da) VALUES "
                "(gen_random_uuid(), :user_id, '[]', 'nuovo', 'persona') RETURNING id"
            ),
            {"user_id": user_id},
        ).scalar()
        company_id = connection.execute(
            text(
                "INSERT INTO companies (id, user_id, nome_azienda, figura_richiesta, progetto, "
                "periodo_da, durata, budget_giornaliero, remoto, numero_risorse, stato) VALUES "
                "(gen_random_uuid(), :user_id, 'ACME', 'Dev', 'Un progetto', '2026-10-01', "
                "'3 mesi', 500, 'remoto', 1, 'nuovo') RETURNING id"
            ),
            {"user_id": user_id},
        ).scalar()
        match_id = connection.execute(
            text(
                "INSERT INTO matches (id, freelancer_id, company_id, cliente_ragione_sociale, "
                "cliente_piva, cliente_sede, stato, created_by) VALUES (gen_random_uuid(), "
                ":freelancer_id, :company_id, 'ACME S.r.l.', '01234567890', 'Milano', 'bozza', "
                ":user_id) RETURNING id"
            ),
            {"freelancer_id": freelancer_id, "company_id": company_id, "user_id": user_id},
        ).scalar()

        def _document(kind: str, match: object, numero: str | None, stato: str) -> None:
            connection.execute(
                text(
                    "INSERT INTO contract_documents (id, kind, freelancer_id, match_id, numero, "
                    "text_version, testo_bozza, data, pdf, stato, created_by) VALUES "
                    "(gen_random_uuid(), :kind, :freelancer_id, :match_id, :numero, '0.1', true, "
                    "'{}', :pdf, :stato, :user_id)"
                ),
                {
                    "kind": kind,
                    "freelancer_id": freelancer_id,
                    "match_id": match,
                    "numero": numero,
                    "pdf": b"%PDF-",
                    "stato": stato,
                    "user_id": user_id,
                },
            )

        refused = (
            ("quadro", match_id, None, "generato"),  # a framework hangs on no match
            ("quadro", None, "2026-001", "generato"),  # and takes no number
            ("lettera", match_id, None, "generato"),  # a letter always has a number
            ("lettera", None, "2026-001", "generato"),  # and a match
            ("lettera", match_id, "2026-001", "disdetto"),  # a notice is a framework's
            ("quadro", None, None, "forse"),  # an unknown state
            ("fattura", None, None, "generato"),  # an unknown kind
        )
        for kind, match, numero, stato in refused:
            with pytest.raises(IntegrityError), connection.begin_nested():
                _document(kind, match, numero, stato)

        with connection.begin_nested():  # the valid shapes are accepted
            _document("quadro", None, None, "generato")
            _document("lettera", match_id, "2026-001", "in_attesa")
        with pytest.raises(IntegrityError), connection.begin_nested():  # a number, once
            _document("lettera", match_id, "2026-001", "generato")
        with pytest.raises(IntegrityError), connection.begin_nested():  # one tax row per card
            for _ in range(2):
                connection.execute(
                    text(
                        "INSERT INTO freelancer_fiscal (id, freelancer_id, codice_fiscale, "
                        "partita_iva, domicilio, updated_by) VALUES (gen_random_uuid(), "
                        ":freelancer_id, 'LVLDAA85T50H501Z', '01234567890', 'Milano', :user_id)"
                    ),
                    {"freelancer_id": freelancer_id, "user_id": user_id},
                )
        outer.rollback()


def test_migration_0017_can_run_again_and_roll_back() -> None:
    """A retried deploy runs 0017's statements over tables that already exist, and the
    downgrade leaves 0016's schema: both must work, and the result must still be the
    models' schema. REB-387: also prove the downgrade actually drops
    the four new tables, and the forced re-run actually recreates them, rather than
    relying only on the final schema diff, which a partial downgrade could still pass."""
    with PostgresContainer("postgres:17-alpine", driver="psycopg") as container:
        url = container.get_connection_url()
        upgrade_to_head(url)
        config = Config(str(INI_PATH))
        config.set_main_option("sqlalchemy.url", url)
        engine = create_engine(url, future=True)
        new_tables = {
            "freelancer_fiscal",
            "matches",
            "contract_documents",
            "contract_letter_counters",
        }

        def _existing_tables(connection: Connection) -> set[str]:
            return set(
                connection.execute(
                    text(
                        "SELECT table_name FROM information_schema.tables "
                        "WHERE table_schema = 'public' AND table_name = ANY(:names)"
                    ),
                    {"names": list(new_tables)},
                ).scalars()
            )

        command.downgrade(config, "0016")
        with engine.connect() as connection:
            assert _existing_tables(connection) == set()

        command.upgrade(config, "head")
        with engine.connect() as connection:
            assert _existing_tables(connection) == new_tables

        with engine.begin() as connection:
            connection.execute(text("UPDATE alembic_version SET version_num = '0016'"))
        command.upgrade(config, "head")
        with engine.connect() as connection:
            assert _existing_tables(connection) == new_tables
            assert (
                connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
                == head_revision()
            )
            diff = compare_metadata(MigrationContext.configure(connection), Base.metadata)
            assert diff == [], diff
        engine.dispose()


def test_an_envelope_belongs_to_one_document_and_keeps_its_item(hub_engine: Engine) -> None:
    """REB-387 phase 3: the webhook finds its document by the envelope's id, so an
    envelope is one document's; and the item the sealed copy is downloaded by is stored
    with it or not at all."""
    with hub_engine.connect() as connection:
        outer = connection.begin()
        user_id = connection.execute(
            text(
                "INSERT INTO users (id, email, nome, cognome, role, attivo) VALUES "
                "(gen_random_uuid(), 'ck-envelopes@studio.it', 'A', 'B', 'admin', true) "
                "RETURNING id"
            )
        ).scalar()
        freelancer_id = connection.execute(
            text(
                "INSERT INTO freelancers (id, user_id, links, stato, compilata_da) VALUES "
                "(gen_random_uuid(), :user_id, '[]', 'nuovo', 'persona') RETURNING id"
            ),
            {"user_id": user_id},
        ).scalar()

        def _document(envelope: str | None, item: str | None) -> None:
            connection.execute(
                text(
                    "INSERT INTO contract_documents (id, kind, freelancer_id, text_version, "
                    "testo_bozza, data, pdf, stato, documenso_id, documenso_item_id, "
                    "created_by) VALUES (gen_random_uuid(), 'quadro', :freelancer_id, '0.1', "
                    "false, '{}', :pdf, 'inviato', :envelope, :item, :user_id)"
                ),
                {
                    "freelancer_id": freelancer_id,
                    "pdf": b"%PDF-",
                    "envelope": envelope,
                    "item": item,
                    "user_id": user_id,
                },
            )

        for envelope, item in (("envelope_1", None), (None, "envelope_item_1")):
            with pytest.raises(IntegrityError), connection.begin_nested():
                _document(envelope, item)
        with connection.begin_nested():
            _document("envelope_1", "envelope_item_1")
            _document(None, None)
            _document(None, None)
        with pytest.raises(IntegrityError), connection.begin_nested():
            _document("envelope_1", "envelope_item_2")
        outer.rollback()


def test_migration_0019_can_run_again_and_roll_back() -> None:
    """A retried deploy runs 0019's statements over columns that already exist, and the
    downgrade leaves 0018's schema: both must work, and the result must be the models'."""
    with PostgresContainer("postgres:17-alpine", driver="psycopg") as container:
        url = container.get_connection_url()
        upgrade_to_head(url)
        config = Config(str(INI_PATH))
        config.set_main_option("sqlalchemy.url", url)
        command.downgrade(config, "0018")
        command.upgrade(config, "head")
        engine = create_engine(url, future=True)
        with engine.begin() as connection:
            connection.execute(text("UPDATE alembic_version SET version_num = '0018'"))
        command.upgrade(config, "head")
        with engine.connect() as connection:
            assert (
                connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
                == head_revision()
            )
            diff = compare_metadata(MigrationContext.configure(connection), Base.metadata)
            assert diff == [], diff
        engine.dispose()


def test_migration_0020_can_run_again_and_roll_back() -> None:
    """A retried deploy runs 0020's statements over tables that already exist, and the
    downgrade leaves 0019's schema: both must work, and the result must be the models'."""
    with PostgresContainer("postgres:17-alpine", driver="psycopg") as container:
        url = container.get_connection_url()
        upgrade_to_head(url)
        config = Config(str(INI_PATH))
        config.set_main_option("sqlalchemy.url", url)
        command.downgrade(config, "0019")
        command.upgrade(config, "head")
        engine = create_engine(url, future=True)
        with engine.begin() as connection:
            connection.execute(text("UPDATE alembic_version SET version_num = '0019'"))
        command.upgrade(config, "head")
        with engine.connect() as connection:
            assert (
                connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
                == head_revision()
            )
            diff = compare_metadata(MigrationContext.configure(connection), Base.metadata)
            assert diff == [], diff
        engine.dispose()


def test_the_campaign_constraints_are_installed(hub_engine: Engine) -> None:
    """A campaign's source and its source's field go together, and every enum column
    refuses a value outside its list: proven with raw SQL, rolled back."""
    with hub_engine.connect() as connection:
        outer = connection.begin()
        user_id = connection.execute(
            text(
                "INSERT INTO users (id, email, nome, cognome, role, attivo, created_at, "
                "updated_at) VALUES (gen_random_uuid(), 'c@rebase.it', 'C', '', 'admin', "
                "true, now(), now()) RETURNING id"
            )
        ).scalar_one()
        base = (
            "INSERT INTO campaigns (id, created_by, nome, slug, fonte, stato_percorso, filtri, "
            "oggetto, testo, bottone_testo, bottone_meta, azione, stato, contenuto_at, "
            "created_at, updated_at) VALUES (gen_random_uuid(), :u, 'n', :slug, :fonte, :sp, "
            "CAST(:filtri AS JSONB), '', '', '', 'area', :azione, 'bozza', now(), now(), now())"
        )
        bad = (
            {"slug": "a", "fonte": "stato", "sp": None, "filtri": None, "azione": "cv"},
            {"slug": "b", "fonte": "filtri", "sp": None, "filtri": None, "azione": "cv"},
            {"slug": "c", "fonte": "stato", "sp": "lead", "filtri": None, "azione": "vola"},
            {"slug": "d", "fonte": "nuvola", "sp": None, "filtri": None, "azione": "cv"},
        )
        for values in bad:
            savepoint = connection.begin_nested()
            with pytest.raises(IntegrityError):
                connection.execute(text(base), {"u": user_id, **values})
            savepoint.rollback()
        connection.execute(
            text(base),
            {
                "u": user_id,
                "slug": "ok",
                "fonte": "stato",
                "sp": "lead",
                "filtri": None,
                "azione": "cv",
            },
        )
        outer.rollback()
