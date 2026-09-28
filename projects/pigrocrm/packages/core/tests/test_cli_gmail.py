"""`pigrocrm gmail-sync`: the cron's way in, on the root installation alone.

There is no daemon in this product and no queue (see `gmail/sync.py`), so "every
fifteen minutes" is somebody else's job -- cron's. This command is the whole of the
contract with it: one cycle per connected mailbox, one line each, and an exit status a
shell can read. Since REB-404 the same run also walks every space of the registry; that
walk is `test_cli_gmail_spaces.py`'s subject. Here the registry is one of this module's
own and empty, so what is left is the root, as a single installation sees it.

Two properties are asserted here rather than described:

* **Who the cron is.** `sync` refuses an actor with no id (`solo un utente può
  sincronizzare una casella`) because the mailbox belongs to a person, so the command
  cannot simply pass `Actor.system()` the way `createadmin` does. It builds a *system*
  actor carrying the mailbox owner's id, and the timeline says so: `actor_type` is
  `system`, because nobody pressed anything.
* **A cron job must not need a human to read a traceback.** Every failure the command
  can foresee -- no mailbox, a revoked credential, a refusal from Gmail -- ends as one
  sentence on stderr and exit 1.

The transport is a `FakeGmail` for the reason every test in this package uses one: what
runs is the real URL building, the real `q` and the real error handling, and only the
socket is replaced.
"""

from collections.abc import Callable, Iterator
from urllib.parse import urlparse
from uuid import UUID

import pytest
from fakes.fake_gmail import API_HOST, FakeGmail
from fakes.gmail_fixtures import connected_account, gmail_settings
from sqlalchemy import Engine, text
from sqlalchemy.engine import make_url

import pigrocrm.core.cli as cli
from pigrocrm.core.auth.models import User
from pigrocrm.core.config import Settings
from pigrocrm.core.customers.models import Customer
from pigrocrm.core.db import session_factory
from pigrocrm.core.db.sidecar import drop_database
from pigrocrm.core.gmail.repository import GmailRepository
from pigrocrm.core.gmail.transport import GmailTransport

# A registry of this module's own, so the walk finds it empty whatever else has
# provisioned a space on this container.
REGISTRY_DB = "prova_cli_gmail_registro"


@pytest.fixture(scope="module")
def registry_url(db_engine: Engine) -> Iterator[str]:
    """Created by the command itself, the first time it opens the registry, as in
    production; dropped once the module is done."""
    url = make_url(db_engine.url.render_as_string(hide_password=False)).set(database=REGISTRY_DB)
    yield url.render_as_string(hide_password=False)
    drop_database(
        Settings(
            database_url=db_engine.url.render_as_string(hide_password=False),
            _env_file=None,  # type: ignore[call-arg]
        ),
        url,
    )


@pytest.fixture
def settings_for(db_engine: Engine, registry_url: str) -> Iterator[Callable[..., Settings]]:
    """`gmail_settings` pointed at this container: the root is the shared database the
    rows below are committed to, the registry is the empty one above."""

    def build(**overrides: object) -> Settings:
        return gmail_settings(
            database_url=db_engine.url.render_as_string(hide_password=False),
            tenants_database_url=registry_url,
            **overrides,
        )

    yield build


@pytest.fixture
def cli_gmail(
    db_engine: Engine, settings_for: Callable[..., Settings], monkeypatch: pytest.MonkeyPatch
) -> Iterator[FakeGmail]:
    """The CLI builds its own settings, engines and transport, exactly as it does in the
    container; here the settings and the transport are the test's own, and the rows it
    commits for real are removed afterwards."""
    fake = FakeGmail()
    monkeypatch.setattr(cli, "get_settings", settings_for)
    monkeypatch.setattr(
        cli, "GmailTransport", lambda: GmailTransport(http=fake, sleep=lambda _: None)
    )
    yield fake
    with db_engine.begin() as connection:
        connection.execute(text("delete from activities where entity_type = 'google_account'"))
        connection.execute(text("delete from gmail_known_addresses"))
        connection.execute(text("delete from google_accounts"))
        connection.execute(text("delete from users where email like 'user-%@example.it'"))
        connection.execute(text("delete from customers where ragione_sociale like 'CLI %'"))


def _connect(
    engine: Engine,
    *,
    email_address: str,
    status: str = "active",
    ruolo: str = "admin",
    attivo: bool = True,
) -> UUID:
    """A mailbox the CLI's own session can see: committed, not merely flushed."""
    with session_factory(engine)() as session:
        account = connected_account(session, email_address=email_address, status=status)
        owner_id = account.user_id
        owner = session.get(User, owner_id)
        assert owner is not None
        owner.ruolo = ruolo
        owner.attivo = attivo
        session.commit()
    return owner_id


def _correspondent(engine: Engine) -> None:
    """One address in the roster, so the cycle really asks Gmail something. With an
    empty roster no request is issued at all, which is the right behaviour and the
    wrong starting point for a test about a request failing."""
    with session_factory(engine)() as session:
        session.add(Customer(ragione_sociale="CLI Acme", email="info@acme.it"))
        session.commit()


def _run(*args: str) -> int:
    return cli.main(["gmail-sync", *args])


def test_gmail_sync_runs_a_cycle_and_says_so_in_one_line(
    cli_gmail: FakeGmail, db_engine: Engine, capsys: pytest.CaptureFixture[str]
) -> None:
    _connect(db_engine, email_address="cron@example.it")

    assert _run() == 0

    out = capsys.readouterr().out.strip()
    assert out.count("\n") == 0
    # The installation first, as `pigrocrm digest` names it, then the mailbox: with the
    # spaces in the same log, an address alone no longer says whose database it is.
    assert f"gmail-sync {cli.ROOT_LABEL} cron@example.it: " in out
    # The counters, which are the only thing a cron log has to say. No subject, no
    # address of a correspondent, no body: a `SyncReport` has no room for one.
    assert "0 messaggi" in out


def test_the_cycle_is_recorded_as_the_system_acting_for_the_mailbox_owner(
    cli_gmail: FakeGmail, db_engine: Engine
) -> None:
    """The actor question, which has a wrong answer that would pass every other test
    here: the owner's own `user` actor. Cron is not the owner, and a timeline that says
    a person synchronised at 03:15 is a small lie the timeline exists not to tell."""
    owner_id = _connect(db_engine, email_address="cron@example.it")

    assert _run() == 0

    with db_engine.connect() as connection:
        actor_type, actor_id = connection.execute(
            text(
                "select actor_type, actor_id from activities "
                "where kind = 'gmail.sync_eseguito' order by occurred_at desc limit 1"
            )
        ).one()
    assert actor_type == "system"
    assert actor_id == owner_id


def test_gmail_sync_on_an_installation_with_no_mailbox_fails_with_a_sentence(
    cli_gmail: FakeGmail, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _run() == 1
    captured = capsys.readouterr()
    assert "nessuna casella" in captured.err
    assert captured.out == ""


def test_gmail_sync_on_a_revoked_credential_exits_one(
    cli_gmail: FakeGmail, db_engine: Engine, capsys: pytest.CaptureFixture[str]
) -> None:
    """The status cron has to be able to act on. Exit 0 here would make a dead
    integration look like fifteen minutes of nothing happening, forever."""
    _connect(db_engine, email_address="cron@example.it", status="revoked")

    assert _run() == 1
    assert "revocato" in capsys.readouterr().err


def test_gmail_sync_picks_the_mailbox_named_on_the_command_line(
    cli_gmail: FakeGmail, db_engine: Engine, capsys: pytest.CaptureFixture[str]
) -> None:
    _connect(db_engine, email_address="prima@example.it")
    _connect(db_engine, email_address="seconda@example.it")

    assert _run("--email", "seconda@example.it") == 0
    assert "seconda@example.it" in capsys.readouterr().out


def test_two_mailboxes_and_no_email_are_both_synchronised_one_line_each(
    cli_gmail: FakeGmail, db_engine: Engine, capsys: pytest.CaptureFixture[str]
) -> None:
    """Until REB-404 this was refused, because picking one would have left the other
    silently unsynchronised. Nothing is picked now: every connected mailbox gets its
    cycle and its own line, in the order they were connected, and both watermarks move."""
    _connect(db_engine, email_address="prima@example.it")
    _connect(db_engine, email_address="seconda@example.it")

    assert _run() == 0

    captured = capsys.readouterr()
    assert captured.err == ""
    lines = captured.out.strip().splitlines()
    assert len(lines) == 2
    assert f"gmail-sync {cli.ROOT_LABEL} prima@example.it: " in lines[0]
    assert f"gmail-sync {cli.ROOT_LABEL} seconda@example.it: " in lines[1]
    with db_engine.connect() as connection:
        watermarks = connection.execute(
            text(
                "select sync_watermark from google_accounts "
                "where email_address in ('prima@example.it', 'seconda@example.it')"
            )
        ).scalars()
        assert [watermark is not None for watermark in watermarks] == [True, True]


def test_an_email_that_is_not_connected_names_the_ones_that_are(
    cli_gmail: FakeGmail, db_engine: Engine, capsys: pytest.CaptureFixture[str]
) -> None:
    _connect(db_engine, email_address="cron@example.it")

    assert _run("--email", "altra@example.it") == 1
    err = capsys.readouterr().err
    assert "altra@example.it" in err
    assert "cron@example.it" in err


def test_a_cycle_already_running_is_reported_and_is_not_a_failure(
    cli_gmail: FakeGmail, db_engine: Engine, capsys: pytest.CaptureFixture[str]
) -> None:
    """Cron every fifteen minutes and somebody pressing Sincronizza is the collision the
    advisory lock exists for. The second caller spent nothing, so an error in the log
    here would be an error for the system working as designed -- and a log that cries
    wolf every quarter of an hour is a log nobody reads."""
    owner_id = _connect(db_engine, email_address="cron@example.it")
    with session_factory(db_engine)() as holder:
        # The real lock, on a real second connection: `pg_try_advisory_lock` is session
        # scoped, so nothing short of another session can hold it against this one.
        repo = GmailRepository(holder)
        account = repo.account_for_user(owner_id)
        assert account is not None
        assert repo.try_sync_lock(account.id)
        try:
            assert _run() == 0
        finally:
            repo.release_sync_lock(account.id)

    assert "già in corso" in capsys.readouterr().out


def test_the_cron_does_not_act_for_a_deactivated_owner(
    cli_gmail: FakeGmail, db_engine: Engine, capsys: pytest.CaptureFixture[str]
) -> None:
    """Deactivating somebody is, everywhere else in the product, the moment they stop
    being able to make the CRM do anything -- `deps.py` turns their session down with
    the same `get_active`. A cron that carried on would spend the Google consent of a
    person the titolare has just switched off, every quarter of an hour, silently."""
    _connect(db_engine, email_address="cron@example.it", attivo=False)

    assert _run() == 1
    err = capsys.readouterr().err
    assert "disattivato" in err
    assert "cron@example.it" in err


def test_the_cron_does_not_synchronise_for_an_owner_who_could_not_press_the_button(
    cli_gmail: FakeGmail, db_engine: Engine, capsys: pytest.CaptureFixture[str]
) -> None:
    """`role="admin"` on the system actor was convenient and wrong: it let a `readonly`
    owner's mailbox synchronise from cron while their own Sincronizza button refuses
    them. The actor carries the owner's real role, and the refusal is in Italian."""
    _connect(db_engine, email_address="cron@example.it", ruolo="readonly")

    assert _run() == 1
    err = capsys.readouterr().err
    assert "readonly" in err
    assert "cron@example.it" in err


def test_an_owner_who_can_write_still_runs(
    cli_gmail: FakeGmail, db_engine: Engine, capsys: pytest.CaptureFixture[str]
) -> None:
    """The other half of the rule: `collaboratore` is a role that may synchronise, and
    reading the owner's role must not have turned the check into "admin only"."""
    _connect(db_engine, email_address="cron@example.it", ruolo="collaboratore")

    assert _run() == 0
    assert "cron@example.it" in capsys.readouterr().out


def test_a_disconnected_mailbox_is_not_listed_as_connected(
    cli_gmail: FakeGmail, db_engine: Engine, capsys: pytest.CaptureFixture[str]
) -> None:
    """One active mailbox and one somebody unhooked is not an ambiguous installation.
    Listing the disconnected one would name, as connected, a mailbox that answers
    «nessuna casella Google collegata» one line later."""
    _connect(db_engine, email_address="attiva@example.it")
    _connect(db_engine, email_address="scollegata@example.it", status="disconnected")

    assert _run() == 0
    out = capsys.readouterr().out
    assert "attiva@example.it" in out
    assert "scollegata@example.it" not in out


def test_the_error_line_carries_the_sentence_and_not_the_entity_prefix(
    cli_gmail: FakeGmail, capsys: pytest.CaptureFixture[str]
) -> None:
    """`Conflict` composes `message` as "entity: reason" for the adapters that render
    its details. In a log an operator reads, `google_account:` in front of the sentence
    says nothing they can act on -- and the runbook prints these lines bare."""
    assert _run() == 1
    err = capsys.readouterr().err.strip()
    assert err.endswith("gmail-sync: nessuna casella Google collegata")


def test_gmail_not_configured_is_one_sentence_and_not_a_traceback(
    cli_gmail: FakeGmail,
    db_engine: Engine,
    settings_for: Callable[..., Settings],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A cron line left behind on an installation whose Google client was removed from
    `.env`. It is a misconfiguration, so exit 1 -- but a readable one."""
    _connect(db_engine, email_address="cron@example.it")
    monkeypatch.setattr(cli, "get_settings", lambda: settings_for(google_client_id=""))

    assert _run() == 1
    assert "Gmail non è configurato" in capsys.readouterr().err


def test_a_refusal_from_gmail_is_one_line_too(
    cli_gmail: FakeGmail,
    db_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The quota row of the runbook's table. `GmailSyncService.sync` does not convert
    `GoogleCallFailed` into a `DomainError` (see that class's docstring), so without the
    `except` in `cli.py` an exhausted Gmail quota would print a stack every fifteen
    minutes -- which is how a log stops being read."""
    _connect(db_engine, email_address="cron@example.it")
    _correspondent(db_engine)

    def exhausted(
        method: str, url: str, headers: dict[str, str], body: bytes | None
    ) -> tuple[int, bytes, dict[str, str]]:
        # By host, so the token refresh still succeeds through the fake: it is the
        # *listing* that has to fail here, and it is the first call after the refresh.
        if urlparse(url).netloc == API_HOST:
            return 429, b'{"error": {"status": "RESOURCE_EXHAUSTED"}}', {"Retry-After": "0"}
        return cli_gmail(method, url, headers, body)

    monkeypatch.setattr(
        cli, "GmailTransport", lambda: GmailTransport(http=exhausted, sleep=lambda _: None)
    )

    assert _run() == 1
    err = capsys.readouterr().err
    assert "elenco dei messaggi fallita (429/RESOURCE_EXHAUSTED)" in err
    assert "Traceback" not in err
