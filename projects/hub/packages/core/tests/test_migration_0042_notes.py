"""0042 (REB-856): `comments` becomes `notes` with the author, the source and the edit and
delete trail; the person's own events are backfilled as such; both `note` columns move into
the thread and go; a retried deploy lands on the same schema; the downgrade puts it back."""

from typing import Any
from uuid import uuid4

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Connection, create_engine, text

import rebase_core.models  # noqa: F401
from rebase_core.db import Base
from rebase_core.migrate import INI_PATH, upgrade_to_head

REVISION = "0042"
NEW_COLUMNS = {
    "autore_id",
    "fonte",
    "modificata_at",
    "modificata_da_id",
    "eliminata_at",
    "eliminata_da_id",
}


def _parent(config: Config) -> str:
    parent = ScriptDirectory.from_config(config).get_revision(REVISION).down_revision
    assert isinstance(parent, str)
    return parent


def _config(url: str) -> Config:
    config = Config(str(INI_PATH))
    config.set_main_option("sqlalchemy.url", url)
    return config


def _columns(connection: Connection, table: str) -> set[str]:
    return {
        name
        for (name,) in connection.execute(
            text("SELECT column_name FROM information_schema.columns WHERE table_name = :t"),
            {"t": table},
        )
    }


def _seed(connection: Connection) -> dict[str, Any]:
    """A person with a card carrying a note, an old comment of an admin, the person's own
    events, and a company request carrying a note."""
    ids = {name: uuid4() for name in ("user", "card", "boss", "company", "blank_card")}
    for key, email in (("user", "a@x.it"), ("boss", "b@x.it")):
        connection.execute(
            text(
                "INSERT INTO users (id, email, nome, cognome, role, attivo) "
                "VALUES (:i, :e, 'N', 'C', 'member', true)"
            ),
            {"i": ids[key], "e": email},
        )
    connection.execute(
        text(
            "INSERT INTO freelancers (id, user_id, stato, note, links, compilata_da) "
            "VALUES (:i, :u, 'nuovo', :n, '[]', 'persona')"
        ),
        {"i": ids["card"], "u": ids["user"], "n": "Chiamare dopo il 3.\nHa un'altra offerta."},
    )
    connection.execute(
        text(
            "INSERT INTO companies (id, user_id, nome_azienda, figura_richiesta, progetto, "
            "periodo_da, durata, budget_giornaliero, remoto, numero_risorse, stato, note) "
            "VALUES (:i, :u, 'Acme', 'dev', 'p', '2026-11-01', '3 mesi', 400, 'remoto', 1, "
            "'nuovo', :n)"
        ),
        {"i": ids["company"], "u": ids["boss"], "n": "Budget da confermare"},
    )
    insert = text(
        "INSERT INTO comments (id, entity_type, entity_id, testo, autore) "
        "VALUES (gen_random_uuid(), :t, :e, :x, :a)"
    )
    for entity, row, body, author in (
        ("freelancer", ids["card"], "Sentito al telefono.", "Ivan"),
        ("freelancer", ids["card"], "CV caricato dalla persona", "N C"),
        ("freelancer", ids["card"], "Profilo aggiornato dalla persona: Tariffa", "N C"),
        ("company", ids["company"], "Richiesta aggiornata dal referente: Durata", "N C"),
    ):
        connection.execute(insert, {"t": entity, "e": row, "x": body, "a": author})
    return ids


def test_upgrade_moves_the_notes_into_the_thread(hub_postgres: Any) -> None:
    with hub_postgres.fresh_container() as container:
        url = container.get_connection_url()
        config = _config(url)
        upgrade_to_head(url)
        engine = create_engine(url, future=True)
        command.downgrade(config, _parent(config))
        with engine.begin() as connection:
            ids = _seed(connection)
        command.upgrade(config, "head")
        with engine.connect() as connection:
            tables = {
                name
                for (name,) in connection.execute(
                    text(
                        "SELECT table_name FROM information_schema.tables "
                        "WHERE table_name IN ('comments', 'notes')"
                    )
                )
            }
            assert tables == {"notes"}
            assert _columns(connection, "notes") >= NEW_COLUMNS
            assert "note" not in _columns(connection, "freelancers")
            assert "note" not in _columns(connection, "companies")
            rows = {
                (entity, testo): (autore, autore_id, fonte)
                for entity, testo, autore, autore_id, fonte in connection.execute(
                    text("SELECT entity_type, testo, autore, autore_id, fonte FROM notes")
                )
            }
            # The row's own note is in the thread, verbatim, signed by a fixed label.
            assert rows[("freelancer", "Chiamare dopo il 3.\nHa un'altra offerta.")] == (
                "Nota precedente",
                None,
                None,
            )
            assert rows[("company", "Budget da confermare")] == ("Nota precedente", None, None)
            # An admin's old comment keeps no source; the person's events are the person's.
            assert rows[("freelancer", "Sentito al telefono.")] == ("Ivan", None, None)
            assert rows[("freelancer", "CV caricato dalla persona")] == (
                "N C",
                ids["user"],
                "persona",
            )
            assert rows[("freelancer", "Profilo aggiornato dalla persona: Tariffa")][2] == "persona"
            assert rows[("company", "Richiesta aggiornata dal referente: Durata")] == (
                "N C",
                ids["boss"],
                "persona",
            )
            assert len(rows) == 6
            diff = compare_metadata(MigrationContext.configure(connection), Base.metadata)
            assert diff == [], diff
        engine.dispose()


def test_upgrade_is_idempotent(hub_postgres: Any) -> None:
    with hub_postgres.fresh_container() as container:
        url = container.get_connection_url()
        config = _config(url)
        upgrade_to_head(url)
        engine = create_engine(url, future=True)
        with engine.begin() as connection:
            connection.execute(
                text("UPDATE alembic_version SET version_num = :parent"),
                {"parent": _parent(config)},
            )
        command.upgrade(config, "head")
        with engine.connect() as connection:
            assert _columns(connection, "notes") >= NEW_COLUMNS
        engine.dispose()


def test_downgrade_puts_the_columns_back(hub_postgres: Any) -> None:
    with hub_postgres.fresh_container() as container:
        url = container.get_connection_url()
        config = _config(url)
        upgrade_to_head(url)
        engine = create_engine(url, future=True)
        command.downgrade(config, _parent(config))
        with engine.begin() as connection:
            ids = _seed(connection)
        command.upgrade(config, "head")
        command.downgrade(config, _parent(config))
        with engine.connect() as connection:
            assert "comments" in {
                name
                for (name,) in connection.execute(
                    text("SELECT table_name FROM information_schema.tables")
                )
            }
            assert not NEW_COLUMNS & _columns(connection, "comments")
            assert (
                connection.execute(
                    text("SELECT note FROM freelancers WHERE id = :i"), {"i": ids["card"]}
                ).scalar_one()
                == "Chiamare dopo il 3.\nHa un'altra offerta."
            )
            assert (
                connection.execute(
                    text("SELECT note FROM companies WHERE id = :i"), {"i": ids["company"]}
                ).scalar_one()
                == "Budget da confermare"
            )
            # The migrated notes left the thread; the comments are the four that were there.
            assert connection.execute(text("SELECT count(*) FROM comments")).scalar_one() == 4
        engine.dispose()
