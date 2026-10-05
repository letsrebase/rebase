"""0030 (REB-658): a referral counts once its referred person is verified. The backfill
keeps every referral that already counts counting and leaves the rest pending, by the
same rule the code applies going forward: a login only proves a referral when it came at
or after the referral was made."""

from itertools import count

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import Connection, create_engine, text
from testcontainers.community.postgres import PostgresContainer

from rebase_core.db import Base
from rebase_core.migrate import INI_PATH, head_revision, upgrade_to_head

_NUMERO = count(1)


def _user(connection: Connection, email: str) -> str:
    return str(
        connection.execute(
            text(
                "INSERT INTO users (id, email, nome, cognome, role, attivo, created_at, "
                "updated_at) VALUES (gen_random_uuid(), :e, 'N', 'C', 'member', true, now(), "
                "now()) RETURNING id"
            ),
            {"e": email},
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


def _company(connection: Connection, user_id: str) -> str:
    return str(
        connection.execute(
            text(
                "INSERT INTO companies (id, user_id, nome_azienda, figura_richiesta, progetto, "
                "periodo_da, durata, budget_giornaliero, remoto, numero_risorse, stato, "
                "created_at, updated_at) VALUES (gen_random_uuid(), :u, 'Acme', 'Dev', 'P', "
                "'2026-10-01', '3 mesi', 500, 'remoto', 1, 'nuovo', now(), now()) RETURNING id"
            ),
            {"u": user_id},
        ).scalar_one()
    )


def _referral(
    connection: Connection, referrer: str, kind: str, entity_id: str, made: str = "10 days"
) -> str:
    return str(
        connection.execute(
            text(
                "INSERT INTO referrals (id, referrer_user_id, kind, entity_id, code, created_at, "
                "updated_at) VALUES (gen_random_uuid(), :r, :k, :e, 'ABCDEF2345', "
                "now() - CAST(:made AS interval), now()) RETURNING id"
            ),
            {"r": referrer, "k": kind, "e": entity_id, "made": made},
        ).scalar_one()
    )


def _login(connection: Connection, user_id: str, ago: str) -> None:
    connection.execute(
        text(
            "INSERT INTO logins (id, user_id, logged_at) "
            "VALUES (gen_random_uuid(), :u, now() - CAST(:ago AS interval))"
        ),
        {"u": user_id, "ago": ago},
    )


def _signed_letter(
    connection: Connection, freelancer_id: str, company_id: str, admin: str, ago: str
) -> str:
    match = connection.execute(
        text(
            "INSERT INTO matches (id, freelancer_id, company_id, cliente_ragione_sociale, "
            "cliente_piva, cliente_sede, created_by, stato, created_at, updated_at) VALUES "
            "(gen_random_uuid(), :f, :c, 'Acme', '01234567890', 'Milano', :a, 'attivo', "
            "now(), now()) RETURNING id"
        ),
        {"f": freelancer_id, "c": company_id, "a": admin},
    ).scalar_one()
    return str(
        connection.execute(
            text(
                "INSERT INTO contract_documents (id, match_id, kind, freelancer_id, "
                "text_version, testo_bozza, data, numero, pdf, stato, signed_at, created_by, "
                "created_at, updated_at) VALUES (gen_random_uuid(), :m, 'lettera', :f, 'v1', "
                "false, '{}', :n, '\\x00', 'firmato', "
                "now() - CAST(:ago AS interval), :a, now(), now()) RETURNING id"
            ),
            {"m": match, "f": freelancer_id, "a": admin, "ago": ago, "n": next(_NUMERO)},
        ).scalar_one()
    )


def _reward(connection: Connection, referral_id: str, document_id: str) -> None:
    connection.execute(
        text(
            "INSERT INTO referral_rewards (id, referral_id, document_id, rate, stato, "
            "created_at, updated_at) VALUES (gen_random_uuid(), :r, :d, 0.3, 'da_confermare', "
            "now() - interval '2 days', now())"
        ),
        {"r": referral_id, "d": document_id},
    )


def _rows(connection: Connection) -> dict[str, tuple[str, bool, str | None]]:
    return {
        str(row.id): (row.stato, row.verified_at is not None, row.verified_via)
        for row in connection.execute(
            text("SELECT id, stato, verified_at, verified_via FROM referrals")
        )
    }


def test_migration_0030_keeps_what_counts_counting_and_runs_again() -> None:
    with PostgresContainer("postgres:17-alpine", driver="psycopg") as container:
        url = container.get_connection_url()
        upgrade_to_head(url)
        config = Config(str(INI_PATH))
        config.set_main_option("sqlalchemy.url", url)
        engine = create_engine(url, future=True)
        command.downgrade(config, "0029")
        with engine.begin() as connection:
            admin = _user(connection, "ivan@rebase.it")
            referrer = _user(connection, "mario@community.it")
            ada, grace, linus = (
                _user(connection, "ada@studio.it"),
                _user(connection, "grace@studio.it"),
                _user(connection, "linus@studio.it"),
            )
            wile, road, tweety = (
                _user(connection, "wile@acme.it"),
                _user(connection, "road@runner.it"),
                _user(connection, "tweety@acme.it"),
            )
            ada_card, grace_card, linus_card = (
                _freelancer(connection, ada),
                _freelancer(connection, grace),
                _freelancer(connection, linus),
            )
            wile_co, road_co, tweety_co = (
                _company(connection, wile),
                _company(connection, road),
                _company(connection, tweety),
            )
            # A reward already written: it counted, whoever logged in. The letter's signer is
            # the freelancer, so the company referral is `storico`, not `lettera`.
            letter = _signed_letter(connection, ada_card, wile_co, admin, "3 days")
            with_reward = _referral(connection, referrer, "company", wile_co)
            _reward(connection, with_reward, letter)
            # A freelancer who signed a letter, with no reward on file: `lettera`.
            signed = _referral(connection, referrer, "freelancer", grace_card)
            _signed_letter(connection, grace_card, road_co, admin, "4 days")
            # A login after the referral was made: `accesso`, at that login.
            logged_in = _referral(connection, referrer, "company", road_co)
            _login(connection, road, "5 days")
            # A login from before the referral proves nothing about it.
            old_login = _referral(connection, referrer, "freelancer", linus_card, made="2 days")
            _login(connection, linus, "6 days")
            # Nothing at all, and a company whose freelancer signed (the company never does).
            plain = _referral(connection, referrer, "company", tweety_co)
            _signed_letter(
                connection,
                _freelancer(connection, _user(connection, "marie@studio.it")),
                tweety_co,
                admin,
                "1 day",
            )
            # A referral whose referred row is gone cannot be proved by anything.
            orphan = _referral(
                connection, referrer, "freelancer", "00000000-0000-0000-0000-00000000dead"
            )
        expected = {
            with_reward: ("verificato", True, "storico"),
            signed: ("verificato", True, "lettera"),
            logged_in: ("verificato", True, "accesso"),
            old_login: ("da_verificare", False, None),
            plain: ("da_verificare", False, None),
            orphan: ("da_verificare", False, None),
        }
        for _ in range(2):  # a retried deploy runs the same revision again
            command.upgrade(config, "head")
            with engine.begin() as connection:
                assert _rows(connection) == expected
                # `verified_at` is the evidence's own moment, not the migration's.
                moments = {
                    str(row.id): row.verified_at
                    for row in connection.execute(
                        text("SELECT id, verified_at FROM referrals WHERE verified_at IS NOT NULL")
                    )
                }
                login_at = connection.execute(
                    text("SELECT logged_at FROM logins WHERE user_id = :u"), {"u": road}
                ).scalar_one()
                assert moments[logged_in] == login_at
                # A person who logs in after the migration is verified by the code, and the
                # revision run again over the columns must not undo it or promote anything else.
                connection.execute(
                    text(
                        "UPDATE referrals SET stato = 'verificato', verified_at = now(), "
                        "verified_via = 'accesso' WHERE id = :i"
                    ),
                    {"i": plain},
                )
                connection.execute(text("UPDATE alembic_version SET version_num = '0029'"))
            command.upgrade(config, "head")
            with engine.begin() as connection:
                assert _rows(connection) == {**expected, plain: ("verificato", True, "accesso")}
                connection.execute(
                    text(
                        "UPDATE referrals SET stato = 'da_verificare', verified_at = NULL, "
                        "verified_via = NULL WHERE id = :i"
                    ),
                    {"i": plain},
                )
            command.downgrade(config, "0029")
            with engine.connect() as connection:
                columns = connection.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_name = 'referrals' AND column_name IN "
                        "('stato', 'verified_at', 'verified_via')"
                    )
                ).all()
                assert columns == []
                assert connection.execute(text("SELECT count(*) FROM referrals")).scalar() == 6
        command.upgrade(config, "head")
        with engine.connect() as connection:
            assert (
                connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
                == head_revision()
            )
            assert _rows(connection) == expected
            diff = compare_metadata(MigrationContext.configure(connection), Base.metadata)
            assert diff == [], diff
        engine.dispose()
