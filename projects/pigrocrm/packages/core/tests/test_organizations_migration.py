"""REB-969: the revision that renames `customers` to `organizations`, over rows planted before
it, both ways. Its own file so the revision's number, which moves when the branch is rebased,
is looked up and never typed."""

from pathlib import Path
from typing import Any

from alembic.command import downgrade, upgrade
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, create_engine, text

CORE_ROOT = Path(__file__).resolve().parents[1]

ENTITY = "00000000-0000-7000-8000-0000000000e1"
CUSTOMER = "00000000-0000-7000-8000-0000000000c1"
PERSON = "00000000-0000-7000-8000-0000000000a1"
DEAL = "00000000-0000-7000-8000-0000000000d1"

RENAMED_FUNCTIONS = (
    ("customer_visible", "organization_visible"),
    ("customer_archived", "organization_archived"),
    ("active_deals_of_customer", "active_deals_of_organization"),
)


NOTES_CHECK_SQL = (
    "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = 'ck_notes_entity_type'"
)


def _body(function: str) -> str:
    return f"SELECT prosrc FROM pg_proc WHERE proname = '{function}'"


def _config(url: str) -> Config:
    config = Config(str(CORE_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(CORE_ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", url)
    return config


def _revisions(config: Config) -> tuple[str, str]:
    """The revision that renames the table, and the one it follows."""
    script = ScriptDirectory.from_config(config)
    (renaming,) = [
        revision
        for revision in script.walk_revisions()
        if revision.path is not None and revision.path.endswith("_organizations.py")
    ]
    assert isinstance(renaming.down_revision, str)
    return renaming.revision, renaming.down_revision


def _scalar(engine: Engine, sql: str) -> Any:
    with engine.connect() as connection:
        return connection.execute(text(sql)).scalar_one()


def _names(engine: Engine, sql: str) -> set[str]:
    with engine.connect() as connection:
        return set(connection.execute(text(sql)).scalars())


def _state(engine: Engine, table: str, prefix: str, other: str) -> dict[str, Any]:
    """What the revision changes, read from the catalog: the table, the names that carry its
    prefix, the three renamed functions, and how many discriminator rows still say `other`."""
    indexes = _names(
        engine,
        f"SELECT indexname FROM pg_indexes WHERE schemaname = 'public' AND tablename = '{table}'",
    )
    constraints = _names(
        engine,
        f"SELECT conname FROM pg_constraint WHERE conrelid = '{table}'::regclass",
    )
    functions = _names(
        engine, "SELECT proname FROM pg_proc WHERE pronamespace = 'public'::regnamespace"
    )
    return {
        "indexes": {name for name in indexes if prefix in name},
        "constraints": {name for name in constraints if prefix in name},
        "functions": {name for pair in RENAMED_FUNCTIONS for name in pair if name in functions},
        "leftover_rows": sum(
            _scalar(engine, f"SELECT count(*) FROM {t} WHERE entity_type = '{other}'")
            for t in ("activities", "notes", "field_definitions")
        ),
    }


def test_the_revision_renames_the_table_its_names_the_functions_and_the_discriminator_both_ways(
    pigrocrm_postgres: Any,
) -> None:
    with pigrocrm_postgres.fresh_container() as container:
        url = container.get_connection_url()
        config = _config(url)
        revision, before = _revisions(config)
        upgrade(config, before)
        engine: Engine = create_engine(url)
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO legal_entities (id, name, legal_name, country) "
                    f"VALUES ('{ENTITY}', 'E', 'E srl', 'IT')"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO customers (id, legal_name, country, legal_entity_id, "
                    f"custom_fields) VALUES ('{CUSTOMER}', 'Acme', 'IT', '{ENTITY}', '{{}}')"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO people (id, first_name, customer_id, custom_fields) "
                    f"VALUES ('{PERSON}', 'Ada', '{CUSTOMER}', '{{}}')"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO notes (id, entity_type, entity_id, text) VALUES "
                    f"(gen_random_uuid(), 'customer', '{CUSTOMER}', 'about Acme'), "
                    f"(gen_random_uuid(), 'person', '{PERSON}', 'about Ada')"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO activities (id, entity_type, entity_id, kind, actor_type, "
                    f"payload, occurred_at) VALUES (gen_random_uuid(), 'customer', '{CUSTOMER}', "
                    "'created', 'user', '{}', now())"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO field_definitions (id, entity_type, key, label, field_type, "
                    "options, required, position, archived, created_at, updated_at) VALUES "
                    "(gen_random_uuid(), 'customer', 'sector', 'Sector', 'text', '[]', false, "
                    "0, false, now(), now())"
                )
            )
        legacy = _state(engine, "customers", "customers", "organization")
        legacy_check = _scalar(
            engine,
            NOTES_CHECK_SQL,
        )

        upgrade(config, revision)
        renamed = _state(engine, "organizations", "organizations", "customer")
        assert _scalar(engine, "SELECT legal_name FROM organizations") == "Acme"
        assert _scalar(engine, "SELECT count(*) FROM people WHERE customer_id IS NOT NULL") == 1
        assert not _names(engine, "SELECT tablename FROM pg_tables WHERE tablename = 'customers'")
        # The index and constraint names moved with the table, and no `customers` name is left.
        assert renamed["indexes"] == {
            name.replace("customers", "organizations") for name in legacy["indexes"]
        }
        assert renamed["constraints"] == {
            name.replace("customers", "organizations") for name in legacy["constraints"]
        }
        assert renamed["functions"] == {new for _, new in RENAMED_FUNCTIONS}
        assert renamed["leftover_rows"] == 0
        assert _scalar(engine, "SELECT count(*) FROM notes WHERE entity_type = 'organization'") == 1
        # The function bodies are text: they read `organizations` and `'organization'` now.
        person_visible = _scalar(engine, _body("person_visible"))
        assert "organizations" in person_visible and "customers" not in person_visible
        entity_visible = _scalar(engine, _body("entity_visible"))
        assert "WHEN 'organization' THEN organization_visible($2)" in entity_visible
        assert "'customer'" not in entity_visible
        # The policies hold the functions by object id, so they follow the rename.
        people_policy = _scalar(
            engine,
            "SELECT qual FROM pg_policies WHERE tablename = 'people' AND policyname = "
            "'legal_entity_scope'",
        )
        assert "organization_visible(customer_id)" in people_policy
        assert "'organization'" in _scalar(
            engine,
            NOTES_CHECK_SQL,
        )
        # A foreign key still says who is billed, and points at the renamed table.
        assert (
            _scalar(
                engine,
                "SELECT confrelid::regclass::text FROM pg_constraint "
                "WHERE conname = 'people_customer_id_fkey'",
            )
            == "organizations"
        )

        downgrade(config, before)
        assert _state(engine, "customers", "customers", "organization") == legacy
        assert _scalar(engine, "SELECT legal_name FROM customers") == "Acme"
        assert _scalar(engine, "SELECT count(*) FROM notes WHERE entity_type = 'customer'") == 1
        assert (
            _scalar(
                engine,
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conname = 'ck_notes_entity_type'",
            )
            == legacy_check
        )
        engine.dispose()
