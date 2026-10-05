from datetime import date
from pathlib import Path
from typing import Any

import pytest
from alembic.autogenerate import compare_metadata
from alembic.command import downgrade, upgrade
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, create_engine, text

import pigrocrm.core.models_registry  # noqa: F401
from pigrocrm.core.config import get_settings
from pigrocrm.core.db import Base

CORE_ROOT = Path(__file__).resolve().parents[1]

# Autogenerate is known to silently omit exactly these shapes: a unique index over a SQL
# expression rather than a bare column (`uq_users_email_lower`), a plain unique index on a
# nullable column (`uq_pipeline_stage_code`), a GIN index -- losing one of those turns a
# JSONB containment filter into a sequential scan -- and, from slice 6, a *partial* GIN
# index over an operator class (`*_trgm`). Named explicitly here so a regression fails
# with the missing index's name instead of a generic metadata diff.
HAND_MAINTAINED_INDEXES = {
    "uq_users_email_lower",
    # REB-615, migration 0045: one default per space (partial, `WHERE predefinita`) and
    # one azienda per fiscal id (partial, functional over `upper(...)`). Both are shapes
    # `compare_metadata` cannot compare, so they are asserted on their definitions below.
    "uq_emitter_profile_predefinita",
    "uq_emitter_profile_partita_iva",
    "uq_emitter_profile_codice_fiscale",
    "uq_pipeline_stage_code",
    "ix_customers_custom_fields",
    "ix_people_custom_fields",
    "ix_deals_custom_fields",
    "uq_invoices_azienda_anno_numero",
    # REB-619: the `(anno, numero)` path the unique index stopped offering to a search
    # across aziende; partial, so `compare_metadata` cannot see it either.
    "ix_invoices_anno_numero",
    "ix_invoices_custom_fields",
    # Same shape as `uq_pipeline_stage_code`: a plain unique index on a nullable
    # column, one of the two shapes autogenerate is known to silently omit.
    "uq_cost_categories_code",
    "ix_time_entries_custom_fields",
    "ix_costs_custom_fields",
    # Same shape as `uq_users_email_lower`: a functional unique index over lower(nome).
    "uq_cost_categories_nome",
    # Slice 6, migration 0021. A *partial* GIN index over an operator class is a fourth
    # shape autogenerate handles poorly, and nine trigram indexes omitted in silence are
    # nine sequential scans that come back a month later.
    "ix_customers_ragione_sociale_trgm",
    "ix_customers_partita_iva_trgm",
    "ix_customers_codice_fiscale_trgm",
    "ix_customers_email_trgm",
    "ix_people_nome_trgm",
    "ix_people_cognome_trgm",
    "ix_people_email_trgm",
    "ix_deals_nome_trgm",
    "ix_documents_titolo_trgm",
    # Slice 6, migration 0022. Residuo R9's other half: one `(column, id)` B-tree per
    # admitted sort key, plus the one descending index the single nullable sort column
    # costs. The twelve ascending ones are ordinary composite indexes that autogenerate
    # handles correctly -- they are listed anyway, because a composite index dropped in
    # silence is the same sequential scan as a GIN index dropped in silence, and the
    # thirteenth is an expression index over `DESC NULLS LAST` that autogenerate cannot
    # compare at all.
    "ix_customers_created_at_id",
    "ix_customers_updated_at_id",
    "ix_customers_ragione_sociale_id",
    "ix_people_created_at_id",
    "ix_people_updated_at_id",
    "ix_people_cognome_id",
    "ix_people_cognome_desc_id",
    "ix_deals_created_at_id",
    "ix_deals_updated_at_id",
    "ix_deals_nome_id",
    "ix_documents_created_at_id",
    "ix_documents_updated_at_id",
    "ix_documents_titolo_id",
    # Slice 6, migration 0023. The two period-filter columns of §4.1. Both are ordinary
    # single-column B-trees that autogenerate handles correctly; they are listed here for
    # the same reason the twelve ascending sort indexes are, which is that an index
    # dropped in silence is a sequential scan on every dashboard load, and this file is
    # where a missing index is supposed to fail by name.
    "ix_deals_chiuso_il",
    "ix_documents_stato_dal",
    # Slice 6, migration 0024. A *descending* expression index over two columns, which is
    # the fifth shape autogenerate cannot compare: `compare_metadata` does not look at
    # index expressions at all, so this one would be dropped in total silence and the
    # global activity feed would go back to sorting the whole table on every dashboard
    # load. Its declared shape is asserted below, on `indexdef` text.
    "ix_activities_recent",
    # Slice 6, migration 0024 as well: the tenth and last trigram index of §8.3, added
    # with the fifth search branch. Same partial-GIN-over-an-operator-class shape as the
    # nine of 0021, and picked up automatically by `TRGM_INDEX_NAMES` below.
    "ix_invoices_causale_trgm",
    # REB-290, migration 0036. Functional (over `lower(email)`) and partial at once --
    # the two shapes autogenerate omits, stacked: a functional index it drops silently
    # re-opens the case-insensitive duplicate, a partial predicate it drops silently
    # makes an accepted or revoked invitation block the address forever. Its declared
    # shape is asserted on `indexdef` text below.
    "uq_invitations_email_lower_open",
}

TRGM_INDEX_NAMES = frozenset(n for n in HAND_MAINTAINED_INDEXES if n.endswith("_trgm"))


def _alembic_config(url: str) -> Config:
    config = Config(str(CORE_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(CORE_ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", url)
    return config


def test_migrations_produce_exactly_the_models_schema(pigrocrm_postgres: Any) -> None:
    """A drift between migrations and models is invisible until deploy day, when the
    application meets a table the code does not expect."""
    with pigrocrm_postgres.fresh_container() as container:
        url = container.get_connection_url()
        upgrade(_alembic_config(url), "head")

        engine: Engine = create_engine(url)
        with engine.connect() as connection:
            context = MigrationContext.configure(connection)
            diff = compare_metadata(context, Base.metadata)
        engine.dispose()

    assert diff == [], f"migrations and models disagree: {diff}"


def test_every_table_the_slice_needs_exists() -> None:
    expected = {
        "users",
        "invitations",
        "personal_access_tokens",
        "refresh_tokens",
        "field_definitions",
        "pipeline_stages",
        "activities",
        "customers",
        "people",
        "deals",
        "fiscal_profile",
        "invoices",
        "invoice_lines",
        "invoice_counters",
        "time_entries",
        "costs",
        "cost_categories",
        "period_locks",
        "google_accounts",
        "google_oauth_states",
        "gmail_messages",
        "gmail_message_links",
        "email_drafts",
        "payment_reminders",
        "automation_config",
    }
    assert expected <= set(Base.metadata.tables)


def test_hand_maintained_indexes_survive_the_migration(pigrocrm_postgres: Any) -> None:
    """`compare_metadata` (above) already proves migrations and models agree overall, but
    a diff report names a mismatch, not a silent gap -- this test names the exact indexes
    that autogenerate is known to drop, so a future regression fails as a missing name
    instead of a generic diff someone has to go decode."""
    with pigrocrm_postgres.fresh_container() as container:
        url = container.get_connection_url()
        upgrade(_alembic_config(url), "head")

        engine: Engine = create_engine(url)
        with engine.connect() as connection:
            rows = connection.execute(
                text("SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = 'public'")
            ).all()
        engine.dispose()

    indexes = {row.indexname: row.indexdef for row in rows}

    missing = HAND_MAINTAINED_INDEXES - set(indexes)
    assert not missing, f"migration did not create these indexes: {missing}"

    # Postgres normalizes the expression to `lower((email)::text)` -- the explicit cast is
    # its own reflection detail, not something to pin exactly; what matters is that this is
    # still a functional index over `email` through `lower()`, not a plain column index.
    email_lower_def = indexes["uq_users_email_lower"]
    assert "UNIQUE" in email_lower_def, "uq_users_email_lower must be a unique index"
    assert "lower(" in email_lower_def and "email" in email_lower_def, (
        f"uq_users_email_lower is not a functional index over lower(email): {email_lower_def}"
    )

    for gin_index in (
        "ix_customers_custom_fields",
        "ix_people_custom_fields",
        "ix_deals_custom_fields",
        "ix_time_entries_custom_fields",
        "ix_costs_custom_fields",
    ):
        assert "USING gin" in indexes[gin_index], f"{gin_index} was not created as a GIN index"

    assert "USING gin" in indexes["ix_invoices_custom_fields"], (
        "ix_invoices_custom_fields was not created as a GIN index"
    )

    cost_categories_code_def = indexes["uq_cost_categories_code"]
    assert "UNIQUE" in cost_categories_code_def, "uq_cost_categories_code must be a unique index"

    cost_categories_nome_def = indexes["uq_cost_categories_nome"]
    assert "UNIQUE" in cost_categories_nome_def, "uq_cost_categories_nome must be a unique index"
    assert "lower(" in cost_categories_nome_def and "nome" in cost_categories_nome_def, (
        f"uq_cost_categories_nome is not a functional index over lower(nome): "
        f"{cost_categories_nome_def}"
    )

    invitations_email_open_def = indexes["uq_invitations_email_lower_open"]
    assert "UNIQUE" in invitations_email_open_def, (
        "uq_invitations_email_lower_open must be a unique index"
    )
    assert "lower(" in invitations_email_open_def and "email" in invitations_email_open_def, (
        f"uq_invitations_email_lower_open is not a functional index over lower(email): "
        f"{invitations_email_open_def}"
    )
    assert "accepted_at IS NULL" in invitations_email_open_def, (
        "uq_invitations_email_lower_open lost its pending predicate, so an accepted or "
        f"revoked invitation would block the address forever: {invitations_email_open_def}"
    )
    anno_numero_def = indexes["uq_invoices_azienda_anno_numero"]
    assert "UNIQUE" in anno_numero_def, "uq_invoices_azienda_anno_numero must be a unique index"
    assert "WHERE" in anno_numero_def and "numero IS NOT NULL" in anno_numero_def, (
        "uq_invoices_azienda_anno_numero lost its partial predicate, so every unnumbered "
        f"draft is now a duplicate of every other: {anno_numero_def}"
    )
    # REB-619: the register is per azienda, so the azienda leads the key.
    assert "azienda_id" in anno_numero_def, anno_numero_def


def test_0045_gives_a_used_database_with_no_emitter_row_its_azienda(
    pigrocrm_postgres: Any,
) -> None:
    """The root installation is not in the registry, so `ensure_defaults` never reaches
    it, and the API updates an azienda by id: a root with no emitter row would have no
    issuer to configure at all. 0045 inserts one, named after the database, on a database
    that already has a user (REB-615). A database with none is one being provisioned,
    whose migrations run before its owner is written: it gets nothing here, so that
    `ensure_defaults` can name its azienda after the space and not after the database."""
    with pigrocrm_postgres.fresh_container() as container:
        url = container.get_connection_url()
        config = _alembic_config(url)
        engine: Engine = create_engine(url)
        upgrade(config, "0044")
        upgrade(config, "0045")
        with engine.connect() as connection:
            empty = connection.execute(text("SELECT count(*) FROM emitter_profile")).scalar_one()
        downgrade(config, "0044")
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO users (id, email, nome, ruolo, attivo) "
                    "VALUES (gen_random_uuid(), 'root@pigro.it', 'Root', 'admin', true)"
                )
            )
        upgrade(config, "0045")
        with engine.connect() as connection:
            rows = connection.execute(
                text("SELECT nome, ragione_sociale, predefinita, attiva FROM emitter_profile")
            ).all()
            db_name = connection.execute(text("SELECT current_database()")).scalar_one()
        engine.dispose()
    assert empty == 0
    assert len(rows) == 1
    assert rows[0].ragione_sociale == db_name
    assert rows[0].nome == db_name[:80]
    assert rows[0].predefinita is True and rows[0].attiva is True


def test_the_azienda_indexes_are_partial_and_the_fiscal_ones_functional(
    pigrocrm_postgres: Any,
) -> None:
    """REB-615: `uq_emitter_profile_predefinita` is what makes «exactly one default»
    a fact of the database, and the two fiscal-id indexes compare the upper-cased
    column, the shape the import classifier compares. None of the three is visible to
    `compare_metadata`."""
    with pigrocrm_postgres.fresh_container() as container:
        url = container.get_connection_url()
        upgrade(_alembic_config(url), "head")
        engine: Engine = create_engine(url)
        with engine.connect() as connection:
            rows = connection.execute(
                text(
                    "SELECT indexname, indexdef FROM pg_indexes WHERE tablename = 'emitter_profile'"
                )
            ).all()
        engine.dispose()
    indexes = {name: definition for name, definition in rows}
    assert "UNIQUE" in indexes["uq_emitter_profile_predefinita"]
    assert "WHERE predefinita" in indexes["uq_emitter_profile_predefinita"]
    for name, column in (
        ("uq_emitter_profile_partita_iva", "partita_iva"),
        ("uq_emitter_profile_codice_fiscale", "codice_fiscale"),
    ):
        assert "UNIQUE" in indexes[name]
        assert f"upper(({column})::text)" in indexes[name], indexes[name]
        assert f"WHERE ({column} IS NOT NULL)" in indexes[name], indexes[name]


def test_0045_binds_the_one_fiscal_row_to_the_one_azienda_and_refuses_to_undo_two(
    pigrocrm_postgres: Any,
) -> None:
    """The migration over real rows planted at 0044, not read as text (REB-615).

    A 120-character `ragione_sociale` proves the `nome` backfill cuts to the column's
    width; the fiscal row proves the backfill of `azienda_id`; a lower-case codice
    fiscale proves the row is brought to the classifier's shape (a P.IVA with an `IT`
    prefix cannot be planted: the column is eleven wide); the downgrade
    with one azienda succeeds, and with two it refuses before touching anything.
    """
    with pigrocrm_postgres.fresh_container() as container:
        url = container.get_connection_url()
        config = _alembic_config(url)
        upgrade(config, "0044")

        engine: Engine = create_engine(url)
        long_name = "S" * 120
        with engine.begin() as connection:
            # Two statements, two executes: psycopg prepares a parametrised statement
            # and refuses more than one command in it.
            connection.execute(
                text(
                    """
                    INSERT INTO emitter_profile (id, singleton, ragione_sociale, partita_iva,
                                                 codice_fiscale, nazione, created_at, updated_at)
                    VALUES ('00000000-0000-7000-8000-0000000000a1', true, :nome, '01234567890',
                            'hmcrft00a01h501k', 'IT', now(), now())
                    """
                ),
                {"nome": long_name},
            )
            connection.execute(
                text(
                    """
                    INSERT INTO fiscal_profile (id, singleton, codice_regime, aliquota_iva_default,
                                                applica_bollo, soglia_bollo, importo_bollo,
                                                condizioni_pagamento, modalita_pagamento,
                                                giorni_scadenza, created_at, updated_at)
                    VALUES ('00000000-0000-7000-8000-0000000000f1', true, 'RF19', 0, true, 77.47,
                            2.00, 'TP02', 'MP05', 30, now(), now())
                    """
                )
            )
        engine.dispose()

        upgrade(config, "0045")

        engine = create_engine(url)
        with engine.connect() as connection:
            azienda = connection.execute(
                text(
                    "SELECT nome, predefinita, attiva, partita_iva, codice_fiscale "
                    "FROM emitter_profile"
                )
            ).one()
            fiscal_azienda = connection.execute(
                text("SELECT azienda_id FROM fiscal_profile")
            ).scalar_one()
            users_flag = connection.execute(
                text(
                    "SELECT column_default FROM information_schema.columns "
                    "WHERE table_name = 'users' AND column_name = 'ambito_limitato'"
                )
            ).scalar_one()
        engine.dispose()
        assert azienda.nome == "S" * 80
        assert azienda.predefinita is True and azienda.attiva is True
        assert azienda.partita_iva == "01234567890"
        assert azienda.codice_fiscale == "HMCRFT00A01H501K"
        assert str(fiscal_azienda) == "00000000-0000-7000-8000-0000000000a1"
        assert users_flag == "false"

        # One azienda: the downgrade goes back to 0044 and the two singletons return.
        downgrade(config, "0044")
        engine = create_engine(url)
        with engine.connect() as connection:
            assert connection.execute(text("SELECT singleton FROM emitter_profile")).scalar_one()
            assert connection.execute(text("SELECT singleton FROM fiscal_profile")).scalar_one()
        engine.dispose()

        # Two aziende: the downgrade refuses before it drops anything.
        upgrade(config, "0045")
        engine = create_engine(url)
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO emitter_profile (id, nome, ragione_sociale, nazione, predefinita,
                                                 attiva, created_at, updated_at)
                    VALUES ('00000000-0000-7000-8000-0000000000a2', 'rebase', 'Rebase S.r.l.',
                            'IT', false, true, now(), now());
                    """
                )
            )
        engine.dispose()
        with pytest.raises(Exception, match="more than one azienda"):
            downgrade(config, "0044")
        engine = create_engine(url)
        with engine.connect() as connection:
            assert (
                connection.execute(text("SELECT count(*) FROM emitter_profile")).scalar_one() == 2
            )
            assert (
                connection.execute(
                    text(
                        "SELECT count(*) FROM information_schema.columns "
                        "WHERE table_name = 'emitter_profile' AND column_name = 'nome'"
                    )
                ).scalar_one()
                == 1
            )
        engine.dispose()


def test_every_trigram_index_is_a_partial_gin_index_over_gin_trgm_ops(
    pigrocrm_postgres: Any,
) -> None:
    """A trigram index created without `gin_trgm_ops` is an ordinary GIN index that
    cannot serve `ILIKE '%x%'` at all, and one created without the `WHERE` clause is
    bigger than it needs to be and leaves residuo R7 open for that table. Both mistakes
    produce a green `compare_metadata`, so they are asserted on the definition text.
    """
    with pigrocrm_postgres.fresh_container() as container:
        url = container.get_connection_url()
        upgrade(_alembic_config(url), "head")

        engine: Engine = create_engine(url)
        with engine.connect() as connection:
            rows = connection.execute(
                text("SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = 'public'")
            ).all()
        engine.dispose()

    indexes = {row.indexname: row.indexdef for row in rows}
    # Ten -- nine from migration 0021 plus `invoices.causale` from 0024 -- and the count is
    # asserted: a set comprehension that silently matched nothing would make every
    # assertion below vacuous, and §8.3 names ten columns rather than "some".
    assert len(TRGM_INDEX_NAMES) == 10
    for name in sorted(TRGM_INDEX_NAMES):
        definition = indexes[name]
        assert "USING gin" in definition, f"{name} is not a GIN index: {definition}"
        assert "gin_trgm_ops" in definition, f"{name} lacks gin_trgm_ops: {definition}"
        assert "WHERE (deleted_at IS NULL)" in definition, (
            f"{name} is not partial on deleted_at IS NULL: {definition}"
        )
        assert "lower(" not in definition, (
            f"{name} wraps the column in lower(), which stops ILIKE on the raw column "
            f"from using it (spec §8.2): {definition}"
        )


# (index name, the exact `USING btree (...)` body Postgres must report). Residuo R9's
# ordering contract is `ORDER BY <col> <dir> NULLS LAST, id <dir>`, and an index only
# serves it when both members are present in that order -- a single-column index on
# `created_at` leaves the `id` tie-break to an in-memory sort, which is the cost the
# thirteen exist to avoid. Pinned as text because the *order* of the members and the
# `DESC NULLS LAST` qualifier are observable nowhere else.
SORT_INDEX_BODIES: dict[str, str] = {
    "ix_customers_created_at_id": "(created_at, id)",
    "ix_customers_updated_at_id": "(updated_at, id)",
    "ix_customers_ragione_sociale_id": "(ragione_sociale, id)",
    "ix_people_created_at_id": "(created_at, id)",
    "ix_people_updated_at_id": "(updated_at, id)",
    "ix_people_cognome_id": "(cognome, id)",
    "ix_people_cognome_desc_id": "(cognome DESC NULLS LAST, id DESC)",
    "ix_deals_created_at_id": "(created_at, id)",
    "ix_deals_updated_at_id": "(updated_at, id)",
    "ix_deals_nome_id": "(nome, id)",
    "ix_documents_created_at_id": "(created_at, id)",
    "ix_documents_updated_at_id": "(updated_at, id)",
    "ix_documents_titolo_id": "(titolo, id)",
}


def test_every_sort_index_is_a_btree_over_the_column_and_the_identifier(
    pigrocrm_postgres: Any,
) -> None:
    """Residuo R9's other half, asserted on the definition text rather than on the name.

    `compare_metadata` does not compare index expressions at all, so the one index that
    matters most here is exactly the one a schema diff would let through:
    `ix_people_cognome_desc_id`. Without it a descending scan of `people.cognome` reads
    the ascending index backwards, which yields NULLS FIRST -- not the order
    `db/sort.py::order_by` declares -- and Postgres sorts the whole table in memory
    instead.
    """
    with pigrocrm_postgres.fresh_container() as container:
        url = container.get_connection_url()
        upgrade(_alembic_config(url), "head")

        engine: Engine = create_engine(url)
        with engine.connect() as connection:
            rows = connection.execute(
                text("SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = 'public'")
            ).all()
        engine.dispose()

    indexes = {row.indexname: row.indexdef for row in rows}
    # Twelve ascending, plus the one the single nullable sort column costs.
    assert len(SORT_INDEX_BODIES) == 13
    for name, body in SORT_INDEX_BODIES.items():
        definition = indexes[name]
        assert f"USING btree {body}" in definition, definition
        # None of the thirteen is partial, unlike the trigram indexes above: ordering has
        # to reach every row the filters admit, and a `WHERE deleted_at IS NULL` predicate
        # here would make the index unusable for any future listing that asks for the
        # deleted ones.
        assert " WHERE " not in definition, definition


def _applied_revision(url: str) -> str:
    engine: Engine = create_engine(url)
    try:
        with engine.connect() as connection:
            return connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    finally:
        engine.dispose()


def test_env_prefers_an_explicit_config_url_over_settings(
    pigrocrm_postgres: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression for a real finding: `env.py` must not unconditionally prefer
    `get_settings()` over whatever URL the `Config` object already carries -- that is
    exactly what made a hand-edited `alembic.ini` (Alembic's own documented mechanism)
    silently do nothing. Proven adversarially: `get_settings()` is pointed at a URL that
    cannot possibly connect (nothing listens on port 1), while `_alembic_config` gives
    `upgrade()` the real container's URL. If `env.py` ever went back to always
    overriding with settings, this fails with a connection error instead of quietly
    passing.
    """
    monkeypatch.setenv(
        "PIGROCRM_DATABASE_URL", "postgresql+psycopg://nobody:nobody@127.0.0.1:1/nobody"
    )
    get_settings.cache_clear()
    try:
        with pigrocrm_postgres.fresh_container() as container:
            url = container.get_connection_url()
            upgrade(_alembic_config(url), "head")
            revision = _applied_revision(url)
    finally:
        get_settings.cache_clear()

    # A literal revision string here is a trap: it goes stale (silently, since nothing
    # re-runs it) the moment a later migration lands, as it already had twice over
    # (0025, then 0026) before this was changed to ask Alembic itself.
    expected_head = ScriptDirectory.from_config(_alembic_config(url)).get_current_head()
    assert revision == expected_head


def test_env_falls_back_to_settings_when_config_has_no_url(
    pigrocrm_postgres: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The brief's own default path, which must keep working exactly as before: a
    `Config` that nobody pointed anywhere (still carrying `alembic.ini`'s placeholder
    `sqlalchemy.url`) falls back to `get_settings()`, the same application-wide
    configuration source the rest of the codebase uses.
    """
    with pigrocrm_postgres.fresh_container() as container:
        url = container.get_connection_url()
        monkeypatch.setenv("PIGROCRM_DATABASE_URL", url)
        get_settings.cache_clear()
        try:
            config = Config(str(CORE_ROOT / "alembic.ini"))
            config.set_main_option("script_location", str(CORE_ROOT / "migrations"))
            # `sqlalchemy.url` deliberately left untouched -- still the ini's placeholder.
            upgrade(config, "head")
            revision = _applied_revision(url)
        finally:
            get_settings.cache_clear()

    # A literal revision string here is a trap: it goes stale (silently, since nothing
    # re-runs it) the moment a later migration lands, as it already had twice over
    # (0025, then 0026) before this was changed to ask Alembic itself.
    expected_head = ScriptDirectory.from_config(config).get_current_head()
    assert revision == expected_head


def test_a_migration_leaves_the_host_process_loggers_emitting(pigrocrm_postgres: Any) -> None:
    """REB-190: `env.py` calls Alembic's `fileConfig`, whose default
    `disable_existing_loggers=True` is right for `alembic upgrade` from a shell and
    wrong inside the API process, where `migrate_to_head` runs on every signup and at
    every boot: it disabled every logger the ini does not name (root, sqlalchemy,
    alembic) -- uvicorn's and `pigrocrm.core.mail`'s among them. CI showed it the day
    the route matrix shifted the xdist distribution and `test_mail.py` landed on the
    worker that had just migrated: `caplog` stayed empty. A handler of our own on a
    pre-existing logger, not `caplog`: `fileConfig` rebuilds the ROOT handler list
    too, so a root-attached capture would go blind for the fix-independent reason.
    """
    import logging

    grabbed: list[str] = []

    class Grab(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            grabbed.append(record.getMessage())

    witness = logging.getLogger("pigrocrm.witness-for-reb-190")
    handler = Grab()
    witness.addHandler(handler)
    witness.setLevel(logging.WARNING)
    try:
        with pigrocrm_postgres.fresh_container() as container:
            upgrade(_alembic_config(container.get_connection_url()), "head")
            witness.warning("ancora viva")
    finally:
        witness.removeHandler(handler)
        witness.setLevel(logging.NOTSET)

    assert "ancora viva" in grabbed, (
        "a migration disabled an existing logger: the first signup in the API process "
        "would silence uvicorn's and the application's own log lines"
    )


def test_stato_dal_is_backfilled_from_the_timeline_and_chiuso_il_is_not() -> None:
    """The asymmetry of spec §4.1, asserted rather than described.

    A migration that quietly backfilled `chiuso_il` from the stage-change payload would
    pass every other test in this file and would silently attribute deals to months
    derived from a renamable string. Asserted on the migration's source text because the
    absence of a statement is observable nowhere else: a schema comparison sees identical
    columns whether they were filled or left null.
    """
    source = (CORE_ROOT / "migrations" / "versions" / "0023_automations_and_dates.py").read_text(
        encoding="utf-8"
    )
    assert "UPDATE documents" in source
    assert "state_changed" in source
    assert "UPDATE deals" not in source, (
        "deals.chiuso_il must not be backfilled: move_stage records stage *names*, which "
        "are renamable (residuo R15). See spec §4.1."
    )
    assert "stage_changed" not in source


def test_the_backfill_reaches_only_offers_and_reads_the_emitters_day() -> None:
    """Two clauses that a passing schema comparison cannot see either.

    `d.stato IS NOT NULL` is what keeps the backfill to offers: `documents.stato` is NULL
    on every non-offer document, and stamping a `stato_dal` on a row with no state would
    make "this offer has been sitting for N days" answer for a contract.

    `AT TIME ZONE 'Europe/Rome'` is the same rule as `db/clock.py`, applied to history: a
    state set at 00:30 CET on 1 January was set on 1 January, and a bare `::date` on a
    `timestamptz` would record it as the previous year.
    """
    source = (CORE_ROOT / "migrations" / "versions" / "0023_automations_and_dates.py").read_text(
        encoding="utf-8"
    )
    assert "d.stato IS NOT NULL" in source
    assert "AT TIME ZONE" in source
    assert 'Europe/Rome"' in source or "Europe/Rome'" in source


def test_the_backfill_actually_fills_the_right_day_on_the_right_rows(
    pigrocrm_postgres: Any,
) -> None:
    """The backfill run against real rows, not read as text.

    Every other assertion about migration 0023 is a substring check, and a substring check
    cannot tell a correct `DISTINCT ON` from one that picks the *first* state change, nor
    a `AT TIME ZONE` that is applied from one that is written and ignored. This runs the
    revision over rows planted at 0022 and reads back what it wrote.

    The newest state change is at 22:30 UTC on 30 June, which is 00:30 on 1 July in Rome
    (CEST, +02:00). Four distinct defects each change the answer:

      * taking the oldest state change instead of the newest -> 10 March;
      * dropping the `kind` filter -> 1 August, the day of the unrelated activity;
      * projecting through UTC instead of the emitter's zone -> 30 June;
      * dropping `d.stato IS NOT NULL` -> the contract gets a `stato_dal` too.
    """
    with pigrocrm_postgres.fresh_container() as container:
        url = container.get_connection_url()
        config = _alembic_config(url)
        upgrade(config, "0022")

        engine: Engine = create_engine(url)
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO customers (id, ragione_sociale, nazione, custom_fields,
                                           created_at, updated_at)
                    VALUES ('00000000-0000-7000-8000-0000000000c1', 'Cliente Storico', 'IT',
                            '{}'::jsonb, now(), now());

                    INSERT INTO pipeline_stages (id, nome, posizione, probabilita_default,
                                                 tipo, created_at, updated_at)
                    VALUES ('00000000-0000-7000-8000-0000000000e1', 'Vinto', 0, 100, 'won',
                            now(), now());

                    INSERT INTO deals (id, nome, customer_id, pipeline_stage_id, probabilita,
                                       custom_fields, created_at, updated_at)
                    VALUES ('00000000-0000-7000-8000-0000000000d0', 'Affare chiuso',
                            '00000000-0000-7000-8000-0000000000c1',
                            '00000000-0000-7000-8000-0000000000e1', 100, '{}'::jsonb,
                            now(), now());

                    INSERT INTO documents (id, customer_id, tipo, titolo, stato,
                                           versione_corrente, custom_fields,
                                           created_at, updated_at)
                    VALUES ('00000000-0000-7000-8000-0000000000a1',
                            '00000000-0000-7000-8000-0000000000c1', 'offerta', 'Offerta',
                            'inviata', 0, '{}'::jsonb, now(), now()),
                           ('00000000-0000-7000-8000-0000000000a2',
                            '00000000-0000-7000-8000-0000000000c1', 'verbale', 'Verbale',
                            NULL, 0, '{}'::jsonb, now(), now());

                    INSERT INTO activities (id, entity_type, entity_id, kind, actor_type,
                                            payload, occurred_at)
                    VALUES ('00000000-0000-7000-8000-0000000000b1', 'document',
                            '00000000-0000-7000-8000-0000000000a1', 'state_changed', 'system',
                            '{"da": "bozza", "a": "inviata"}'::jsonb,
                            '2026-03-10T09:00:00+00:00'),
                           ('00000000-0000-7000-8000-0000000000b2', 'document',
                            '00000000-0000-7000-8000-0000000000a1', 'state_changed', 'system',
                            '{"da": "inviata", "a": "accettata"}'::jsonb,
                            '2026-06-30T22:30:00+00:00'),
                           ('00000000-0000-7000-8000-0000000000b3', 'document',
                            '00000000-0000-7000-8000-0000000000a1', 'version_added', 'system',
                            '{}'::jsonb, '2026-08-01T12:00:00+00:00'),
                           ('00000000-0000-7000-8000-0000000000b4', 'document',
                            '00000000-0000-7000-8000-0000000000a2', 'state_changed', 'system',
                            '{"da": "bozza", "a": "inviata"}'::jsonb,
                            '2026-05-05T12:00:00+00:00'),
                           ('00000000-0000-7000-8000-0000000000b5', 'deal',
                            '00000000-0000-7000-8000-0000000000d0', 'stage_changed', 'system',
                            '{"from": "Offerta", "to": "Vinto"}'::jsonb,
                            '2026-04-04T12:00:00+00:00');
                    """
                )
            )
        engine.dispose()

        upgrade(config, "0023")

        engine = create_engine(url)
        with engine.connect() as connection:
            offerta, verbale = (
                connection.execute(text("SELECT stato_dal FROM documents ORDER BY id"))
                .scalars()
                .all()
            )
            chiuso_il = connection.execute(text("SELECT chiuso_il FROM deals")).scalar_one()
        engine.dispose()

    assert offerta == date(2026, 7, 1), offerta
    assert verbale is None, "a document with no stato must not acquire a stato_dal"
    assert chiuso_il is None, (
        "deals.chiuso_il must stay null: a closure deduced from a renamable stage name is "
        "a conversion rate that is plausible and wrong (spec §4.1)"
    )


def test_the_migration_seeds_exactly_one_automation_config_row(pigrocrm_postgres: Any) -> None:
    """Two paths build this schema and both must produce one row.

    `Base.metadata.create_all` (the test suite) leaves the table empty and
    `AutomationConfigRepository.get_or_create` fills it on first read; a migrated
    installation gets the row from `0023` before the first request. A migration that
    created the table and forgot the `INSERT` would still pass every schema comparison in
    this file -- slice 3 lost `proforma_riferimento_seq` to exactly that gap -- and the
    first `GET` would then be the request that writes.

    Asserted as a count, not as "at least one": a second seeded row would make
    `get_or_create`'s `LIMIT 1` return whichever the planner preferred, so an operator's
    change to the configuration could stop taking effect between one request and the next.
    """
    with pigrocrm_postgres.fresh_container() as container:
        url = container.get_connection_url()
        upgrade(_alembic_config(url), "head")

        engine: Engine = create_engine(url)
        with engine.connect() as connection:
            rows = connection.execute(
                text(
                    "SELECT a1_offerta_accettata_vince_deal, a2_offerta_inviata_avanza_deal "
                    "FROM automation_config"
                )
            ).all()
        engine.dispose()

    assert len(rows) == 1, rows
    # Both defaults are `true`, and they arrive from the *server* default: the migration's
    # INSERT names only `id`. An automation nobody switched on is one nobody knows exists.
    assert rows[0] == (True, True)


def test_the_activity_feed_index_is_descending_on_both_columns(pigrocrm_postgres: Any) -> None:
    """An ascending index would still be used -- backwards -- but a backward scan of
    `(occurred_at, id)` yields the tie-break ascending within each instant, which is not
    the order `ActivityRepository.recent` declares. Asserting the declared shape is what
    stops a future "simplification" to a single-column ascending index that cannot serve
    the tie-break at all.

    On `indexdef` text and not through `compare_metadata`, which does not compare index
    expressions: a descending expression index is exactly the shape a schema diff lets
    through in silence.
    """
    with pigrocrm_postgres.fresh_container() as container:
        url = container.get_connection_url()
        upgrade(_alembic_config(url), "head")
        engine: Engine = create_engine(url)
        with engine.connect() as connection:
            definition = connection.execute(
                text(
                    "SELECT indexdef FROM pg_indexes "
                    "WHERE schemaname = 'public' AND indexname = 'ix_activities_recent'"
                )
            ).scalar_one()
        engine.dispose()
    assert "occurred_at DESC" in definition, definition
    assert "id DESC" in definition, definition
    # Both members, in this order: an index on `occurred_at DESC` alone leaves the
    # tie-break to an in-memory sort, which is the cost this index exists to remove.
    assert "USING btree (occurred_at DESC, id DESC)" in definition, definition


def test_a_write_folder_configured_before_0027_is_not_assumed_verified(
    pigrocrm_postgres: Any,
) -> None:
    """The one thing migration 0027 has to get right about the rows that already exist.

    `storage_folder_verified` answers "did anybody ever prove this folder is reachable
    with this credential?", and for every folder configured before the column existed
    the honest answer is no -- `set_roots` verified a *new* folder and had nowhere to
    record it, so the flag cannot be backfilled from anything. A `server_default` of
    `true`, or a backfill "because the folder is set", would make the first save after
    an upgrade skip the very verification the column was added to make happen.

    Asserted against a real row planted at 0026: a schema comparison sees the same
    column whichever default it carries.
    """
    with pigrocrm_postgres.fresh_container() as container:
        url = container.get_connection_url()
        config = _alembic_config(url)
        upgrade(config, "0026")

        engine: Engine = create_engine(url)
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO users (id, email, password_hash, nome, ruolo, attivo,
                                       created_at, updated_at)
                    VALUES ('00000000-0000-7000-8000-0000000000f1', 'titolare@example.it',
                            'x', 'Titolare', 'admin', true, now(), now());

                    INSERT INTO google_drive_accounts (
                        id, user_id, google_sub, email_address, refresh_token_ciphertext,
                        refresh_token_nonce, scopes_granted, status, root_folder_ids,
                        storage_folder_id, connected_at, created_at, updated_at)
                    VALUES ('00000000-0000-7000-8000-0000000000f2',
                            '00000000-0000-7000-8000-0000000000f1', 'sub-1',
                            'titolare@example.it', '\\x01'::bytea, '\\x02'::bytea,
                            '[]'::jsonb, 'active', '[]'::jsonb, '1AbCdEfGhIjKlMnOpQ',
                            now(), now(), now());
                    """
                )
            )
        engine.dispose()

        upgrade(config, "0027")

        engine = create_engine(url)
        with engine.connect() as connection:
            verified = connection.execute(
                text("SELECT storage_folder_verified FROM google_drive_accounts")
            ).scalar_one()
        engine.dispose()

    assert verified is False, (
        "a folder configured before the column existed was never proven: assuming it was "
        "makes the first save after the upgrade skip the verification 0027 exists to run"
    )


def test_0029_moves_any_legacy_import_provenance_to_esterno(pigrocrm_postgres: Any) -> None:
    """The two things migration 0029 has to get right about the rows that already exist.

    `invoices.importata_da` is read back by the API and by MCP, and its value used to be
    the *name of a product* -- the previous invoicing tool. Renaming the literal in the
    schema without moving the stored rows would leave the fourteen imported invoices
    holding a value no `Literal` admits any more: `InvoiceRead` would still hand it to a
    client, and the badge would print it. So the rename is a data migration.

    Seeded here with a value the migration has never been told about, because that is
    what it now claims: it matches on "neither NULL nor `'esterno'`" rather than on one
    spelling, so any legacy provenance in any older database is collapsed, and a row
    PigroCRM issued itself stays NULL. Naming the old value here would have put it back
    in the repository, and pinning the assertion to that one name would have let a
    different legacy spelling through while the test stayed green.

    The downgrade is asserted to be a no-op on purpose. It used to restore the old
    literal; that literal is gone from this repository, so restoring it is the one thing
    the rollback must not do, and dropping to NULL would tell every reader that PigroCRM
    issued these documents itself.

    The `imported` activity each of those rows wrote copied the value into its JSONB
    payload (`invoices/service.py::import_issued`), and `ActivityRead` hands the payload
    to the client whole -- so a payload left behind keeps printing the old name on the
    invoice's own timeline. Both halves move together, or neither has moved.

    Asserted against real rows planted at 0028 rather than through the ORM: the column
    is a `String(20)` on both sides of the migration, so a schema comparison sees no
    difference at all and only the stored value tells the two apart.
    """
    with pigrocrm_postgres.fresh_container() as container:
        url = container.get_connection_url()
        config = _alembic_config(url)
        upgrade(config, "0028")

        engine: Engine = create_engine(url)
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO customers (id, ragione_sociale, nazione, custom_fields,
                                           created_at, updated_at)
                    VALUES ('00000000-0000-7000-8000-0000000000e1', 'Cliente Uno', 'IT',
                            '{}'::jsonb, now(), now());

                    INSERT INTO invoices (id, customer_id, tipo, stato, anno, numero,
                                          data_emissione, tipo_documento, divisa,
                                          imponibile, imposta, bollo, totale,
                                          stato_pagamento, custom_fields, importata_da,
                                          created_at, updated_at)
                    VALUES ('00000000-0000-7000-8000-0000000000e2',
                            '00000000-0000-7000-8000-0000000000e1', 'fattura', 'emessa',
                            2026, 7, '2026-05-05', 'TD01', 'EUR',
                            100.00, 0.00, 0.00, 100.00, 'da_incassare', '{}'::jsonb,
                            'legacy', now(), now()),
                           ('00000000-0000-7000-8000-0000000000e3',
                            '00000000-0000-7000-8000-0000000000e1', 'fattura', 'emessa',
                            2026, 18, '2026-09-05', 'TD01', 'EUR',
                            100.00, 0.00, 0.00, 100.00, 'da_incassare', '{}'::jsonb,
                            NULL, now(), now());

                    INSERT INTO activities (id, entity_type, entity_id, kind, actor_type,
                                            payload, occurred_at)
                    VALUES ('00000000-0000-7000-8000-0000000000e4', 'invoice',
                            '00000000-0000-7000-8000-0000000000e2', 'imported', 'user',
                            '{"anno": 2026, "numero": 7, "totale": "100.00",
                               "importata_da": "legacy"}'::jsonb, now()),
                           ('00000000-0000-7000-8000-0000000000e5', 'invoice',
                            '00000000-0000-7000-8000-0000000000e3', 'issued', 'user',
                            '{"anno": 2026, "numero": 18}'::jsonb, now());
                    """
                )
            )
        engine.dispose()

        upgrade(config, "0029")

        engine = create_engine(url)
        with engine.connect() as connection:
            after_upgrade = dict(
                connection.execute(text("SELECT numero, importata_da FROM invoices")).all()
            )
            payloads_after_upgrade = dict(
                connection.execute(text("SELECT kind, payload FROM activities ORDER BY kind")).all()
            )
        engine.dispose()

        downgrade(config, "0028")

        engine = create_engine(url)
        with engine.connect() as connection:
            after_downgrade = dict(
                connection.execute(text("SELECT numero, importata_da FROM invoices")).all()
            )
            payloads_after_downgrade = dict(
                connection.execute(text("SELECT kind, payload FROM activities ORDER BY kind")).all()
            )
        engine.dispose()

    assert after_upgrade == {7: "esterno", 18: None}, (
        "the imported rows must carry the new literal, and an invoice PigroCRM issued "
        f"itself must stay NULL: got {after_upgrade}"
    )
    assert after_downgrade == {7: "esterno", 18: None}, (
        "the downgrade must not resurrect the old product name, and must not drop to "
        f"NULL either, which would claim PigroCRM issued the document: got {after_downgrade}"
    )
    assert payloads_after_upgrade["imported"]["importata_da"] == "esterno", (
        "the payload of the import's own activity travels to the timeline whole, so a "
        f"payload left behind prints the old name there: got {payloads_after_upgrade}"
    )
    # The rest of the payload is untouched, and an activity of another `kind` gains no
    # key it never had: `jsonb_set` on a missing path would *add* one.
    assert payloads_after_upgrade["imported"]["numero"] == 7
    assert "importata_da" not in payloads_after_upgrade["issued"]
    assert payloads_after_downgrade["imported"]["importata_da"] == "esterno"


def _competenza_checks(url: str) -> set[str]:
    engine: Engine = create_engine(url)
    try:
        with engine.connect() as connection:
            return set(
                connection.execute(
                    text(
                        "SELECT conname FROM pg_constraint "
                        "WHERE conrelid = 'invoices'::regclass "
                        "AND conname LIKE 'ck_invoices_competenza%'"
                    )
                ).scalars()
            )
    finally:
        engine.dispose()


def _proforma_dates(url: str) -> dict[str, date | None]:
    engine: Engine = create_engine(url)
    try:
        with engine.connect() as connection:
            return dict(
                connection.execute(
                    text(
                        "SELECT coalesce(riferimento, numero::text, 'bozza'), data_emissione "
                        "FROM invoices"
                    )
                ).all()
            )
    finally:
        engine.dispose()


def test_0033_dates_every_undated_proforma_in_rome_and_touches_no_fattura(
    pigrocrm_postgres: Any,
) -> None:
    """The backfill of migration 0033 (ORB-63), asserted on rows planted at 0032.

    A proforma used to keep `data_emissione` `NULL` until a fattura was issued from it,
    and its PDF printed the Europe/Rome civil date of `created_at`. The backfill has to
    write exactly that date -- so no proforma anyone has already received changes its
    date -- and it has to be the *Rome* date: a row created at 22:30 UTC on 30 June is a
    document dated 1 July, and one created at 23:30 UTC on New Year's Eve belongs to the
    next year. `date(created_at)` would have said June and December. The two fattura
    rows are the other half of the assertion: a draft's `NULL` means "not yet issued"
    and must stay, and an issued row's date is a register entry the migration may not
    look at.

    Then the round trip. The downgrade drops the two period columns and their CHECKs and
    deliberately leaves the dates in place (its docstring says why), so upgrading again
    must find them already set and move nothing: the `WHERE data_emissione IS NULL` is
    what makes the UPDATE idempotent, and this is where that is proven rather than read.
    """
    with pigrocrm_postgres.fresh_container() as container:
        url = container.get_connection_url()
        config = _alembic_config(url)
        upgrade(config, "0032")

        engine: Engine = create_engine(url)
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO customers (id, ragione_sociale, nazione, custom_fields,
                                           created_at, updated_at)
                    VALUES ('00000000-0000-7000-8000-0000000000f1', 'Cliente Uno', 'IT',
                            '{}'::jsonb, now(), now());

                    INSERT INTO invoices (id, customer_id, tipo, stato, anno, numero,
                                          riferimento, data_emissione, tipo_documento,
                                          divisa, imponibile, imposta, bollo, totale,
                                          stato_pagamento, custom_fields,
                                          created_at, updated_at)
                    VALUES ('00000000-0000-7000-8000-0000000000f2',
                            '00000000-0000-7000-8000-0000000000f1', 'proforma', 'bozza',
                            NULL, NULL, 'PROV-2026-0001', NULL, 'TD01', 'EUR',
                            100.00, 0.00, 0.00, 100.00, 'da_incassare', '{}'::jsonb,
                            '2026-06-30T22:30:00Z', '2026-06-30T22:30:00Z'),
                           ('00000000-0000-7000-8000-0000000000f3',
                            '00000000-0000-7000-8000-0000000000f1', 'proforma', 'confermata',
                            NULL, NULL, 'PROV-2025-0009', NULL, 'TD01', 'EUR',
                            100.00, 0.00, 0.00, 100.00, 'da_incassare', '{}'::jsonb,
                            '2025-12-31T23:30:00Z', '2025-12-31T23:30:00Z'),
                           ('00000000-0000-7000-8000-0000000000f4',
                            '00000000-0000-7000-8000-0000000000f1', 'fattura', 'bozza',
                            NULL, NULL, NULL, NULL, 'TD01', 'EUR',
                            100.00, 0.00, 0.00, 100.00, 'da_incassare', '{}'::jsonb,
                            '2026-06-30T22:30:00Z', '2026-06-30T22:30:00Z'),
                           ('00000000-0000-7000-8000-0000000000f5',
                            '00000000-0000-7000-8000-0000000000f1', 'fattura', 'emessa',
                            2026, 1, NULL, '2026-05-05', 'TD01', 'EUR',
                            100.00, 0.00, 0.00, 100.00, 'da_incassare', '{}'::jsonb,
                            '2026-05-05T10:00:00Z', '2026-05-05T10:00:00Z');
                    """
                )
            )
        engine.dispose()
        assert _competenza_checks(url) == set()

        upgrade(config, "0033")
        after_upgrade = _proforma_dates(url)
        checks_after_upgrade = _competenza_checks(url)

        downgrade(config, "0032")
        after_downgrade = _proforma_dates(url)
        checks_after_downgrade = _competenza_checks(url)

        upgrade(config, "0033")
        after_second_upgrade = _proforma_dates(url)
        checks_after_second_upgrade = _competenza_checks(url)

    expected = {
        "PROV-2026-0001": date(2026, 7, 1),
        "PROV-2025-0009": date(2026, 1, 1),
        "bozza": None,
        "1": date(2026, 5, 5),
    }
    assert after_upgrade == expected, (
        "every undated proforma takes the Europe/Rome civil date of its created_at, a "
        f"fattura draft stays NULL and an issued fattura keeps its date: got {after_upgrade}"
    )
    assert checks_after_upgrade == {
        "ck_invoices_competenza_together",
        "ck_invoices_competenza_ordered",
    }
    assert after_downgrade == expected, (
        f"the downgrade leaves the proforma dates in place on purpose: got {after_downgrade}"
    )
    assert checks_after_downgrade == set()
    assert after_second_upgrade == expected, (
        "the UPDATE is idempotent: a date already set is not moved again, so a second "
        f"upgrade changes nothing: got {after_second_upgrade}"
    )
    assert checks_after_second_upgrade == checks_after_upgrade


def test_0041_backfills_pack_id_and_pack_version_on_the_existing_row(
    pigrocrm_postgres: Any,
) -> None:
    """REB-361. The `server_default` alone already makes a row inserted at
    `ADD COLUMN` time satisfy the `NOT NULL` constraint; this asserts the belt-and-
    suspenders `UPDATE` the migration also runs, against a row planted before the
    column existed at all -- exactly the one row `fiscal_profile` has ever held in
    production."""
    with pigrocrm_postgres.fresh_container() as container:
        url = container.get_connection_url()
        config = _alembic_config(url)
        upgrade(config, "0039")

        engine: Engine = create_engine(url)
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO fiscal_profile (id, singleton, codice_regime,
                        aliquota_iva_default, applica_bollo, soglia_bollo,
                        importo_bollo, condizioni_pagamento, modalita_pagamento,
                        giorni_scadenza, created_at, updated_at)
                    VALUES ('00000000-0000-7000-8000-0000000000f1', true, 'RF19',
                        0.00, true, 77.47, 2.00, 'TP02', 'MP05', 30, now(), now());
                    """
                )
            )
        engine.dispose()

        upgrade(config, "0041")

        engine = create_engine(url)
        with engine.connect() as connection:
            pack_id, pack_version = connection.execute(
                text("SELECT pack_id, pack_version FROM fiscal_profile")
            ).one()
        engine.dispose()

    assert (pack_id, pack_version) == ("it-flat-rate", "1")


def test_0046_keys_the_register_by_azienda_over_planted_rows_and_refuses_to_undo_two(
    pigrocrm_postgres: Any,
) -> None:
    """The migration over real rows planted at 0045, not read as text (REB-619): a
    default azienda, a fiscal profile, a counter and a declared gap. The upgrade keys
    counter and gap to the azienda, swaps the two uniques and the primary key, lets
    `codice_regime` be empty and widens the P.IVA; the downgrade with one azienda goes
    back, and with a second azienda's counter it refuses before touching anything."""
    azienda = "00000000-0000-7000-8000-0000000000a1"
    with pigrocrm_postgres.fresh_container() as container:
        url = container.get_connection_url()
        config = _alembic_config(url)
        upgrade(config, "0045")

        engine: Engine = create_engine(url)
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO emitter_profile (id, nome, predefinita, attiva, ragione_sociale,
                                                 nazione, created_at, updated_at)
                    VALUES (:id, 'Studio', true, true, 'Studio Rossi', 'IT', now(), now())
                    """
                ),
                {"id": azienda},
            )
            connection.execute(
                text(
                    """
                    INSERT INTO fiscal_profile (id, azienda_id, codice_regime, aliquota_iva_default,
                                                applica_bollo, soglia_bollo, importo_bollo,
                                                condizioni_pagamento, modalita_pagamento,
                                                giorni_scadenza, created_at, updated_at)
                    VALUES ('00000000-0000-7000-8000-0000000000f1', :id, 'RF19', 0, true, 77.47,
                            2.00, 'TP02', 'MP05', 30, now(), now())
                    """
                ),
                {"id": azienda},
            )
            connection.execute(
                text("INSERT INTO invoice_counters (anno, ultimo_numero) VALUES (2026, 3)")
            )
            connection.execute(
                text(
                    "INSERT INTO invoice_register_gaps (id, anno, numero, motivo, dichiarato_il) "
                    "VALUES ('00000000-0000-7000-8000-0000000000b1', 2026, 2, 'mai emessa', now())"
                )
            )
            # A numbered invoice too, the one table every real space has rows in: the
            # backfill, the NOT NULL and the swap of the partial unique index with data
            # under it are what production meets.
            connection.execute(
                text(
                    "INSERT INTO customers (id, ragione_sociale, nazione, custom_fields, "
                    "created_at, updated_at) VALUES ('00000000-0000-7000-8000-0000000000c1', "
                    "'Acme', 'IT', '{}', now(), now())"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO invoices (id, customer_id, tipo, stato, anno, numero, "
                    "data_emissione, tipo_documento, divisa, imponibile, imposta, bollo, totale, "
                    "stato_pagamento, custom_fields, created_at, updated_at) VALUES "
                    "('00000000-0000-7000-8000-0000000000d1', "
                    "'00000000-0000-7000-8000-0000000000c1', 'fattura', 'emessa', 2026, 3, "
                    "'2026-03-01', 'TD01', 'EUR', 100, 0, 0, 100, 'da_incassare', '{}', "
                    "now(), now())"
                )
            )
        engine.dispose()

        upgrade(config, "0046")

        engine = create_engine(url)
        with engine.connect() as connection:
            invoice_azienda = connection.execute(
                text("SELECT azienda_id FROM invoices WHERE numero = 3")
            ).scalar_one()
            counter = connection.execute(
                text("SELECT azienda_id, anno, ultimo_numero FROM invoice_counters")
            ).one()
            gap = connection.execute(
                text("SELECT azienda_id, anno, numero FROM invoice_register_gaps")
            ).one()
            pk = connection.execute(
                text(
                    "SELECT array_agg(a.attname ORDER BY k.n) FROM pg_index i "
                    "JOIN LATERAL unnest(i.indkey) WITH ORDINALITY AS k(attnum, n) ON true "
                    "JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = k.attnum "
                    "WHERE i.indrelid = 'invoice_counters'::regclass AND i.indisprimary"
                )
            ).scalar_one()
            invoice_indexes = set(
                connection.execute(
                    text("SELECT indexname FROM pg_indexes WHERE tablename = 'invoices'")
                ).scalars()
            )
            regime_nullable, piva_width = connection.execute(
                text(
                    "SELECT (SELECT is_nullable FROM information_schema.columns "
                    "        WHERE table_name = 'fiscal_profile' "
                    "        AND column_name = 'codice_regime'), "
                    "       (SELECT character_maximum_length FROM information_schema.columns "
                    "        WHERE table_name = 'emitter_profile' AND column_name = 'partita_iva')"
                )
            ).one()
        engine.dispose()
        assert str(invoice_azienda) == azienda
        assert (str(counter.azienda_id), counter.anno, counter.ultimo_numero) == (azienda, 2026, 3)
        assert (str(gap.azienda_id), gap.anno, gap.numero) == (azienda, 2026, 2)
        assert pk == ["azienda_id", "anno"]
        assert "uq_invoices_azienda_anno_numero" in invoice_indexes
        assert "ix_invoices_anno_numero" in invoice_indexes
        assert "uq_invoices_anno_numero" not in invoice_indexes
        assert "ix_invoices_azienda_id" in invoice_indexes
        assert (regime_nullable, piva_width) == ("YES", 20)

        # One azienda: the downgrade goes back to 0045 and the old key returns.
        downgrade(config, "0045")
        engine = create_engine(url)
        with engine.connect() as connection:
            invoice_indexes = set(
                connection.execute(
                    text("SELECT indexname FROM pg_indexes WHERE tablename = 'invoices'")
                ).scalars()
            )
            counter_columns = set(
                connection.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_name = 'invoice_counters'"
                    )
                ).scalars()
            )
        engine.dispose()
        assert "uq_invoices_anno_numero" in invoice_indexes
        assert "ix_invoices_anno_numero" not in invoice_indexes
        assert "azienda_id" not in counter_columns

        # The downgrade refuses what it could not give back: a profile with no regime
        # code, a P.IVA wider than the old column, and then a second azienda with a
        # register of its own; each time the head stays at 0046.
        upgrade(config, "0046")
        engine = create_engine(url)
        for setup, undo, reason in (
            (
                "UPDATE fiscal_profile SET codice_regime = NULL",
                "UPDATE fiscal_profile SET codice_regime = 'RF19'",
                "no regime code",
            ),
            (
                "UPDATE emitter_profile SET partita_iva = 'FR12345678901'",
                "UPDATE emitter_profile SET partita_iva = NULL",
                "longer than 11",
            ),
        ):
            with engine.begin() as connection:
                connection.execute(text(setup))
            with pytest.raises(Exception, match=reason):
                downgrade(config, "0045")
            with engine.begin() as connection:
                connection.execute(text(undo))
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO emitter_profile (id, nome, predefinita, attiva, ragione_sociale,
                                                 nazione, created_at, updated_at)
                    VALUES ('00000000-0000-7000-8000-0000000000a2', 'rebase', false, true,
                            'Rebase S.r.l.', 'IT', now(), now())
                    """
                )
            )
            connection.execute(
                text(
                    "INSERT INTO invoice_counters (azienda_id, anno, ultimo_numero) "
                    "VALUES ('00000000-0000-7000-8000-0000000000a2', 2026, 1)"
                )
            )
        engine.dispose()
        with pytest.raises(Exception, match="more than one azienda"):
            downgrade(config, "0045")
        engine = create_engine(url)
        with engine.connect() as connection:
            head = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        engine.dispose()
        assert head == "0046"


def test_0047_binds_every_existing_row_to_the_one_azienda_and_a_cost_to_its_deals(
    pigrocrm_postgres: Any,
) -> None:
    """The migration over real rows planted at 0046 (REB-623): a customer, a deal, a
    contract, a document, a cost on the deal and a shared one. Every owned row is bound
    to the default azienda, the cost on a deal to the deal's, the shared cost to none;
    the downgrade drops the five columns and the indexes with them."""
    azienda = "00000000-0000-7000-8000-0000000000a1"
    with pigrocrm_postgres.fresh_container() as container:
        url = container.get_connection_url()
        config = _alembic_config(url)
        upgrade(config, "0046")

        engine: Engine = create_engine(url)
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO emitter_profile (id, nome, predefinita, attiva, ragione_sociale, "
                    "nazione, created_at, updated_at) VALUES (:id, 'Studio', true, true, "
                    "'Studio Rossi', 'IT', now(), now())"
                ),
                {"id": azienda},
            )
            connection.execute(
                text(
                    "INSERT INTO customers (id, ragione_sociale, nazione, custom_fields, "
                    "created_at, updated_at) VALUES ('00000000-0000-7000-8000-0000000000c1', "
                    "'Acme', 'IT', '{}', now(), now())"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO pipeline_stages (id, nome, posizione, probabilita_default, tipo, "
                    "created_at, updated_at) VALUES ('00000000-0000-7000-8000-0000000000e1', "
                    "'Lead', 0, 10, 'open', now(), now())"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO deals (id, nome, customer_id, pipeline_stage_id, probabilita, "
                    "custom_fields, created_at, updated_at) VALUES "
                    "('00000000-0000-7000-8000-0000000000d1', 'Deal', "
                    "'00000000-0000-7000-8000-0000000000c1', "
                    "'00000000-0000-7000-8000-0000000000e1', 10, '{}', now(), now())"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO contracts (id, customer_id, titolo, inizio, tipo_rinnovo, "
                    "preavviso_disdetta_giorni, cadenza_fatturazione, divisa, stato, "
                    "politica_spese, custom_fields, created_at, updated_at) VALUES "
                    "('00000000-0000-7000-8000-00000000000c', "
                    "'00000000-0000-7000-8000-0000000000c1', 'Consulenza', '2026-01-01', "
                    "'nessuno', 30, 'mensile', 'EUR', 'bozza', '{\"tipo\": \"non_rimborsabile\"}', "
                    "'{}', now(), now())"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO documents (id, customer_id, tipo, titolo, versione_corrente, "
                    "custom_fields, created_at, updated_at) VALUES "
                    "('00000000-0000-7000-8000-00000000000d', "
                    "'00000000-0000-7000-8000-0000000000c1', 'documento', 'Brief', 0, '{}', "
                    "now(), now())"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO cost_categories (id, nome, posizione, archiviata, created_at, "
                    "updated_at) VALUES ('00000000-0000-7000-8000-00000000000a', 'Viaggi', 0, "
                    "false, now(), now())"
                )
            )
            for cost_id, deal in (
                ("00000000-0000-7000-8000-0000000000f1", "'00000000-0000-7000-8000-0000000000d1'"),
                ("00000000-0000-7000-8000-0000000000f2", "NULL"),
            ):
                connection.execute(
                    text(
                        "INSERT INTO costs (id, deal_id, category_id, data, importo, descrizione, "
                        f"custom_fields, created_at, updated_at) VALUES (:id, {deal}, "
                        "'00000000-0000-7000-8000-00000000000a', '2026-03-01', 10, 'x', '{}', "
                        "now(), now())"
                    ),
                    {"id": cost_id},
                )
        engine.dispose()

        upgrade(config, "0047")

        engine = create_engine(url)
        with engine.connect() as connection:
            owned = {
                table: str(
                    connection.execute(text(f"SELECT azienda_id FROM {table}")).scalar_one()  # noqa: S608
                )
                for table in ("customers", "deals", "contracts", "documents")
            }
            costs = dict(
                connection.execute(
                    text("SELECT id::text, azienda_id::text FROM costs ORDER BY id")
                ).all()
            )
            indexes = set(
                connection.execute(
                    text("SELECT indexname FROM pg_indexes WHERE indexname LIKE 'ix_%_azienda_id'")
                ).scalars()
            )
        engine.dispose()
        assert set(owned.values()) == {azienda}
        assert costs["00000000-0000-7000-8000-0000000000f1"] == azienda
        assert costs["00000000-0000-7000-8000-0000000000f2"] is None
        assert indexes >= {
            "ix_customers_azienda_id",
            "ix_deals_azienda_id",
            "ix_contracts_azienda_id",
            "ix_documents_azienda_id",
            "ix_costs_azienda_id",
        }

        downgrade(config, "0046")
        engine = create_engine(url)
        with engine.connect() as connection:
            columns = set(
                connection.execute(
                    text(
                        "SELECT table_name FROM information_schema.columns "
                        "WHERE column_name = 'azienda_id'"
                    )
                ).scalars()
            )
        engine.dispose()
        assert columns == {
            "invoices",
            "invoice_counters",
            "invoice_register_gaps",
            "fiscal_profile",
            "user_aziende",
        }


def test_0049_puts_the_fast_path_first_and_its_downgrade_restores_0048(
    pigrocrm_postgres: Any,
) -> None:
    """The migration over the real policies, both ways (REB-656): at head every
    through-the-parent policy opens on `scope_tutte() OR (`, with `WITH CHECK` equal to
    `USING` except the links' `true`, and the eight functions open on the spelled-out
    setting test; one step down, 0048's predicates and bodies are back as 0048 wrote
    them, and up again is the same as the first time."""

    def read(url: str) -> tuple[dict[str, tuple[str, str]], dict[str, str]]:
        engine: Engine = create_engine(url)
        with engine.connect() as connection:
            policies = {
                row.tablename: (row.qual, row.with_check)
                for row in connection.execute(
                    text(
                        "SELECT tablename, qual, with_check FROM pg_policies "
                        "WHERE policyname = 'ambito_azienda'"
                    )
                )
            }
            bodies = dict(
                connection.execute(
                    text(
                        "SELECT proname, prosrc FROM pg_proc WHERE proname IN ("
                        "'customer_visibile', 'person_visibile', 'deal_visibile', "
                        "'invoice_visibile', 'contract_visibile', 'document_visibile', "
                        "'work_unit_visibile', 'entita_visibile')"
                    )
                ).all()
            )
        engine.dispose()
        return policies, bodies

    fast_path = {
        "people",
        "time_entries",
        "time_timers",
        "invoice_lines",
        "payment_reminders",
        "document_versions",
        "proposals",
        "rate_cards",
        "renewal_assumptions",
        "contract_expenses",
        "work_units",
        "approvals",
        "work_unit_transitions",
        "attivita",
        "gmail_message_links",
        "email_drafts",
        "gmail_messages",
        "activities",
    }
    with pigrocrm_postgres.fresh_container() as container:
        url = container.get_connection_url()
        config = _alembic_config(url)
        upgrade(config, "0048")
        at_0048 = read(url)
        upgrade(config, "0049")
        at_head = read(url)
        downgrade(config, "0048")
        back_at_0048 = read(url)
        upgrade(config, "0049")
        at_head_again = read(url)

    def squeeze(expression: str) -> str:
        # Postgres re-indents a `CASE` it nests and drops the parentheses around a lone
        # function call, so the comparison is on the tokens, not the layout.
        return "".join(expression.split())

    policies, bodies = at_head
    assert set(policies) == set(at_0048[0])
    for table, (using, check) in policies.items():
        before_using, before_check = at_0048[0][table]
        if table in fast_path:
            assert squeeze(using).startswith("(scope_tutte()OR"), table
            rest = squeeze(using)[len("(scope_tutte()OR") : -1]
            assert rest in (squeeze(before_using), f"({squeeze(before_using)})"), table
            assert check == ("true" if table == "gmail_message_links" else using), table
        else:
            assert (using, check) == (before_using, before_check), table
    assert len(bodies) == 8
    spelled_out = "current_setting('pigrocrm.aziende', true) = '*' OR "
    for name, body in bodies.items():
        if name == "entita_visibile":
            assert body.lstrip().startswith("SELECT CASE $1"), name
            assert body.count(f"THEN {spelled_out}EXISTS (") == at_0048[1][name].count(
                "THEN scope_tutte() OR EXISTS ("
            ), name
        else:
            assert body.lstrip().startswith(f"SELECT {spelled_out}"), name
            assert at_0048[1][name].lstrip().startswith("SELECT scope_tutte() OR "), name
    assert back_at_0048 == at_0048
    assert at_head_again == at_head
