"""REB-970: the revision that gives organizations roles and the unique VAT number index, over
rows planted before it, both ways, and over a space whose rows already collide. Its own file so
the revision's number, which moves when the branch is rebased, is looked up and never typed."""

from pathlib import Path
from typing import Any

from alembic.command import downgrade, upgrade
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session

from pigrocrm.core.organizations.duplicates import duplicate_vat_groups, ensure_vat_index
from pigrocrm.core.organizations.roles import VAT_UNIQUE_INDEX

CORE_ROOT = Path(__file__).resolve().parents[1]

ENTITY = "00000000-0000-7000-8000-0000000000e1"
ACME = "00000000-0000-7000-8000-0000000000c1"
BETA = "00000000-0000-7000-8000-0000000000c2"
BETA_COPY = "00000000-0000-7000-8000-0000000000c3"

NEW_COLUMNS = {
    "roles",
    "supplier_payment_days",
    "supplier_payment_end_of_month",
    "supplier_kind",
    "supplier_area",
    "supplier_supplies",
    "customer_kind",
    "is_withholding_agent",
}


def _config(url: str) -> Config:
    config = Config(str(CORE_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(CORE_ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", url)
    return config


def _revisions(config: Config) -> tuple[str, str]:
    """The revision that adds the roles, and the one it follows."""
    script = ScriptDirectory.from_config(config)
    (revision,) = [
        r
        for r in script.walk_revisions()
        if r.path is not None and r.path.endswith("_organization_roles.py")
    ]
    assert isinstance(revision.down_revision, str)
    return revision.revision, revision.down_revision


def _scalar(engine: Engine, sql: str) -> Any:
    with engine.connect() as connection:
        return connection.execute(text(sql)).scalar_one()


def _columns(engine: Engine) -> set[str]:
    with engine.connect() as connection:
        return set(
            connection.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'organizations'"
                )
            ).scalars()
        )


def _plant(engine: Engine, *, duplicate: bool) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO legal_entities (id, name, legal_name, country) "
                f"VALUES ('{ENTITY}', 'E', 'E srl', 'IT')"
            )
        )
        rows = [(ACME, "Acme", "12345678901"), (BETA, "Beta", "10987654321")]
        if duplicate:
            rows.append((BETA_COPY, "Beta twin", "10987654321"))
        for identifier, name, vat in rows:
            connection.execute(
                text(
                    "INSERT INTO organizations (id, legal_name, vat_number, country, "
                    f"legal_entity_id, custom_fields) VALUES ('{identifier}', '{name}', '{vat}', "
                    f"'IT', '{ENTITY}', '{{}}')"
                )
            )


def test_the_revision_gives_every_row_the_customer_role_and_builds_the_index_both_ways(
    pigrocrm_postgres: Any,
) -> None:
    with pigrocrm_postgres.fresh_container() as container:
        url = container.get_connection_url()
        config = _config(url)
        revision, before = _revisions(config)
        upgrade(config, before)
        engine = create_engine(url)
        _plant(engine, duplicate=False)
        assert not NEW_COLUMNS & _columns(engine)

        upgrade(config, revision)
        assert _columns(engine) >= NEW_COLUMNS
        assert _scalar(engine, "SELECT count(*) FROM organizations WHERE roles = '{customer}'") == 2
        assert (
            _scalar(engine, "SELECT count(*) FROM organizations WHERE NOT is_withholding_agent")
            == 2
        )
        assert (
            _scalar(
                engine, f"SELECT count(*) FROM pg_indexes WHERE indexname = '{VAT_UNIQUE_INDEX}'"
            )
            == 1
        )
        assert "gin" in _scalar(
            engine, "SELECT indexdef FROM pg_indexes WHERE indexname = 'ix_organizations_roles'"
        )
        # A foreign number fits, and the index refuses the same number twice.
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO organizations (id, legal_name, vat_number, country, "
                    f"legal_entity_id, custom_fields, roles) VALUES (gen_random_uuid(), 'NL', "
                    f"'NL123456789B01', 'NL', '{ENTITY}', '{{}}', '{{supplier}}')"
                )
            )
        with engine.connect() as connection:
            try:
                connection.execute(
                    text(
                        "INSERT INTO organizations (id, legal_name, vat_number, country, "
                        f"legal_entity_id, custom_fields) VALUES (gen_random_uuid(), 'again', "
                        f"'nl123456789b01', 'NL', '{ENTITY}', '{{}}')"
                    )
                )
            except Exception as exc:  # noqa: BLE001
                assert VAT_UNIQUE_INDEX in str(exc)
            else:
                raise AssertionError("the index took a second row with the same number")
        with engine.begin() as connection:
            connection.execute(text("DELETE FROM organizations WHERE country = 'NL'"))

        downgrade(config, before)
        assert not NEW_COLUMNS & _columns(engine)
        assert (
            _scalar(
                engine, f"SELECT count(*) FROM pg_indexes WHERE indexname = '{VAT_UNIQUE_INDEX}'"
            )
            == 0
        )
        assert _scalar(engine, "SELECT count(*) FROM organizations") == 2

        # And forward again over the same rows.
        upgrade(config, revision)
        assert _scalar(engine, "SELECT count(*) FROM organizations WHERE roles = '{customer}'") == 2
        engine.dispose()


def test_rows_that_already_collide_do_not_fail_the_revision_and_the_index_waits_for_a_person(
    pigrocrm_postgres: Any, capfd: Any
) -> None:
    with pigrocrm_postgres.fresh_container() as container:
        url = container.get_connection_url()
        config = _config(url)
        revision, before = _revisions(config)
        upgrade(config, before)
        engine = create_engine(url)
        _plant(engine, duplicate=True)

        upgrade(config, revision)
        assert "duplicate-organizations" in capfd.readouterr().err
        assert (
            _scalar(
                engine, f"SELECT count(*) FROM pg_indexes WHERE indexname = '{VAT_UNIQUE_INDEX}'"
            )
            == 0
        )
        assert _scalar(engine, "SELECT count(*) FROM organizations") == 3

        with Session(engine) as session:
            (group,) = duplicate_vat_groups(session)
            assert [row.legal_name for row in group.organizations] == ["Beta", "Beta twin"]
            assert ensure_vat_index(session) == "blocked"
            session.execute(
                text(f"UPDATE organizations SET deleted_at = now() WHERE id = '{BETA_COPY}'")
            )
            session.commit()
            assert ensure_vat_index(session) == "created"
        assert (
            _scalar(
                engine, f"SELECT count(*) FROM pg_indexes WHERE indexname = '{VAT_UNIQUE_INDEX}'"
            )
            == 1
        )

        downgrade(config, before)
        engine.dispose()
