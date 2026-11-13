"""Migration 0001 adopts the table PigroCRM's sidecar left behind, rows included."""

from typing import Any

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import Connection, Engine, create_engine, text
from sqlalchemy.exc import IntegrityError

import rebase_core.models  # noqa: F401
from rebase_core.db import Base
from rebase_core.migrate import INI_PATH, head_revision, upgrade_to_head


def test_the_migrations_produce_exactly_the_models_schema(hub_engine: Engine) -> None:
    with hub_engine.connect() as connection:
        context = MigrationContext.configure(connection)
        diff = compare_metadata(context, Base.metadata)
    assert diff == [], diff


def test_the_company_check_constraints_are_installed(hub_engine: Engine) -> None:
    """REB-380: `ck_companies_onsite_days_range`, `ck_companies_onsite_days_together`
    and `ck_companies_headcount_positive` are real database constraints, not only a
    Pydantic rule -- proven with a raw `INSERT` that bypasses the schema entirely. Every
    attempt runs inside one savepoint of a transaction rolled back at the end, so nothing
    here leaves a row behind for another test."""
    with hub_engine.connect() as connection:
        outer = connection.begin()
        user_id = connection.execute(
            text(
                "INSERT INTO users (id, email, first_name, last_name, role, active) "
                "VALUES (gen_random_uuid(), 'ck-companies@studio.it', 'A', 'B', "
                "'member', true) RETURNING id"
            )
        ).scalar()

        def _insert(remote: str, onsite_days: str, headcount: int) -> None:
            connection.execute(
                text(
                    "INSERT INTO companies (id, user_id, company_name, requested_role, "
                    "project, period_from, duration, daily_budget, remote, "
                    "onsite_days, headcount, status) VALUES (gen_random_uuid(), "
                    f":user_id, 'ACME', 'Dev', 'Un progetto', '2026-10-01', '3 mesi', "
                    f"500, :remote, {onsite_days}, :headcount, 'new')"
                ),
                {"user_id": user_id, "remote": remote, "headcount": headcount},
            )

        with pytest.raises(IntegrityError), connection.begin_nested():  # hybrid, no days
            _insert("hybrid", "NULL", 1)

        with pytest.raises(IntegrityError), connection.begin_nested():  # not hybrid, days set
            _insert("remote", "2", 1)

        with pytest.raises(IntegrityError), connection.begin_nested():  # days outside 1-4
            _insert("hybrid", "5", 1)

        with pytest.raises(IntegrityError), connection.begin_nested():  # fewer than one person
            _insert("remote", "NULL", 0)

        with connection.begin_nested():  # the same shape, valid, must be accepted
            _insert("hybrid", "3", 2)

        outer.rollback()


def test_a_live_grant_is_one_per_person_and_company(hub_engine: Engine) -> None:
    """REB-518: 0023 shipped the grants' partial unique index on `(user_id)`, and 0025
    swaps it for the one on `(user_id, company_id)`; after `upgrade head` only the
    second is there."""
    with hub_engine.connect() as connection:
        indexes = set(
            connection.execute(
                text("SELECT indexname FROM pg_indexes WHERE tablename = 'talent_cloud_grants'")
            ).scalars()
        )
    assert {
        "uq_talent_cloud_grants_user_company_live",
        "uq_talent_cloud_grants_user_id_live",
    } & indexes == {"uq_talent_cloud_grants_user_company_live"}


def test_the_production_table_is_adopted_with_its_rows(hub_postgres: Any) -> None:
    """The shape `create_all` gave the table on 2026-09-07 plus the columns two
    `ADD COLUMN IF NOT EXISTS` rounds added later, with a row in it: after `upgrade`
    the row is still there, the late columns exist, and the version table is at head."""
    with hub_postgres.fresh_container() as container:
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
            assert {"first_name", "last_name", "linkedin_url", "utm_id"} <= columns
            version = connection.execute(text("SELECT version_num FROM alembic_version"))
            assert version.scalar() == head_revision()
            # And the result is the models' schema, on the adopted table as on a fresh one.
            diff = compare_metadata(MigrationContext.configure(connection), Base.metadata)
            assert diff == [], diff
        engine.dispose()


def test_migration_0017_can_run_again_and_roll_back(hub_postgres: Any) -> None:
    """A retried deploy runs 0017's statements over tables that already exist, and the
    downgrade leaves 0016's schema: both must work, and the result must still be the
    models' schema. REB-387: also prove the downgrade actually drops
    the four new tables, and the forced re-run actually recreates them, rather than
    relying only on the final schema diff, which a partial downgrade could still pass."""
    with hub_postgres.fresh_container() as container:
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

        command.upgrade(config, "0052")
        with engine.connect() as connection:
            assert _existing_tables(connection) == new_tables

        command.downgrade(config, "0017")
        with engine.begin() as connection:
            connection.execute(text("UPDATE alembic_version SET version_num = '0016'"))
        command.upgrade(config, "0052")
        with engine.connect() as connection:
            assert _existing_tables(connection) == new_tables
            assert (
                connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
                == "0052"
            )
        engine.dispose()


def test_migration_0053_drops_the_match_side_and_its_downgrade_recreates_it_empty(
    hub_postgres: Any,
) -> None:
    """REB-1108: at head the four tables are gone; the downgrade puts them back, empty and
    with their constraints, and the upgrade drops them again."""
    with hub_postgres.fresh_container() as container:
        url = container.get_connection_url()
        upgrade_to_head(url)
        config = Config(str(INI_PATH))
        config.set_main_option("sqlalchemy.url", url)
        engine = create_engine(url, future=True)
        names = [
            "freelancer_fiscal",
            "matches",
            "contract_documents",
            "contract_letter_counters",
        ]

        def held() -> set[str]:
            with engine.connect() as connection:
                return set(
                    connection.execute(
                        text(
                            "SELECT table_name FROM information_schema.tables "
                            "WHERE table_schema = 'public' AND table_name = ANY(:n)"
                        ),
                        {"n": names},
                    ).scalars()
                )

        assert held() == set()
        command.downgrade(config, "0052")
        assert held() == set(names)
        with engine.connect() as connection:
            for name in names:
                assert connection.execute(text(f"SELECT count(*) FROM {name}")).scalar() == 0
        command.upgrade(config, "head")
        assert held() == set()
        engine.dispose()


def test_migration_0019_can_run_again_and_roll_back(hub_postgres: Any) -> None:
    """A retried deploy runs 0019's statements over columns that already exist, and the
    downgrade leaves 0018's schema: both must work, and the result must be the models'."""
    with hub_postgres.fresh_container() as container:
        url = container.get_connection_url()
        upgrade_to_head(url)
        config = Config(str(INI_PATH))
        config.set_main_option("sqlalchemy.url", url)
        command.downgrade(config, "0018")
        command.upgrade(config, "head")
        engine = create_engine(url, future=True)
        command.downgrade(config, "0019")
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


def test_migration_0020_can_run_again_and_roll_back(hub_postgres: Any) -> None:
    """A retried deploy runs 0020's statements over tables that already exist, and the
    downgrade leaves 0019's schema: both must work, and the result must be the models'."""
    with hub_postgres.fresh_container() as container:
        url = container.get_connection_url()
        upgrade_to_head(url)
        config = Config(str(INI_PATH))
        config.set_main_option("sqlalchemy.url", url)
        command.downgrade(config, "0019")
        command.upgrade(config, "head")
        engine = create_engine(url, future=True)
        command.downgrade(config, "0020")
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


def test_migration_0021_backfills_every_active_match_as_to_link(hub_postgres: Any) -> None:
    """REB-497, § 3.1 of the design: a match already `active` when 0021 ships waits as
    `to_link`, so the sweep links it; nothing else would ever look at it again. A
    match in any other state keeps no link state until it turns active."""
    with hub_postgres.fresh_container() as container:
        url = container.get_connection_url()
        config = Config(str(INI_PATH))
        config.set_main_option("sqlalchemy.url", url)
        command.upgrade(config, "0020")
        engine = create_engine(url, future=True)
        with engine.begin() as connection:
            user_id = connection.execute(
                text(
                    "INSERT INTO users (id, email, nome, cognome, role, attivo) VALUES "
                    "(gen_random_uuid(), 'ada@studio.it', 'Ada', 'Lovelace', 'admin', true) "
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
                    "INSERT INTO companies (id, user_id, nome_azienda, figura_richiesta, "
                    "progetto, periodo_da, durata, budget_giornaliero, remoto, "
                    "giorni_presenza, numero_risorse, stato) VALUES (gen_random_uuid(), "
                    ":user_id, 'ACME', 'Dev', 'Un progetto', '2026-10-01', '3 mesi', 500, "
                    "'remoto', NULL, 1, 'nuovo') RETURNING id"
                ),
                {"user_id": user_id},
            ).scalar()
            for old_status in ("attivo", "bozza", "in_firma", "concluso"):
                connection.execute(
                    text(
                        "INSERT INTO matches (id, freelancer_id, company_id, "
                        "cliente_ragione_sociale, cliente_piva, cliente_sede, stato, "
                        "created_by) VALUES (gen_random_uuid(), :freelancer_id, "
                        ":company_id, 'ACME S.r.l.', '01234567890', 'Milano', :stato, "
                        ":user_id)"
                    ),
                    {
                        "freelancer_id": freelancer_id,
                        "company_id": company_id,
                        "stato": old_status,
                        "user_id": user_id,
                    },
                )

        command.upgrade(config, "0052")

        with engine.connect() as connection:
            states = dict(
                connection.execute(text("SELECT status, pigro_status FROM matches")).all()
            )
        assert states == {
            "active": "to_link",
            "draft": None,
            "awaiting_signature": None,
            "concluded": None,
        }
        engine.dispose()


def test_the_campaign_constraints_are_installed(hub_engine: Engine) -> None:
    """A campaign's source and its source's field go together, and every enum column of
    the three tables refuses a value outside its list: all ten `CHECK` constraints,
    each proven with raw SQL that bypasses the schema entirely, rolled back."""
    with hub_engine.connect() as connection:
        outer = connection.begin()
        user_id = connection.execute(
            text(
                "INSERT INTO users (id, email, first_name, last_name, role, active, created_at, "
                "updated_at) VALUES (gen_random_uuid(), 'c@rebase.it', 'C', '', 'admin', "
                "true, now(), now()) RETURNING id"
            )
        ).scalar_one()
        base = (
            "INSERT INTO campaigns (id, created_by, name, slug, source, journey_status, filters, "
            "follows_id, subject, text, button_text, button_meta, action, status, "
            "content_at, created_at, updated_at) VALUES (gen_random_uuid(), :u, 'n', :slug, "
            ":source, :sp, CAST(:filters AS JSONB), :segue, '', '', '', :button_meta, :action, "
            ":status, now(), now(), now())"
        )
        valid = {
            "source": "status",
            "sp": "lead",
            "filters": None,
            "segue": None,
            "button_meta": "area",
            "action": "cv",
            "status": "draft",
        }
        bad = (
            {**valid, "slug": "a", "sp": None},  # ck_campaigns_journey_status
            {**valid, "slug": "b", "source": "filters"},  # ck_campaigns_filters
            {**valid, "slug": "c", "action": "vola"},  # ck_campaigns_action
            {**valid, "slug": "d", "source": "nuvola"},  # ck_campaigns_source
            {**valid, "slug": "e", "status": "volante"},  # ck_campaigns_status
            {**valid, "slug": "f", "button_meta": "cielo"},  # ck_campaigns_button_meta
            # ck_campaigns_follows
            {**valid, "slug": "g", "source": "list", "sp": None, "filters": None},
        )
        for values in bad:
            savepoint = connection.begin_nested()
            with pytest.raises(IntegrityError):
                connection.execute(text(base), {"u": user_id, **values})
            savepoint.rollback()
        campaign_id = connection.execute(
            text(base + " RETURNING id"), {"u": user_id, "slug": "ok", **valid}
        ).scalar_one()

        recipient = (
            "INSERT INTO campaign_recipients (id, campaign_id, email, kind, pigro_slugs, "
            "code, before, status, attempts, unsubscribe_token, created_at) VALUES "
            "(gen_random_uuid(), :campaign_id, :email, :kind, '[]', 'abc12345', '{}', "
            ":status, 0, :token, now())"
        )
        for values in (
            # ck_campaign_recipients_kind
            {"email": "r1@rebase.it", "kind": "volante", "status": "queued", "token": "t1"},
            # ck_campaign_recipients_status
            {"email": "r2@rebase.it", "kind": "lead", "status": "spedita", "token": "t2"},
        ):
            savepoint = connection.begin_nested()
            with pytest.raises(IntegrityError):
                connection.execute(text(recipient), {"campaign_id": campaign_id, **values})
            savepoint.rollback()
        connection.execute(
            text(recipient),
            {
                "campaign_id": campaign_id,
                "email": "r3@rebase.it",
                "kind": "lead",
                "status": "queued",
                "token": "t3",
            },
        )

        optout = "INSERT INTO campaign_optouts (email, source) VALUES (:email, :source)"
        savepoint = connection.begin_nested()  # ck_campaign_optouts_source
        with pytest.raises(IntegrityError):
            connection.execute(text(optout), {"email": "o1@rebase.it", "source": "spam"})
        savepoint.rollback()
        connection.execute(text(optout), {"email": "o2@rebase.it", "source": "link"})

        outer.rollback()


def test_a_migration_leaves_the_host_process_loggers_emitting(hub_postgres: Any) -> None:
    """REB-660, the twin of pigrocrm's REB-190: `env.py` calls Alembic's `fileConfig`,
    whose default `disable_existing_loggers=True` is right for `alembic upgrade` from a
    shell and wrong inside the API process, where `upgrade_to_head` runs on every
    signup. The default disabled every logger the ini does not name, including other
    projects' loggers alive on the same xdist worker: a hub migration left
    `pigrocrm.core.mail` silent and `test_mail.py`'s `caplog` empty the day `loadfile`
    put them together. A witness logger with a handler of our own (not `caplog`, since
    `fileConfig` rebuilds the root handler list and a root-attached capture would go
    blind for the fix-independent reason) proves a migration leaves it emitting.
    """
    import logging

    grabbed: list[str] = []

    class Grab(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            grabbed.append(record.getMessage())

    witness = logging.getLogger("rebase_core.witness-for-reb-660")
    handler = Grab()
    witness.addHandler(handler)
    witness.setLevel(logging.WARNING)
    try:
        with hub_postgres.fresh_container() as container:
            upgrade_to_head(container.get_connection_url())
            witness.warning("still alive")
    finally:
        witness.removeHandler(handler)
        witness.setLevel(logging.NOTSET)

    assert grabbed == ["still alive"]
