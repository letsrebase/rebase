"""`pigrocrm resetpassword` read from a pipe, the way an operator without a tty runs it."""

import io
from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

import pigrocrm.core.cli as cli
from pigrocrm.core.actor import Actor
from pigrocrm.core.auth.passwords import verify_password
from pigrocrm.core.auth.schemas import UserCreate
from pigrocrm.core.auth.service import UserService


@pytest.fixture
def cli_engine(db_engine: Engine, monkeypatch: pytest.MonkeyPatch) -> Iterator[Engine]:
    """The CLI builds its own engine from settings; here it gets the test container's,
    and the rows it writes are removed afterwards (the CLI commits for real)."""
    monkeypatch.setattr(cli, "create_engine_from_settings", lambda settings: db_engine)
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
                email="cli-reset@studio.it", password="vecchia-password-1", nome="C", ruolo="admin"
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


def test_createadmin_gives_a_root_with_no_azienda_one_named_after_the_admin(
    cli_engine: Engine, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """REB-615: the root installation is not in the registry, so `ensure_defaults`
    never reaches it, and no route creates an azienda; the first admin brings the one
    row Impostazioni → Aziende needs, and a later admin leaves it alone."""

    def run(email: str, nome: str) -> int:
        monkeypatch.setattr("sys.stdin", io.StringIO("una-password-lunga-1\n"))
        monkeypatch.setattr(
            "sys.argv", ["pigrocrm", "createadmin", "--email", email, "--nome", nome]
        )
        return cli.main()

    # A root with no azienda is the case under test, and the worker's database carries
    # the one committed default every other world shares (REB-623): it is set aside
    # for the duration and put back at the end, with the identity it had.
    with cli_engine.begin() as connection:
        kept = connection.execute(
            text("select id, nome, ragione_sociale, nazione from emitter_profile where predefinita")
        ).all()
        connection.execute(text("delete from fiscal_profile"))
        connection.execute(text("delete from emitter_profile where predefinita"))
    with cli_engine.connect() as connection:
        before = set(connection.execute(text("select id from emitter_profile")).scalars())
    try:
        assert run("cli-root@studio.it", "Ada Lovelace") == 0
        out = capsys.readouterr().out
        assert "Creato amministratore cli-root@studio.it" in out
        assert "Creata l'azienda predefinita «Ada Lovelace»" in out
        with cli_engine.connect() as connection:
            rows = connection.execute(
                text("select nome, ragione_sociale, predefinita, attiva from emitter_profile")
            ).all()
        assert rows == [("Ada Lovelace", "Ada Lovelace", True, True)]

        assert run("cli-root-2@studio.it", "Grace Hopper") == 0
        assert "azienda" not in capsys.readouterr().out
        with cli_engine.connect() as connection:
            names = connection.execute(text("select nome from emitter_profile")).scalars().all()
        assert names == ["Ada Lovelace"]
    finally:
        with cli_engine.begin() as connection:
            added = [
                row_id
                for row_id in connection.execute(text("select id from emitter_profile")).scalars()
                if row_id not in before
            ]
            for row_id in added:
                connection.execute(
                    text("delete from emitter_profile where id = :id"), {"id": row_id}
                )
            for row in kept:
                connection.execute(
                    text(
                        "insert into emitter_profile (id, nome, ragione_sociale, nazione, "
                        "predefinita, attiva, created_at, updated_at) "
                        "values (:id, :nome, :rs, :nazione, true, true, now(), now())"
                    ),
                    {
                        "id": row.id,
                        "nome": row.nome,
                        "rs": row.ragione_sociale,
                        "nazione": row.nazione,
                    },
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
