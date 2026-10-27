"""`pigrocrm reminders`: the pass from cron, against a real registry and real spaces.

Spec 2026-10-26 §9, REB-736. The command is the one piece of the reminders that knows there
is more than one space: it visits the root installation, walks the tenant registry, opens
each space's own database and hands `ReminderRun` one engine per space. So this file
provisions two spaces for real (`CREATE DATABASE`, the migrations to head, the first
admin), as `test_cli_digest.py` does, gives each an attività due today, and then runs
`cli.main(["reminders", ...])`.

The card's proof is in the first test: nobody calls the pass for a space, the command does,
and the space's owner has the reminder in their list afterwards with the time the pass put
it there.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import timedelta
from uuid import UUID

import pytest
from fakes.azienda_fixtures import committed_default_azienda
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

import pigrocrm.core.cli as cli
from pigrocrm.core.actor import Actor
from pigrocrm.core.attivita.models import Attivita
from pigrocrm.core.auth.models import User
from pigrocrm.core.config import Settings
from pigrocrm.core.db import session_factory, today_local
from pigrocrm.core.db.sidecar import create_database_if_missing, drop_database
from pigrocrm.core.reminders.run import REMINDERS_LOCK_NAMESPACE, ReminderOutcome, ReminderRun
from pigrocrm.core.reminders.service import ReminderService
from pigrocrm.core.tenants import TenantService, TenantSignup, ensure_tenants_database
from pigrocrm.core.tenants.database import tenant_database_name, tenant_database_url
from pigrocrm.core.tenants.service import migrate_to_head

UNO = "prova-promemoria-uno"
DUE = "prova-promemoria-due"
TITOLARI = {UNO: "uno@studio.it", DUE: "due@studio.it"}
SPAZI = (UNO, DUE)
ROOT_DB = "prova_promemoria_radice"
ROOT_OWNER = "titolare@radice.it"


@pytest.fixture(scope="module")
def settings(db_engine: Engine) -> Iterator[Settings]:
    """The environment the command reads: a root of this module's own, on the session's
    server, migrated to head and given one active admin (its titolare), with the registry
    and every space derived from that server as in production."""
    shared_url = db_engine.url.render_as_string(hide_password=False)
    root_url = make_url(shared_url).set(database=ROOT_DB)
    helper = Settings(database_url=shared_url, _env_file=None)  # type: ignore[call-arg]
    configured = Settings(
        database_url=root_url.render_as_string(hide_password=False),
        timezone="Europe/Rome",
        _env_file=None,  # type: ignore[call-arg]
    )
    create_database_if_missing(helper, root_url)
    try:
        migrate_to_head(configured, configured.database_url)
        with _engine(configured.database_url) as engine, session_factory(engine)() as session:
            session.add(User(email=ROOT_OWNER, nome="Titolare", ruolo="admin", attivo=True))
            committed_default_azienda(session, nome="Radice")
            session.commit()
        yield configured
    finally:
        drop_database(helper, root_url)


class _engine:
    """An engine for one block, disposed of after it."""

    def __init__(self, url: object) -> None:
        self.engine = create_engine(url, future=True)  # type: ignore[arg-type]

    def __enter__(self) -> Engine:
        return self.engine

    def __exit__(self, *exc: object) -> None:
        self.engine.dispose()


@pytest.fixture(scope="module")
def registry(settings: Settings) -> Iterator[Engine]:
    engine = ensure_tenants_database(settings)
    yield engine
    engine.dispose()


@pytest.fixture(scope="module")
def spazi(settings: Settings, registry: Engine) -> Iterator[None]:
    session: Session = session_factory(registry)()
    service = TenantService(session, settings)
    try:
        for slug in SPAZI:
            service.provision(TenantSignup(slug=slug, nome=f"Studio {slug}", email=TITOLARI[slug]))
        yield
    finally:
        for slug in SPAZI:
            drop_database(settings, tenant_database_url(settings, tenant_database_name(slug)))
        session.execute(
            text("delete from tenants where slug = any(:slugs)"), {"slugs": list(SPAZI)}
        )
        session.commit()
        session.close()


def _url(settings: Settings, slug: str | None) -> object:
    return (
        settings.database_url
        if slug is None
        else tenant_database_url(settings, tenant_database_name(slug))
    )


def _owner_id(settings: Settings, slug: str | None) -> UUID:
    email = ROOT_OWNER if slug is None else TITOLARI[slug]
    with _engine(_url(settings, slug)) as engine, engine.connect() as connection:
        return connection.execute(
            text("select id from users where email = :email"), {"email": email}
        ).scalar_one()


@pytest.fixture(autouse=True)
def _a_due_attivita_in_every_space(settings: Settings, spazi: None) -> Iterator[None]:
    """One attività due today, assigned to each installation's owner, and everything a pass
    writes undone afterwards so each test starts from a space nobody has reminded."""
    today = today_local(settings)
    for slug in (None, *SPAZI):
        owner = _owner_id(settings, slug)
        with _engine(_url(settings, slug)) as engine, session_factory(engine)() as session:
            session.add(Attivita(titolo="Mandare il preventivo", scadenza=today, assegnata_a=owner))
            session.commit()
    yield
    for slug in (None, *SPAZI):
        with _engine(_url(settings, slug)) as engine, engine.begin() as connection:
            for table in (
                "reminder_delivery",
                "reminder_ack",
                "reminder_snooze",
                "reminder_job_run",
                "attivita",
            ):
                connection.execute(text(f"delete from {table}"))  # noqa: S608 - fixed names


@pytest.fixture
def cron(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "get_settings", lambda: settings)


def _deliveries(settings: Settings, slug: str | None) -> int:
    with _engine(_url(settings, slug)) as engine, engine.connect() as connection:
        return connection.execute(text("select count(*) from reminder_delivery")).scalar_one()


def _clock(settings: Settings, slug: str | None) -> int:
    with _engine(_url(settings, slug)) as engine, engine.connect() as connection:
        return connection.execute(text("select count(*) from reminder_job_run")).scalar_one()


def _clock_row(settings: Settings, slug: str | None) -> tuple[bool, str | None] | None:
    """The space's `reminders` row as `(it has run, its failure)`, `None` for no row."""
    with _engine(_url(settings, slug)) as engine, engine.connect() as connection:
        row = connection.execute(
            text("select ran_at, failed_at, failure from reminder_job_run where job = 'reminders'")
        ).one_or_none()
    if row is None:
        return None
    assert (row.failed_at is None) == (row.failure is None)
    return row.ran_at is not None, row.failure


# -- the command -----------------------------------------------------------------------------


def test_the_cron_raises_a_reminder_in_every_space_on_its_own(
    settings: Settings, cron: None, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["reminders"]) == 0

    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == f"{cli.ROOT_LABEL}: promemoria 1, nuovi 1, chiusi 0"
    assert f"{UNO}: promemoria 1, nuovi 1, chiusi 0" in lines
    assert f"{DUE}: promemoria 1, nuovi 1, chiusi 0" in lines
    # Each space's owner has the reminder, with the time the pass put it in their list.
    for slug in SPAZI:
        owner = _owner_id(settings, slug)
        with _engine(_url(settings, slug)) as engine, session_factory(engine)() as session:
            listed = ReminderService(session, settings).list(
                Actor(id=owner, type="user", role="admin")
            )
        assert [(item.type, item.severity) for item in listed] == [("attivita_due", "warning")]
        assert listed[0].delivered_at is not None, "the pass wrote it down"
        assert _clock(settings, slug) == 1, "and the clock `job_late` reads"


def test_a_second_pass_delivers_nothing_again(
    settings: Settings, cron: None, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["reminders"]) == 0
    capsys.readouterr()

    assert cli.main(["reminders"]) == 0

    lines = capsys.readouterr().out.splitlines()
    assert f"{UNO}: promemoria 1, nuovi 0, chiusi 0" in lines
    assert _deliveries(settings, UNO) == 1, "one row, however many passes"
    assert _clock(settings, UNO) == 1


def test_a_rehearsal_says_what_would_go_and_writes_nothing(
    settings: Settings, cron: None, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["reminders", "--dry-run"]) == 0

    out = capsys.readouterr().out
    assert f"{UNO}: promemoria 1, nuovi 1, chiusi 0 (prova)" in out
    assert _deliveries(settings, UNO) == 0
    assert _clock(settings, UNO) == 0, "not even the clock: a rehearsal is not a pass"


def test_one_space_by_name_and_the_root_by_its_label(
    settings: Settings, cron: None, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["reminders", "--slug", UNO]) == 0
    out = capsys.readouterr().out
    assert out.splitlines() == [f"{UNO}: promemoria 1, nuovi 1, chiusi 0"]
    assert _deliveries(settings, DUE) == 0

    assert cli.main(["reminders", "--slug", "root"]) == 0
    assert capsys.readouterr().out.splitlines() == [
        f"{cli.ROOT_LABEL}: promemoria 1, nuovi 1, chiusi 0"
    ]


def test_a_slug_that_is_not_in_the_registry_is_said_and_fails(
    cron: None, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["reminders", "--slug", "non-esiste"]) == 1

    captured = capsys.readouterr()
    assert "non-esiste: non nel registro" in captured.err
    assert captured.out == ""


def test_another_day_is_rehearsed_as_that_day_and_writes_nothing(
    settings: Settings, cron: None, capsys: pytest.CaptureFixture[str]
) -> None:
    tomorrow = (today_local(settings) + timedelta(days=1)).isoformat()

    assert cli.main(["reminders", "--slug", UNO, "--data", tomorrow, "--dry-run"]) == 0

    # Due today is overdue by tomorrow: a `serious` where today it was a `warning`.
    assert capsys.readouterr().out.splitlines() == [
        f"{UNO}: promemoria 1, nuovi 1, chiusi 0 (prova)"
    ]
    with _engine(_url(settings, UNO)) as engine, engine.connect() as connection:
        assert connection.execute(text("select count(*) from reminder_delivery")).scalar_one() == 0


def test_another_day_is_refused_without_a_dry_run(
    settings: Settings, cron: None, capsys: pytest.CaptureFixture[str]
) -> None:
    tomorrow = (today_local(settings) + timedelta(days=1)).isoformat()

    assert cli.main(["reminders", "--slug", UNO, "--data", tomorrow]) == 2

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "--dry-run" in captured.err
    with _engine(_url(settings, UNO)) as engine, engine.connect() as connection:
        assert connection.execute(text("select count(*) from reminder_delivery")).scalar_one() == 0


def test_a_failing_space_is_one_line_with_the_exception_name_and_the_next_goes_on(
    settings: Settings,
    cron: None,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    original = ReminderRun.run_for_space

    def run(self, slug, engine, **kwargs):  # type: ignore[no-untyped-def]
        if slug == UNO:
            raise RuntimeError("postgresql://user:secret@host/db")
        return original(self, slug, engine, **kwargs)

    monkeypatch.setattr(ReminderRun, "run_for_space", run)

    assert cli.main(["reminders"]) == 1, "a line on stderr is the exit status the ping reads"

    captured = capsys.readouterr()
    assert captured.err.splitlines() == [f"{UNO}: saltato (RuntimeError)"]
    assert "secret" not in captured.err + captured.out, "the type, never the message"
    assert f"{DUE}: promemoria 1, nuovi 1, chiusi 0" in captured.out
    assert _deliveries(settings, DUE) == 1
    # REB-764: the failure is on the space's clock, as the type; the other space ran.
    assert _clock_row(settings, UNO) == (False, "RuntimeError")
    assert _clock_row(settings, DUE) == (True, None)

    # The next pass that works ends the failure.
    monkeypatch.setattr(ReminderRun, "run_for_space", original)
    assert cli.main(["reminders", "--slug", UNO]) == 0
    assert _clock_row(settings, UNO) == (True, None)


def test_a_space_behind_the_image_is_skipped_by_name_and_not_migrated(
    settings: Settings, cron: None, capsys: pytest.CaptureFixture[str]
) -> None:
    with _engine(_url(settings, UNO)) as engine, engine.begin() as connection:
        connection.execute(text("alter table reminder_job_run rename to reminder_job_run_away"))
    try:
        assert cli.main(["reminders", "--slug", UNO]) == 1
    finally:
        with _engine(_url(settings, UNO)) as engine, engine.begin() as connection:
            connection.execute(text("alter table reminder_job_run_away rename to reminder_job_run"))

    # One line: the clock could not hold the failure either, and that is the same fault.
    assert capsys.readouterr().err.splitlines() == [f"{UNO}: saltato (ProgrammingError)"]
    assert _deliveries(settings, UNO) == 0, "the failed pass wrote nothing"


def test_a_pass_that_finds_another_one_running_says_so_and_does_nothing(
    settings: Settings, cron: None, capsys: pytest.CaptureFixture[str]
) -> None:
    holder = create_engine(_url(settings, UNO), future=True)  # type: ignore[arg-type]
    with holder.connect() as connection:
        connection = connection.execution_options(isolation_level="AUTOCOMMIT")
        connection.execute(
            text("select pg_advisory_lock(:namespace, 1)"), {"namespace": REMINDERS_LOCK_NAMESPACE}
        )
        try:
            assert cli.main(["reminders", "--slug", UNO]) == 0, "the lock working is not a failure"
        finally:
            connection.execute(
                text("select pg_advisory_unlock(:namespace, 1)"),
                {"namespace": REMINDERS_LOCK_NAMESPACE},
            )
    holder.dispose()

    captured = capsys.readouterr()
    assert captured.out.splitlines() == [f"{UNO}: saltato (in_corso)"]
    assert captured.err == ""
    assert _deliveries(settings, UNO) == 0
    assert _clock(settings, UNO) == 0

    # The lock was released: the next pass gets it.
    assert cli.main(["reminders", "--slug", UNO]) == 0
    assert _deliveries(settings, UNO) == 1


def test_an_unreachable_registry_is_a_line_and_the_root_still_runs(
    settings: Settings,
    cron: None,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def broken(_settings: Settings) -> Engine:
        raise RuntimeError("down")

    monkeypatch.setattr("pigrocrm.core.tenants.ensure_tenants_database", broken)

    assert cli.main(["reminders"]) == 1

    captured = capsys.readouterr()
    assert "registro degli spazi non raggiungibile (RuntimeError)" in captured.err
    assert captured.out.splitlines() == [f"{cli.ROOT_LABEL}: promemoria 1, nuovi 1, chiusi 0"]


def test_the_reminder_arguments_reject_a_date_that_is_not_one() -> None:
    with pytest.raises(SystemExit) as raised:
        cli.main(["reminders", "--data", "domani"])

    assert raised.value.code == 2


# -- what the other channels sent, and the key pair ------------------------------------------


def test_the_line_tells_what_the_other_channels_sent_only_when_they_sent(
    capsys: pytest.CaptureFixture[str],
) -> None:
    cli._stampa_promemoria(
        ReminderOutcome("acme", "eseguito", raised=3, delivered=2), dry_run=False
    )
    cli._stampa_promemoria(
        ReminderOutcome("acme", "eseguito", raised=3, delivered=2, pushes=2, emails=1),
        dry_run=False,
    )
    cli._stampa_promemoria(
        ReminderOutcome("acme", "eseguito", raised=3, delivered=2, emails=1), dry_run=True
    )

    assert capsys.readouterr().out.splitlines() == [
        "acme: promemoria 3, nuovi 2, chiusi 0",
        "acme: promemoria 3, nuovi 2, chiusi 0, push 2, email 1",
        "acme: promemoria 3, nuovi 2, chiusi 0, email 1 (prova)",
    ]


def test_vapid_keys_prints_a_private_key_the_application_can_use(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from pigrocrm.core.reminders.push import vapid_public_key

    assert cli.main(["vapid-keys"]) == 0

    private_line, public_line = capsys.readouterr().out.splitlines()
    assert private_line.startswith("PIGROCRM_VAPID_PRIVATE_KEY=")
    private = private_line.split("=", 1)[1]
    assert public_line.endswith(vapid_public_key(private)), "the public key printed is its own"
