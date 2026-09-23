"""Migration 0011 backfills `users` from three sources, correctly, and every foreign
key it moves off keeps resolving through the new, `users`-backed path afterwards.

A database is brought to 0010 (the schema the day before this migration), seeded with
raw rows the way the real production tables hold them, then upgraded to head -- proving
the backfill against a database that has never seen `rebase_core.models`, the same
proof `test_the_production_table_is_adopted_with_its_rows` gives migration 0001.
"""

import hashlib

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from testcontainers.community.postgres import PostgresContainer

from rebase_core.admin_tokens import TOKEN_PREFIX, AdminTokenService
from rebase_core.config import Settings
from rebase_core.db import create_engine_from_settings, session_factory
from rebase_core.migrate import INI_PATH


def _upgrade(url: str, revision: str) -> None:
    config = Config(str(INI_PATH))
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, revision)


def test_migration_a_backfills_users_from_freelancers_admin_users_and_companies() -> None:
    with PostgresContainer("postgres:17-alpine", driver="psycopg") as container:
        url = container.get_connection_url()
        _upgrade(url, "0010")
        engine = create_engine(url, future=True)
        with engine.begin() as connection:
            # 1. A freelancer, whose card already holds both names -- becomes a
            #    plain member.
            connection.execute(
                text(
                    "INSERT INTO freelancers (id, nome, cognome, email, links, stato, "
                    "compilata_da, created_at, updated_at) VALUES "
                    "(gen_random_uuid(), 'Ada', 'Lovelace', 'ada@studio.it', '[]', "
                    "'nuovo', 'persona', now(), now())"
                )
            )
            # 2. The same person is also an active admin_users row (case-insensitive
            #    match, `au.nome` a single string this time): promoted, but *not*
            #    re-inserted, so the freelancer's own cognome survives untouched.
            connection.execute(
                text(
                    "INSERT INTO admin_users (id, email, nome, password_hash, attivo, "
                    "created_at, updated_at) VALUES (gen_random_uuid(), 'ADA@studio.it', "
                    "'Ada Lovelace Admin', 'x', true, now(), now())"
                )
            )
            # 3. A freelancer who is also an admin, but a *deactivated* one: stays a
            #    member, and `attivo` stays true -- it is the freelancer's own flag,
            #    unaffected by the admin_users row's.
            connection.execute(
                text(
                    "INSERT INTO freelancers (id, nome, cognome, email, links, stato, "
                    "compilata_da, created_at, updated_at) VALUES "
                    "(gen_random_uuid(), 'Bob', 'Marley', 'bob@studio.it', '[]', "
                    "'nuovo', 'persona', now(), now())"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO admin_users (id, email, nome, password_hash, attivo, "
                    "created_at, updated_at) VALUES (gen_random_uuid(), 'bob@studio.it', "
                    "'Bob Marley', 'x', false, now(), now())"
                )
            )
            # 4. An admin with no card and no company request at all: a brand-new row,
            #    `cognome` left blank on purpose (decision (d)).
            connection.execute(
                text(
                    "INSERT INTO admin_users (id, email, nome, password_hash, attivo, "
                    "created_at, updated_at) VALUES (gen_random_uuid(), 'ivan@rebase.it', "
                    "'Ivan Fiore', 'x', true, now(), now())"
                )
            )
            # 5. A third admin, for the token-migration case below.
            carla_id = connection.execute(
                text(
                    "INSERT INTO admin_users (id, email, nome, password_hash, attivo, "
                    "created_at, updated_at) VALUES (gen_random_uuid(), 'carla@rebase.it', "
                    "'Carla Cattivi', 'x', true, now(), now()) RETURNING id"
                )
            ).scalar_one()
            raw_token = TOKEN_PREFIX + "a" * 40
            digest = hashlib.sha256(raw_token.encode()).hexdigest()
            connection.execute(
                text(
                    "INSERT INTO admin_tokens (id, admin_id, nome, token_hash, prefix, "
                    "created_at, updated_at) VALUES (gen_random_uuid(), :admin_id, "
                    "'Claude Code', :token_hash, :prefix, now(), now())"
                ),
                {"admin_id": carla_id, "token_hash": digest, "prefix": raw_token[:12]},
            )
            # 6. Two company requests from the same address, oldest first: only the
            #    oldest referente survives the `DISTINCT ON`.
            connection.execute(
                text(
                    "INSERT INTO companies (id, nome_azienda, referente, email, progetto, "
                    "periodo_da, durata, budget_giornaliero, stato, created_at, updated_at) "
                    "VALUES (gen_random_uuid(), 'ACME', 'Wile E. Coyote', 'acme@example.it', "
                    "'Un progetto', '2026-01-01', '3 mesi', '500', 'nuovo', "
                    "'2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO companies (id, nome_azienda, referente, email, progetto, "
                    "periodo_da, durata, budget_giornaliero, stato, created_at, updated_at) "
                    "VALUES (gen_random_uuid(), 'ACME', 'Road Runner', 'acme@example.it', "
                    "'Un altro progetto', '2026-02-01', '2 mesi', '600', 'nuovo', "
                    "'2026-02-01T00:00:00Z', '2026-02-01T00:00:00Z')"
                )
            )

        _upgrade(url, "0011")

        with engine.connect() as connection:
            rows = {
                row.email: row
                for row in connection.execute(
                    text("SELECT email, nome, cognome, role, attivo FROM users")
                ).mappings()
            }
            assert set(rows) == {
                "ada@studio.it",
                "bob@studio.it",
                "ivan@rebase.it",
                "carla@rebase.it",
                "acme@example.it",
            }

            ada = rows["ada@studio.it"]
            assert (ada["nome"], ada["cognome"], ada["role"], ada["attivo"]) == (
                "Ada",
                "Lovelace",
                "admin",
                True,
            ), "the freelancer's own two names survive; the admin_users row only promotes the role"

            bob = rows["bob@studio.it"]
            assert (bob["role"], bob["attivo"]) == (
                "member",
                True,
            ), "a deactivated admin who is also a freelancer stays a member, attivo=true"

            ivan = rows["ivan@rebase.it"]
            assert (ivan["nome"], ivan["cognome"], ivan["role"]) == (
                "Ivan Fiore",
                "",
                "admin",
            ), "an admin with no card at all: cognome blank on purpose (decision d)"

            acme = rows["acme@example.it"]
            assert (acme["nome"], acme["cognome"], acme["role"]) == (
                "Wile E. Coyote",
                "",
                "member",
            ), "DISTINCT ON picks the oldest company request, not the newest"

            # Every freelancer and every company got its user_id backfilled.
            linked = (
                connection.execute(
                    text(
                        "SELECT f.email = u.email AS matches FROM freelancers f "
                        "JOIN users u ON u.id = f.user_id"
                    )
                )
                .scalars()
                .all()
            )
            assert linked and all(linked)
            linked_companies = (
                connection.execute(
                    text(
                        "SELECT c.email = u.email AS matches FROM companies c "
                        "JOIN users u ON u.id = c.user_id"
                    )
                )
                .scalars()
                .all()
            )
            assert linked_companies and all(linked_companies)

            # The migrated token still resolves, through the new, users-backed path --
            # `AdminTokenService.resolve` reads through the `User` ORM model, which
            # always reflects the package's current head (REB-380 added a column after
            # 0011): brought the rest of the way there first, since this assertion is
            # about the `user_id` link migration 0011 wrote, not about any migration
            # after it. `connection`'s own still-open read transaction is committed
            # first: an `ALTER TABLE` later in the chain takes an exclusive lock that
            # would otherwise block forever on the `ACCESS SHARE` the open reads above
            # are still holding.
            connection.commit()
            _upgrade(url, "head")
            factory = session_factory(
                create_engine_from_settings(Settings(database_url=url, _env_file=None))  # type: ignore[call-arg]
            )
            session = factory()
            try:
                resolved = AdminTokenService(session).resolve(raw_token)
                assert resolved.email == "carla@rebase.it" and resolved.nome == "Carla Cattivi"
            finally:
                session.close()
        engine.dispose()
