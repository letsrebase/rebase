from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from alembic.autogenerate import compare_metadata
from alembic.command import downgrade, upgrade
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import IntegrityError

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
    # REB-1014, migration 0087: one Gmail mailbox per person (partial, `WHERE provider = 'gmail'`).
    "uq_mailboxes_one_gmail_per_user",
    # REB-1015, migration 0097: one IMAP mailbox per owner and address (an expression, and the
    # space's NULL owner folded to a zero uuid so that two of its own count as one).
    "uq_mailboxes_imap_address",
    # REB-615, migration 0045: one default per space (partial, `WHERE predefinita`) and
    # one azienda per fiscal id (partial, functional over `upper(...)`). Both are shapes
    # `compare_metadata` cannot compare, so they are asserted on their definitions below.
    "uq_legal_entities_default",
    "uq_legal_entities_vat_number",
    "uq_legal_entities_tax_code",
    "uq_pipeline_stage_code",
    "ix_organizations_custom_fields",
    "ix_people_custom_fields",
    "ix_deals_custom_fields",
    "uq_invoices_legal_entity_year_number",
    # REB-619: the `(anno, numero)` path the unique index stopped offering to a search
    # across aziende; partial, so `compare_metadata` cannot see it either.
    "ix_invoices_year_number",
    "ix_invoices_custom_fields",
    # Same shape as `uq_pipeline_stage_code`: a plain unique index on a nullable
    # column, one of the two shapes autogenerate is known to silently omit.
    "uq_cost_categories_code",
    "ix_time_entries_custom_fields",
    "ix_purchases_custom_fields",
    # Same shape as `uq_users_email_lower`: a functional unique index over lower(name).
    "uq_cost_categories_name",
    # Slice 6, migration 0021. A *partial* GIN index over an operator class is a fourth
    # shape autogenerate handles poorly, and nine trigram indexes omitted in silence are
    # nine sequential scans that come back a month later.
    "ix_organizations_legal_name_trgm",
    "ix_organizations_vat_number_trgm",
    "ix_organizations_tax_code_trgm",
    "ix_people_name_trgm",
    "ix_people_last_name_trgm",
    "ix_deals_name_trgm",
    "ix_documents_title_trgm",
    # Slice 6, migration 0022. Residuo R9's other half: one `(column, id)` B-tree per
    # admitted sort key, plus the one descending index the single nullable sort column
    # costs. The twelve ascending ones are ordinary composite indexes that autogenerate
    # handles correctly -- they are listed anyway, because a composite index dropped in
    # silence is the same sequential scan as a GIN index dropped in silence, and the
    # thirteenth is an expression index over `DESC NULLS LAST` that autogenerate cannot
    # compare at all.
    "ix_organizations_created_at_id",
    "ix_organizations_updated_at_id",
    "ix_organizations_legal_name_id",
    "ix_people_created_at_id",
    "ix_people_updated_at_id",
    "ix_people_last_name_id",
    "ix_people_last_name_desc_id",
    "ix_deals_created_at_id",
    "ix_deals_updated_at_id",
    "ix_deals_name_id",
    "ix_documents_created_at_id",
    "ix_documents_updated_at_id",
    "ix_documents_title_id",
    # Slice 6, migration 0023. The two period-filter columns of §4.1. Both are ordinary
    # single-column B-trees that autogenerate handles correctly; they are listed here for
    # the same reason the twelve ascending sort indexes are, which is that an index
    # dropped in silence is a sequential scan on every dashboard load, and this file is
    # where a missing index is supposed to fail by name.
    "ix_deals_closed_at",
    # REB-879, migration 0064: the references of the engagements door, a `text[]` with a GIN
    # index, the shape autogenerate does not compare.
    "ix_deals_rebase_refs",
    "ix_documents_status_since",
    # Slice 6, migration 0024. A *descending* expression index over two columns, which is
    # the fifth shape autogenerate cannot compare: `compare_metadata` does not look at
    # index expressions at all, so this one would be dropped in total silence and the
    # global activity feed would go back to sorting the whole table on every dashboard
    # load. Its declared shape is asserted below, on `indexdef` text.
    "ix_activities_recent",
    # Slice 6, migration 0024 as well: the tenth and last trigram index of §8.3, added
    # with the fifth search branch. Same partial-GIN-over-an-operator-class shape as the
    # nine of 0021, and picked up automatically by `TRGM_INDEX_NAMES` below.
    "ix_invoices_memo_trgm",
    # REB-723, migration 0052: the eleventh and twelfth, `tasks.title` and `.note`, added
    # with the sixth search branch. Same shape, picked up by `TRGM_INDEX_NAMES` below.
    "ix_tasks_title_trgm",
    "ix_tasks_note_trgm",
    # REB-290, migration 0036. Functional (over `lower(email)`) and partial at once --
    # the two shapes autogenerate omits, stacked: a functional index it drops silently
    # re-opens the case-insensitive duplicate, a partial predicate it drops silently
    # makes an accepted or revoked invitation block the address forever. Its declared
    # shape is asserted on `indexdef` text below.
    "uq_invitations_email_lower_open",
    # REB-940, migration 0073: the contact points' partial unique indexes, which autogenerate
    # does not compare (one point per owner, kind and value; one primary per owner and kind),
    # and their two trigram indexes, which replace the people and customers email ones (and
    # the lowercase ones of 0067 with them: the roster now looks `(kind, normalized)` up).
    "uq_contact_points_person_value",
    "uq_contact_points_customer_value",
    "uq_contact_points_person_primary",
    "uq_contact_points_customer_primary",
    "ix_contact_points_value_trgm",
    "ix_contact_points_normalized_trgm",
}

# The trigram indexes of the soft-deletable tables: partial on `deleted_at IS NULL`. The two of
# `contact_points` have no such column and are checked on their own below.
CONTACT_TRGM_INDEX_NAMES = frozenset(
    {"ix_contact_points_value_trgm", "ix_contact_points_normalized_trgm"}
)
TRGM_INDEX_NAMES = frozenset(
    n for n in HAND_MAINTAINED_INDEXES if n.endswith("_trgm") and n not in CONTACT_TRGM_INDEX_NAMES
)


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
        "organizations",
        "people",
        "deals",
        "fiscal_profile",
        "invoices",
        "invoice_lines",
        "invoice_counters",
        "time_entries",
        "purchases",
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
        "ix_organizations_custom_fields",
        "ix_people_custom_fields",
        "ix_deals_custom_fields",
        "ix_time_entries_custom_fields",
        "ix_purchases_custom_fields",
    ):
        assert "USING gin" in indexes[gin_index], f"{gin_index} was not created as a GIN index"

    assert "USING gin" in indexes["ix_invoices_custom_fields"], (
        "ix_invoices_custom_fields was not created as a GIN index"
    )

    cost_categories_code_def = indexes["uq_cost_categories_code"]
    assert "UNIQUE" in cost_categories_code_def, "uq_cost_categories_code must be a unique index"

    cost_categories_name_def = indexes["uq_cost_categories_name"]
    assert "UNIQUE" in cost_categories_name_def, "uq_cost_categories_name must be a unique index"
    assert "lower(" in cost_categories_name_def and "name" in cost_categories_name_def, (
        f"uq_cost_categories_name is not a functional index over lower(name): "
        f"{cost_categories_name_def}"
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
    year_number_def = indexes["uq_invoices_legal_entity_year_number"]
    assert "UNIQUE" in year_number_def, "uq_invoices_azienda_anno_numero must be a unique index"
    assert "WHERE" in year_number_def and "number IS NOT NULL" in year_number_def, (
        "uq_invoices_azienda_anno_numero lost its partial predicate, so every unnumbered "
        f"draft is now a duplicate of every other: {year_number_def}"
    )
    # REB-619: each legal entity keeps its own register, so the legal entity leads the key.
    assert "legal_entity_id" in year_number_def, year_number_def


def test_0045_gives_to_used_database_with_no_legal_entity_row_its_legal_entity(
    pigrocrm_postgres: Any,
) -> None:
    """The root installation is not in the registry, so `ensure_defaults` never reaches
    it, and the API updates a legal entity by id: a root with no emitter row would have no
    issuer to configure at all. 0045 inserts one, named after the database, on a database
    that already has a user (REB-615). A database with none is one being provisioned,
    whose migrations run before its owner is written: it gets nothing here, so that
    `ensure_defaults` can name its legal entity after the space and not after the database."""
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
                text(
                    "SELECT nome AS name, ragione_sociale AS legal_name, "
                    "predefinita AS is_default, attiva AS active FROM emitter_profile"
                )
            ).all()
            db_name = connection.execute(text("SELECT current_database()")).scalar_one()
        engine.dispose()
    assert empty == 0
    assert len(rows) == 1
    assert rows[0].legal_name == db_name
    assert rows[0].name == db_name[:80]
    assert rows[0].is_default is True and rows[0].active is True


def test_the_legal_entity_indexes_are_partial_and_the_fiscal_ones_functional(
    pigrocrm_postgres: Any,
) -> None:
    """REB-615: `uq_legal_entities_default` is what makes «exactly one default»
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
                    "SELECT indexname, indexdef FROM pg_indexes WHERE tablename = 'legal_entities'"
                )
            ).all()
        engine.dispose()
    indexes = {name: definition for name, definition in rows}
    assert "UNIQUE" in indexes["uq_legal_entities_default"]
    assert "WHERE is_default" in indexes["uq_legal_entities_default"]
    for name, column in (
        ("uq_legal_entities_vat_number", "vat_number"),
        ("uq_legal_entities_tax_code", "tax_code"),
    ):
        assert "UNIQUE" in indexes[name]
        assert f"upper(({column})::text)" in indexes[name], indexes[name]
        assert f"WHERE ({column} IS NOT NULL)" in indexes[name], indexes[name]


def test_0045_binds_the_one_fiscal_row_to_the_one_legal_entity_and_refuses_to_undo_two(
    pigrocrm_postgres: Any,
) -> None:
    """The migration over real rows planted at 0044, not read as text (REB-615).

    A 120-character `ragione_sociale` proves the `nome` backfill cuts to the column's
    width; the fiscal row proves the backfill of `legal_entity_id`; a lower-case tax
    code proves the row is brought to the classifier's shape (a P.IVA with an `IT`
    prefix cannot be planted: the column is eleven wide); the downgrade
    with one legal entity succeeds, and with two it refuses before touching anything.
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
            legal_entity = connection.execute(
                text(
                    "SELECT nome AS name, predefinita AS is_default, attiva AS active, "
                    "partita_iva AS vat_number, codice_fiscale AS tax_code "
                    "FROM emitter_profile"
                )
            ).one()
            fiscal_legal_entity = connection.execute(
                text("SELECT azienda_id FROM fiscal_profile")
            ).scalar_one()
            users_flag = connection.execute(
                text(
                    "SELECT column_default FROM information_schema.columns "
                    "WHERE table_name = 'users' AND column_name = 'ambito_limitato'"
                )
            ).scalar_one()
        engine.dispose()
        assert legal_entity.name == "S" * 80
        assert legal_entity.is_default is True and legal_entity.active is True
        assert legal_entity.vat_number == "01234567890"
        assert legal_entity.tax_code == "HMCRFT00A01H501K"
        assert str(fiscal_legal_entity) == "00000000-0000-7000-8000-0000000000a1"
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
    # Ten -- seven of migration 0021 (the two email ones moved to `contact_points` in 0073),
    # `invoices.memo` from 0024 and the two of `tasks` from 0052 -- and the count is asserted:
    # a set comprehension that silently matched nothing would make every assertion below
    # vacuous, and §8.3 names the columns rather than "some".
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
    for name in sorted(CONTACT_TRGM_INDEX_NAMES):
        definition = indexes[name]
        assert "USING gin" in definition and "gin_trgm_ops" in definition, definition
        assert "lower(" not in definition, definition


# (index name, the exact `USING btree (...)` body Postgres must report). Residuo R9's
# ordering contract is `ORDER BY <col> <dir> NULLS LAST, id <dir>`, and an index only
# serves it when both members are present in that order -- a single-column index on
# `created_at` leaves the `id` tie-break to an in-memory sort, which is the cost the
# thirteen exist to avoid. Pinned as text because the *order* of the members and the
# `DESC NULLS LAST` qualifier are observable nowhere else.
SORT_INDEX_BODIES: dict[str, str] = {
    "ix_organizations_created_at_id": "(created_at, id)",
    "ix_organizations_updated_at_id": "(updated_at, id)",
    "ix_organizations_legal_name_id": "(legal_name, id)",
    "ix_people_created_at_id": "(created_at, id)",
    "ix_people_updated_at_id": "(updated_at, id)",
    "ix_people_last_name_id": "(last_name, id)",
    "ix_people_last_name_desc_id": "(last_name DESC NULLS LAST, id DESC)",
    "ix_deals_created_at_id": "(created_at, id)",
    "ix_deals_updated_at_id": "(updated_at, id)",
    "ix_deals_name_id": "(name, id)",
    "ix_documents_created_at_id": "(created_at, id)",
    "ix_documents_updated_at_id": "(updated_at, id)",
    "ix_documents_title_id": "(title, id)",
}


def test_every_sort_index_is_a_btree_over_the_column_and_the_identifier(
    pigrocrm_postgres: Any,
) -> None:
    """Residuo R9's other half, asserted on the definition text rather than on the name.

    `compare_metadata` does not compare index expressions at all, so the one index that
    matters most here is exactly the one a schema diff would let through:
    `ix_people_last_name_desc_id`. Without it a descending scan of `people.last_name` reads
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


def test_status_since_is_backfilled_from_the_timeline_and_closed_at_is_not() -> None:
    """The asymmetry of spec §4.1, asserted rather than described.

    A migration that quietly backfilled `closed_at` from the stage-change payload would
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

    `d.stato IS NOT NULL` is what keeps the backfill to offers: `documents.status` is NULL
    on every non-offer document, and stamping a `status_since` on a row with no state would
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
      * dropping `d.stato IS NOT NULL` -> the contract gets a `status_since` too.
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
            offer, minutes = (
                connection.execute(text("SELECT stato_dal FROM documents ORDER BY id"))
                .scalars()
                .all()
            )
            closed_at = connection.execute(text("SELECT chiuso_il FROM deals")).scalar_one()
        engine.dispose()

    assert offer == date(2026, 7, 1), offer
    assert minutes is None, "a document with no stato must not acquire a stato_dal"
    assert closed_at is None, (
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
                    "SELECT a1_wins_accepted_offer_deal, a2_advances_sent_offer_deal "
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


def test_0029_moves_any_legacy_import_provenance_to_external(pigrocrm_postgres: Any) -> None:
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


def _accrual_checks(url: str) -> set[str]:
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


def test_0033_dates_every_undated_proforma_in_rome_and_touches_no_invoice(
    pigrocrm_postgres: Any,
) -> None:
    """The backfill of migration 0033 (ORB-63), asserted on rows planted at 0032.

    A proforma used to keep `data_emissione` `NULL` until a invoice was issued from it,
    and its PDF printed the Europe/Rome civil date of `created_at`. The backfill has to
    write exactly that date -- so no proforma anyone has already received changes its
    date -- and it has to be the *Rome* date: a row created at 22:30 UTC on 30 June is a
    document dated 1 July, and one created at 23:30 UTC on New Year's Eve belongs to the
    next year. `date(created_at)` would have said June and December. The two invoice
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
        assert _accrual_checks(url) == set()

        upgrade(config, "0033")
        after_upgrade = _proforma_dates(url)
        checks_after_upgrade = _accrual_checks(url)

        downgrade(config, "0032")
        after_downgrade = _proforma_dates(url)
        checks_after_downgrade = _accrual_checks(url)

        upgrade(config, "0033")
        after_second_upgrade = _proforma_dates(url)
        checks_after_second_upgrade = _accrual_checks(url)

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


def test_0046_keys_the_register_by_legal_entity_over_planted_rows_and_refuses_to_undo_two(
    pigrocrm_postgres: Any,
) -> None:
    """The migration over real rows planted at 0045, not read as text (REB-619): a
    default legal entity, a fiscal profile, a counter and a declared gap. The upgrade keys
    counter and gap to the legal entity, swaps the two uniques and the primary key, lets
    `regime_code` be empty and widens the P.IVA; the downgrade with one legal entity goes
    back, and with a second legal entity's counter it refuses before touching anything."""
    legal_entity = "00000000-0000-7000-8000-0000000000a1"
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
                {"id": legal_entity},
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
                {"id": legal_entity},
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
            invoice_legal_entity = connection.execute(
                text("SELECT azienda_id FROM invoices WHERE numero = 3")
            ).scalar_one()
            counter = connection.execute(
                text(
                    "SELECT azienda_id AS legal_entity_id, anno AS year, "
                    "ultimo_numero AS last_number FROM invoice_counters"
                )
            ).one()
            gap = connection.execute(
                text(
                    "SELECT azienda_id AS legal_entity_id, anno AS year, numero AS number "
                    "FROM invoice_register_gaps"
                )
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
            regime_nullable, vat_number_width = connection.execute(
                text(
                    "SELECT (SELECT is_nullable FROM information_schema.columns "
                    "        WHERE table_name = 'fiscal_profile' "
                    "        AND column_name = 'codice_regime'), "
                    "       (SELECT character_maximum_length FROM information_schema.columns "
                    "        WHERE table_name = 'emitter_profile' AND column_name = 'partita_iva')"
                )
            ).one()
        engine.dispose()
        assert str(invoice_legal_entity) == legal_entity
        assert (str(counter.legal_entity_id), counter.year, counter.last_number) == (
            legal_entity,
            2026,
            3,
        )
        assert (str(gap.legal_entity_id), gap.year, gap.number) == (legal_entity, 2026, 2)
        assert pk == ["azienda_id", "anno"]
        assert "uq_invoices_azienda_anno_numero" in invoice_indexes
        assert "ix_invoices_anno_numero" in invoice_indexes
        assert "uq_invoices_anno_numero" not in invoice_indexes
        assert "ix_invoices_azienda_id" in invoice_indexes
        assert (regime_nullable, vat_number_width) == ("YES", 20)

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


def test_0047_binds_every_existing_row_to_the_one_legal_entity_and_to_cost_to_its_deals(
    pigrocrm_postgres: Any,
) -> None:
    """The migration over real rows planted at 0046 (REB-623): a customer, a deal, a
    contract, a document, a cost on the deal and a shared one. Every owned row is bound
    to the default azienda, the cost on a deal to the deal's, the shared cost to none;
    the downgrade drops the five columns and the indexes with them."""
    legal_entity = "00000000-0000-7000-8000-0000000000a1"
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
                {"id": legal_entity},
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
        assert set(owned.values()) == {legal_entity}
        assert costs["00000000-0000-7000-8000-0000000000f1"] == legal_entity
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


def _order_shape(url: str) -> tuple[set[str], set[str]]:
    engine: Engine = create_engine(url)
    try:
        with engine.connect() as connection:
            columns = set(
                connection.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_name = 'invoices' AND column_name LIKE 'ordine\\_%'"
                    )
                ).scalars()
            )
            checks = set(
                connection.execute(
                    text(
                        "SELECT conname FROM pg_constraint "
                        "WHERE conrelid = 'invoices'::regclass "
                        "AND conname = 'ck_invoices_ordine_needs_numero'"
                    )
                ).scalars()
            )
            return columns, checks
    finally:
        engine.dispose()


def _insert_orphan_tender_code_draft(url: str) -> None:
    """A draft invoice with a tender code and no order number, straight through SQL."""
    engine: Engine = create_engine(url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO emitter_profile (id, nome, predefinita, attiva, "
                    "ragione_sociale, nazione, created_at, updated_at) VALUES "
                    "('00000000-0000-7000-8000-0000000000a1', 'Studio', true, true, "
                    "'Studio Rossi', 'IT', now(), now())"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO customers (id, azienda_id, ragione_sociale, nazione, "
                    "custom_fields, created_at, updated_at) VALUES "
                    "('00000000-0000-7000-8000-0000000000c1', "
                    "'00000000-0000-7000-8000-0000000000a1', 'Acme', 'IT', '{}', now(), now())"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO invoices (id, azienda_id, customer_id, tipo, stato, "
                    "tipo_documento, divisa, imponibile, imposta, bollo, totale, "
                    "stato_pagamento, custom_fields, ordine_cig, created_at, updated_at) VALUES "
                    "('00000000-0000-7000-8000-0000000000d1', "
                    "'00000000-0000-7000-8000-0000000000a1', "
                    "'00000000-0000-7000-8000-0000000000c1', 'fattura', 'bozza', 'TD01', "
                    "'EUR', 0, 0, 0, 0, 'da_incassare', '{}', 'Z0A1B2C3D4', now(), now())"
                )
            )
    finally:
        engine.dispose()


def test_0050_adds_the_purchase_order_columns_and_its_downgrade_drops_them(
    pigrocrm_postgres: Any,
) -> None:
    """REB-693: four nullable columns and one CHECK, no backfill, both ways."""
    purchase_order = {"ordine_numero", "ordine_data", "ordine_cig", "ordine_cup"}
    with pigrocrm_postgres.fresh_container() as container:
        url = container.get_connection_url()
        config = _alembic_config(url)
        upgrade(config, "0049")
        assert _order_shape(url) == (set(), set())
        upgrade(config, "0050")
        assert _order_shape(url) == (purchase_order, {"ck_invoices_ordine_needs_numero"})
        with pytest.raises(IntegrityError, match="ck_invoices_ordine_needs_numero"):
            _insert_orphan_tender_code_draft(url)
        downgrade(config, "0049")
        assert _order_shape(url) == (set(), set())
        upgrade(config, "0050")
        assert _order_shape(url) == (purchase_order, {"ck_invoices_ordine_needs_numero"})


def _invitation_mail_columns(url: str) -> set[str]:
    engine: Engine = create_engine(url)
    try:
        with engine.connect() as connection:
            return set(
                connection.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_name = 'invitations' AND column_name LIKE 'mail\\_%'"
                    )
                ).scalars()
            )
    finally:
        engine.dispose()


def test_0051_adds_the_invitation_mail_columns_and_its_downgrade_drops_them(
    pigrocrm_postgres: Any,
) -> None:
    """REB-678: three nullable columns, no backfill, both ways."""
    mail = {"mail_id", "mail_status", "mail_sent_at"}
    with pigrocrm_postgres.fresh_container() as container:
        url = container.get_connection_url()
        config = _alembic_config(url)
        upgrade(config, "0050")
        assert _invitation_mail_columns(url) == set()
        upgrade(config, "0051")
        assert _invitation_mail_columns(url) == mail
        downgrade(config, "0050")
        assert _invitation_mail_columns(url) == set()
        upgrade(config, "0051")
        assert _invitation_mail_columns(url) == mail


def test_0058_repoints_an_ordinary_profile_at_its_own_pack_and_clears_the_forfettarios_figures(
    pigrocrm_postgres: Any,
) -> None:
    """REB-760 over real rows planted at 0057: an `RF01` profile left on the flat-rate
    regime's pack with its coefficients, and a flat-rate profile beside it. The first moves
    to `it-standard` with the flat-rate figures and the stamp duty cleared; the second is
    untouched; the downgrade points the first back."""
    ordinary, flat_rate = (
        "00000000-0000-7000-8000-0000000000b1",
        "00000000-0000-7000-8000-0000000000b2",
    )
    with pigrocrm_postgres.fresh_container() as container:
        url = container.get_connection_url()
        config = _alembic_config(url)
        upgrade(config, "0057")
        engine: Engine = create_engine(url)
        with engine.begin() as connection:
            for legal_entity, profile_name, regime, rate, stamp_duty, params in (
                (ordinary, "ordinaria", "RF01", "22.00", "true", ("67.00", "5.00", "26.07")),
                (flat_rate, "forfettaria", "RF19", "0.00", "true", ("78.00", "5.00", "26.07")),
            ):
                connection.execute(
                    text(
                        """
                        INSERT INTO emitter_profile (id, nome, predefinita, attiva,
                            ragione_sociale, nazione, created_at, updated_at)
                        VALUES (:id, :nome, false, true, :nome, 'IT', now(), now())
                        """
                    ),
                    {"id": legal_entity, "nome": profile_name},
                )
                connection.execute(
                    text(
                        f"""
                        INSERT INTO fiscal_profile (id, azienda_id, codice_regime,
                            aliquota_iva_default, applica_bollo, soglia_bollo, importo_bollo,
                            condizioni_pagamento, modalita_pagamento, giorni_scadenza,
                            coefficiente_redditivita, aliquota_imposta_sostitutiva,
                            aliquota_inps, natura_default, riferimento_normativo, pack_id,
                            pack_version, created_at, updated_at)
                        VALUES (gen_random_uuid(), :azienda, :regime, :rate, {stamp_duty}, 77.47,
                            2.00, 'TP02', 'MP05', 30, :c, :s, :i, 'N2.2', 'L. 190/2014',
                            'it-flat-rate', '1', now(), now())
                        """
                    ),
                    {
                        "azienda": legal_entity,
                        "regime": regime,
                        "rate": rate,
                        "c": params[0],
                        "s": params[1],
                        "i": params[2],
                    },
                )
        engine.dispose()

        def read() -> dict[str, tuple[Any, ...]]:
            engine = create_engine(url)
            try:
                with engine.connect() as connection:
                    rows = connection.execute(
                        text(
                            "SELECT azienda_id::text, pack_id, applica_bollo, "
                            "coefficiente_redditivita, aliquota_imposta_sostitutiva, "
                            "aliquota_inps, natura_default, riferimento_normativo "
                            "FROM fiscal_profile"
                        )
                    ).all()
                return {row[0]: tuple(row[1:]) for row in rows}
            finally:
                engine.dispose()

        upgrade(config, "0058")
        after = read()
        assert after[ordinary] == ("it-standard", False, None, None, None, None, None)
        assert after[flat_rate] == (
            "it-flat-rate",
            True,
            Decimal("78.00"),
            Decimal("5.00"),
            Decimal("26.07"),
            "N2.2",
            "L. 190/2014",
        )

        downgrade(config, "0057")
        assert read()[ordinary][0] == "it-flat-rate"
        assert read()[flat_rate][0] == "it-flat-rate"


def _cap_shape(url: str) -> tuple[set[str], set[str]]:
    engine: Engine = create_engine(url)
    try:
        with engine.connect() as connection:
            columns = set(
                connection.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_name = 'contracts' AND column_name = 'tetto_quota_cliente'"
                    )
                ).scalars()
            )
            checks = set(
                connection.execute(
                    text(
                        "SELECT conname FROM pg_constraint "
                        "WHERE conname = 'ck_contracts_tetto_quota_cliente_range'"
                    )
                ).scalars()
            )
        return columns, checks
    finally:
        engine.dispose()


def test_0059_adds_the_share_cap_column_and_its_range_check_both_ways(
    pigrocrm_postgres: Any,
) -> None:
    """REB-761: one nullable column and one CHECK, no backfill, both ways."""
    with pigrocrm_postgres.fresh_container() as container:
        url = container.get_connection_url()
        config = _alembic_config(url)
        upgrade(config, "0058")
        assert _cap_shape(url) == (set(), set())
        upgrade(config, "0059")
        assert _cap_shape(url) == (
            {"tetto_quota_cliente"},
            {"ck_contracts_tetto_quota_cliente_range"},
        )
        downgrade(config, "0058")
        assert _cap_shape(url) == (set(), set())
        upgrade(config, "0059")
        assert _cap_shape(url)[1] == {"ck_contracts_tetto_quota_cliente_range"}


def _clause_notes_shape(url: str) -> tuple[set[str], bool, set[str]]:
    engine: Engine = create_engine(url)
    try:
        with engine.connect() as connection:
            columns = set(
                connection.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_name = 'clause_notes'"
                    )
                ).scalars()
            )
            forced = bool(
                connection.execute(
                    text("SELECT relforcerowsecurity FROM pg_class WHERE relname = 'clause_notes'")
                ).scalar()
            )
            policies = set(
                connection.execute(
                    text("SELECT policyname FROM pg_policies WHERE tablename = 'clause_notes'")
                ).scalars()
            )
        return columns, forced, policies
    finally:
        engine.dispose()


def test_0060_creates_clause_notes_under_the_legal_entities_policy_and_its_downgrade_drops_it(
    pigrocrm_postgres: Any,
) -> None:
    """REB-762: one table keyed on a contract, with the row-level policy every table that
    reaches an azienda carries, forced; no backfill, both ways."""
    columns = {
        "id",
        "contract_id",
        "riferimento_clausola",
        "testo_letterale",
        "interpretazione_adottata",
        "note",
        "created_at",
        "updated_at",
    }
    with pigrocrm_postgres.fresh_container() as container:
        url = container.get_connection_url()
        config = _alembic_config(url)
        upgrade(config, "0059")
        assert _clause_notes_shape(url) == (set(), False, set())
        upgrade(config, "0060")
        assert _clause_notes_shape(url) == (columns, True, {"ambito_azienda"})
        downgrade(config, "0059")
        assert _clause_notes_shape(url) == (set(), False, set())
        upgrade(config, "0060")
        assert _clause_notes_shape(url)[0] == columns


def _job_run_shape(url: str) -> tuple[set[str], bool]:
    """`reminder_job_run`'s columns beside `job`, and whether `ran_at` is nullable."""
    engine: Engine = create_engine(url)
    try:
        with engine.connect() as connection:
            rows = connection.execute(
                text(
                    "SELECT column_name, is_nullable FROM information_schema.columns "
                    "WHERE table_name = 'reminder_job_run' AND column_name IN "
                    "('ran_at', 'failed_at', 'failure')"
                )
            ).all()
    finally:
        engine.dispose()
    return {row[0] for row in rows}, {row[0]: row[1] for row in rows}["ran_at"] == "YES"


def _plant_job_run(url: str, columns: str, values: str) -> None:
    engine: Engine = create_engine(url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(  # noqa: S608 - fixed test fragments
                    f"INSERT INTO reminder_job_run (id, job, {columns}) "
                    f"VALUES (gen_random_uuid(), {values})"
                )
            )
    finally:
        engine.dispose()


def _job_runs(url: str) -> list[str]:
    engine: Engine = create_engine(url)
    try:
        with engine.connect() as connection:
            return list(
                connection.execute(text("SELECT job FROM reminder_job_run ORDER BY job")).scalars()
            )
    finally:
        engine.dispose()


def test_0061_adds_the_failure_columns_keeps_the_rows_and_its_downgrade_drops_the_failed_ones(
    pigrocrm_postgres: Any,
) -> None:
    """REB-764: two nullable columns, `ran_at` nullable and two CHECKs, no backfill; the
    downgrade drops the rows with no success, which the old shape cannot hold, and keeps
    the others."""
    with pigrocrm_postgres.fresh_container() as container:
        url = container.get_connection_url()
        config = _alembic_config(url)
        upgrade(config, "0060")
        assert _job_run_shape(url) == ({"ran_at"}, False)
        _plant_job_run(url, "ran_at", "'reminders', now()")

        upgrade(config, "0061")
        assert _job_run_shape(url) == ({"ran_at", "failed_at", "failure"}, True)
        assert _job_runs(url) == ["reminders"], "the reminder pass's row is untouched"
        _plant_job_run(url, "failed_at, failure", "'automations', now(), 'RuntimeError'")
        assert _job_runs(url) == ["automations", "reminders"]

        with pytest.raises(IntegrityError, match="ck_reminder_job_run_failure_pair"):
            _plant_job_run(url, "failed_at", "'digest', now()")
        with pytest.raises(IntegrityError, match="ck_reminder_job_run_has_a_time"):
            _plant_job_run(url, "ran_at", "'digest', NULL")

        downgrade(config, "0060")
        assert _job_run_shape(url) == ({"ran_at"}, False)
        assert _job_runs(url) == ["reminders"], "a job that never succeeded has nothing to keep"


def _plant_tokens(url: str, switch_row: str | None) -> None:
    """One member with a live and a revoked token, and the space's `mcp_full_access` row
    when `switch_row` is not None."""
    engine: Engine = create_engine(url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO users (id, email, password_hash, nome, ruolo, attivo) "
                    "VALUES ('00000000-0000-7000-8000-0000000000f1', 'titolare@example.it', "
                    "'x', 'Titolare', 'admin', true)"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO personal_access_tokens "
                    "(id, user_id, nome, token_hash, prefix, revoked_at) VALUES "
                    "(gen_random_uuid(), '00000000-0000-7000-8000-0000000000f1', 'viva', "
                    "'h1', 'pgc_aaaa', NULL), "
                    "(gen_random_uuid(), '00000000-0000-7000-8000-0000000000f1', 'morta', "
                    "'h2', 'pgc_bbbb', now())"
                )
            )
            if switch_row is not None:
                connection.execute(
                    text(
                        "INSERT INTO space_settings (key, value) VALUES ('mcp_full_access', :value)"
                    ),
                    {"value": switch_row},
                )
    finally:
        engine.dispose()


def _token_flags(url: str) -> dict[str, bool]:
    engine: Engine = create_engine(url)
    try:
        with engine.connect() as connection:
            rows = connection.execute(text("SELECT nome, full_access FROM personal_access_tokens"))
            return {token_name: flag for token_name, flag in rows}
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    ("switch_row", "environment", "expected"),
    [
        # The space's own row wins either way: on, with the environment closed...
        ("true", "false", True),
        ("sì", "false", True),
        # ...and off, with the environment open, which is the space that closed it.
        ("false", "true", False),
        # No row: the space inherits the environment, as `space_base_settings` gives it.
        (None, "true", True),
        (None, "false", False),
    ],
)
def test_0063_gives_every_existing_token_what_its_space_switch_gave_it_yesterday(
    pigrocrm_postgres: Any,
    monkeypatch: pytest.MonkeyPatch,
    switch_row: str | None,
    environment: str,
    expected: bool,
) -> None:
    """REB-810: the switch moves to the token and nobody loses a door. Whatever the space's
    effective `mcp_full_access` was, every token (the revoked one too, so the list reads
    one way) now carries it as its own flag, and the downgrade drops the column."""
    monkeypatch.setenv("PIGROCRM_MCP_FULL_ACCESS", environment)
    get_settings.cache_clear()
    try:
        with pigrocrm_postgres.fresh_container() as container:
            url = container.get_connection_url()
            config = _alembic_config(url)
            upgrade(config, "0062")
            _plant_tokens(url, switch_row)

            upgrade(config, "0063")
            assert _token_flags(url) == {"viva": expected, "morta": expected}

            downgrade(config, "0062")
            engine: Engine = create_engine(url)
            try:
                with engine.connect() as connection:
                    columns = set(
                        connection.execute(
                            text(
                                "SELECT column_name FROM information_schema.columns "
                                "WHERE table_name = 'personal_access_tokens'"
                            )
                        ).scalars()
                    )
            finally:
                engine.dispose()
            assert "full_access" not in columns
    finally:
        get_settings.cache_clear()


def test_0063_leaves_a_token_made_afterwards_closed(
    pigrocrm_postgres: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The column's default is closed: a token inserted by anything that does not name the
    flag, in a space whose switch was open, is not opened by the migration's backfill."""
    monkeypatch.setenv("PIGROCRM_MCP_FULL_ACCESS", "true")
    get_settings.cache_clear()
    try:
        with pigrocrm_postgres.fresh_container() as container:
            url = container.get_connection_url()
            config = _alembic_config(url)
            upgrade(config, "0062")
            _plant_tokens(url, "true")
            upgrade(config, "0063")
            engine: Engine = create_engine(url)
            try:
                with engine.begin() as connection:
                    connection.execute(
                        text(
                            "INSERT INTO personal_access_tokens "
                            "(id, user_id, nome, token_hash, prefix) VALUES "
                            "(gen_random_uuid(), '00000000-0000-7000-8000-0000000000f1', "
                            "'nuova', 'h3', 'pgc_cccc')"
                        )
                    )
            finally:
                engine.dispose()
            assert _token_flags(url)["nuova"] is False
    finally:
        get_settings.cache_clear()


LEGAL_ENTITY_0064 = "00000000-0000-7000-8000-0000000000a1"
STAGE_0064 = "00000000-0000-7000-8000-0000000000e1"
REQUEST_1, REQUEST_2, REQUEST_3, REQUEST_4 = (
    f"rebase:request=00000000-0000-4000-8000-00000000000{n}" for n in range(1, 5)
)
MATCH_1, MATCH_2 = (f"rebase:match=00000000-0000-4000-8000-0000000000a{n}" for n in range(1, 3))


def _plant_0064(url: str) -> None:
    """Three customers, a person and six deals as 0063 leaves them, each with the `note`
    the old code could have written: plain text, nothing, only spaces, a reference line
    under a sentence, a reference alone, two references, a reference inside a line."""
    engine: Engine = create_engine(url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO emitter_profile (id, nome, predefinita, attiva, ragione_sociale, "
                    "nazione, created_at, updated_at) VALUES (:id, 'Studio', true, true, "
                    "'Studio Rossi', 'IT', now(), now())"
                ),
                {"id": LEGAL_ENTITY_0064},
            )
            connection.execute(
                text(
                    "INSERT INTO pipeline_stages (id, nome, posizione, probabilita_default, tipo, "
                    "created_at, updated_at) VALUES (:id, 'Lead', 0, 10, 'open', now(), now())"
                ),
                {"id": STAGE_0064},
            )
            customers = {
                "c1": "Cliente storico\nsu due righe",
                "c2": None,
                "c3": "   \n",
            }
            for key, note in customers.items():
                connection.execute(
                    text(
                        "INSERT INTO customers (id, ragione_sociale, nazione, azienda_id, note, "
                        "custom_fields, created_at, updated_at) VALUES "
                        f"('00000000-0000-7000-8000-0000000000{key}', :nome, 'IT', :azienda, "
                        ":note, '{}', '2025-01-01', '2026-01-10 09:30:00+00')"
                    ),
                    {"nome": f"Cliente {key}", "azienda": LEGAL_ENTITY_0064, "note": note},
                )
            connection.execute(
                text(
                    "INSERT INTO people (id, nome, customer_id, note, custom_fields, created_at, "
                    "updated_at) VALUES ('00000000-0000-7000-8000-0000000000b1', 'Anna', "
                    "'00000000-0000-7000-8000-0000000000c1', 'Contatto storico', '{}', "
                    "'2025-01-01', '2026-02-11 10:00:00+00')"
                )
            )
            deals = {
                "d1": f"Riga uno\n{REQUEST_1}",
                "d2": MATCH_1,
                "d3": f"Testo\n{REQUEST_2}\n{REQUEST_3}\n",
                "d4": f"Vedi {MATCH_2} nel testo",
                "d5": None,
                "d6": "Solo testo",
            }
            for key, note in deals.items():
                connection.execute(
                    text(
                        "INSERT INTO deals (id, nome, customer_id, azienda_id, pipeline_stage_id, "
                        "probabilita, note, custom_fields, created_at, updated_at) VALUES "
                        f"('00000000-0000-7000-8000-0000000000{key}', :nome, "
                        "'00000000-0000-7000-8000-0000000000c1', :azienda, :stage, 10, :note, "
                        "'{}', '2025-01-01', '2026-03-12 11:15:00+00')"
                    ),
                    {
                        "nome": f"Deal {key}",
                        "azienda": LEGAL_ENTITY_0064,
                        "stage": STAGE_0064,
                        "note": note,
                    },
                )
    finally:
        engine.dispose()


def _moved_0064(
    url: str,
) -> tuple[list[tuple[str, str, str, str | None, str | None, str]], dict[str, list[str]]]:
    engine: Engine = create_engine(url)
    try:
        with engine.connect() as connection:
            notes = [
                tuple(row)
                for row in connection.execute(
                    text(
                        "SELECT entity_type, right(entity_id::text, 2), testo, fonte, "
                        "autore_id::text, created_at::text FROM notes "
                        "ORDER BY entity_type, entity_id"
                    )
                )
            ]
            refs = {
                row[0]: row[1]
                for row in connection.execute(
                    text("SELECT right(id::text, 2), rebase_refs FROM deals ORDER BY 1")
                )
            }
        return notes, refs  # type: ignore[return-value]
    finally:
        engine.dispose()


def _note_columns(url: str) -> set[str]:
    engine: Engine = create_engine(url)
    try:
        with engine.connect() as connection:
            return set(
                connection.execute(
                    text(
                        "SELECT table_name FROM information_schema.columns "
                        "WHERE column_name = 'note' "
                        "AND table_name IN ('deals', 'customers', 'people')"
                    )
                ).scalars()
            )
    finally:
        engine.dispose()


def _table_shape(url: str, table: str) -> tuple[bool, bool, set[str]]:
    engine: Engine = create_engine(url)
    try:
        with engine.connect() as connection:
            exists = bool(
                connection.execute(
                    text("SELECT to_regclass(:name) IS NOT NULL"), {"name": f"public.{table}"}
                ).scalar()
            )
            forced = bool(
                connection.execute(
                    text("SELECT relforcerowsecurity FROM pg_class WHERE relname = :name"),
                    {"name": table},
                ).scalar()
            )
            policies = set(
                connection.execute(
                    text("SELECT policyname FROM pg_policies WHERE tablename = :name"),
                    {"name": table},
                ).scalars()
            )
        return exists, forced, policies
    finally:
        engine.dispose()


def test_0064_moves_every_note_into_a_thread_and_the_markers_into_a_column(
    pigrocrm_postgres: Any,
) -> None:
    """REB-879: over the notes planted at 0063. Each non-blank `note` of the three tables
    becomes the first note of its row, with no author and no source and the row's
    `updated_at` as its date; a reference line of a deal's note leaves the text and goes to
    `rebase_refs`; the three columns are dropped; the table is under the azienda policy,
    forced. The downgrade rebuilds the columns, references back as the last lines."""
    with pigrocrm_postgres.fresh_container() as container:
        url = container.get_connection_url()
        config = _alembic_config(url)
        upgrade(config, "0063")
        _plant_0064(url)
        assert _note_columns(url) == {"deals", "customers", "people"}
        assert _table_shape(url, "notes") == (False, False, set())
        assert _table_shape(url, "note_revisions") == (False, False, set())

        upgrade(config, "0064")

        notes, refs = _moved_0064(url)
        assert notes == [
            (
                "customer",
                "c1",
                "Cliente storico\nsu due righe",
                None,
                None,
                "2026-01-10 09:30:00+00",
            ),
            ("deal", "d1", "Riga uno", None, None, "2026-03-12 11:15:00+00"),
            ("deal", "d3", "Testo", None, None, "2026-03-12 11:15:00+00"),
            ("deal", "d4", f"Vedi {MATCH_2} nel testo", None, None, "2026-03-12 11:15:00+00"),
            ("deal", "d6", "Solo testo", None, None, "2026-03-12 11:15:00+00"),
            ("person", "b1", "Contatto storico", None, None, "2026-02-11 10:00:00+00"),
        ]
        assert refs == {
            "d1": [REQUEST_1],
            "d2": [MATCH_1],
            "d3": [REQUEST_2, REQUEST_3],
            "d4": [MATCH_2],
            "d5": [],
            "d6": [],
        }
        assert _note_columns(url) == set()
        assert _table_shape(url, "notes") == (True, True, {"ambito_azienda"})
        assert _table_shape(url, "note_revisions") == (True, True, {"ambito_azienda"})

        downgrade(config, "0063")

        assert _table_shape(url, "notes")[0] is False
        assert _table_shape(url, "note_revisions")[0] is False
        assert _note_columns(url) == {"deals", "customers", "people"}
        engine: Engine = create_engine(url)
        with engine.connect() as connection:
            restored = {
                row[0]: row[1]
                for row in connection.execute(
                    text(
                        "SELECT right(id::text, 2), note FROM deals "
                        "UNION ALL SELECT right(id::text, 2), note FROM customers "
                        "UNION ALL SELECT right(id::text, 2), note FROM people"
                    )
                )
            }
        engine.dispose()
        assert restored["d1"] == f"Riga uno\n{REQUEST_1}"
        assert restored["d2"] == MATCH_1
        assert restored["d3"] == f"Testo\n{REQUEST_2}\n{REQUEST_3}"
        assert restored["c1"] == "Cliente storico\nsu due righe"
        assert restored["b1"] == "Contatto storico"
        assert restored["d5"] is None and restored["c2"] is None

        # And forward again over what the downgrade left: the same thread.
        upgrade(config, "0064")
        assert _moved_0064(url)[1] == refs


def test_0064_runs_on_a_database_with_no_notes_at_all(pigrocrm_postgres: Any) -> None:
    with pigrocrm_postgres.fresh_container() as container:
        url = container.get_connection_url()
        config = _alembic_config(url)
        upgrade(config, "0063")
        upgrade(config, "0064")
        assert _moved_0064(url) == ([], {})


def test_0068_renames_the_vocabulary_and_the_json_both_ways(pigrocrm_postgres: Any) -> None:
    """REB-898 over rows planted at 0067: a task with its status and origin, a pipeline
    stage code, a space setting key and three timeline entries (a renamed kind with renamed
    payload keys and values, a kind that keeps its name next to English keys of its own, and
    a task kind). Up, the words and keys are English and the tables carry the new names;
    down, every row is what was planted."""
    task_id = "00000000-0000-7000-8000-0000000000a1"
    with pigrocrm_postgres.fresh_container() as container:
        url = container.get_connection_url()
        config = _alembic_config(url)
        upgrade(config, "0067")
        engine: Engine = create_engine(url)
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO attivita (id, titolo, stato, origine, regola, completata_il) "
                    "VALUES (:id, 't', 'completata', 'automazione', 'deal_fermo', now())"
                ),
                {"id": task_id},
            )
            connection.execute(
                text(
                    "INSERT INTO pipeline_stages (id, code, nome, posizione, probabilita_default,"
                    " tipo) VALUES (gen_random_uuid(), 'negoziazione', 'N', 1, 50, 'open')"
                )
            )
            connection.execute(
                text("INSERT INTO space_settings (key, value) VALUES ('solleciti_grace_days', '3')")
            )
            connection.execute(
                text(
                    "INSERT INTO activities (id, entity_type, entity_id, kind, actor_type, "
                    "payload, occurred_at) VALUES "
                    "(gen_random_uuid(), 'attivita', :id, 'completata', 'user', "
                    ' \'{"il": "2026-01-01"}\', now()), '
                    "(gen_random_uuid(), 'emitter_profile', :id, 'created', 'user', "
                    ' \'{"nome": "Studio", "changed": ["nome", "predefinita"]}\', now()), '
                    "(gen_random_uuid(), 'deal', :id, 'stage_changed', 'user', "
                    ' \'{"from": "a", "to": "b"}\', now())'
                ),
                {"id": task_id},
            )
        engine.dispose()

        before = _read_0068(url, "attivita", "stato, origine, regola")
        upgrade(config, "0068")
        after = _read_0068(url, "tasks", "status, origin, rule")
        downgrade(config, "0067")
        round_trip = _read_0068(url, "attivita", "stato, origine, regola")
    assert after[0] == ("completed", "automation", "stalled_deal")
    assert after[1] == "negotiation"
    assert after[2] == "payment_reminders_grace_days"
    assert after[3] == [
        ("deal", "stage_changed", {"to": "b", "from": "a"}),
        ("legal_entity", "created", {"name": "Studio", "changed": ["name", "is_default"]}),
        ("task", "completed", {"at": "2026-01-01"}),
    ]
    assert before == round_trip


def _read_0068(url: str, table: str, columns: str) -> tuple[Any, ...]:
    engine: Engine = create_engine(url)
    try:
        with engine.connect() as connection:
            task = tuple(
                connection.execute(text(f"SELECT {columns} FROM {table}")).one()  # noqa: S608
            )
            stage = connection.execute(text("SELECT code FROM pipeline_stages")).scalar_one()
            setting = connection.execute(text("SELECT key FROM space_settings")).scalar_one()
            timeline = connection.execute(
                text("SELECT entity_type, kind, payload FROM activities ORDER BY entity_type")
            ).all()
        return task, stage, setting, [tuple(row) for row in timeline]
    finally:
        engine.dispose()


def test_0069_renames_the_postal_code_column_and_its_json_both_ways(pigrocrm_postgres: Any) -> None:
    """REB-898 over rows planted at 0068: a customer and a legal entity with their `cap`, an
    invoice whose snapshot carries it in both parties, and timeline entries naming it in
    `changed`, `before` and `after`. Up, the columns, the snapshot keys and the payload
    names say `postal_code`; down, every row is what was planted."""
    snapshot = (
        '{"version": 1, "legal_entity": {"legal_name": "L", "cap": "00100"},'
        ' "customer": {"legal_name": "C", "cap": "20124"}, "tax": {}}'
    )
    payload = '{"changed": ["cap", "city"], "before": {"cap": "1"}, "after": {"cap": "2"}}'
    with pigrocrm_postgres.fresh_container() as container:
        url = container.get_connection_url()
        config = _alembic_config(url)
        upgrade(config, "0068")
        engine: Engine = create_engine(url)
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO legal_entities (id, name, legal_name, country, cap) VALUES "
                    "('00000000-0000-7000-8000-0000000000e1', 'E', 'E srl', 'IT', '00100')"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO customers (id, legal_name, country, cap, custom_fields, "
                    "legal_entity_id) VALUES ('00000000-0000-7000-8000-0000000000c1', 'C srl', "
                    "'IT', '20124', '{}', '00000000-0000-7000-8000-0000000000e1')"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO activities (id, entity_type, entity_id, kind, actor_type, "
                    "payload, occurred_at) VALUES "
                    "(gen_random_uuid(), 'customer', gen_random_uuid(), 'updated', 'user', "
                    "CAST(:payload AS jsonb), now()), "
                    "(gen_random_uuid(), 'deal', gen_random_uuid(), 'updated', 'user', "
                    "CAST(:payload AS jsonb), now())"
                ),
                {"payload": payload},
            )
            connection.execute(text("SELECT set_config('pigrocrm.legal_entities', '*', false)"))
            connection.execute(
                text(
                    "INSERT INTO invoices (id, customer_id, legal_entity_id, type, status, "
                    "sdi_document_type, currency, taxable_amount, vat_amount, stamp_duty, total, "
                    "payment_status, custom_fields, snapshot_version, snapshot) VALUES "
                    "('00000000-0000-7000-8000-0000000000f1', "
                    "'00000000-0000-7000-8000-0000000000c1', "
                    "'00000000-0000-7000-8000-0000000000e1', 'invoice', 'draft', "
                    "'TD01', 'EUR', 0, 0, 0, 0, 'unpaid', '{}', 1, CAST(:snapshot AS jsonb))"
                ),
                {"snapshot": snapshot},
            )
        engine.dispose()

        before = _read_0069(url, "cap")
        upgrade(config, "0069")
        after = _read_0069(url, "postal_code")
        downgrade(config, "0068")
        round_trip = _read_0069(url, "cap")
    assert after[0] == ("00100", "20124")
    assert after[1]["legal_entity"] == {"legal_name": "L", "postal_code": "00100"}
    assert after[1]["customer"] == {"legal_name": "C", "postal_code": "20124"}
    assert after[2] == {
        "customer": {
            "changed": ["postal_code", "city"],
            "before": {"postal_code": "1"},
            "after": {"postal_code": "2"},
        },
        "deal": {"changed": ["cap", "city"], "before": {"cap": "1"}, "after": {"cap": "2"}},
    }
    assert before == round_trip


def _read_0069(url: str, column: str) -> tuple[Any, ...]:
    engine: Engine = create_engine(url)
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT set_config('pigrocrm.legal_entities', '*', false)"))
            columns = tuple(
                connection.execute(
                    text(
                        f"SELECT (SELECT {column} FROM legal_entities), "  # noqa: S608
                        f"(SELECT {column} FROM customers)"
                    )
                ).one()
            )
            snapshot = connection.execute(text("SELECT snapshot FROM invoices")).scalar_one()
            timeline = {
                entity: payload
                for entity, payload in connection.execute(
                    text("SELECT entity_type, payload FROM activities")
                ).all()
            }
        return columns, snapshot, timeline
    finally:
        engine.dispose()


def test_0070_gives_every_valued_deal_one_lump_sum_line_and_drops_with_the_values_kept(
    pigrocrm_postgres: Any,
) -> None:
    """REB-928 over deals planted at 0069: two with a value, one with none. Up, each valued
    deal has one `flat`, `one_off` line of quantity 1 at its value and the unvalued one has
    none; the table is forced and policied. Down, the table is gone and the values stay."""
    entity = "00000000-0000-7000-8000-0000000000e1"
    stage = "00000000-0000-7000-8000-0000000000f1"
    customer = "00000000-0000-7000-8000-0000000000c1"
    values = {"d1": "1234.50", "d2": "0.00", "d3": None}
    with pigrocrm_postgres.fresh_container() as container:
        url = container.get_connection_url()
        config = _alembic_config(url)
        upgrade(config, "0069")
        engine: Engine = create_engine(url)
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO legal_entities (id, name, legal_name, country) "
                    f"VALUES ('{entity}', 'E', 'E srl', 'IT')"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO pipeline_stages (id, name, position, default_probability, type) "
                    f"VALUES ('{stage}', 'Lead', 0, 10, 'open')"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO customers (id, legal_name, country, legal_entity_id, "
                    f"custom_fields) VALUES ('{customer}', 'C', 'IT', '{entity}', '{{}}')"
                )
            )
            for key, value in values.items():
                connection.execute(
                    text(
                        "INSERT INTO deals (id, name, customer_id, legal_entity_id, "
                        "pipeline_stage_id, probability, expected_value, custom_fields) VALUES "
                        f"('00000000-0000-7000-8000-0000000000{key}', :name, '{customer}', "
                        f"'{entity}', '{stage}', 10, :value, '{{}}')"
                    ),
                    {"name": key, "value": value},
                )
        engine.dispose()

        upgrade(config, "0070")
        engine = create_engine(url)
        with engine.connect() as connection:
            lines = connection.execute(
                text(
                    "SELECT d.name, l.position, l.unit, l.delivery_period, l.unit_price, "
                    "l.quantity, l.start, l.duration_months FROM deal_value_lines l "
                    "JOIN deals d ON d.id = l.deal_id ORDER BY d.name"
                )
            ).all()
        engine.dispose()
        assert [tuple(row) for row in lines] == [
            ("d1", 0, "flat", "one_off", Decimal("1234.50"), Decimal("1.00"), None, None),
            ("d2", 0, "flat", "one_off", Decimal("0.00"), Decimal("1.00"), None, None),
        ]
        assert _table_shape(url, "deal_value_lines") == (True, True, {"legal_entity_scope"})

        downgrade(config, "0069")
        assert _table_shape(url, "deal_value_lines")[0] is False
        engine = create_engine(url)
        with engine.connect() as connection:
            kept = connection.execute(
                text("SELECT name, expected_value FROM deals ORDER BY name")
            ).all()
        engine.dispose()
        assert [tuple(row) for row in kept] == [
            ("d1", Decimal("1234.50")),
            ("d2", Decimal("0.00")),
            ("d3", None),
        ]


def test_0070_refuses_a_line_the_service_would_refuse(pigrocrm_postgres: Any) -> None:
    """The CHECKs are the second line behind `DealService`: a flat line of quantity 2, a flat
    one-off with a duration, a negative price and a zero quantity are each refused by the
    database, and `(deal_id, position)` is unique."""
    with pigrocrm_postgres.fresh_container() as container:
        url = container.get_connection_url()
        config = _alembic_config(url)
        upgrade(config, "0070")
        engine: Engine = create_engine(url)
        deal = "00000000-0000-7000-8000-0000000000d1"
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO legal_entities (id, name, legal_name, country) "
                    "VALUES ('00000000-0000-7000-8000-0000000000e1', 'E', 'E srl', 'IT')"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO pipeline_stages (id, name, position, default_probability, type) "
                    "VALUES ('00000000-0000-7000-8000-0000000000f1', 'Lead', 0, 10, 'open')"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO customers (id, legal_name, country, legal_entity_id, "
                    "custom_fields) VALUES ('00000000-0000-7000-8000-0000000000c1', 'C', 'IT', "
                    "'00000000-0000-7000-8000-0000000000e1', '{}')"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO deals (id, name, customer_id, legal_entity_id, "
                    "pipeline_stage_id, probability, custom_fields) VALUES "
                    f"('{deal}', 'D', '00000000-0000-7000-8000-0000000000c1', "
                    "'00000000-0000-7000-8000-0000000000e1', "
                    "'00000000-0000-7000-8000-0000000000f1', 10, '{}')"
                )
            )

        def insert(position: int, unit: str, period: str, price: str, qty: str, months: str):  # type: ignore[no-untyped-def]
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO deal_value_lines (id, deal_id, position, unit, "
                        "delivery_period, unit_price, quantity, duration_months) VALUES "
                        f"(gen_random_uuid(), '{deal}', {position}, '{unit}', '{period}', "
                        f"{price}, {qty}, {months})"
                    )
                )

        insert(0, "flat", "monthly", "10", "1", "null")
        for args in (
            (1, "flat", "monthly", "10", "2", "null"),
            (1, "flat", "one_off", "10", "1", "3"),
            (1, "flat", "one_off", "-1", "1", "null"),
            (1, "day", "monthly", "10", "0", "null"),
            (1, "day", "monthly", "10", "1", "0"),
            (1, "week", "monthly", "10", "1", "null"),
            (1, "flat", "quarterly", "10", "1", "null"),
            (0, "flat", "monthly", "10", "1", "null"),
        ):
            with pytest.raises(IntegrityError):
                insert(*args)
        insert(1, "day", "one_off", "800", "5", "1")
        engine.dispose()


def test_0073_turns_the_five_columns_into_primary_work_points_and_downgrade_restores_them(
    pigrocrm_postgres: Any,
) -> None:
    """REB-940 over people and customers planted at 0072. Up, each non-blank value becomes one
    primary `work` point (email, then phone, then LinkedIn, numbered from 0), compared in the
    shape the service compares (lowercase address, digits of a number), a phone with no digit
    is not carried over, the five columns and their indexes are gone and the table is forced and
    policied. Down, the primary values are back in the columns, with their indexes."""
    entity = "00000000-0000-7000-8000-0000000000e1"
    customer = "00000000-0000-7000-8000-0000000000c1"
    ada = "00000000-0000-7000-8000-0000000000a1"
    blank = "00000000-0000-7000-8000-0000000000a2"
    phone_only = "00000000-0000-7000-8000-0000000000a3"
    with pigrocrm_postgres.fresh_container() as container:
        url = container.get_connection_url()
        config = _alembic_config(url)
        upgrade(config, "0072")
        engine: Engine = create_engine(url)
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO legal_entities (id, name, legal_name, country) "
                    f"VALUES ('{entity}', 'E', 'E srl', 'IT')"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO customers (id, legal_name, country, legal_entity_id, email, "
                    f"phone, custom_fields) VALUES ('{customer}', 'C', 'IT', '{entity}', "
                    "' Info@Acme.IT ', '+39 02 7654321', '{}')"
                )
            )
            for person, first, email, phone, linkedin in (
                (ada, "Ada", " Ada@Acme.IT ", "+39 02-123 4567", "https://www.linkedin.com/in/ada"),
                (blank, "Blank", "  ", "n/a", None),
                (phone_only, "Phone", None, "0039 333 1234567", None),
            ):
                connection.execute(
                    text(
                        "INSERT INTO people (id, first_name, customer_id, email, phone, "
                        "linkedin, custom_fields) VALUES (:id, :first, :customer, :email, "
                        ":phone, :linkedin, '{}')"
                    ),
                    {
                        "id": person,
                        "first": first,
                        "customer": customer,
                        "email": email,
                        "phone": phone,
                        "linkedin": linkedin,
                    },
                )
        engine.dispose()

        upgrade(config, "0073")
        engine = create_engine(url)
        with engine.connect() as connection:
            points = connection.execute(
                text(
                    "SELECT coalesce(person_id, customer_id)::text, kind, value, normalized, "
                    "label, is_primary, position FROM contact_points "
                    "ORDER BY 1, position"
                )
            ).all()
            columns = set(
                connection.execute(
                    text(
                        "SELECT table_name || '.' || column_name FROM information_schema.columns "
                        "WHERE table_name IN ('people', 'customers') "
                        "AND column_name IN ('email', 'phone', 'linkedin')"
                    )
                ).scalars()
            )
            indexes = set(
                connection.execute(
                    text(
                        "SELECT indexname FROM pg_indexes "
                        "WHERE tablename IN ('people', 'customers') "
                        "AND indexname LIKE 'ix_%_email%'"
                    )
                ).scalars()
            )
        engine.dispose()
        assert [tuple(row) for row in points] == [
            (ada, "email", "Ada@Acme.IT", "ada@acme.it", "work", True, 0),
            (ada, "phone", "+39 02-123 4567", "+39021234567", "work", True, 1),
            (
                ada,
                "linkedin",
                "https://www.linkedin.com/in/ada",
                "https://www.linkedin.com/in/ada",
                "work",
                True,
                2,
            ),
            (phone_only, "phone", "0039 333 1234567", "00393331234567", "work", True, 0),
            (customer, "email", "Info@Acme.IT", "info@acme.it", "work", True, 0),
            (customer, "phone", "+39 02 7654321", "+39027654321", "work", True, 1),
        ]
        assert columns == set() and indexes == set()
        assert _table_shape(url, "contact_points") == (True, True, {"legal_entity_scope"})

        downgrade(config, "0072")
        assert _table_shape(url, "contact_points")[0] is False
        engine = create_engine(url)
        with engine.connect() as connection:
            restored = connection.execute(
                text("SELECT first_name, email, phone, linkedin FROM people ORDER BY first_name")
            ).all()
            customer_row = connection.execute(text("SELECT email, phone FROM customers")).one()
            indexes = set(
                connection.execute(
                    text(
                        "SELECT indexname FROM pg_indexes "
                        "WHERE tablename IN ('people', 'customers') "
                        "AND indexname LIKE 'ix_%_email%'"
                    )
                ).scalars()
            )
        engine.dispose()
        assert [tuple(row) for row in restored] == [
            ("Ada", "Ada@Acme.IT", "+39 02-123 4567", "https://www.linkedin.com/in/ada"),
            ("Blank", None, None, None),
            ("Phone", None, "0039 333 1234567", None),
        ]
        assert tuple(customer_row) == ("Info@Acme.IT", "+39 02 7654321")
        assert indexes == {
            "ix_people_email",
            "ix_people_email_lower",
            "ix_people_email_trgm",
            "ix_customers_email",
            "ix_customers_email_lower",
            "ix_customers_email_trgm",
        }


def test_0073_carries_legacy_free_text_and_an_owner_holding_it_saves_unchanged(
    pigrocrm_postgres: Any, capfd: pytest.CaptureFixture[str]
) -> None:
    """REB-940 over free text the old columns allowed. It is carried over as typed (nothing is
    validated by the migration); a phone with no digit becomes the note of its owner's email
    point, or, with no email to carry it, is named in the log with the owner's id; and an owner
    that holds such values saves, with the list read back, unchanged and with something else
    added: what predates the rules is never the reason a save is refused."""
    from sqlalchemy.orm import Session

    from pigrocrm.core.actor import Actor
    from pigrocrm.core.contact_points.schemas import ContactPointIn
    from pigrocrm.core.people.schemas import PersonUpdate
    from pigrocrm.core.people.service import PersonService

    entity = "00000000-0000-7000-8000-0000000000e1"
    customer = "00000000-0000-7000-8000-0000000000c1"
    legacy = "00000000-0000-7000-8000-0000000000b1"
    noted = "00000000-0000-7000-8000-0000000000b2"
    orphan = "00000000-0000-7000-8000-0000000000b3"
    with pigrocrm_postgres.fresh_container() as container:
        url = container.get_connection_url()
        config = _alembic_config(url)
        upgrade(config, "0072")
        engine: Engine = create_engine(url)
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO legal_entities (id, name, legal_name, country) "
                    f"VALUES ('{entity}', 'E', 'E srl', 'IT')"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO customers (id, legal_name, country, legal_entity_id, "
                    "custom_fields) VALUES (:id, 'C', 'IT', :entity, '{}')"
                ),
                {"id": customer, "entity": entity},
            )
            for person, email, phone, linkedin in (
                (legacy, "ada(at)acme", "tel. 02 123 45 (ufficio)", "Ada Lovelace on LinkedIn"),
                (noted, "noted@acme.it", "n/a", None),
                (orphan, None, "chiedere in portineria", None),
            ):
                connection.execute(
                    text(
                        "INSERT INTO people (id, first_name, customer_id, email, phone, "
                        "linkedin, custom_fields) VALUES (:id, :name, :customer, :email, "
                        ":phone, :linkedin, '{}')"
                    ),
                    {
                        "id": person,
                        "name": f"P{person[-2:]}",
                        "customer": customer,
                        "email": email,
                        "phone": phone,
                        "linkedin": linkedin,
                    },
                )
        engine.dispose()

        capfd.readouterr()
        upgrade(config, "0073")
        # Alembic's own logging setup replaces the logger's handlers, so the line is read where
        # it is written: the console.
        seen = capfd.readouterr().err.splitlines()
        lines = [m for m in seen if "legacy phone" in m]
        assert len(lines) == 1 and orphan in lines[0] and "chiedere in portineria" in lines[0]

        # The service below reads through today's models, which name `organizations`: bring
        # the database on to head (REB-969) now that the log line of 0073 has been read.
        upgrade(config, "head")

        engine = create_engine(url)
        with Session(engine) as session:
            service = PersonService(session)
            actor = Actor(id=None, type="system", role="admin")
            before = service.get(UUID(legacy), actor)
            assert [(p.kind, p.value) for p in before.contact_points] == [
                ("email", "ada(at)acme"),
                ("phone", "tel. 02 123 45 (ufficio)"),
                ("linkedin", "Ada Lovelace on LinkedIn"),
            ]
            assert service.get(UUID(noted), actor).contact_points[0].note == "Telefono: n/a"
            assert service.get(UUID(orphan), actor).contact_points == []

            sent = [
                ContactPointIn.model_validate(p.model_dump(exclude={"id", "position"}))
                for p in before.contact_points
            ]
            # Unchanged: saves, and writes nothing.
            unchanged = service.update(UUID(legacy), PersonUpdate(contact_points=sent), actor)
            assert [p.id for p in unchanged.contact_points] == [p.id for p in before.contact_points]
            # With a new point beside them: saves, and the legacy values stay as they were.
            sent.append(ContactPointIn(kind="email", value="ada@acme.it"))
            grown = service.update(UUID(legacy), PersonUpdate(contact_points=sent), actor)
            assert [(p.kind, p.value) for p in grown.contact_points] == [
                ("email", "ada(at)acme"),
                ("phone", "tel. 02 123 45 (ufficio)"),
                ("linkedin", "Ada Lovelace on LinkedIn"),
                ("email", "ada@acme.it"),
            ]
            assert grown.email == "ada(at)acme"
        engine.dispose()


def test_0075_gives_every_legal_entity_31_december_adds_the_policied_table_and_downgrade_undoes_it(
    pigrocrm_postgres: Any,
) -> None:
    """REB-974 over a legal entity and its profile planted at 0074. Up, the entity reads 12 and
    31 from the column default, the profile has no INPS category and no ISA, `fiscal_estimates`
    exists, forced and policied, and the two check constraints refuse a category that is not
    one of the two and a 30 February. Down, the table and the five columns are gone."""
    entity = "00000000-0000-7000-8000-0000000000e1"
    with pigrocrm_postgres.fresh_container() as container:
        url = container.get_connection_url()
        config = _alembic_config(url)
        upgrade(config, "0074")
        engine: Engine = create_engine(url)
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO legal_entities (id, name, legal_name, country) "
                    f"VALUES ('{entity}', 'E', 'E srl', 'IT')"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO fiscal_profile (id, legal_entity_id, default_vat_rate, "
                    "apply_stamp_duty, stamp_duty_threshold, stamp_duty_amount, payment_terms, "
                    "payment_method, due_days) VALUES "
                    f"(gen_random_uuid(), '{entity}', 0, true, 77.47, 2.00, 'TP02', 'MP05', 30)"
                )
            )
        engine.dispose()

        upgrade(config, "0075")
        engine = create_engine(url)
        with engine.connect() as connection:
            year_end = connection.execute(
                text("SELECT fiscal_year_end_month, fiscal_year_end_day FROM legal_entities")
            ).one()
            parameters = connection.execute(
                text("SELECT inps_category, isa FROM fiscal_profile")
            ).one()
        assert tuple(year_end) == (12, 31)
        assert tuple(parameters) == (None, None)
        assert _table_shape(url, "fiscal_estimates") == (True, True, {"legal_entity_scope"})
        for statement in (
            "UPDATE fiscal_profile SET inps_category = 'cassa_forense'",
            "UPDATE legal_entities SET fiscal_year_end_month = 2, fiscal_year_end_day = 30",
        ):
            with pytest.raises(IntegrityError), engine.begin() as connection:
                connection.execute(text(statement))
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE legal_entities SET fiscal_year_end_month = 2, fiscal_year_end_day = 29"
                )
            )
        engine.dispose()

        downgrade(config, "0074")
        assert _table_shape(url, "fiscal_estimates")[0] is False
        engine = create_engine(url)
        with engine.connect() as connection:
            columns = set(
                connection.execute(
                    text(
                        "SELECT table_name || '.' || column_name FROM information_schema.columns "
                        "WHERE column_name IN ('inps_category', 'isa', "
                        "'fiscal_year_end_month', 'fiscal_year_end_day')"
                    )
                ).scalars()
            )
        engine.dispose()
        assert columns == set()


def _due_items_shape(url: str) -> tuple[set[str], set[str], set[str]]:
    """Which of the two due item tables exist, which of them carry a forced row-level
    security, and which carry the `legal_entity_scope` policy."""
    engine: Engine = create_engine(url)
    try:
        with engine.connect() as connection:
            tables = {
                row[0]
                for row in connection.execute(
                    text(
                        "SELECT tablename FROM pg_tables WHERE schemaname = 'public' "
                        "AND tablename IN ('due_items', 'due_item_settlements')"
                    )
                )
            }
            forced = {
                row[0]
                for row in connection.execute(
                    text(
                        "SELECT relname FROM pg_class WHERE relrowsecurity AND relforcerowsecurity "
                        "AND relname IN ('due_items', 'due_item_settlements')"
                    )
                )
            }
            policies = {
                row[0]
                for row in connection.execute(
                    text(
                        "SELECT tablename FROM pg_policies WHERE policyname = 'legal_entity_scope' "
                        "AND tablename IN ('due_items', 'due_item_settlements')"
                    )
                )
            }
    finally:
        engine.dispose()
    return tables, forced, policies


def test_0076_creates_the_due_item_tables_under_the_scope_policy_and_downgrade_drops_them(
    pigrocrm_postgres: Any,
) -> None:
    """REB-967: two new tables, both with the row-level policy every table that reaches a
    legal entity carries, forced; nothing is backfilled, so both ways are a pure create and
    drop."""
    both = {"due_items", "due_item_settlements"}
    with pigrocrm_postgres.fresh_container() as container:
        url = container.get_connection_url()
        config = _alembic_config(url)
        upgrade(config, "0075")
        assert _due_items_shape(url) == (set(), set(), set())
        upgrade(config, "0076")
        assert _due_items_shape(url) == (both, both, both)
        downgrade(config, "0075")
        assert _due_items_shape(url) == (set(), set(), set())
        upgrade(config, "0076")
        assert _due_items_shape(url)[0] == both


def _tables_shape(url: str, names: set[str]) -> tuple[set[str], set[str], set[str]]:
    """Which of `names` exist, which carry a forced row-level security, and which carry the
    `legal_entity_scope` policy."""
    engine: Engine = create_engine(url)
    listed = ", ".join(f"'{name}'" for name in sorted(names))
    try:
        with engine.connect() as connection:
            tables = {
                row[0]
                for row in connection.execute(
                    text(
                        "SELECT tablename FROM pg_tables WHERE schemaname = 'public' "
                        f"AND tablename IN ({listed})"
                    )
                )
            }
            forced = {
                row[0]
                for row in connection.execute(
                    text(
                        "SELECT relname FROM pg_class WHERE relrowsecurity AND relforcerowsecurity "
                        f"AND relname IN ({listed})"
                    )
                )
            }
            policies = {
                row[0]
                for row in connection.execute(
                    text(
                        "SELECT tablename FROM pg_policies WHERE policyname = 'legal_entity_scope' "
                        f"AND tablename IN ({listed})"
                    )
                )
            }
    finally:
        engine.dispose()
    return tables, forced, policies


def _entity_visible_names(url: str) -> tuple[bool, bool]:
    """Whether the row-level function of the activity feed names `staff_member` and
    `payroll_settings`."""
    engine: Engine = create_engine(url)
    try:
        with engine.connect() as connection:
            definition = connection.execute(
                text(
                    "SELECT pg_get_functiondef('entity_visible(text, uuid, boolean)'::regprocedure)"
                )
            ).scalar_one()
    finally:
        engine.dispose()
    return "'staff_member'" in definition, "'payroll_settings'" in definition


def test_0079_creates_the_staff_tables_under_the_scope_policy_and_downgrade_drops_them(
    pigrocrm_postgres: Any,
) -> None:
    """REB-977: two new tables, both with the row-level policy every table that reaches a
    legal entity carries, forced; nothing is backfilled, so both ways are a pure create and
    drop."""
    both = {"staff_members", "payroll_settings"}
    with pigrocrm_postgres.fresh_container() as container:
        url = container.get_connection_url()
        config = _alembic_config(url)
        upgrade(config, "0078")
        assert _tables_shape(url, both) == (set(), set(), set())
        assert _entity_visible_names(url) == (False, False)
        upgrade(config, "0079")
        assert _tables_shape(url, both) == (both, both, both)
        assert _entity_visible_names(url) == (True, True)
        downgrade(config, "0078")
        assert _tables_shape(url, both) == (set(), set(), set())
        assert _entity_visible_names(url) == (False, False)
        upgrade(config, "0079")
        assert _tables_shape(url, both)[0] == both
        assert _entity_visible_names(url) == (True, True)


def _collaborators_shape(url: str) -> tuple[bool, bool, bool]:
    """Whether `engagements` exists, whether `organizations` has `hub_freelancer_id`, and whether
    the unique index on it is there."""
    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            table = connection.execute(
                text("SELECT to_regclass('public.engagements') IS NOT NULL")
            ).scalar_one()
            column = connection.execute(
                text(
                    "SELECT count(*) > 0 FROM information_schema.columns WHERE "
                    "table_name = 'organizations' AND column_name = 'hub_freelancer_id'"
                )
            ).scalar_one()
            index = connection.execute(
                text(
                    "SELECT count(*) > 0 FROM pg_indexes WHERE "
                    "indexname = 'uq_organizations_hub_freelancer_id'"
                )
            ).scalar_one()
    finally:
        engine.dispose()
    return bool(table), bool(column), bool(index)


def test_0081_adds_the_hub_freelancer_id_and_the_engagements_table_and_downgrade_removes_them(
    pigrocrm_postgres: Any,
) -> None:
    """REB-976: a column and a table, nothing backfilled. The checks that keep the payment rule
    and `days_per_week` honest are in the schema, so a row that breaks them is refused here."""
    with pigrocrm_postgres.fresh_container() as container:
        url = container.get_connection_url()
        config = _alembic_config(url)
        upgrade(config, "0080")
        assert _collaborators_shape(url) == (False, False, False)
        upgrade(config, "0081")
        assert _collaborators_shape(url) == (True, True, True)

        engine = create_engine(url)
        try:
            with engine.begin() as connection:
                freelancer = connection.execute(
                    text(
                        "INSERT INTO organizations "
                        "(id, legal_name, country, custom_fields, roles, hub_freelancer_id) "
                        "VALUES (gen_random_uuid(), 'F', 'IT', '{}', "
                        "ARRAY['freelancer']::varchar[], "
                        "gen_random_uuid()) RETURNING id"
                    )
                ).scalar_one()
            insert = text(
                "INSERT INTO engagements (id, freelancer_id, role, starts_on, fee, fee_unit, "
                "payment_rule, payment_days, collection_days, days_per_week) VALUES "
                "(gen_random_uuid(), :f, 'r', '2026-11-02', 100, 'day', :rule, :days, :coll, :dpw)"
            )
            ok = {"f": freelancer, "rule": "fixed_term", "days": 30, "coll": None, "dpw": 2.5}
            with engine.begin() as connection:
                connection.execute(insert, ok)
            for bad in (
                {**ok, "days": 61},
                {**ok, "coll": 10},
                {**ok, "rule": "on_collection", "days": 30, "coll": 10},
                {**ok, "rule": "on_collection", "days": None, "coll": 61},
                {**ok, "dpw": 0.7},
                {**ok, "dpw": 5.5},
            ):
                with pytest.raises(IntegrityError), engine.begin() as connection:
                    connection.execute(insert, bad)
            with pytest.raises(IntegrityError), engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO organizations "
                        "(id, legal_name, country, custom_fields, hub_freelancer_id) "
                        "VALUES (gen_random_uuid(), 'C', 'IT', '{}', gen_random_uuid())"
                    )
                )
        finally:
            engine.dispose()

        downgrade(config, "0080")
        assert _collaborators_shape(url) == (False, False, False)
        upgrade(config, "0081")
        assert _collaborators_shape(url)[0] is True


_BANK_TABLES = {
    "bank_accounts",
    "bank_statements",
    "bank_movements",
    "statement_movements",
    "organization_ibans",
}


def test_0084_adds_the_bank_tables_with_row_security_and_downgrade_removes_them(
    pigrocrm_postgres: Any,
) -> None:
    """REB-998: five new tables, each with the row-level policy forced; nothing is backfilled,
    so both ways are a pure create and drop."""
    with pigrocrm_postgres.fresh_container() as container:
        url = container.get_connection_url()
        config = _alembic_config(url)
        upgrade(config, "0081")
        assert _tables_shape(url, _BANK_TABLES) == (set(), set(), set())
        upgrade(config, "0084")
        assert _tables_shape(url, _BANK_TABLES) == (_BANK_TABLES, _BANK_TABLES, _BANK_TABLES)
        downgrade(config, "0081")
        assert _tables_shape(url, _BANK_TABLES) == (set(), set(), set())
        upgrade(config, "0084")
        assert _tables_shape(url, _BANK_TABLES)[0] == _BANK_TABLES


def _end_to_end_column(url: str) -> bool:
    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            return bool(
                connection.execute(
                    text(
                        "SELECT count(*) > 0 FROM information_schema.columns WHERE "
                        "table_name = 'bank_movements' AND column_name = 'end_to_end_id'"
                    )
                ).scalar_one()
            )
    finally:
        engine.dispose()


def test_0085_adds_the_end_to_end_id_column_and_downgrade_removes_it(
    pigrocrm_postgres: Any,
) -> None:
    """REB-1017: one nullable column and its index, nothing backfilled."""
    with pigrocrm_postgres.fresh_container() as container:
        url = container.get_connection_url()
        config = _alembic_config(url)
        upgrade(config, "0084")
        assert _end_to_end_column(url) is False
        upgrade(config, "0085")
        assert _end_to_end_column(url) is True
        downgrade(config, "0084")
        assert _end_to_end_column(url) is False
        upgrade(config, "0085")
        assert _end_to_end_column(url) is True


_ENTITY_VISIBLE_DEF = text(
    "SELECT pg_get_functiondef('entity_visible(text, uuid, boolean)'::regprocedure)"
)


def test_0086_turns_every_cost_into_an_expense_and_downgrade_turns_them_back(
    pigrocrm_postgres: Any,
) -> None:
    """REB-993: the table is renamed with its rows, so a cost keeps its id, `created_at`,
    description, custom fields and soft delete, and its history, its custom-field definitions
    and the space's module switch follow it into the new names. What a cost counted is
    `cost_amount` from the first day."""
    cost_ids = {
        "live": "00000000-0000-7000-8000-0000000000f1",
        "archived": "00000000-0000-7000-8000-0000000000f2",
        "refund": "00000000-0000-7000-8000-0000000000f3",
    }
    category = "00000000-0000-7000-8000-00000000000a"
    with pigrocrm_postgres.fresh_container() as container:
        url = container.get_connection_url()
        config = _alembic_config(url)
        upgrade(config, "0085")
        engine = create_engine(url)
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO cost_categories (id, name, position, archived, created_at, "
                    "updated_at) VALUES (:category, 'Viaggi', 0, false, now(), now())"
                ),
                {"category": category},
            )
            for key, cost_id in cost_ids.items():
                connection.execute(
                    text(
                        "INSERT INTO costs (id, category_id, date, amount, description, supplier, "
                        "custom_fields, created_at, updated_at, deleted_at) VALUES (:id, "
                        ":category, '2026-03-01', :amount, :description, 'Trenitalia', "
                        "CAST(:custom AS jsonb), '2026-03-05T10:00:00+00', now(), "
                        "CASE WHEN :archived THEN now() END)"
                    ),
                    {
                        "id": cost_id,
                        "category": category,
                        "amount": Decimal("-5.00") if key == "refund" else Decimal("10.50"),
                        "description": f"cost {key}",
                        "custom": '{"project": "alpha"}',
                        "archived": key == "archived",
                    },
                )
                connection.execute(
                    text(
                        "INSERT INTO activities (id, entity_type, entity_id, kind, actor_type, "
                        "payload, occurred_at) VALUES (gen_random_uuid(), 'cost', :id, "
                        "'created', 'user', '{}', now())"
                    ),
                    {"id": cost_id},
                )
            connection.execute(
                text(
                    "INSERT INTO field_definitions (id, entity_type, key, label, field_type, "
                    "options, required, position, archived, created_at, updated_at) VALUES "
                    "(gen_random_uuid(), 'cost', 'project', 'Progetto', 'text', '[]', false, 0, "
                    "false, now(), now())"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO space_settings (key, value, updated_at) "
                    "VALUES ('module_costs', 'false', now())"
                )
            )
        engine.dispose()

        upgrade(config, "0086")

        engine = create_engine(url)
        with engine.connect() as connection:
            rows = {
                row.id: row
                for row in connection.execute(
                    text(
                        "SELECT id::text AS id, kind, status, merchant_name, description, amount, "
                        "cost_amount, currency, custom_fields, created_at, deleted_at, supplier_id "
                        "FROM purchases"
                    )
                )
            }
            assert set(rows) == set(cost_ids.values())
            for key, cost_id in cost_ids.items():
                row = rows[cost_id]
                assert (row.kind, row.status, row.currency) == ("expense", "registered", "EUR")
                assert row.merchant_name == "Trenitalia"
                assert row.description == f"cost {key}"
                assert row.cost_amount == row.amount
                assert row.custom_fields == {"project": "alpha"}
                assert row.created_at.isoformat().startswith("2026-03-05T10:00:00")
                assert (row.deleted_at is not None) == (key == "archived")
                assert row.supplier_id is None
            assert rows[cost_ids["refund"]].amount == Decimal("-5.00")
            assert not connection.execute(text("SELECT to_regclass('public.costs')")).scalar_one()
            history = connection.execute(
                text(
                    "SELECT count(*) FROM activities WHERE entity_type = 'purchase' "
                    "AND entity_id::text = ANY(:ids)"
                ),
                {"ids": list(cost_ids.values())},
            ).scalar_one()
            assert history == 3
            assert (
                connection.execute(
                    text("SELECT count(*) FROM activities WHERE entity_type = 'cost'")
                ).scalar_one()
                == 0
            )
            assert (
                connection.execute(
                    text("SELECT entity_type FROM field_definitions WHERE key = 'project'")
                ).scalar_one()
                == "purchase"
            )
            assert (
                connection.execute(
                    text("SELECT key FROM space_settings WHERE value = 'false'")
                ).scalar_one()
                == "module_purchases"
            )
            body = connection.execute(_ENTITY_VISIBLE_DEF).scalar_one()
            assert "WHEN 'purchase'" in body and "FROM purchases c" in body
            assert "'cost'" not in body and "FROM costs" not in body
            names = {
                row[0]
                for row in connection.execute(
                    text("SELECT conname FROM pg_constraint WHERE conrelid = 'purchases'::regclass")
                )
            }
            assert {
                "purchases_pkey",
                "ck_purchases_amount_not_zero",
                "purchases_category_id_fkey",
                "purchases_deal_id_fkey",
            } <= names
            assert not [name for name in names if "costs" in name]
        assert _tables_shape(url, {"purchases", "purchase_vat_lines"}) == (
            {"purchases", "purchase_vat_lines"},
            {"purchases", "purchase_vat_lines"},
            {"purchases", "purchase_vat_lines"},
        )

        supplier = "00000000-0000-7000-8000-0000000000e1"
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO organizations (id, legal_name, country, custom_fields, roles) "
                    "VALUES (:id, 'Acme Srl', 'IT', '{}', ARRAY['supplier']::varchar[])"
                ),
                {"id": supplier},
            )
        insert = text(
            "INSERT INTO purchases (id, kind, category_id, supplier_id, date, amount, "
            "cost_amount, description, custom_fields) VALUES (gen_random_uuid(), :kind, "
            ":category, :supplier, '2026-03-02', :amount, :amount, 'x', '{}')"
        )
        ok = {"kind": "invoice", "category": category, "supplier": supplier, "amount": 122}
        with engine.begin() as connection:
            connection.execute(insert, ok)
        for bad in (
            {**ok, "supplier": None},  # an invoice names its supplier
            {**ok, "amount": -122},  # an invoice is positive
            {**ok, "kind": "credit_note", "amount": 122},  # a credit note is negative
            {**ok, "kind": "bill"},  # not a kind
        ):
            with pytest.raises(IntegrityError), engine.begin() as connection:
                connection.execute(insert, bad)
        engine.dispose()

        downgrade(config, "0085")

        engine = create_engine(url)
        with engine.connect() as connection:
            costs = {
                row.id: row
                for row in connection.execute(
                    text("SELECT id::text AS id, supplier, amount, deleted_at FROM costs")
                )
            }
            assert set(cost_ids.values()) <= set(costs)
            # The supplier's invoice, with no free-text name, stands under the organization's.
            assert {row.supplier for row in costs.values()} == {"Trenitalia", "Acme Srl"}
            assert (
                connection.execute(
                    text("SELECT entity_type FROM field_definitions WHERE key = 'project'")
                ).scalar_one()
                == "cost"
            )
            assert (
                connection.execute(
                    text("SELECT key FROM space_settings WHERE value = 'false'")
                ).scalar_one()
                == "module_costs"
            )
            assert not connection.execute(
                text("SELECT to_regclass('public.purchase_vat_lines')")
            ).scalar_one()
            assert "WHEN 'cost'" in connection.execute(_ENTITY_VISIBLE_DEF).scalar_one()
        engine.dispose()
        upgrade(config, "0086")
        assert _tables_shape(url, {"purchases"})[0] == {"purchases"}


def test_0087_wraps_every_google_account_in_a_mailbox_and_renames_the_sync_job(
    pigrocrm_postgres: Any,
) -> None:
    """REB-1014: each account gets one mailbox with the purpose `correspondence` and nothing
    copied from it, the job clock of `gmail-sync` is kept under `mail-sync`, and `downgrade`
    puts the name back and drops both tables."""
    with pigrocrm_postgres.fresh_container() as container:
        url = container.get_connection_url()
        config = _alembic_config(url)
        upgrade(config, "0086")
        engine = create_engine(url)
        try:
            with engine.begin() as connection:
                owner = connection.execute(
                    text(
                        "INSERT INTO users (id, email, password_hash, name, role, active) VALUES "
                        "(gen_random_uuid(), 'o@example.test', 'x', 'O', 'admin', true) "
                        "RETURNING id"
                    )
                ).scalar_one()
                account = connection.execute(
                    text(
                        "INSERT INTO google_accounts (id, user_id, google_sub, email_address, "
                        "refresh_token_ciphertext, refresh_token_nonce, scopes_granted, status, "
                        "gmail_store_bodies) "
                        "VALUES (gen_random_uuid(), :u, 's', 'a@example.test', 'x', 'x', "
                        "'[]'::jsonb, 'revoked', true) RETURNING id"
                    ),
                    {"u": owner},
                ).scalar_one()
                connection.execute(
                    text(
                        "INSERT INTO reminder_job_run (id, job, ran_at) "
                        "VALUES (gen_random_uuid(), 'gmail-sync', now())"
                    )
                )

            upgrade(config, "0087")

            with engine.connect() as connection:
                index = connection.execute(
                    text(
                        "SELECT indexdef FROM pg_indexes "
                        "WHERE indexname = 'uq_mailboxes_one_gmail_per_user'"
                    )
                ).scalar_one()
                rows = connection.execute(
                    text(
                        "SELECT m.owner_user_id, m.provider, m.google_account_id, m.status, "
                        "m.space_owned, p.purpose, p.space_owned, p.sync_watermark "
                        "FROM mailboxes m JOIN mailbox_purposes p ON p.mailbox_id = m.id"
                    )
                ).all()
                jobs = connection.execute(text("SELECT job FROM reminder_job_run")).scalars().all()
            assert "UNIQUE" in index and "provider" in index and "gmail" in index
            assert [tuple(row) for row in rows] == [
                (owner, "gmail", account, None, False, "correspondence", False, None)
            ]
            assert jobs == ["mail-sync"]

            downgrade(config, "0086")
            with engine.connect() as connection:
                assert connection.execute(
                    text("SELECT to_regclass('public.mailboxes') IS NULL")
                ).scalar_one()
                assert connection.execute(
                    text("SELECT to_regclass('public.mailbox_purposes') IS NULL")
                ).scalar_one()
                jobs = connection.execute(text("SELECT job FROM reminder_job_run")).scalars().all()
            assert jobs == ["gmail-sync"]

            upgrade(config, "0087")
            with engine.connect() as connection:
                assert connection.execute(text("SELECT count(*) FROM mailboxes")).scalar_one() == 1
        finally:
            engine.dispose()


def test_0088_creates_the_declared_tax_figures_table_under_the_scope_policy(
    pigrocrm_postgres: Any,
) -> None:
    """REB-1006: a new table with the row policy of every table that reaches a legal entity,
    forced; nothing is backfilled, so both ways are a pure create and drop."""
    with pigrocrm_postgres.fresh_container() as container:
        url = container.get_connection_url()
        config = _alembic_config(url)
        upgrade(config, "0087")
        assert _table_shape(url, "declared_tax_figures")[0] is False
        upgrade(config, "0088")
        assert _table_shape(url, "declared_tax_figures") == (True, True, {"legal_entity_scope"})
        downgrade(config, "0087")
        assert _table_shape(url, "declared_tax_figures")[0] is False
        upgrade(config, "0088")
        assert _table_shape(url, "declared_tax_figures")[0] is True


def _sales_charges_columns(url: str) -> set[str]:
    """The columns 0089 adds to `invoices`, as present."""
    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            rows = connection.execute(
                text(
                    "SELECT column_name FROM information_schema.columns WHERE "
                    "table_name = 'invoices' AND column_name IN ('net_receivable', "
                    "'withholding_amount', 'withholding_rate', 'split_payment')"
                )
            ).all()
    finally:
        engine.dispose()
    return {column for (column,) in rows}


def test_0089_backfills_the_net_receivable_with_the_total_and_downgrade_removes_the_columns(
    pigrocrm_postgres: Any,
) -> None:
    """REB-994: an invoice that exists is owed what it printed, with no withholding and no split
    payment; the check refuses a negative withholding."""
    entity = "00000000-0000-7000-8000-0000000000e1"
    customer = "00000000-0000-7000-8000-0000000000c1"
    invoice = "00000000-0000-7000-8000-0000000000f1"
    columns = {"net_receivable", "withholding_amount", "withholding_rate", "split_payment"}
    with pigrocrm_postgres.fresh_container() as container:
        url = container.get_connection_url()
        config = _alembic_config(url)
        upgrade(config, "0088")
        assert _sales_charges_columns(url) == set()
        engine: Engine = create_engine(url)
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO legal_entities (id, name, legal_name, country) "
                    f"VALUES ('{entity}', 'E', 'E srl', 'IT')"
                )
            )
            connection.execute(text("SELECT set_config('pigrocrm.legal_entities', '*', false)"))
            connection.execute(
                text(
                    "INSERT INTO organizations (id, legal_name, country, custom_fields, "
                    f"legal_entity_id) VALUES ('{customer}', 'C srl', 'IT', '{{}}', '{entity}')"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO invoices (id, customer_id, legal_entity_id, type, status, "
                    "sdi_document_type, currency, taxable_amount, vat_amount, stamp_duty, total, "
                    "payment_status, custom_fields) VALUES "
                    f"('{invoice}', '{customer}', '{entity}', 'invoice', 'draft', 'TD01', 'EUR', "
                    "100, 22, 0, 122, 'unpaid', '{}')"
                )
            )
        engine.dispose()

        upgrade(config, "0089")
        assert _sales_charges_columns(url) == columns
        engine = create_engine(url)
        with engine.begin() as connection:
            connection.execute(text("SELECT set_config('pigrocrm.legal_entities', '*', false)"))
            row = connection.execute(
                text(
                    "SELECT net_receivable, withholding_amount, withholding_rate, split_payment "
                    f"FROM invoices WHERE id = '{invoice}'"
                )
            ).one()
        assert row == (Decimal("122.00"), Decimal("0.00"), None, False)
        with pytest.raises(IntegrityError), engine.begin() as connection:
            connection.execute(text("SELECT set_config('pigrocrm.legal_entities', '*', false)"))
            connection.execute(
                text(f"UPDATE invoices SET withholding_amount = -1 WHERE id = '{invoice}'")
            )
        engine.dispose()

        downgrade(config, "0088")
        assert _sales_charges_columns(url) == set()
        upgrade(config, "0089")
        assert _sales_charges_columns(url) == columns


def _forecast_key_state(url: str) -> tuple[bool, bool, str]:
    """Whether `due_items` has `forecast_key` and its partial unique index, and the text of the
    check on the rule's columns, with the whitespace and parentheses taken out."""
    engine = create_engine(url)
    try:
        with engine.begin() as connection:
            column = connection.execute(
                text(
                    "SELECT count(*) > 0 FROM information_schema.columns WHERE "
                    "table_name = 'due_items' AND column_name = 'forecast_key'"
                )
            ).scalar_one()
            index = connection.execute(
                text(
                    "SELECT count(*) > 0 FROM pg_indexes WHERE "
                    "indexname = 'uq_due_items_forecast_key'"
                )
            ).scalar_one()
            definition = connection.execute(
                text(
                    "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                    "WHERE conname = 'ck_due_items_rule_columns'"
                )
            ).scalar_one()
    finally:
        engine.dispose()
    flat = "".join(definition.split()).replace("(", "").replace(")", "")
    return bool(column), bool(index), flat


def test_0090_adds_the_forecast_key_and_lets_a_waiting_item_carry_its_offset(
    pigrocrm_postgres: Any,
) -> None:
    """REB-1003: one nullable column with its partial unique index, and a relaxed check: only
    the waiting `after_settlement` clause stops requiring a zero offset."""
    waiting_strict = "rule_not_afterISNOTNULLANDrule_offset_days=0"
    with pigrocrm_postgres.fresh_container() as container:
        url = container.get_connection_url()
        config = _alembic_config(url)
        upgrade(config, "0089")
        column, index, check = _forecast_key_state(url)
        assert (column, index) == (False, False)
        assert waiting_strict in check
        upgrade(config, "0090")
        column, index, check = _forecast_key_state(url)
        assert (column, index) == (True, True)
        assert waiting_strict not in check
        downgrade(config, "0089")
        column, index, check = _forecast_key_state(url)
        assert (column, index) == (False, False)
        assert waiting_strict in check
        upgrade(config, "0090")
        assert _forecast_key_state(url)[:2] == (True, True)


def _payroll_columns(url: str) -> set[str]:
    """Which of the columns 0091 adds exist."""
    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            return {
                f"{row[0]}.{row[1]}"
                for row in connection.execute(
                    text(
                        "SELECT table_name, column_name FROM information_schema.columns WHERE "
                        "(table_name = 'staff_members' AND column_name IN "
                        "('employee_inps_rate', 'thirteenth_month', 'fourteenth_month')) OR "
                        "(table_name = 'due_items' AND column_name = 'staff_member_id')"
                    )
                )
            }
    finally:
        engine.dispose()


_PAYROLL_COLUMNS = {
    "staff_members.employee_inps_rate",
    "staff_members.thirteenth_month",
    "staff_members.fourteenth_month",
    "due_items.staff_member_id",
}


def test_0091_adds_the_payroll_inputs_the_payslips_and_the_staff_counterparty(
    pigrocrm_postgres: Any,
) -> None:
    """REB-1008: three nullable staff columns, the payslip table under the forced row policy
    and a staff counterparty on due items; the downgrade removes all of it, and a due item owed
    to a person goes with it rather than surviving in a shape that cannot hold it."""
    with pigrocrm_postgres.fresh_container() as container:
        url = container.get_connection_url()
        config = _alembic_config(url)
        upgrade(config, "0090")
        assert _payroll_columns(url) == set()
        assert _tables_shape(url, {"staff_payslips"}) == (set(), set(), set())
        upgrade(config, "0091")
        assert _payroll_columns(url) == _PAYROLL_COLUMNS
        assert _tables_shape(url, {"staff_payslips"}) == (
            {"staff_payslips"},
            {"staff_payslips"},
            {"staff_payslips"},
        )
        downgrade(config, "0090")
        assert _payroll_columns(url) == set()
        assert _tables_shape(url, {"staff_payslips"}) == (set(), set(), set())
        upgrade(config, "0091")
        assert _payroll_columns(url) == _PAYROLL_COLUMNS


def _letters_shape(url: str) -> tuple[bool, bool, bool, bool]:
    """Whether `letter_counters`, `templates.letter_type`, `documents.engagement_id` and
    `legal_entities.legal_representative` exist."""
    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            table = connection.execute(
                text("SELECT to_regclass('public.letter_counters') IS NOT NULL")
            ).scalar_one()
            columns = {
                (row[0], row[1])
                for row in connection.execute(
                    text("SELECT table_name, column_name FROM information_schema.columns")
                )
            }
    finally:
        engine.dispose()
    return (
        bool(table),
        ("templates", "letter_type") in columns,
        ("documents", "engagement_id") in columns,
        ("legal_entities", "legal_representative") in columns,
    )


def test_0092_adds_the_letter_columns_and_downgrade_removes_them(pigrocrm_postgres: Any) -> None:
    """REB-1018: nothing backfilled; the template check keeps the regime on the two letter types."""
    with pigrocrm_postgres.fresh_container() as container:
        url = container.get_connection_url()
        config = _alembic_config(url)
        upgrade(config, "0091")
        assert _letters_shape(url) == (False, False, False, False)
        upgrade(config, "0092")
        assert _letters_shape(url) == (True, True, True, True)
        engine = create_engine(url)
        try:
            insert = text(
                "INSERT INTO templates (id, name, type, body_markdown, declared_variables, "
                "active, letter_type) VALUES (gen_random_uuid(), :n, :t, 'x', '[]', true, :l)"
            )
            for bad in (
                {"n": "a", "t": "engagement_letter", "l": None},
                {"n": "b", "t": "offer", "l": "ordinary"},
                {"n": "c", "t": "engagement_letter", "l": "weekly"},
            ):
                with pytest.raises(IntegrityError), engine.begin() as connection:
                    connection.execute(insert, bad)
            with engine.begin() as connection:
                connection.execute(insert, {"n": "d", "t": "engagement_letter", "l": "ordinary"})
            with pytest.raises(IntegrityError), engine.begin() as connection:
                connection.execute(insert, {"n": "e", "t": "engagement_letter", "l": "ordinary"})
        finally:
            engine.dispose()
        downgrade(config, "0091")
        assert _letters_shape(url) == (False, False, False, False)
        upgrade(config, "0092")
        assert _letters_shape(url)[0] is True


def _forecast_schema(url: str) -> dict[str, bool]:
    engine = create_engine(url)
    try:
        with engine.connect() as connection:

            def exists(sql: str) -> bool:
                return bool(connection.execute(text(sql)).scalar_one())

            return {
                "line_columns": exists(
                    "SELECT count(*) = 2 FROM information_schema.columns WHERE "
                    "table_name = 'invoice_lines' AND column_name IN "
                    "('rate_card_id', 'period_start')"
                ),
                "line_check": exists(
                    "SELECT count(*) > 0 FROM pg_constraint WHERE "
                    "conname = 'ck_invoice_lines_rate_card_with_period'"
                ),
            }
    finally:
        engine.dispose()


def test_0093_adds_the_billed_period_to_invoice_lines_and_downgrade_removes_it(
    pigrocrm_postgres: Any,
) -> None:
    """REB-997: nullable columns and a both-or-neither check."""
    with pigrocrm_postgres.fresh_container() as container:
        url = container.get_connection_url()
        config = _alembic_config(url)
        upgrade(config, "0092")
        assert not any(_forecast_schema(url).values())
        upgrade(config, "0093")
        assert all(_forecast_schema(url).values())
        downgrade(config, "0092")
        assert not any(_forecast_schema(url).values())
        upgrade(config, "0093")
        assert all(_forecast_schema(url).values())


def test_0094_creates_the_accountant_exports_table_under_the_scope_policy(
    pigrocrm_postgres: Any,
) -> None:
    """REB-1012: a new table with the row policy of every table that reaches a legal entity,
    forced, and a branch of `entity_visible` for the activities its exports write; nothing is
    backfilled, so both ways are a pure create and drop."""
    with pigrocrm_postgres.fresh_container() as container:
        url = container.get_connection_url()
        config = _alembic_config(url)
        upgrade(config, "0093")
        assert _table_shape(url, "accountant_exports")[0] is False
        assert _entity_visible_has(url, "accountant_export") is False
        upgrade(config, "0094")
        assert _table_shape(url, "accountant_exports") == (True, True, {"legal_entity_scope"})
        assert _entity_visible_has(url, "accountant_export") is True
        downgrade(config, "0093")
        assert _table_shape(url, "accountant_exports")[0] is False
        assert _entity_visible_has(url, "accountant_export") is False
        upgrade(config, "0094")
        assert _table_shape(url, "accountant_exports")[0] is True


def _entity_visible_has(url: str, entity_type: str) -> bool:
    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            return bool(
                connection.execute(
                    text(
                        "SELECT position(:kind in pg_get_functiondef("
                        "'public.entity_visible(text, uuid, boolean)'::regprocedure)) > 0"
                    ),
                    {"kind": f"'{entity_type}'"},
                ).scalar_one()
            )
    finally:
        engine.dispose()


def test_0095_adds_the_engagement_monthly_reports_table_and_downgrade_removes_it(
    pigrocrm_postgres: Any,
) -> None:
    """REB-1002: one table with the forced row-level policy; its checks keep a month on its first
    day, a decided row dated, and a held day with its reason, and one row a month an engagement."""
    tables = {"engagement_monthly_reports"}
    with pigrocrm_postgres.fresh_container() as container:
        url = container.get_connection_url()
        config = _alembic_config(url)
        upgrade(config, "0094")
        assert _tables_shape(url, tables) == (set(), set(), set())
        upgrade(config, "0095")
        assert _tables_shape(url, tables) == (tables, tables, tables)

        engine = create_engine(url)
        try:
            with engine.begin() as connection:
                connection.execute(text("SELECT set_config('pigrocrm.legal_entities', '*', false)"))
                freelancer = connection.execute(
                    text(
                        "INSERT INTO organizations (id, legal_name, country, custom_fields, roles) "
                        "VALUES (gen_random_uuid(), 'F', 'IT', '{}', "
                        "ARRAY['freelancer']::varchar[]) RETURNING id"
                    )
                ).scalar_one()
                engagement = connection.execute(
                    text(
                        "INSERT INTO engagements (id, freelancer_id, role, starts_on, fee, "
                        "fee_unit, payment_rule, payment_days) VALUES (gen_random_uuid(), :f, "
                        "'r', '2026-10-01', 100, 'day', 'fixed_term', 30) RETURNING id"
                    ),
                    {"f": freelancer},
                ).scalar_one()
            insert = text(
                "INSERT INTO engagement_monthly_reports (id, engagement_id, freelancer_id, month, "
                "state, received_on, tacit_on, decided_on, objection, held_dates) VALUES "
                "(gen_random_uuid(), :e, :f, :month, :state, '2026-11-03', '2026-11-12', "
                "CAST(:decided AS date), :objection, CAST(:held AS jsonb))"
            )
            ok = {
                "e": engagement,
                "f": freelancer,
                "month": "2026-10-01",
                "state": "approved",
                "decided": "2026-11-13",
                "objection": "no",
                "held": '["2026-10-05"]',
            }
            with engine.begin() as connection:
                connection.execute(text("SELECT set_config('pigrocrm.legal_entities', '*', false)"))
                connection.execute(insert, ok)
            for bad in (
                {**ok, "month": "2026-11-02"},
                {**ok, "state": "requested"},
                {**ok, "decided": None},
                {**ok, "month": "2026-12-01", "objection": None},
                {**ok, "month": "2026-12-01", "held": '{"a": 1}'},
            ):
                with pytest.raises(IntegrityError), engine.begin() as connection:
                    connection.execute(
                        text("SELECT set_config('pigrocrm.legal_entities', '*', false)")
                    )
                    connection.execute(insert, bad)
            with pytest.raises(IntegrityError), engine.begin() as connection:
                connection.execute(text("SELECT set_config('pigrocrm.legal_entities', '*', false)"))
                connection.execute(insert, ok)
        finally:
            engine.dispose()

        downgrade(config, "0094")
        assert _tables_shape(url, tables) == (set(), set(), set())
        upgrade(config, "0095")
        assert _tables_shape(url, tables)[0] == tables


def _iva_frequency(url: str) -> list[str] | None:
    """The `iva_frequency` of every fiscal profile, `None` while the column does not exist."""
    engine: Engine = create_engine(url)
    try:
        with engine.begin() as connection:
            connection.execute(text("SELECT set_config('pigrocrm.legal_entities', '*', false)"))
            present = connection.execute(
                text(
                    "SELECT count(*) FROM information_schema.columns WHERE "
                    "table_name = 'fiscal_profile' AND column_name = 'iva_frequency'"
                )
            ).scalar_one()
            if not present:
                return None
            return list(
                connection.execute(text("SELECT iva_frequency FROM fiscal_profile")).scalars()
            )
    finally:
        engine.dispose()


def test_0096_creates_the_iva_settlements_table_and_every_profile_settles_monthly(
    pigrocrm_postgres: Any,
) -> None:
    """REB-1021: a new table under the row policy of every table that reaches a legal entity,
    and a frequency column every existing profile reads as `monthly`; both ways reverse."""
    entity = "00000000-0000-7000-8000-0000000000e2"
    with pigrocrm_postgres.fresh_container() as container:
        url = container.get_connection_url()
        config = _alembic_config(url)
        upgrade(config, "0095")
        assert _table_shape(url, "iva_settlements")[0] is False
        engine: Engine = create_engine(url)
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO legal_entities (id, name, legal_name, country) "
                    f"VALUES ('{entity}', 'E', 'E srl', 'IT')"
                )
            )
            connection.execute(text("SELECT set_config('pigrocrm.legal_entities', '*', false)"))
            connection.execute(
                text(
                    "INSERT INTO fiscal_profile (id, legal_entity_id, regime_code, "
                    "default_vat_rate, apply_stamp_duty, stamp_duty_threshold, stamp_duty_amount, "
                    "payment_terms, payment_method, due_days, pack_id, pack_version) VALUES "
                    f"('00000000-0000-7000-8000-0000000000f2', '{entity}', 'RF01', 22, false, "
                    "77.47, 2.00, 'TP02', 'MP05', 30, 'it-standard', '1')"
                )
            )
        engine.dispose()

        upgrade(config, "0096")
        assert _table_shape(url, "iva_settlements") == (True, True, {"legal_entity_scope"})
        assert _iva_frequency(url) == ["monthly"]
        downgrade(config, "0095")
        assert _table_shape(url, "iva_settlements")[0] is False
        assert _iva_frequency(url) is None
        upgrade(config, "0096")
        assert _table_shape(url, "iva_settlements")[0] is True


def test_0097_adds_the_imap_connection_to_mailboxes_and_downgrade_removes_it(
    pigrocrm_postgres: Any,
) -> None:
    """REB-1015: an IMAP mailbox holds its connection and a sealed password, a Gmail one none of
    it, and one address is one mailbox per owner; downgrade drops the IMAP rows with the columns."""
    with pigrocrm_postgres.fresh_container() as container:
        url = container.get_connection_url()
        config = _alembic_config(url)
        upgrade(config, "0093")
        upgrade(config, "0097")
        engine = create_engine(url)
        insert = text(
            "INSERT INTO mailboxes (id, provider, status, address, imap_host, imap_port, "
            "imap_security, imap_username, password_ciphertext, password_nonce) VALUES "
            "(gen_random_uuid(), 'imap', 'active', :a, 'imap.example.test', :p, :s, 'u', "
            "'\\x01', '\\x02')"
        )
        try:
            with engine.begin() as connection:
                connection.execute(insert, {"a": "A@x.test", "p": 993, "s": "tls"})
            for params in (
                {"a": "a@x.test", "p": 993, "s": "tls"},  # the same address, the same owner
                {"a": "b@x.test", "p": 143, "s": "none"},  # no plaintext choice
                {"a": "b@x.test", "p": 0, "s": "tls"},
            ):
                with pytest.raises(IntegrityError), engine.begin() as connection:
                    connection.execute(insert, params)
            with pytest.raises(IntegrityError), engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO mailboxes (id, provider, status, address, imap_host, "
                        "imap_port, imap_security, imap_username) VALUES (gen_random_uuid(), "
                        "'imap', 'active', 'c@x.test', 'h', 993, 'tls', 'u')"
                    )
                )

            downgrade(config, "0093")
            with engine.connect() as connection:
                columns = set(
                    connection.execute(
                        text(
                            "SELECT column_name FROM information_schema.columns "
                            "WHERE table_name = 'mailboxes'"
                        )
                    ).scalars()
                )
                assert connection.execute(text("SELECT count(*) FROM mailboxes")).scalar() == 0
            assert columns.isdisjoint({"imap_host", "password_ciphertext", "last_error"})
        finally:
            engine.dispose()


def test_0098_creates_the_recurring_expenses_table_under_the_row_policy(
    pigrocrm_postgres: Any,
) -> None:
    """REB-1022: a new table under the row policy of every table that reaches a legal entity;
    it reverses and comes back."""
    with pigrocrm_postgres.fresh_container() as container:
        url = container.get_connection_url()
        config = _alembic_config(url)
        upgrade(config, "0097")
        assert _table_shape(url, "recurring_expenses")[0] is False
        upgrade(config, "0098")
        assert _table_shape(url, "recurring_expenses") == (True, True, {"legal_entity_scope"})
        downgrade(config, "0097")
        assert _table_shape(url, "recurring_expenses")[0] is False
        upgrade(config, "0098")
        assert _table_shape(url, "recurring_expenses")[0] is True


def test_0099_adds_the_message_columns_to_inbox_items_and_downgrade_keeps_the_items(
    pigrocrm_postgres: Any,
) -> None:
    """REB-1016: a file that came by mail says which message, one that did not says nothing of
    one, and downgrade leaves the items as uploads."""
    with pigrocrm_postgres.fresh_container() as container:
        url = container.get_connection_url()
        config = _alembic_config(url)
        upgrade(config, "0098")
        upgrade(config, "0099")
        engine = create_engine(url)
        base = (
            "INSERT INTO inbox_items (id, source, original_name, size_bytes, sha256, "
            "content_sha256, state{cols}) VALUES (gen_random_uuid(), '{source}', 'a.xml', 1, "
            "'a', 'a', 'new'{vals})"
        )
        try:
            with engine.begin() as connection:
                connection.execute(
                    text(
                        base.format(
                            cols=", mail_uid, mail_uidvalidity", source="mailbox", vals=", 5, 7"
                        )
                    )
                )
            for source, cols, vals in (
                ("mailbox", "", ""),  # a message-less mailbox item
                ("upload", ", mail_uid, mail_uidvalidity", ", 5, 7"),  # an upload with a message
                ("other", "", ""),
            ):
                with pytest.raises(IntegrityError), engine.begin() as connection:
                    connection.execute(text(base.format(cols=cols, source=source, vals=vals)))
            downgrade(config, "0098")
            with engine.connect() as connection:
                sources = connection.execute(text("SELECT source FROM inbox_items")).scalars().all()
            assert sources == ["upload"]
            upgrade(config, "0099")
        finally:
            engine.dispose()


def test_0100_adds_the_signature_columns_and_downgrade_removes_them(
    pigrocrm_postgres: Any,
) -> None:
    """REB-1019: the signature columns of `documents`, and back."""
    with pigrocrm_postgres.fresh_container() as container:
        url = container.get_connection_url()
        config = _alembic_config(url)
        upgrade(config, "0099")
        engine = create_engine(url)
        sql = (
            "SELECT count(*) FROM information_schema.columns WHERE table_name = 'documents' "
            "AND column_name IN ('signature_state', 'envelope_id', 'signed_version', 'notice_at')"
        )
        try:
            with engine.connect() as connection:
                assert connection.execute(text(sql)).scalar_one() == 0
            upgrade(config, "0100")
            with engine.connect() as connection:
                assert connection.execute(text(sql)).scalar_one() == 4
            downgrade(config, "0099")
            with engine.connect() as connection:
                assert connection.execute(text(sql)).scalar_one() == 0
            upgrade(config, "0100")
        finally:
            engine.dispose()


def test_0101_creates_the_iva_period_locks_table_under_the_row_policy(
    pigrocrm_postgres: Any,
) -> None:
    """REB-1036: a new table under the row policy of every table that reaches a legal entity;
    it reverses and comes back."""
    with pigrocrm_postgres.fresh_container() as container:
        url = container.get_connection_url()
        config = _alembic_config(url)
        upgrade(config, "0100")
        assert _table_shape(url, "iva_period_locks")[0] is False
        upgrade(config, "0101")
        assert _table_shape(url, "iva_period_locks") == (True, True, {"legal_entity_scope"})
        downgrade(config, "0100")
        assert _table_shape(url, "iva_period_locks")[0] is False
        upgrade(config, "0101")
        assert _table_shape(url, "iva_period_locks")[0] is True


def _evidence_column(url: str) -> bool:
    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            return bool(
                connection.execute(
                    text(
                        "SELECT count(*) > 0 FROM information_schema.columns WHERE "
                        "table_name = 'due_item_settlements' AND column_name = 'bank_movement_id'"
                    )
                ).scalar_one()
            )
    finally:
        engine.dispose()


def test_0102_creates_the_movement_match_tables_and_the_settlement_evidence_column(
    pigrocrm_postgres: Any,
) -> None:
    """REB-1042: two new tables under the row policy and one nullable column on the payments;
    nothing is backfilled, and it reverses and comes back."""
    tables = ("movement_matches", "movement_match_items")
    with pigrocrm_postgres.fresh_container() as container:
        url = container.get_connection_url()
        config = _alembic_config(url)
        upgrade(config, "0101")
        assert all(_table_shape(url, table)[0] is False for table in tables)
        assert _evidence_column(url) is False
        upgrade(config, "0102")
        for table in tables:
            assert _table_shape(url, table) == (True, True, {"legal_entity_scope"})
        assert _evidence_column(url) is True
        downgrade(config, "0101")
        assert all(_table_shape(url, table)[0] is False for table in tables)
        assert _evidence_column(url) is False
        upgrade(config, "0102")
        assert _table_shape(url, "movement_matches")[0] is True


def _column_exists(url: str, table: str, column: str) -> bool:
    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            return bool(
                connection.execute(
                    text(
                        "SELECT count(*) > 0 FROM information_schema.columns WHERE "
                        "table_name = :table AND column_name = :column"
                    ),
                    {"table": table, "column": column},
                ).scalar_one()
            )
    finally:
        engine.dispose()


def _proposal_checks(url: str) -> dict[str, str]:
    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            return {
                row[0]: row[1]
                for row in connection.execute(
                    text(
                        "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint "
                        "WHERE conrelid = 'proposals'::regclass AND contype = 'c' "
                        "AND conname IN ('ck_proposals_target_type', 'ck_proposals_source')"
                    )
                )
            }
    finally:
        engine.dispose()


def test_0103_adds_payslip_proposals_closed_payroll_months_and_the_payslip_reading_switch(
    pigrocrm_postgres: Any,
) -> None:
    """REB-1045: the proposals' two checks learn `payslip`, a payslip links its inbox item, a
    closed payroll month is a table under the row policy, the model gets a payslip switch; and it
    reverses and comes back."""
    with pigrocrm_postgres.fresh_container() as container:
        url = container.get_connection_url()
        config = _alembic_config(url)
        upgrade(config, "0102")
        assert _table_shape(url, "payroll_month_closures")[0] is False
        assert _column_exists(url, "staff_payslips", "inbox_item_id") is False
        assert _column_exists(url, "ai_model_config", "read_payslips") is False
        assert all("payslip" not in sql for sql in _proposal_checks(url).values())
        upgrade(config, "0103")
        assert _table_shape(url, "payroll_month_closures") == (True, True, {"legal_entity_scope"})
        assert _column_exists(url, "staff_payslips", "inbox_item_id") is True
        assert _column_exists(url, "ai_model_config", "read_payslips") is True
        checks = _proposal_checks(url)
        assert len(checks) == 2 and all("payslip" in sql for sql in checks.values())
        downgrade(config, "0102")
        assert _table_shape(url, "payroll_month_closures")[0] is False
        assert _column_exists(url, "staff_payslips", "inbox_item_id") is False
        assert _column_exists(url, "ai_model_config", "read_payslips") is False
        assert all("payslip" not in sql for sql in _proposal_checks(url).values())
        upgrade(config, "0103")
        assert _table_shape(url, "payroll_month_closures")[0] is True


def test_0104_creates_the_export_share_link_tables_under_the_scope_policy(
    pigrocrm_postgres: Any,
) -> None:
    """REB-1047: three new tables under the row policy of every table that reaches a legal entity,
    nothing backfilled; it reverses and comes back."""
    tables = (
        "accountant_export_links",
        "accountant_export_link_openings",
        "accountant_export_drive_writes",
    )
    with pigrocrm_postgres.fresh_container() as container:
        url = container.get_connection_url()
        config = _alembic_config(url)
        upgrade(config, "0103")
        assert all(_table_shape(url, table)[0] is False for table in tables)
        upgrade(config, "0104")
        for table in tables:
            assert _table_shape(url, table) == (True, True, {"legal_entity_scope"})
        downgrade(config, "0103")
        assert all(_table_shape(url, table)[0] is False for table in tables)
        upgrade(config, "0104")
        assert _table_shape(url, "accountant_export_links")[0] is True


def _purchase_kinds_and_staff_column(url: str) -> tuple[bool, str]:
    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            column = bool(
                connection.execute(
                    text(
                        "SELECT count(*) > 0 FROM information_schema.columns WHERE "
                        "table_name = 'purchases' AND column_name = 'staff_member_id'"
                    )
                ).scalar_one()
            )
            kind_check = connection.execute(
                text(
                    "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                    "WHERE conname = 'ck_purchases_kind'"
                )
            ).scalar_one()
            return column, str(kind_check)
    finally:
        engine.dispose()


def test_0105_creates_expense_notes_and_teaches_purchases_the_expense_note_kind(
    pigrocrm_postgres: Any,
) -> None:
    """REB-1046: one new table under the row policy, a new purchase kind and the staff member it
    pays back; the downgrade removes the purchases of that kind and comes back."""
    with pigrocrm_postgres.fresh_container() as container:
        url = container.get_connection_url()
        config = _alembic_config(url)
        upgrade(config, "0104")
        assert _table_shape(url, "expense_notes")[0] is False
        column, kind_check = _purchase_kinds_and_staff_column(url)
        assert column is False and "expense_note" not in kind_check
        upgrade(config, "0105")
        assert _table_shape(url, "expense_notes") == (True, True, {"legal_entity_scope"})
        column, kind_check = _purchase_kinds_and_staff_column(url)
        assert column is True and "expense_note" in kind_check
        downgrade(config, "0104")
        assert _table_shape(url, "expense_notes")[0] is False
        column, kind_check = _purchase_kinds_and_staff_column(url)
        assert column is False and "expense_note" not in kind_check
        upgrade(config, "0105")
        assert _table_shape(url, "expense_notes")[0] is True


def test_0106_adds_the_export_kind_the_recipient_and_the_code_table(
    pigrocrm_postgres: Any,
) -> None:
    """REB-1048: two columns on the export (every earlier export is an accountant's, with no pay),
    one on the link (a plain link has no recipient), and the table of one-time codes under the row
    policy; it reverses and comes back."""
    with pigrocrm_postgres.fresh_container() as container:
        url = container.get_connection_url()
        config = _alembic_config(url)
        upgrade(config, "0105")
        assert _table_shape(url, "accountant_export_link_codes")[0] is False
        assert not _column_exists(url, "accountant_exports", "kind")
        assert not _column_exists(url, "accountant_export_links", "recipient_email")
        upgrade(config, "0106")
        assert _table_shape(url, "accountant_export_link_codes") == (
            True,
            True,
            {"legal_entity_scope"},
        )
        for table, column in (
            ("accountant_exports", "kind"),
            ("accountant_exports", "payroll"),
            ("accountant_export_links", "recipient_email"),
        ):
            assert _column_exists(url, table, column)
        downgrade(config, "0105")
        assert _table_shape(url, "accountant_export_link_codes")[0] is False
        assert not _column_exists(url, "accountant_exports", "payroll")
        upgrade(config, "0106")
        assert _column_exists(url, "accountant_exports", "kind")


def test_0107_adds_the_taxpayer_facts_to_the_fiscal_profile(pigrocrm_postgres: Any) -> None:
    """REB-1070: nine nullable columns, so a profile written before keeps reading as «not
    stated»; it reverses and comes back."""
    columns = (
        "inps_trade",
        "inps_covered_elsewhere",
        "inps_before_1996",
        "flat_rate_inps_reduction",
        "ateco_group",
        "flat_rate_start_year",
        "startup_no_prior_activity",
        "startup_not_a_continuation",
        "startup_predecessor_within_limit",
    )
    with pigrocrm_postgres.fresh_container() as container:
        url = container.get_connection_url()
        config = _alembic_config(url)
        upgrade(config, "0106")
        assert not any(_column_exists(url, "fiscal_profile", name) for name in columns)
        upgrade(config, "0107")
        assert all(_column_exists(url, "fiscal_profile", name) for name in columns)
        downgrade(config, "0106")
        assert not any(_column_exists(url, "fiscal_profile", name) for name in columns)
        upgrade(config, "0107")
        assert _column_exists(url, "fiscal_profile", "inps_trade")


def test_0108_creates_the_tax_advance_methods_table_under_the_scope_policy(
    pigrocrm_postgres: Any,
) -> None:
    """REB-1074: a new table with the row policy of every table that reaches a legal entity,
    forced; nothing is backfilled (a year with no row is `historical`), so both ways are a pure
    create and drop."""
    with pigrocrm_postgres.fresh_container() as container:
        url = container.get_connection_url()
        config = _alembic_config(url)
        upgrade(config, "0107")
        assert _table_shape(url, "tax_advance_methods")[0] is False
        upgrade(config, "0108")
        assert _table_shape(url, "tax_advance_methods") == (True, True, {"legal_entity_scope"})
        downgrade(config, "0107")
        assert _table_shape(url, "tax_advance_methods")[0] is False
        upgrade(config, "0108")
        assert _table_shape(url, "tax_advance_methods")[0] is True


def test_0109_creates_the_tax_payment_plan_tables_under_the_scope_policy(
    pigrocrm_postgres: Any,
) -> None:
    """REB-1075: two new tables with the row policy of every table that reaches a legal entity,
    forced; nothing is backfilled (no row is one payment, no notice is known), so both ways are a
    pure create and drop."""
    tables = ("tax_instalment_plans", "tax_notice_plans")
    with pigrocrm_postgres.fresh_container() as container:
        url = container.get_connection_url()
        config = _alembic_config(url)
        upgrade(config, "0108")
        assert all(_table_shape(url, table)[0] is False for table in tables)
        upgrade(config, "0109")
        for table in tables:
            assert _table_shape(url, table) == (True, True, {"legal_entity_scope"})
        downgrade(config, "0108")
        assert all(_table_shape(url, table)[0] is False for table in tables)
        upgrade(config, "0109")
        assert all(_table_shape(url, table)[0] is True for table in tables)


def test_0110_creates_the_credit_and_f24_tables_under_the_scope_policy(
    pigrocrm_postgres: Any,
) -> None:
    """REB-1076: four new tables with the row policy of every table that reaches a legal entity,
    forced, and the credit flag on the match items. Nothing is backfilled, so both ways are a
    pure create and drop."""
    tables = ("tax_credits", "f24_groups", "f24_group_items", "f24_group_receipts")
    with pigrocrm_postgres.fresh_container() as container:
        url = container.get_connection_url()
        config = _alembic_config(url)
        upgrade(config, "0109")
        assert not any(_table_shape(url, table)[0] for table in tables)
        assert not _column_exists(url, "movement_match_items", "credit")
        upgrade(config, "0110")
        for table in tables:
            assert _table_shape(url, table) == (True, True, {"legal_entity_scope"})
        assert _column_exists(url, "movement_match_items", "credit")
        downgrade(config, "0109")
        assert not any(_table_shape(url, table)[0] for table in tables)
        assert not _column_exists(url, "movement_match_items", "credit")
        upgrade(config, "0110")
        assert all(_table_shape(url, table)[0] for table in tables)


def test_0111_adds_the_staff_contract_type_with_no_backfill(pigrocrm_postgres: Any) -> None:
    """REB-1081: a nullable column («not stated»), so both ways are a pure add and drop."""
    with pigrocrm_postgres.fresh_container() as container:
        url = container.get_connection_url()
        config = _alembic_config(url)
        upgrade(config, "0110")
        assert not _column_exists(url, "staff_members", "contract_type")
        upgrade(config, "0111")
        assert _column_exists(url, "staff_members", "contract_type")
        downgrade(config, "0110")
        assert not _column_exists(url, "staff_members", "contract_type")
        upgrade(config, "0111")
        assert _column_exists(url, "staff_members", "contract_type")


def test_0113_adds_what_a_filed_f24_group_records_both_ways(pigrocrm_postgres: Any) -> None:
    """REB-1093: two nullable line columns and a relaxed group constraint; nothing a filing wrote
    exists before it, so both ways keep the rows that are there."""
    columns = ("used_amount", "settlement_id")
    with pigrocrm_postgres.fresh_container() as container:
        url = container.get_connection_url()
        config = _alembic_config(url)
        upgrade(config, "0112")
        assert not any(_column_exists(url, "f24_group_items", column) for column in columns)
        upgrade(config, "0113")
        assert all(_column_exists(url, "f24_group_items", column) for column in columns)
        downgrade(config, "0112")
        assert not any(_column_exists(url, "f24_group_items", column) for column in columns)
        upgrade(config, "0113")
        assert all(_column_exists(url, "f24_group_items", column) for column in columns)


def test_0114_adds_the_income_kind_and_refuses_split_payment_with_reverse_charge(
    pigrocrm_postgres: Any,
) -> None:
    """REB-1099: a purchase that carries both flags stops the upgrade and names itself, once
    corrected the check holds; the income kind is a nullable, checked column."""
    category = "00000000-0000-7000-8000-00000000000a"
    supplier = "00000000-0000-7000-8000-0000000000e1"
    purchase = "00000000-0000-7000-8000-0000000000f1"
    with pigrocrm_postgres.fresh_container() as container:
        url = container.get_connection_url()
        config = _alembic_config(url)
        upgrade(config, "0113")
        assert not _column_exists(url, "fiscal_profile", "income_kind")
        engine = create_engine(url)
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO cost_categories (id, name, position, archived, created_at, "
                    "updated_at) VALUES (:id, 'Altro', 0, false, now(), now())"
                ),
                {"id": category},
            )
            connection.execute(
                text(
                    "INSERT INTO organizations (id, legal_name, country, custom_fields, roles) "
                    "VALUES (:id, 'Acme Srl', 'IT', '{}', ARRAY['supplier']::varchar[])"
                ),
                {"id": supplier},
            )
            connection.execute(
                text(
                    "INSERT INTO purchases (id, kind, category_id, supplier_id, date, amount, "
                    "cost_amount, description, custom_fields, split_payment, reverse_charge) "
                    "VALUES (:id, 'invoice', :category, :supplier, '2026-03-02', 122, 122, 'x', "
                    "'{}', true, true)"
                ),
                {"id": purchase, "category": category, "supplier": supplier},
            )
        with pytest.raises(RuntimeError, match=purchase):
            upgrade(config, "0114")
        with engine.begin() as connection:
            connection.execute(text("UPDATE purchases SET split_payment = false"))
        upgrade(config, "0114")
        assert _column_exists(url, "fiscal_profile", "income_kind")
        with pytest.raises(IntegrityError), engine.begin() as connection:
            connection.execute(text("UPDATE purchases SET split_payment = true"))
        engine.dispose()
        downgrade(config, "0113")
        assert not _column_exists(url, "fiscal_profile", "income_kind")


def _numeric_shape(url: str, table: str, column: str) -> tuple[int | None, int | None]:
    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            row = connection.execute(
                text(
                    "SELECT numeric_precision, numeric_scale FROM information_schema.columns "
                    "WHERE table_name = :table AND column_name = :column"
                ),
                {"table": table, "column": column},
            ).one()
            return row[0], row[1]
    finally:
        engine.dispose()


def test_0115_adds_the_inps_reduction_facts_and_widens_the_inail_rate(
    pigrocrm_postgres: Any,
) -> None:
    """REB-1097: three nullable profile columns (no backfill) and the INAIL rate's third decimal,
    since 9.75 per mille is 0.975 percent; going down returns the two-decimal column."""
    facts = (
        "inps_over_65_reduction",
        "inps_new_enrolment_reduction",
        "inps_first_enrolment_month",
    )
    with pigrocrm_postgres.fresh_container() as container:
        url = container.get_connection_url()
        config = _alembic_config(url)
        upgrade(config, "0114")
        assert not any(_column_exists(url, "fiscal_profile", column) for column in facts)
        assert _numeric_shape(url, "staff_members", "employer_inail_rate") == (5, 2)
        upgrade(config, "0115")
        assert all(_column_exists(url, "fiscal_profile", column) for column in facts)
        assert _numeric_shape(url, "staff_members", "employer_inail_rate") == (6, 3)
        downgrade(config, "0114")
        assert not any(_column_exists(url, "fiscal_profile", column) for column in facts)
        assert _numeric_shape(url, "staff_members", "employer_inail_rate") == (5, 2)
        upgrade(config, "0115")
        assert _numeric_shape(url, "staff_members", "employer_inail_rate") == (6, 3)


def test_0117_adds_the_company_activity_codes_with_no_backfill(pigrocrm_postgres: Any) -> None:
    """REB-1086: two nullable columns («not stated») with a CHECK on the shape of a code, so
    both ways are a pure add and drop."""
    columns = ("ateco_2007_code", "ateco_2025_code")
    with pigrocrm_postgres.fresh_container() as container:
        url = container.get_connection_url()
        config = _alembic_config(url)
        upgrade(config, "0116")
        assert not any(_column_exists(url, "fiscal_profile", name) for name in columns)
        upgrade(config, "0117")
        assert all(_column_exists(url, "fiscal_profile", name) for name in columns)
        downgrade(config, "0116")
        assert not any(_column_exists(url, "fiscal_profile", name) for name in columns)
        upgrade(config, "0117")
        assert all(_column_exists(url, "fiscal_profile", name) for name in columns)


def test_0118_adds_the_registration_date_and_the_f24_period_with_no_backfill(
    pigrocrm_postgres: Any,
) -> None:
    """REB-1100: nullable columns («not stated»), so both ways are a pure add and drop."""
    with pigrocrm_postgres.fresh_container() as container:
        url = container.get_connection_url()
        config = _alembic_config(url)
        upgrade(config, "0117")
        assert not _column_exists(url, "legal_entities", "registration_date")
        assert not _column_exists(url, "f24_group_items", "reference_period")
        upgrade(config, "0118")
        assert _column_exists(url, "legal_entities", "registration_date")
        assert _column_exists(url, "f24_group_items", "reference_period")
        downgrade(config, "0117")
        assert not _column_exists(url, "legal_entities", "registration_date")
        assert not _column_exists(url, "f24_group_items", "reference_period")
        upgrade(config, "0118")
        assert _column_exists(url, "legal_entities", "registration_date")


def test_0119_creates_the_tax_losses_table_under_the_scope_policy(
    pigrocrm_postgres: Any,
) -> None:
    """REB-1088: a new table with the row policy of every table that reaches a legal entity,
    forced; nothing is backfilled (no loss is known until one is typed), so both ways are a pure
    create and drop."""
    with pigrocrm_postgres.fresh_container() as container:
        url = container.get_connection_url()
        config = _alembic_config(url)
        upgrade(config, "0118")
        assert _table_shape(url, "tax_losses")[0] is False
        upgrade(config, "0119")
        assert _table_shape(url, "tax_losses") == (True, True, {"legal_entity_scope"})
        downgrade(config, "0118")
        assert _table_shape(url, "tax_losses")[0] is False
        upgrade(config, "0119")
        assert _table_shape(url, "tax_losses")[0] is True


def test_0120_removes_the_open_legacy_credits_and_dissolves_the_open_groups_that_held_them(
    pigrocrm_postgres: Any,
) -> None:
    """REB-1100: an open credit item of the two retired origins is dropped with its row, and the
    open F24 group that held it is dissolved with its lines released; a credit that is a line of a
    settled group, or has a payment on it, is left as it is. Going down changes nothing."""
    entity = "00000000-0000-7000-8000-0000000000a1"
    settlement = "00000000-0000-7000-8000-0000000000a2"

    def credit(number: int) -> str:
        return f"00000000-0000-7000-8000-0000000001{number:02d}"

    def item(number: int) -> str:
        return f"00000000-0000-7000-8000-0000000002{number:02d}"

    def group(number: int) -> str:
        return f"00000000-0000-7000-8000-0000000003{number:02d}"

    with pigrocrm_postgres.fresh_container() as container:
        url = container.get_connection_url()
        config = _alembic_config(url)
        upgrade(config, "0119")
        engine: Engine = create_engine(url)
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO legal_entities (id, name, legal_name, country) "
                    f"VALUES ('{entity}', 'E', 'E srl', 'IT')"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO iva_settlements (id, legal_entity_id, frequency, year, "
                    "period_number, period_start, period_end, pack_id, pack_version, sales_vat, "
                    "reverse_charge_vat, purchase_vat, credit_in, debit_in, surcharge, result, "
                    "outcome, payment_due_on) VALUES "
                    f"('{settlement}', '{entity}', 'quarterly', 2026, 1, '2026-01-01', "
                    "'2026-03-31', 'it-standard', '1', 100, 0, 400, 0, 0, 0, -300, 'credit', "
                    "'2026-05-18')"
                )
            )
            # 1: a withholding credit in an open group with a balance; 2: an IVA credit no group
            # holds; 3: a credit in a settled group, its line released; 4: a credit with a payment.
            for number, origin, year, settlement_id in (
                (1, "withholding_suffered", 2024, None),
                (2, "iva_carry_forward", 2026, settlement),
                (3, "withholding_suffered", 2025, None),
                (4, "withholding_suffered", 2026, None),
            ):
                connection.execute(
                    text(
                        "INSERT INTO tax_credits (id, legal_entity_id, origin, tax, tax_year, "
                        "iva_settlement_id, amount) VALUES (:id, :entity, :origin, :tax, :year, "
                        ":settlement, 200)"
                    ),
                    {
                        "id": credit(number),
                        "entity": entity,
                        "origin": origin,
                        "tax": "iva" if origin == "iva_carry_forward" else "irpef",
                        "year": year,
                        "settlement": settlement_id,
                    },
                )
                connection.execute(
                    text(
                        "INSERT INTO due_items (id, legal_entity_id, direction, state, "
                        "counterparty_type, tax, amount, source_kind, source_id, own_date, "
                        "due_date) VALUES (:id, :entity, 'out', 'expected', 'tax_office', :tax, "
                        "-200, 'tax_credit', :source, '2026-12-01', '2026-12-01')"
                    ),
                    {
                        "id": item(number),
                        "entity": entity,
                        "tax": "iva" if origin == "iva_carry_forward" else "irpef",
                        "source": credit(number),
                    },
                )
            connection.execute(
                text(
                    "INSERT INTO due_items (id, legal_entity_id, direction, state, "
                    "counterparty_type, tax, amount, source_kind, source_id, own_date, due_date) "
                    f"VALUES ('{item(9)}', '{entity}', 'out', 'expected', 'tax_office', 'irpef', "
                    f"900, 'tax_balance', '{credit(9)}', '2026-12-01', '2026-12-01')"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO due_item_settlements (id, due_item_id, legal_entity_id, amount, "
                    "settled_on, recorded_by_type) VALUES "
                    f"('{credit(90)}', '{item(4)}', '{entity}', -200, '2026-12-01', 'system')"
                )
            )
            for number, state in ((1, "open"), (2, "settled")):
                connection.execute(
                    text(
                        "INSERT INTO f24_groups (id, legal_entity_id, payment_date, state, "
                        "settled_on, created_by_type) VALUES (:id, :entity, '2026-12-01', :state, "
                        ":settled_on, 'user')"
                    ),
                    {
                        "id": group(number),
                        "entity": entity,
                        "state": state,
                        "settled_on": "2026-12-01" if state == "settled" else None,
                    },
                )
            for number, item_number, group_number, released in (
                (1, 1, 1, False),
                (2, 9, 1, False),
                (3, 3, 2, True),
            ):
                connection.execute(
                    text(
                        "INSERT INTO f24_group_items (id, group_id, due_item_id, legal_entity_id, "
                        "amount, released_at) VALUES (:id, :group, :item, :entity, -200, :released)"
                    ),
                    {
                        "id": f"00000000-0000-7000-8000-0000000004{number:02d}",
                        "group": group(group_number),
                        "item": item(item_number),
                        "entity": entity,
                        "released": "2026-12-02T00:00:00+00" if released else None,
                    },
                )

        upgrade(config, "0120")

        with engine.connect() as connection:
            credits = {
                str(row[0]) for row in connection.execute(text("SELECT id FROM tax_credits"))
            }
            states = {
                str(row[0]): (row[1], row[2])
                for row in connection.execute(
                    text("SELECT id, state, dropped_reason FROM due_items")
                )
            }
            groups = {
                str(row[0]): row[1]
                for row in connection.execute(text("SELECT id, state FROM f24_groups"))
            }
            live_lines = {
                str(row[0])
                for row in connection.execute(
                    text("SELECT due_item_id FROM f24_group_items WHERE released_at IS NULL")
                )
            }
        # The credits of an open group and of no group are gone; the used one and the one in a
        # settled group stay.
        assert credits == {credit(3), credit(4)}
        assert states[item(1)][0] == "dropped" and states[item(1)][1] is not None
        assert states[item(2)][0] == "dropped"
        assert states[item(3)][0] == "expected" and states[item(4)][0] == "expected"
        # The balance that shared the open group is untouched and free again.
        assert states[item(9)][0] == "expected"
        assert groups == {group(1): "dissolved", group(2): "settled"}
        assert live_lines == set()

        downgrade(config, "0119")
        upgrade(config, "0120")
        engine.dispose()


def test_0121_adds_the_disabled_worker_fact_with_no_backfill(pigrocrm_postgres: Any) -> None:
    """REB-1091: one nullable column («not stated»), the contract types widened to the apprentice
    and the training contract; both ways keep the rows that are there."""
    with pigrocrm_postgres.fresh_container() as container:
        url = container.get_connection_url()
        config = _alembic_config(url)
        upgrade(config, "0120")
        assert not _column_exists(url, "staff_members", "disabled_worker")
        upgrade(config, "0121")
        assert _column_exists(url, "staff_members", "disabled_worker")
        downgrade(config, "0120")
        assert not _column_exists(url, "staff_members", "disabled_worker")
        upgrade(config, "0121")
        assert _column_exists(url, "staff_members", "disabled_worker")


def test_0122_adds_the_seasonal_contract_history_with_no_backfill(pigrocrm_postgres: Any) -> None:
    """REB-1090: one nullable JSONB column («not stated»), no backfill; both ways keep the rows
    that are there."""
    with pigrocrm_postgres.fresh_container() as container:
        url = container.get_connection_url()
        config = _alembic_config(url)
        upgrade(config, "0121")
        assert not _column_exists(url, "staff_members", "seasonal_contracts")
        upgrade(config, "0122")
        assert _column_exists(url, "staff_members", "seasonal_contracts")
        downgrade(config, "0121")
        assert not _column_exists(url, "staff_members", "seasonal_contracts")
        upgrade(config, "0122")
        assert _column_exists(url, "staff_members", "seasonal_contracts")


def test_0124_adds_the_purchase_deductibility_columns_with_no_backfill(
    pigrocrm_postgres: Any,
) -> None:
    """REB-1089: three nullable columns («not stated») with their CHECKs, so both ways are a pure
    add and drop."""
    columns = (
        ("cost_categories", "deductibility_kind"),
        ("purchases", "deductibility_kind"),
        ("purchases", "asset_group"),
    )
    with pigrocrm_postgres.fresh_container() as container:
        url = container.get_connection_url()
        config = _alembic_config(url)
        upgrade(config, "0122")
        assert not any(_column_exists(url, table, name) for table, name in columns)
        upgrade(config, "0124")
        assert all(_column_exists(url, table, name) for table, name in columns)
        downgrade(config, "0122")
        assert not any(_column_exists(url, table, name) for table, name in columns)
        upgrade(config, "0124")
        assert all(_column_exists(url, table, name) for table, name in columns)


def test_0125_adds_the_sites_table_and_the_workplace_columns_keeping_the_staff(
    pigrocrm_postgres: Any,
) -> None:
    """REB-1092: a policed table for the sites and two staff columns that say nothing until a
    person states them, so a staff member that exists stays «not stated» both ways; a person
    cannot be at a site and at the registered office, nor at another legal entity's site."""
    entity = "00000000-0000-7000-8000-0000000000a1"
    other = "00000000-0000-7000-8000-0000000000a2"
    member = "00000000-0000-7000-8000-0000000000b1"
    site = "00000000-0000-7000-8000-0000000000c1"
    with pigrocrm_postgres.fresh_container() as container:
        url = container.get_connection_url()
        config = _alembic_config(url)
        upgrade(config, "0122")
        assert not _table_shape(url, "legal_entity_sites")[0]
        engine = create_engine(url)
        with engine.begin() as connection:
            for row_id, name in ((entity, "uno"), (other, "due")):
                connection.execute(
                    text(
                        "INSERT INTO legal_entities (id, name, legal_name, country) "
                        "VALUES (:id, :name, 'Esempio S.r.l.', 'IT')"
                    ),
                    {"id": row_id, "name": name},
                )
            connection.execute(
                text(
                    "INSERT INTO staff_members (id, legal_entity_id, kind, name, start_date, "
                    "gross_annual_pay, monthly_payments, part_time_percent) "
                    "VALUES (:id, :entity, 'employee', 'Giulia', '2026-01-01', 24000, 13, 100)"
                ),
                {"id": member, "entity": entity},
            )
        upgrade(config, "0125")
        assert _table_shape(url, "legal_entity_sites") == (True, True, {"legal_entity_scope"})
        with engine.begin() as connection:
            row = connection.execute(
                text("SELECT site_id, at_registered_office FROM staff_members WHERE id = :id"),
                {"id": member},
            ).one()
            assert tuple(row) == (None, False)
            connection.execute(
                text(
                    "INSERT INTO legal_entity_sites (id, legal_entity_id, region) "
                    "VALUES (:id, :entity, '05')"
                ),
                {"id": site, "entity": entity},
            )
            connection.execute(
                text("UPDATE staff_members SET site_id = :site WHERE id = :id"),
                {"site": site, "id": member},
            )
        with pytest.raises(IntegrityError), engine.begin() as connection:
            connection.execute(
                text("UPDATE staff_members SET at_registered_office = true WHERE id = :id"),
                {"id": member},
            )
        with pytest.raises(IntegrityError), engine.begin() as connection:
            connection.execute(
                text("UPDATE staff_members SET legal_entity_id = :other WHERE id = :id"),
                {"other": other, "id": member},
            )
        downgrade(config, "0124")
        assert not _table_shape(url, "legal_entity_sites")[0]
        assert not _column_exists(url, "staff_members", "site_id")
        assert not _column_exists(url, "staff_members", "at_registered_office")
        upgrade(config, "0125")
        assert _column_exists(url, "staff_members", "at_registered_office")
        engine.dispose()
