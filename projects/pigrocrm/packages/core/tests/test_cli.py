"""`pigrocrm resetpassword` read from a pipe, the way an operator without a tty runs it."""

import io
from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session

import pigrocrm.core.cli as cli
from pigrocrm.core.actor import Actor
from pigrocrm.core.auth.passwords import verify_password
from pigrocrm.core.auth.schemas import UserCreate
from pigrocrm.core.auth.service import UserService
from pigrocrm.core.db.role import ensure_application_role


@pytest.fixture
def cli_engine(db_engine: Engine, monkeypatch: pytest.MonkeyPatch) -> Iterator[Engine]:
    """The CLI builds its own engine from settings; here it gets the test container's,
    and the rows it writes are removed afterwards (the CLI commits for real)."""
    monkeypatch.setattr(
        "pigrocrm.core.cli.admin.create_engine_from_settings", lambda settings: db_engine
    )
    yield db_engine
    with db_engine.begin() as connection:
        connection.execute(text("delete from activities where entity_type = 'user'"))
        connection.execute(text("delete from refresh_tokens"))
        connection.execute(text("delete from users where email like 'cli-%'"))


def test_resetpassword_reads_the_new_password_from_stdin_when_there_is_no_tty(
    cli_engine: Engine,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from pigrocrm.core.db import session_factory

    with session_factory(cli_engine)() as session:
        UserService(session).create(
            UserCreate(
                email="cli-reset@studio.it", password="vecchia-password-1", name="C", role="admin"
            ),
            Actor.system(),
        )
    monkeypatch.setattr("sys.stdin", io.StringIO("nuova-password-2026\n"))
    monkeypatch.setattr("sys.argv", ["pigrocrm", "resetpassword", "--email", "cli-reset@studio.it"])

    assert cli.main() == 0
    assert "Password aggiornata per cli-reset@studio.it" in capsys.readouterr().out
    with cli_engine.connect() as connection:
        stored = connection.execute(
            text("select password_hash from users where email = 'cli-reset@studio.it'")
        ).scalar_one()
    assert verify_password("nuova-password-2026", stored)


@pytest.fixture
def root_without_legal_entity(cli_engine: Engine) -> Iterator[set[object]]:
    """The root as `createadmin` finds a freshly migrated one: no legal entity. The worker's
    database carries the one committed default every other world shares (REB-623): it is
    set aside for the duration and put back at the end, with the identity it had. Yields
    the ids that exist while it is away, so the test's own rows can be told apart."""
    with cli_engine.begin() as connection:
        kept = connection.execute(
            text("select id, name, legal_name, country from legal_entities where is_default")
        ).all()
        connection.execute(text("delete from fiscal_profile"))
        connection.execute(text("delete from legal_entities where is_default"))
    with cli_engine.connect() as connection:
        before = set(connection.execute(text("select id from legal_entities")).scalars())
    try:
        yield before
    finally:
        with cli_engine.begin() as connection:
            added = [
                row_id
                for row_id in connection.execute(text("select id from legal_entities")).scalars()
                if row_id not in before
            ]
            for row_id in added:
                connection.execute(
                    text("delete from legal_entities where id = :id"), {"id": row_id}
                )
            for row in kept:
                connection.execute(
                    text(
                        "insert into legal_entities (id, name, legal_name, country, "
                        "is_default, active, created_at, updated_at) "
                        "values (:id, :name, :rs, :country, true, true, now(), now())"
                    ),
                    {
                        "id": row.id,
                        "name": row.name,
                        "rs": row.legal_name,
                        "country": row.country,
                    },
                )


def _createadmin(monkeypatch: pytest.MonkeyPatch, email: str, name: str) -> int:
    monkeypatch.setattr("sys.stdin", io.StringIO("una-password-lunga-1\n"))
    monkeypatch.setattr("sys.argv", ["pigrocrm", "createadmin", "--email", email, "--name", name])
    return cli.main()


def test_createadmin_gives_to_root_with_no_legal_entity_one_named_after_the_admin(
    cli_engine: Engine,
    root_without_legal_entity: set[object],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """REB-615: the root installation is not in the registry, so `ensure_defaults`
    never reaches it, and no route creates a legal entity; the first admin brings the one
    row Settings → Legal entities needs, and a later admin leaves it alone."""
    assert _createadmin(monkeypatch, "cli-root@studio.it", "Ada Lovelace") == 0
    out = capsys.readouterr().out
    assert "Creato amministratore cli-root@studio.it" in out
    assert "Creata l'azienda predefinita «Ada Lovelace»" in out
    with cli_engine.connect() as connection:
        rows = connection.execute(
            text("select name, legal_name, is_default, active from legal_entities")
        ).all()
    assert rows == [("Ada Lovelace", "Ada Lovelace", True, True)]

    assert _createadmin(monkeypatch, "cli-root-2@studio.it", "Grace Hopper") == 0
    assert "legal_entity" not in capsys.readouterr().out
    with cli_engine.connect() as connection:
        names = connection.execute(text("select name from legal_entities")).scalars().all()
    assert names == ["Ada Lovelace"]


def test_createadmin_works_as_the_application_role_the_api_container_connects_as(
    cli_engine: Engine,
    root_without_legal_entity: set[object],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """B-01. The documented production path runs the command in the `api` container,
    which connects as `pigrocrm_app` (`NOBYPASSRLS`). The policy on `legal_entities` reads
    the scope the session binds, and an unbound session saw no legal entity and was refused the
    insert after the admin row had already been committed. The other tests run as the
    superuser, to whom policies do not apply."""
    app_url = cli_engine.url.set(username="pigrocrm_app_cli_test", password="cli-test-secret")
    assert ensure_application_role(cli_engine.url, app_url)
    app_engine = create_engine(app_url, future=True)
    monkeypatch.setattr(
        "pigrocrm.core.cli.admin.create_engine_from_settings", lambda settings: app_engine
    )

    assert _createadmin(monkeypatch, "cli-app-role@studio.it", "Ada Lovelace") == 0

    assert "Creata l'azienda predefinita «Ada Lovelace»" in capsys.readouterr().out
    with cli_engine.connect() as connection:
        assert connection.execute(text("select name from legal_entities")).scalars().all() == [
            "Ada Lovelace"
        ]
        assert (
            connection.execute(
                text("select count(*) from users where email = 'cli-app-role@studio.it'")
            ).scalar_one()
            == 1
        )


def test_resetpassword_reports_a_short_password_without_a_traceback(
    cli_engine: Engine, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO("corta\n"))
    monkeypatch.setattr(
        "sys.argv", ["pigrocrm", "resetpassword", "--email", "cli-nessuno@studio.it"]
    )
    assert cli.main() == 1
    assert "almeno" in capsys.readouterr().err
