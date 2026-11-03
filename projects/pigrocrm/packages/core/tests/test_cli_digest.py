"""`pigrocrm digest`: the Monday cron, against a real registry and real spaces.

Spec 2026-09-16 §3.4. The command is the only piece of the weekly report that knows
there is more than one space: it visits the root installation, walks the tenant registry,
opens each space's own database and hands `DigestRun` one session per space. So this file
provisions two spaces for real -- `CREATE DATABASE`, the repository's migrations to head,
the first admin -- exactly as `test_tenants.py` does, gives each of them one customer and
one invoice issued in the week that has just closed, and then runs
`cli.main(["digest", ...])`.

The root is a database of this module's own too, migrated the same way, rather than the
session's shared one (REB-263): the command now reports on whatever the root holds, and
the shared database holds whatever the other tests on this worker left in it.

Nothing is asserted against a mock. The sender is a `RecordingSender` holding the real
`Mail` objects the run handed it, and what a space remembers of its week is read back out
of its `digests` table in a connection of the test's own.

The two spaces are provisioned once for the module (a provisioning is a `CREATE DATABASE`
plus thirty-five migrations, and five tests do not need five of them); what a run *writes*
is undone after every test, so each one starts from a space that has never been mailed.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from fakes.legal_entity_fixtures import committed_default_legal_entity
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

import pigrocrm.core.cli as cli
import pigrocrm.core.cli.cron as cli_cron
from pigrocrm.core import telemetry
from pigrocrm.core.auth.models import User
from pigrocrm.core.config import Settings
from pigrocrm.core.customers.models import Customer
from pigrocrm.core.db import session_factory, today_local
from pigrocrm.core.db.sidecar import create_database_if_missing, drop_database
from pigrocrm.core.digest.run import SEND_REJECTED, DigestOutcome, DigestRun
from pigrocrm.core.digest.service import iso_week
from pigrocrm.core.invoices.models import Invoice
from pigrocrm.core.mail import Mail, RecordingSender
from pigrocrm.core.reminders.run import ROOT_LABEL
from pigrocrm.core.tenants import (
    TenantService,
    TenantSignup,
    ensure_tenants_database,
)
from pigrocrm.core.tenants.database import tenant_database_name, tenant_database_url
from pigrocrm.core.tenants.service import migrate_to_head

PUBLIC_URL = "https://crm.example.it"
FIRST_SPACE = "prova-digest-uno"
SECOND_SPACE = "prova-digest-due"
# Provisioned like the others and left as it is born: nobody has entered a customer, a
# deal, an invoice or an hour, which is the one space that hears nothing (§2).
EMPTY = "prova-digest-vuoto"
OWNERS = {FIRST_SPACE: "uno@studio.it", SECOND_SPACE: "due@studio.it"}
SPACES = (FIRST_SPACE, SECOND_SPACE, EMPTY)
OWNER_EMPTY = "vuoto@studio.it"
# The root installation: its own database, and `PIGROCRM_ROOT_SLUG` in its links.
ROOT_DB = "prova_digest_radice"
ROOT_SLUG = "prova-digest-radice"
# Created first and switched off since, so "the first admin" alone would be the wrong one.
FORMER_ADMIN = "ex@radice.it"
ROOT_OWNER = "titolare@radice.it"
ROOT_COLLEAGUE = "collega@radice.it"


# --- the container, the registry, and three spaces in it -----------------------------


@pytest.fixture(scope="module")
def settings(db_engine: Engine) -> Iterator[Settings]:
    """The environment the command reads. `public_url` is the root's: each space's own is
    that plus its slug, and that is the difference the mails are checked for.

    `database_url` is a root of this module's own, on the session's server, migrated to
    head like a space and given three users and one invoice in the week (`_seed_root`). The
    registry and every space are derived from that URL's server, as in production."""
    shared_url = db_engine.url.render_as_string(hide_password=False)
    root_url = make_url(shared_url).set(database=ROOT_DB)
    helper = Settings(database_url=shared_url, _env_file=None)  # type: ignore[call-arg]
    configured = Settings(
        database_url=root_url.render_as_string(hide_password=False),
        public_url=PUBLIC_URL,
        root_slug=ROOT_SLUG,
        timezone="Europe/Rome",
        _env_file=None,  # type: ignore[call-arg]
    )
    create_database_if_missing(helper, root_url)
    try:
        migrate_to_head(configured, configured.database_url)
        _seed_root(configured)
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
    """Three provisioned spaces: two with something to report, one just born."""
    session: Session = session_factory(registry)()
    service = TenantService(session, settings)
    try:
        for slug in SPACES:
            service.provision(
                TenantSignup(
                    slug=slug,
                    name=f"Studio {slug}",
                    email=OWNERS.get(slug, OWNER_EMPTY),
                )
            )
            if slug in OWNERS:
                _one_invoice(settings, slug, _last_week(settings))
        yield
    finally:
        for slug in SPACES:
            _drop(settings, slug)
        session.execute(
            text("delete from tenants where slug = any(:slugs)"), {"slugs": list(SPACES)}
        )
        session.commit()
        session.close()


@pytest.fixture(autouse=True)
def _none_week_sent(settings: Settings, spaces: None) -> Iterator[None]:
    """What a run writes, undone. The `digests` row is what makes the second Monday say
    «già inviato», so a test that left one behind would decide the next one's answer."""
    yield
    for engine in [_space_engine(settings, slug) for slug in SPACES] + [_root_engine(settings)]:
        try:
            with engine.begin() as connection:
                connection.execute(text("delete from activities where entity_type = 'digest'"))
                connection.execute(text("delete from digests"))
                connection.execute(text("delete from reminder_job_run"))
        finally:
            engine.dispose()


@pytest.fixture
def cron(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> RecordingSender:
    """The command as the container runs it, except for the two things that leave the
    machine: Resend becomes a sender that keeps the mails, PostHog is absent -- which is
    the ordinary state of an installation with no key, not a special case."""
    sender = RecordingSender()
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    monkeypatch.setattr(cli_cron, "sender_from_settings", lambda _settings: sender)
    monkeypatch.setattr(cli_cron, "tracker_from_settings", lambda _settings: None)
    return sender


# --- the corpus, and reading a space back --------------------------------------------


def _last_week(settings: Settings) -> tuple[date, date]:
    """The week that has just closed, computed here and not through `previous_week`: a
    fixture that derived its window from a function under test would move with it."""
    today = today_local(settings)
    monday = today - timedelta(days=today.isoweekday() - 1 + 7)
    return monday, monday + timedelta(days=6)


def _space_engine(settings: Settings, slug: str) -> Engine:
    return create_engine(tenant_database_url(settings, tenant_database_name(slug)), future=True)


def _root_engine(settings: Settings) -> Engine:
    return create_engine(settings.database_url, future=True)


def _seed_root(settings: Settings) -> None:
    """The root as `createadmin` and a year of use leave it: an admin who has since been
    switched off, created first; the titolare, the first admin still active; a
    collaborator; and one invoice in the week."""
    monday = _last_week(settings)[0]
    engine = _root_engine(settings)
    try:
        with session_factory(engine)() as session:
            for days, email, role, active in (
                (300, FORMER_ADMIN, "admin", False),
                (200, ROOT_OWNER, "admin", True),
                (100, ROOT_COLLEAGUE, "collaborator", True),
            ):
                session.add(
                    User(
                        email=email,
                        name=email.split("@")[0],
                        role=role,
                        active=active,
                        created_at=datetime.combine(
                            monday - timedelta(days=days), datetime.min.time(), UTC
                        ),
                    )
                )
            # And the azienda `createadmin` writes with the first admin (REB-615), which
            # the root's one invoice below belongs to (REB-619).
            committed_default_legal_entity(session, name="Radice")
            session.commit()
    finally:
        engine.dispose()
    _write_invoice(_root_engine(settings), _last_week(settings))


def _one_invoice(settings: Settings, slug: str, week: tuple[date, date]) -> None:
    _write_invoice(_space_engine(settings, slug), week)


def _write_invoice(engine: Engine, week: tuple[date, date]) -> None:
    """One customer and one invoice issued inside the week: enough that the space is not
    empty (§2) and that the report has a section in it. Disposes of `engine`."""
    date_from, date_to = week
    try:
        with session_factory(engine)() as session:
            customer = Customer(legal_name="Cliente Uno", country="IT", custom_fields={})
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
                    taxable_amount=Decimal("1000.00"),
                    vat_amount=Decimal("0.00"),
                    stamp_duty=Decimal("0.00"),
                    total=Decimal("1000.00"),
                    issue_date=date_from + timedelta(days=1),
                    due_date=date_to + timedelta(days=120),
                    payment_date=None,
                    sdi_document_type="TD01",
                    currency="EUR",
                    custom_fields={},
                )
            )
            session.commit()
    finally:
        engine.dispose()


def _weeks(settings: Settings, slug: str | None) -> list[str]:
    """What the space remembers of the weeks it was mailed; `None` is the root."""
    engine = _space_engine(settings, slug) if slug is not None else _root_engine(settings)
    try:
        with engine.connect() as connection:
            return list(
                connection.execute(text("select week from digests order by week")).scalars().all()
            )
    finally:
        engine.dispose()


def _clock(settings: Settings, slug: str | None) -> tuple[bool, str | None] | None:
    """The space's `digest` row as `(it has run, its failure)`, `None` for no row: what
    the Settings → Runs page and `job_late` read."""
    engine = _space_engine(settings, slug) if slug is not None else _root_engine(settings)
    try:
        with engine.connect() as connection:
            row = connection.execute(
                text("select ran_at, failed_at, failure from reminder_job_run where job = 'digest'")
            ).one_or_none()
    finally:
        engine.dispose()
    if row is None:
        return None
    assert (row.failed_at is None) == (row.failure is None)
    return row.ran_at is not None, row.failure


def _my(sender: RecordingSender) -> list[Mail]:
    """Only the mails addressed to this file's two owners: the registry is shared with
    whatever else has provisioned a space on this container."""
    return [mail for mail in sender.sent if mail.to in set(OWNERS.values())]


# --- the command ---------------------------------------------------------------------


def test_the_cron_mails_every_space_its_week_and_prints_one_line_each(
    settings: Settings, cron: RecordingSender, capsys: pytest.CaptureFixture[str]
) -> None:
    iso = iso_week(_last_week(settings)[0])

    assert cli.main(["digest"]) == 0

    out = capsys.readouterr().out
    assert f"{FIRST_SPACE}: sent to 1" in out
    assert f"{SECOND_SPACE}: sent to 1" in out
    # The space nobody has started using hears nothing, and says so in the same log: a
    # report where every section is empty is the one mail §2 exists to prevent.
    assert f"{EMPTY}: empty" in out
    assert OWNER_EMPTY not in {mail.to for mail in cron.sent}
    assert sorted(mail.to for mail in _my(cron)) == sorted(OWNERS.values())
    # Each space's links are the root's public URL plus its own slug: `space_base_settings`
    # per space, and not the root's for everybody.
    for slug, address in OWNERS.items():
        mail = next(mail for mail in _my(cron) if mail.to == address)
        assert f"{PUBLIC_URL}/{slug}/app" in mail.text
        assert _weeks(settings, slug) == [iso]
    # The root installation is not in the registry and is visited all the same, first.
    assert out.splitlines()[0] == f"{ROOT_LABEL}: sent to 2"
    assert _weeks(settings, None) == [iso]


@pytest.mark.parametrize(
    ("root_slug", "base"),
    [
        (ROOT_SLUG, f"{PUBLIC_URL}/{ROOT_SLUG}/app"),
        # No root slug: the root answers without a prefix, and `root` still names it.
        ("", f"{PUBLIC_URL}/app"),
    ],
)
def test_the_root_installation_gets_its_week_like_a_space(
    settings: Settings,
    cron: RecordingSender,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    root_slug: str,
    base: str,
) -> None:
    """REB-263: the root holds the owner's real data and has no registry row, so the
    walk alone never mailed it. Its database is `database_url`, its owner the first
    admin still active, its links carry `PIGROCRM_ROOT_SLUG` like a space's carry its
    slug, and `--slug root` visits it alone, without the registry."""

    def no_registry(_settings: Settings) -> Engine:
        raise AssertionError("the root is not in the registry")

    monkeypatch.setattr(
        cli, "get_settings", lambda: settings.model_copy(update={"root_slug": root_slug})
    )
    monkeypatch.setattr("pigrocrm.core.cli.spaces_walk.ensure_tenants_database", no_registry)
    iso = iso_week(_last_week(settings)[0])

    assert cli.main(["digest", "--slug", ROOT_LABEL]) == 0

    captured = capsys.readouterr()
    assert captured.out.strip() == f"{ROOT_LABEL}: sent to 2"
    assert captured.err == ""
    root_mails = [mail for mail in cron.sent if mail.to.endswith("@radice.it")]
    # Every active user who kept it on, as in a space; never the one switched off.
    assert sorted(mail.to for mail in root_mails) == [ROOT_COLLEAGUE, ROOT_OWNER]
    assert all(f"{base}/" in mail.text for mail in root_mails)
    assert all(f"{PUBLIC_URL}//" not in mail.text for mail in root_mails)
    assert _my(cron) == []
    assert _weeks(settings, None) == [iso]
    # The week was read and recorded as the titolare: the first admin still active, not
    # the older one who was switched off.
    engine = _root_engine(settings)
    try:
        with engine.connect() as connection:
            actors = connection.execute(
                text(
                    "select u.email from activities a join users u on u.id = a.actor_id "
                    "where a.entity_type = 'digest'"
                )
            ).scalars()
            assert list(actors) == [ROOT_OWNER]
    finally:
        engine.dispose()


def test_a_week_already_sent_is_not_sent_a_second_time(
    settings: Settings, cron: RecordingSender, capsys: pytest.CaptureFixture[str]
) -> None:
    """Two crons on the same Monday -- a retry, a rerun by hand -- are one mail. The
    `digests` row is unique on the week, and the second run says which week it is skipping
    so the log answers «did last week go out» without opening the database."""
    iso = iso_week(_last_week(settings)[0])
    assert cli.main(["digest", "--slug", FIRST_SPACE]) == 0
    capsys.readouterr()

    assert cli.main(["digest", "--slug", FIRST_SPACE]) == 0

    assert capsys.readouterr().out.strip() == f"{FIRST_SPACE}: already sent for {iso}"
    assert len(_my(cron)) == 1


def test_a_rehearsal_says_what_would_go_out_and_sends_nothing(
    settings: Settings, cron: RecordingSender, capsys: pytest.CaptureFixture[str]
) -> None:
    """`--dry-run` is how the cron line is verified by hand on the day it is installed.
    It must be distinguishable in the log from the real thing, or the rehearsal reads as
    the week having gone out."""
    assert cli.main(["digest", "--slug", FIRST_SPACE, "--dry-run"]) == 0

    assert capsys.readouterr().out.strip() == f"{FIRST_SPACE}: sent to 1 (dry run)"
    assert _my(cron) == []
    assert _weeks(settings, FIRST_SPACE) == []


def test_slug_visits_that_space_and_leaves_the_others_alone(
    settings: Settings, cron: RecordingSender, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["digest", "--slug", SECOND_SPACE]) == 0

    out = capsys.readouterr().out
    assert f"{SECOND_SPACE}: sent to 1" in out
    assert FIRST_SPACE not in out
    assert [mail.to for mail in _my(cron)] == [OWNERS[SECOND_SPACE]]
    assert _weeks(settings, FIRST_SPACE) == []


def test_a_slug_that_is_not_in_the_registry_is_one_line_and_not_a_failure(
    cron: RecordingSender, capsys: pytest.CaptureFixture[str]
) -> None:
    """A typo in a cron line must not look like a week that went out, and must not take
    the exit status of the run with it: the other spaces are mailed by other lines."""
    assert cli.main(["digest", "--slug", "prova-digest-mai-esistito"]) == 0

    captured = capsys.readouterr()
    assert captured.err.strip() == "prova-digest-mai-esistito: not in the registry"
    assert captured.out == ""
    assert _my(cron) == []


def test_the_root_slug_is_not_a_way_to_name_the_root(
    cron: RecordingSender, capsys: pytest.CaptureFixture[str]
) -> None:
    """`--slug` with `PIGROCRM_ROOT_SLUG` goes on meaning a registry row: one created before
    the root took that name would otherwise be shadowed, and a `--force` meant for it would
    resend the root's week instead. Only `root` names the root."""
    assert cli.main(["digest", "--slug", ROOT_SLUG]) == 0

    captured = capsys.readouterr()
    assert captured.err.strip() == f"{ROOT_SLUG}: not in the registry"
    assert captured.out == ""
    assert cron.sent == []


def test_a_space_where_everybody_switched_it_off_is_said_and_not_sent(
    settings: Settings, cron: RecordingSender, capsys: pytest.CaptureFixture[str]
) -> None:
    """Switching the report off is a decision the CRM already carries
    (`users.weekly_digest`), and the cron reports it rather than obeying it in
    silence: a space that receives nothing for weeks has to be distinguishable in the log
    from a space that is not being visited at all."""
    engine = _space_engine(settings, FIRST_SPACE)
    try:
        with engine.begin() as connection:
            connection.execute(text("update users set weekly_digest = false"))
        assert cli.main(["digest", "--slug", FIRST_SPACE]) == 0

        assert capsys.readouterr().out.strip() == f"{FIRST_SPACE}: no recipient"
        assert _my(cron) == []
        assert _weeks(settings, FIRST_SPACE) == []
    finally:
        with engine.begin() as connection:
            connection.execute(text("update users set weekly_digest = true"))
        engine.dispose()


def test_force_with_a_day_of_that_week_sends_it_again(
    settings: Settings, cron: RecordingSender, capsys: pytest.CaptureFixture[str]
) -> None:
    """How a week is resent: `--date` names any day of it and `--force` says the row that
    is already there is not an answer. The row moves, it is not duplicated."""
    monday = _last_week(settings)[0]
    iso = iso_week(monday)
    assert cli.main(["digest", "--slug", FIRST_SPACE]) == 0
    capsys.readouterr()

    assert cli.main(["digest", "--slug", FIRST_SPACE, "--force", "--date", monday.isoformat()]) == 0

    assert capsys.readouterr().out.strip() == f"{FIRST_SPACE}: sent to 1"
    assert [mail.to for mail in _my(cron)] == [OWNERS[FIRST_SPACE], OWNERS[FIRST_SPACE]]
    assert _weeks(settings, FIRST_SPACE) == [iso]


def test_a_space_whose_database_is_gone_does_not_take_the_others_with_it(
    settings: Settings,
    cron: RecordingSender,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The contract the whole command is built around: the spaces are independent.

    A space whose database cannot be opened -- dropped, moved, never created -- is one
    `skipped` line on stderr naming the exception's type, and the spaces beside it are
    mailed in the same run. `tenant_database_url` is patched on the module `digest`
    imports it from, because that is where the name is resolved at call time; this file's
    own `_space_engine` holds the real function and goes on reaching the real databases, which
    is what lets the fixtures clean up after this test.
    """
    import pigrocrm.core.cli.spaces_walk as tenants_database

    real = tenants_database.tenant_database_url
    monkeypatch.setattr(
        tenants_database,
        "tenant_database_url",
        lambda settings, db_name: real(
            settings,
            "pigrocrm_space_never_created"
            if db_name == tenant_database_name(SECOND_SPACE)
            else db_name,
        ),
    )

    assert cli.main(["digest"]) == 0

    captured = capsys.readouterr()
    assert f"{FIRST_SPACE}: sent to 1" in captured.out
    assert SECOND_SPACE not in captured.out
    # The type and never the text: a psycopg error carries the connection's URL.
    assert captured.err.strip() == f"{SECOND_SPACE}: skipped (OperationalError)"
    assert [mail.to for mail in _my(cron)] == [OWNERS[FIRST_SPACE]]
    assert _weeks(settings, SECOND_SPACE) == []


def test_a_client_that_cannot_be_built_is_one_line_and_still_shuts_the_sdk_down(
    cron: RecordingSender,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """PostHog's client is built once per run, and building it can fail -- a malformed
    key, a host it refuses. Built outside the `try` it would be a traceback in a cron log
    and an SDK left running; inside it, it is a line, an exit 0, and a `shutdown()` that
    happens anyway, which is the whole reason that `finally` exists.
    """
    closed: list[str] = []

    def explode(_settings: Settings) -> None:
        raise RuntimeError("chiave storta")

    monkeypatch.setattr(cli_cron, "tracker_from_settings", explode)
    monkeypatch.setattr(telemetry, "shutdown", lambda: closed.append("shutdown"))

    assert cli.main(["digest", "--slug", FIRST_SPACE]) == 0

    captured = capsys.readouterr()
    assert captured.err.strip() == "sending cannot be configured (RuntimeError)"
    assert captured.out == ""
    assert closed == ["shutdown"]
    assert _my(cron) == []


def test_an_unreachable_registry_is_one_line_on_stderr_and_still_exits_zero(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The same contract `ensure-space-defaults` has: this runs from cron, and a registry
    that cannot be reached is a line an operator can act on, never a traceback -- and
    never the URL, which carries the password."""
    sender = RecordingSender()
    broken = Settings(
        database_url="postgresql+psycopg://utente:segreta@127.0.0.1:1/nessuno",
        _env_file=None,  # type: ignore[call-arg]
    )
    monkeypatch.setattr(cli, "get_settings", lambda: broken)
    monkeypatch.setattr(cli_cron, "sender_from_settings", lambda _settings: sender)
    monkeypatch.setattr(cli_cron, "tracker_from_settings", lambda _settings: None)

    assert cli.main(["digest"]) == 0

    captured = capsys.readouterr()
    assert "space registry unreachable" in captured.err
    # The root is not in the registry, so it is still tried, and fails on its own line.
    assert f"{ROOT_LABEL}: skipped (OperationalError)" in captured.err
    assert "segreta" not in captured.err
    assert sender.sent == []


# --- the teardown --------------------------------------------------------------------


def _drop(settings: Settings, slug: str) -> None:
    drop_database(settings, tenant_database_url(settings, tenant_database_name(slug)))


# --- the space's clock (REB-764) -----------------------------------------------------


def test_every_space_the_command_walked_has_its_clock_moved_whatever_the_outcome(
    settings: Settings, cron: RecordingSender, capsys: pytest.CaptureFixture[str]
) -> None:
    """A week that went out, a space with nothing to report and a root all ran: each has the
    row `job_late` and Impostazioni read, with no failure."""
    assert cli.main(["digest"]) == 0

    for slug in (*SPACES, None):
        assert _clock(settings, slug) == (True, None), slug
    assert "clock not written" not in capsys.readouterr().err


def test_a_space_where_nobody_wants_the_report_still_counts_as_a_run(
    settings: Settings, cron: RecordingSender
) -> None:
    engine = _space_engine(settings, FIRST_SPACE)
    try:
        with engine.begin() as connection:
            connection.execute(text("update users set weekly_digest = false"))
        assert cli.main(["digest", "--slug", FIRST_SPACE]) == 0
        assert _clock(settings, FIRST_SPACE) == (True, None), "no recipient ran to the end"
    finally:
        with engine.begin() as connection:
            connection.execute(text("update users set weekly_digest = true"))
        engine.dispose()


def test_a_rehearsal_leaves_the_clock_alone(settings: Settings, cron: RecordingSender) -> None:
    assert cli.main(["digest", "--slug", FIRST_SPACE, "--dry-run"]) == 0

    assert _clock(settings, FIRST_SPACE) is None


def test_a_space_that_skips_its_week_records_why_and_the_next_run_clears_it(
    settings: Settings,
    cron: RecordingSender,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    original = DigestRun.send_for_space

    def refused(self, slug, *args, **kwargs):  # type: ignore[no-untyped-def]
        if slug == FIRST_SPACE:
            return DigestOutcome(slug, "skipped", "2026-W43", reason=SEND_REJECTED)
        return original(self, slug, *args, **kwargs)

    monkeypatch.setattr(DigestRun, "send_for_space", refused)
    assert cli.main(["digest", "--slug", FIRST_SPACE]) == 0
    capsys.readouterr()

    assert _clock(settings, FIRST_SPACE) == (False, SEND_REJECTED), "a failure and no success yet"

    monkeypatch.setattr(DigestRun, "send_for_space", original)
    assert cli.main(["digest", "--slug", FIRST_SPACE]) == 0
    assert _clock(settings, FIRST_SPACE) == (True, None), "the success ends the failure"


def test_a_space_the_command_cannot_open_is_recorded_nowhere_and_says_so_once(
    settings: Settings,
    cron: RecordingSender,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A database that does not answer cannot hold its own failure: the line on stderr is
    the whole record, and the clock simply stops moving, which reads as late."""
    import pigrocrm.core.cli.spaces_walk as tenants_database

    real = tenants_database.tenant_database_url
    monkeypatch.setattr(
        tenants_database,
        "tenant_database_url",
        lambda settings, db_name: real(
            settings,
            "pigrocrm_space_never_created"
            if db_name == tenant_database_name(SECOND_SPACE)
            else db_name,
        ),
    )

    assert cli.main(["digest"]) == 0

    assert capsys.readouterr().err.strip() == f"{SECOND_SPACE}: skipped (OperationalError)"
    assert _clock(settings, FIRST_SPACE) == (True, None)


def test_an_error_before_the_send_records_the_exception_type(
    settings: Settings,
    cron: RecordingSender,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def broken(self, slug, *args, **kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError("postgresql://user:secret@host/db")

    monkeypatch.setattr(DigestRun, "send_for_space", broken)

    assert cli.main(["digest", "--slug", FIRST_SPACE]) == 0

    assert capsys.readouterr().err.splitlines() == [f"{FIRST_SPACE}: skipped (RuntimeError)"]
    assert _clock(settings, FIRST_SPACE) == (False, "RuntimeError")


def test_a_clock_that_cannot_be_written_is_a_line_and_the_digest_still_exits_zero(
    settings: Settings,
    cron: RecordingSender,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The digest's exit status is 0 whatever happens, by its own contract."""
    from pigrocrm.core.reminders.jobs import JobClock

    def refuse(self, job, at=None):  # type: ignore[no-untyped-def]
        raise RuntimeError("postgresql://user:secret@host/db")

    monkeypatch.setattr(JobClock, "succeeded", refuse)

    assert cli.main(["digest", "--slug", FIRST_SPACE]) == 0

    captured = capsys.readouterr()
    assert captured.err.splitlines() == [f"{FIRST_SPACE}: clock not written (RuntimeError)"]
    assert f"{FIRST_SPACE}: sent to 1" in captured.out
    assert _clock(settings, FIRST_SPACE) is None
