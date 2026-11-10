"""`pigrocrm automations`: the daily pass from cron, against a real registry and real spaces.

REB-727. The command is the one piece of the pass that knows there is more than one space: it
visits the root installation, walks the tenant registry, opens each space's own database and
hands `AutomationPass` a session per space. So this file provisions two spaces for real, as
`test_cli_reminders.py` does, gives the root and each space one customer with an invoice
past its due date, and runs `cli.main(["automations", ...])`.

The card's proof is the second test: run twice, the second run opens nothing.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import timedelta
from decimal import Decimal

import pytest
from fakes.legal_entity_fixtures import committed_default_legal_entity
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

import pigrocrm.core.cli as cli
from pigrocrm.core.automations.daily import AutomationPass
from pigrocrm.core.config import Settings
from pigrocrm.core.db import session_factory, today_local
from pigrocrm.core.db.sidecar import create_database_if_missing, drop_database
from pigrocrm.core.invoices.models import Invoice
from pigrocrm.core.organizations.models import Organization
from pigrocrm.core.tasks.models import Task
from pigrocrm.core.tenants import TenantService, TenantSignup, ensure_tenants_database
from pigrocrm.core.tenants.database import tenant_database_name, tenant_database_url
from pigrocrm.core.tenants.service import migrate_to_head

FIRST_SPACE = "prova-automazioni-uno"
SECOND_SPACE = "prova-automazioni-due"
SPACES = (FIRST_SPACE, SECOND_SPACE)
ROOT_DB = "prova_automazioni_radice"


class _engine:
    def __init__(self, url: object) -> None:
        self.engine = create_engine(url, future=True)  # type: ignore[arg-type]

    def __enter__(self) -> Engine:
        return self.engine

    def __exit__(self, *exc: object) -> None:
        self.engine.dispose()


@pytest.fixture(scope="module")
def settings(db_engine: Engine) -> Iterator[Settings]:
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
            committed_default_legal_entity(session, name="Radice")
            session.commit()
        yield configured
    finally:
        drop_database(helper, root_url)


@pytest.fixture(scope="module")
def registry(settings: Settings) -> Iterator[Engine]:
    engine = ensure_tenants_database(settings)
    yield engine
    engine.dispose()


@pytest.fixture(scope="module")
def spaces(settings: Settings, registry: Engine) -> Iterator[None]:
    session: Session = session_factory(registry)()
    service = TenantService(session, settings)
    try:
        for slug in SPACES:
            service.provision(
                TenantSignup(
                    first_name="Ada", slug=slug, name=f"Studio {slug}", email=f"{slug}@studio.it"
                )
            )
        yield
    finally:
        for slug in SPACES:
            drop_database(settings, tenant_database_url(settings, tenant_database_name(slug)))
        session.execute(
            text("delete from tenants where slug = any(:slugs)"), {"slugs": list(SPACES)}
        )
        session.commit()
        session.close()


def _url(settings: Settings, slug: str | None) -> object:
    return (
        settings.database_url
        if slug is None
        else tenant_database_url(settings, tenant_database_name(slug))
    )


@pytest.fixture(autouse=True)
def _an_overdue_invoice_in_every_space(settings: Settings, spaces: None) -> Iterator[None]:
    """One issued, unpaid invoice a few days late in the root and in each space, and what a
    pass wrote undone afterwards so every test starts from spaces nobody has run."""
    today = today_local(settings)
    for slug in (None, *SPACES):
        with _engine(_url(settings, slug)) as engine, session_factory(engine)() as session:
            customer = Organization(legal_name="Acme S.r.l.", country="IT", custom_fields={})
            session.add(customer)
            session.flush()
            session.add(
                Invoice(
                    customer_id=customer.id,
                    type="invoice",
                    status="issued",
                    payment_status="unpaid",
                    year=2026,
                    number=1,
                    taxable_amount=Decimal("100.00"),
                    vat_amount=Decimal("22.00"),
                    stamp_duty=Decimal("0.00"),
                    total=Decimal("122.00"),
                    issue_date=today - timedelta(days=40),
                    due_date=today - timedelta(days=5),
                )
            )
            session.commit()
    yield
    for slug in (None, *SPACES):
        with _engine(_url(settings, slug)) as engine, engine.begin() as connection:
            connection.execute(text("delete from activities where entity_type = 'task'"))
            connection.execute(text("delete from tasks"))
            connection.execute(text("delete from automation_config"))
            connection.execute(text("delete from reminder_job_run"))
            # The spaces are migrated databases, which the test template's trigger does not
            # reach: the invoice's due item goes first.
            connection.execute(text("delete from due_item_settlements"))
            connection.execute(text("delete from due_items"))
            connection.execute(text("delete from invoices"))
            connection.execute(text("delete from organizations"))


@pytest.fixture(autouse=True)
def _the_environment(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "get_settings", lambda: settings)


def _count(settings: Settings, slug: str | None) -> int:
    with _engine(_url(settings, slug)) as engine, session_factory(engine)() as session:
        return len(session.query(Task).filter(Task.rule == "overdue_invoice").all())


def _clock(settings: Settings, slug: str | None) -> tuple[bool, str | None] | None:
    """The space's `automations` row as `(it has run, its failure)`, `None` for no row."""
    with _engine(_url(settings, slug)) as engine, engine.connect() as connection:
        row = connection.execute(
            text(
                "select ran_at, failed_at, failure from reminder_job_run where job = 'automations'"
            )
        ).one_or_none()
    if row is None:
        return None
    assert (row.failed_at is None) == (row.failure is None)
    return row.ran_at is not None, row.failure


def test_the_command_opens_one_task_in_the_root_and_in_every_space(
    settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["automations"]) == 0

    out = capsys.readouterr().out
    for label in ("root", *SPACES):
        assert f"{label}: overdue_invoice opened 1, already open 0" in out
        assert f"{label}: stalled_deal opened 0, already open 0" in out
        assert _count(settings, None if label == "root" else label) == 1


def test_a_run_moves_the_clock_of_every_space_it_walked(
    settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    """REB-764: the row the Settings → Runs page and `job_late` read."""
    assert cli.main(["automations"]) == 0

    for slug in (None, *SPACES):
        assert _clock(settings, slug) == (True, None), slug


def test_a_rehearsal_leaves_the_clock_alone(
    settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["automations", "--dry-run"]) == 0

    for slug in (None, *SPACES):
        assert _clock(settings, slug) is None, slug


def test_a_failing_space_records_its_failure_the_others_their_success_and_a_rerun_clears_it(
    settings: Settings, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    original = AutomationPass.run

    def run(self, **kwargs):  # type: ignore[no-untyped-def]
        if self.session.get_bind().url.database == tenant_database_name(FIRST_SPACE):
            raise RuntimeError("postgresql://user:secret@host/db")
        return original(self, **kwargs)

    monkeypatch.setattr(AutomationPass, "run", run)
    assert cli.main(["automations"]) == 1

    assert _clock(settings, FIRST_SPACE) == (False, "RuntimeError"), "the type, never the message"
    assert _clock(settings, SECOND_SPACE) == (True, None)
    assert "secret" not in capsys.readouterr().err

    monkeypatch.setattr(AutomationPass, "run", original)
    assert cli.main(["automations"]) == 0
    assert _clock(settings, FIRST_SPACE) == (True, None)


def test_a_clock_that_cannot_be_written_is_a_line_and_an_exit_status(
    settings: Settings, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from pigrocrm.core.reminders.jobs import JobClock

    def refuse(self, job, at=None):  # type: ignore[no-untyped-def]
        raise RuntimeError("postgresql://user:secret@host/db")

    monkeypatch.setattr(JobClock, "succeeded", refuse)

    assert cli.main(["automations", "--slug", FIRST_SPACE]) == 1, (
        "the ping should not say it is fine"
    )

    captured = capsys.readouterr()
    assert captured.err.splitlines() == [f"{FIRST_SPACE}: clock not written (RuntimeError)"]
    assert "secret" not in captured.err
    assert f"{FIRST_SPACE}: overdue_invoice opened 1" in captured.out, "the pass itself worked"
    assert _clock(settings, FIRST_SPACE) is None


def test_the_second_run_opens_nothing_new(
    settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["automations"]) == 0
    capsys.readouterr()
    assert cli.main(["automations"]) == 0

    out = capsys.readouterr().out
    for label in ("root", *SPACES):
        assert f"{label}: overdue_invoice opened 0, already open 1" in out
        assert _count(settings, None if label == "root" else label) == 1


def test_a_rehearsal_says_what_it_would_open_and_writes_nothing(
    settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["automations", "--dry-run", "--slug", FIRST_SPACE]) == 0

    out = capsys.readouterr().out
    assert f"{FIRST_SPACE}: overdue_invoice opened 1, already open 0 (dry run)" in out
    assert "root:" not in out and SECOND_SPACE not in out
    assert _count(settings, FIRST_SPACE) == 0


def test_a_switched_off_rule_is_reported_and_opens_nothing(
    settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    with _engine(_url(settings, FIRST_SPACE)) as engine, engine.begin() as connection:
        connection.execute(
            text(
                "insert into automation_config (id, a5_overdue_invoice) "
                "values (gen_random_uuid(), false)"
            )
        )

    assert cli.main(["automations", "--slug", FIRST_SPACE]) == 0

    assert f"{FIRST_SPACE}: overdue_invoice disabled" in capsys.readouterr().out
    assert _count(settings, FIRST_SPACE) == 0


def test_another_day_is_only_a_rehearsal(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert cli.main(["automations", "--date", "2030-01-01"]) == 2
    assert "--date changes the day" in capsys.readouterr().err


def test_an_unknown_slug_is_an_error(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["automations", "--slug", "non-esiste"]) == 1
    assert "non-esiste: not in the registry" in capsys.readouterr().err
