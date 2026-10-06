"""0029 (REB-578): a cloud proposal remembers its company. The backfill names the company
only where the person has only ever held grants for one; everything else stays `NULL`."""

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import Connection, create_engine, text
from testcontainers.community.postgres import PostgresContainer

from rebase_core.db import Base
from rebase_core.migrate import INI_PATH, head_revision, upgrade_to_head


def _user(connection: Connection, email: str, role: str = "freelancer") -> str:
    return str(
        connection.execute(
            text(
                "INSERT INTO users (id, email, nome, cognome, role, attivo, created_at, "
                "updated_at) VALUES (gen_random_uuid(), :e, 'N', 'C', :r, true, now(), now()) "
                "RETURNING id"
            ),
            {"e": email, "r": role},
        ).scalar_one()
    )


def _company(connection: Connection, user_id: str, name: str) -> str:
    return str(
        connection.execute(
            text(
                "INSERT INTO companies (id, user_id, nome_azienda, figura_richiesta, progetto, "
                "periodo_da, durata, budget_giornaliero, remoto, numero_risorse, stato, "
                "created_at, updated_at) VALUES (gen_random_uuid(), :u, :n, 'Dev', 'P', "
                "'2026-10-01', '3 mesi', 500, 'remoto', 1, 'nuovo', now(), now()) RETURNING id"
            ),
            {"u": user_id, "n": name},
        ).scalar_one()
    )


def _grant(connection: Connection, user_id: str, company_id: str, admin_id: str, ago: str) -> None:
    connection.execute(
        text(
            "INSERT INTO talent_cloud_grants (id, user_id, company_id, granted_by, granted_at) "
            "VALUES (gen_random_uuid(), :u, :c, :a, now() - CAST(:ago AS interval))"
        ),
        {"u": user_id, "c": company_id, "a": admin_id, "ago": ago},
    )


def _proposal(
    connection: Connection, user_id: str | None, origine: str, age: str, errore: str | None = None
) -> str:
    return str(
        connection.execute(
            text(
                "INSERT INTO team_proposals (id, descrizione, riassunto, luogo, team, economia, "
                "model, input_tokens, output_tokens, cache_read_tokens, origine, user_id, "
                "errore, created_at) VALUES (gen_random_uuid(), 'd', 'r', '{}', '[]', '{}', "
                "'m', 0, 0, 0, :o, :u, :e, now() - CAST(:age AS interval)) RETURNING id"
            ),
            {"o": origine, "u": user_id, "e": errore, "age": age},
        ).scalar_one()
    )


def test_migration_0029_backfills_only_what_is_not_a_guess_and_runs_again() -> None:
    with PostgresContainer("postgres:17-alpine", driver="psycopg") as container:
        url = container.get_connection_url()
        upgrade_to_head(url)
        config = Config(str(INI_PATH))
        config.set_main_option("sqlalchemy.url", url)
        engine = create_engine(url, future=True)
        command.downgrade(config, "0028")
        with engine.begin() as connection:
            admin = _user(connection, "ivan@rebase.it", "admin")
            solo = _user(connection, "solo@acme.it")
            both = _user(connection, "both@acme.it")
            acme = _company(connection, solo, "Acme")
            beta = _company(connection, both, "Beta")
            gamma = _company(connection, both, "Gamma")
            _grant(connection, solo, acme, admin, "2 days")
            _grant(connection, both, beta, admin, "2 days")
            _grant(connection, both, gamma, admin, "1 day")
            ids = {
                "solo": _proposal(connection, solo, "cloud", "1 hour"),
                "before_grant": _proposal(connection, solo, "cloud", "3 days"),
                "both": _proposal(connection, both, "cloud", "1 hour"),
                "attempt": _proposal(connection, solo, "cloud", "1 hour", "llm_unavailable"),
                "public": _proposal(connection, None, "pubblico", "1 hour"),
                "admin": _proposal(connection, admin, "admin", "1 hour"),
            }
        for _ in range(2):  # a retried deploy runs the same revision again
            command.upgrade(config, "head")
            with engine.begin() as connection:
                stored = {
                    name: connection.execute(
                        text("SELECT company_id FROM team_proposals WHERE id = :i"), {"i": row_id}
                    ).scalar_one()
                    for name, row_id in ids.items()
                }
                assert str(stored["solo"]) == acme
                assert all(stored[name] is None for name in stored if name != "solo"), stored
            command.downgrade(config, "0028")
            with engine.connect() as connection:
                columns = connection.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_name = 'team_proposals' AND column_name = 'company_id'"
                    )
                ).all()
                assert columns == []
            command.upgrade(config, "head")
            command.downgrade(config, "0028")
        command.upgrade(config, "head")
        with engine.begin() as connection:
            # The revision again over the column it already added.
            connection.execute(text("UPDATE alembic_version SET version_num = '0028'"))
        command.upgrade(config, "head")
        with engine.connect() as connection:
            assert (
                str(
                    connection.execute(
                        text("SELECT company_id FROM team_proposals WHERE id = :i"),
                        {"i": ids["solo"]},
                    ).scalar_one()
                )
                == acme
            )
            assert (
                connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
                == head_revision()
            )
            diff = compare_metadata(MigrationContext.configure(connection), Base.metadata)
            assert diff == [], diff
        engine.dispose()
