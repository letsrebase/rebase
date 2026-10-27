"""0035 (REB-800): LinkedIn addresses rewritten to the stored shape, `merged_into_id` and
`cv_uploaded_at` added, and the two unique indexes created only when nothing shares a
profile. The case that matters is production's: a pair of people that share a profile
(up to a trailing slash) must not stop the migration, and the index waits for the merge."""

from typing import Any

from alembic import command
from alembic.config import Config
from sqlalchemy import Connection, Engine, create_engine, text

import rebase_core.models  # noqa: F401
from rebase_core.migrate import INI_PATH, upgrade_to_head

INDEXES = ("uq_users_linkedin_lower", "uq_signups_linkedin_lower")


def _indexes(connection: Connection) -> set[str]:
    return {
        name
        for (name,) in connection.execute(
            text("SELECT indexname FROM pg_indexes WHERE indexname = ANY(:names)"),
            {"names": list(INDEXES)},
        )
    }


def _user(connection: Connection, email: str, linkedin: str | None) -> str:
    return str(
        connection.execute(
            text(
                "INSERT INTO users (id, email, nome, cognome, linkedin_url, role, attivo, "
                "created_at, updated_at) VALUES (gen_random_uuid(), :e, 'A', 'B', :l, "
                "'member', true, now(), now()) RETURNING id"
            ),
            {"e": email, "l": linkedin},
        ).scalar_one()
    )


def _card(connection: Connection, user_id: str, cv: bytes | None) -> str:
    return str(
        connection.execute(
            text(
                "INSERT INTO freelancers (id, user_id, cv_bytes, cv_size, stato, links, "
                "compilata_da, created_at, updated_at) VALUES (gen_random_uuid(), :u, :c, "
                ":s, 'nuovo', '[]', 'persona', now(), '2026-10-24 10:00+00') RETURNING id"
            ),
            {"u": user_id, "c": cv, "s": None if cv is None else len(cv)},
        ).scalar_one()
    )


def _signup(connection: Connection, email: str, linkedin: str | None) -> None:
    connection.execute(
        text(
            "INSERT INTO signups (id, email, linkedin_url, created_at) "
            "VALUES (gen_random_uuid(), :e, :l, now())"
        ),
        {"e": email, "l": linkedin},
    )


def test_migration_0035_normalises_and_waits_for_the_merge_on_a_shared_profile(
    hub_postgres: Any,
) -> None:
    with hub_postgres.fresh_container() as container:
        url = container.get_connection_url()
        upgrade_to_head(url)
        config = Config(str(INI_PATH))
        config.set_main_option("sqlalchemy.url", url)
        engine = create_engine(url, future=True)
        command.downgrade(config, "0034")
        with engine.begin() as connection:
            assert _indexes(connection) == set()
            first = _user(
                connection, "vecchia@studio.it", "https://www.linkedin.com/in/vittoria-lanzo"
            )
            second = _user(
                connection, "nuova@studio.it", "https://www.linkedin.com/in/Vittoria-Lanzo/"
            )
            single = _user(
                connection, "sola@studio.it", "http://it.linkedin.com/in/J%C3%BCrgen-M?trk=x"
            )
            company = _user(
                connection, "azienda@studio.it", "https://www.linkedin.com/company/Acme/"
            )
            blank = _user(connection, "senza@studio.it", None)
            # Two people with the same company page, and two blank addresses: neither is a
            # profile, so neither may stop an index or look like one person.
            _user(connection, "azienda2@studio.it", "https://www.linkedin.com/company/Acme/")
            _user(connection, "vuoto1@studio.it", "")
            _user(connection, "vuoto2@studio.it", "   ")
            with_cv = _card(connection, second, b"%PDF-1.7")
            without_cv = _card(connection, blank, None)
            _signup(connection, "lead@studio.it", "LINKEDIN.com/in/Lead/")

        def check(engine: Engine) -> None:
            with engine.begin() as connection:
                urls = dict(connection.execute(text("SELECT email, linkedin_url FROM users")).all())
                assert urls["vecchia@studio.it"] == "https://www.linkedin.com/in/vittoria-lanzo"
                assert urls["nuova@studio.it"] == "https://www.linkedin.com/in/vittoria-lanzo"
                assert urls["sola@studio.it"] == "https://www.linkedin.com/in/jürgen-m"
                # Not a personal profile: left exactly as it was.
                assert urls["azienda@studio.it"] == "https://www.linkedin.com/company/Acme/"
                assert urls["senza@studio.it"] is None
                assert urls["vuoto1@studio.it"] is None and urls["vuoto2@studio.it"] is None
                assert (
                    connection.execute(text("SELECT linkedin_url FROM signups")).scalar_one()
                    == "https://www.linkedin.com/in/lead"
                )
                # The pair that shares a profile stops the users' index and nothing else.
                assert _indexes(connection) == {"uq_signups_linkedin_lower"}
                assert first and single and company
                stamps = dict(
                    connection.execute(
                        text("SELECT id::text, cv_uploaded_at FROM freelancers")
                    ).all()
                )
                assert stamps[with_cv] is not None and stamps[without_cv] is None
                assert (
                    connection.execute(
                        text("SELECT count(*) FROM users WHERE merged_into_id IS NOT NULL")
                    ).scalar_one()
                    == 0
                )

        command.upgrade(config, "head")
        check(engine)
        # A retried deploy runs the same revision again, on data already rewritten.
        command.downgrade(config, "0034")
        command.upgrade(config, "head")
        check(engine)

        # The merge ran (here: one of the two gave its profile up): the index can follow.
        with engine.begin() as connection:
            connection.execute(
                text("UPDATE users SET linkedin_url = NULL WHERE email = 'vecchia@studio.it'")
            )
            connection.execute(
                text(
                    "CREATE UNIQUE INDEX uq_users_linkedin_lower ON users (lower(linkedin_url)) "
                    "WHERE linkedin_url LIKE 'https://www.linkedin.com/in/%'"
                )
            )
            assert _indexes(connection) == set(INDEXES)


def test_a_database_with_no_shared_profile_gets_both_indexes(hub_postgres: Any) -> None:
    with hub_postgres.fresh_container() as container:
        url = container.get_connection_url()
        upgrade_to_head(url)
        config = Config(str(INI_PATH))
        config.set_main_option("sqlalchemy.url", url)
        engine = create_engine(url, future=True)
        command.downgrade(config, "0034")
        with engine.begin() as connection:
            _user(connection, "uno@studio.it", "https://www.linkedin.com/in/uno/")
            _user(connection, "due@studio.it", None)
            _user(connection, "tre@studio.it", None)
        command.upgrade(config, "head")
        with engine.begin() as connection:
            assert _indexes(connection) == set(INDEXES)
            assert (
                connection.execute(
                    text("SELECT linkedin_url FROM users WHERE email = 'uno@studio.it'")
                ).scalar_one()
                == "https://www.linkedin.com/in/uno"
            )
