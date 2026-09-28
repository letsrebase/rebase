"""`pigrocrm gmail-sync` walks the registry: every connected mailbox of every space.

REB-404. With the root's Google client lent to the spaces (REB-394) a space can connect
Gmail, and until this card its mailbox synchronised only when somebody pressed
«Sincronizza». The cron line now does what `pigrocrm digest` does since REB-263: the
root installation first, then each space of the registry, one database at a time, with
that space's own settings, one line per mailbox, and a space that fails never stopping
the next.

Everything here is real except the network. The root is a database of this module's
own, migrated to head; the registry is one of this module's own too, so the walk finds
exactly the two spaces provisioned below and nothing another module left on the
container; the two spaces are provisioned by `TenantService`, `CREATE DATABASE` and the
migrations included. Each mailbox's refresh token is sealed the way the OAuth callback
seals it: with the root's key for the root, with the key derived for the space
(`space_token_key`) for a space. A run that handed a space the root's settings would
therefore fail to open that space's token, which is what makes "with the space's
effective settings" something the tests below can see.
"""

from __future__ import annotations

import base64
from collections.abc import Iterator
from urllib.parse import parse_qs

import pytest
from fakes.fake_gmail import TOKEN_HOST, FakeGmail
from fakes.gmail_fixtures import TOKEN_KEY, gmail_settings
from sqlalchemy import Engine, create_engine, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

import pigrocrm.core.cli as cli
from pigrocrm.core.auth.models import User
from pigrocrm.core.config import Settings
from pigrocrm.core.customers.models import Customer
from pigrocrm.core.db import session_factory
from pigrocrm.core.db.sidecar import create_database_if_missing, drop_database
from pigrocrm.core.gmail.crypto import seal
from pigrocrm.core.gmail.models import GoogleAccount
from pigrocrm.core.gmail.schemas import REQUESTED_SCOPES
from pigrocrm.core.gmail.transport import GmailTransport
from pigrocrm.core.tenants import TenantService, TenantSignup, ensure_tenants_database
from pigrocrm.core.tenants.database import tenant_database_name, tenant_database_url
from pigrocrm.core.tenants.google import space_token_key
from pigrocrm.core.tenants.service import migrate_to_head

ROOT_DB = "cli_gmail_spaces_root"
REGISTRY_DB = "cli_gmail_spaces_registry"
ONE = "cli-gmail-one"
TWO = "cli-gmail-two"
SPACES = (ONE, TWO)
# The address each installation's mailbox answers to, and the refresh token sealed in
# it. Three different tokens, so the fake's token endpoint can say which ones it saw.
MAILBOXES = {
    None: ("root@example.it", "1//refresh-root"),
    ONE: ("one@example.it", "1//refresh-one"),
    TWO: ("two@example.it", "1//refresh-two"),
}
ROOT_OWNER = "owner@root.example"


# --- the root, the registry, and two spaces in it ------------------------------------


@pytest.fixture(scope="module")
def settings(db_engine: Engine) -> Iterator[Settings]:
    """The environment the command reads: a root of this module's own, migrated to head,
    a registry of this module's own, and the root's Google client lent to the spaces."""
    shared_url = db_engine.url.render_as_string(hide_password=False)
    root_url = make_url(shared_url).set(database=ROOT_DB)
    registry_url = make_url(shared_url).set(database=REGISTRY_DB)
    helper = Settings(database_url=shared_url, _env_file=None)  # type: ignore[call-arg]
    configured = gmail_settings(
        database_url=root_url.render_as_string(hide_password=False),
        tenants_database_url=registry_url.render_as_string(hide_password=False),
        google_shared_client=True,
    )
    create_database_if_missing(helper, root_url)
    try:
        migrate_to_head(configured, configured.database_url)
        yield configured
    finally:
        drop_database(helper, root_url)
        drop_database(helper, registry_url)


@pytest.fixture(scope="module")
def spaces(settings: Settings) -> Iterator[None]:
    registry = ensure_tenants_database(settings)
    session: Session = session_factory(registry)()
    try:
        service = TenantService(session, settings)
        for slug in SPACES:
            service.provision(
                TenantSignup(slug=slug, nome=f"Studio {slug}", email=f"owner@{slug}.example")
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
        registry.dispose()


@pytest.fixture(autouse=True)
def _no_mailbox_left(settings: Settings, spaces: None) -> Iterator[None]:
    """What a test connects and what a run writes, undone in all three databases."""
    yield
    for slug in (None, *SPACES):
        engine = _engine(settings, slug)
        try:
            with engine.begin() as connection:
                connection.execute(
                    text("delete from activities where entity_type = 'google_account'")
                )
                connection.execute(text("delete from gmail_known_addresses"))
                connection.execute(text("delete from google_accounts"))
                connection.execute(text("delete from customers where ragione_sociale like 'CLI %'"))
        finally:
            engine.dispose()


@pytest.fixture
def cron(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> FakeGmail:
    """The command as the container runs it, with Google replaced by the fake."""
    fake = FakeGmail()
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    monkeypatch.setattr(
        cli, "GmailTransport", lambda: GmailTransport(http=fake, sleep=lambda _: None)
    )
    return fake


# --- a mailbox, and reading one back -------------------------------------------------


def _engine(settings: Settings, slug: str | None) -> Engine:
    if slug is None:
        return create_engine(settings.database_url, future=True)
    return create_engine(tenant_database_url(settings, tenant_database_name(slug)), future=True)


def _key(settings: Settings, slug: str | None) -> bytes:
    """The key the OAuth callback seals with: the root's own, or the one derived for the
    space from it (`tenants/google.py`)."""
    if slug is None:
        return TOKEN_KEY
    return base64.b64decode(space_token_key(settings.google_token_key, slug))


def _connect(settings: Settings, slug: str | None, *, status: str = "active") -> None:
    """A connected mailbox, owned by the installation's owner, plus one customer with
    an address, so the cycle really refreshes the token and asks Gmail something. With an
    empty roster no request leaves at all, and a token sealed with the wrong key would
    never be opened."""
    address, refresh_token = MAILBOXES[slug]
    engine = _engine(settings, slug)
    try:
        with session_factory(engine)() as session:
            owner = session.scalars(
                select(User).where(User.ruolo == "admin").order_by(User.created_at)
            ).first()
            if owner is None:
                owner = User(email=ROOT_OWNER, nome="Owner", ruolo="admin", attivo=True)
                session.add(owner)
                session.flush()
            ciphertext, nonce = seal(refresh_token, _key(settings, slug))
            session.add(
                GoogleAccount(
                    user_id=owner.id,
                    google_sub=f"sub-{owner.id}",
                    email_address=address,
                    refresh_token_ciphertext=ciphertext,
                    refresh_token_nonce=nonce,
                    scopes_granted=list(REQUESTED_SCOPES),
                    status=status,
                )
            )
            session.add(Customer(ragione_sociale="CLI Acme", email="info@acme.it"))
            session.commit()
    finally:
        engine.dispose()


def _watermark(settings: Settings, slug: str | None) -> object:
    engine = _engine(settings, slug)
    try:
        with engine.connect() as connection:
            return connection.execute(
                text("select sync_watermark from google_accounts")
            ).scalar_one()
    finally:
        engine.dispose()


def _refresh_tokens(fake: FakeGmail) -> set[str]:
    """The refresh tokens Google was shown: one per mailbox whose token was opened."""
    return {
        parse_qs((request.body or b"").decode())["refresh_token"][0]
        for request in fake.requests
        if request.host == TOKEN_HOST
    }


def _line(label: str, slug: str | None) -> str:
    return f"gmail-sync {label} {MAILBOXES[slug][0]}: "


def _run(*args: str) -> int:
    return cli.main(["gmail-sync", *args])


# --- the walk ------------------------------------------------------------------------


def test_one_run_synchronises_the_root_and_every_space_one_line_each(
    settings: Settings, cron: FakeGmail, capsys: pytest.CaptureFixture[str]
) -> None:
    """The card's «Done when»: two spaces each holding a connected mailbox, one run, a
    line for each and both watermarks advanced. The root is in it too, first, as in the
    digest."""
    for slug in (None, *SPACES):
        _connect(settings, slug)

    assert _run() == 0

    captured = capsys.readouterr()
    assert captured.err == ""
    lines = captured.out.strip().splitlines()
    assert len(lines) == 3
    assert _line(cli.ROOT_LABEL, None) in lines[0]
    assert _line(ONE, ONE) in lines[1]
    assert _line(TWO, TWO) in lines[2]
    assert all("messaggi nuovi" in line for line in lines)
    for slug in (None, *SPACES):
        assert _watermark(settings, slug) is not None
    # Each installation's token was opened with its own key, which only its own
    # effective settings carry: the root's key opens nothing sealed for a space.
    assert _refresh_tokens(cron) == {token for _, token in MAILBOXES.values()}


def test_a_space_with_no_mailbox_says_nothing(
    settings: Settings, cron: FakeGmail, capsys: pytest.CaptureFixture[str]
) -> None:
    """Most spaces never connect Gmail, and the run is every fifteen minutes: a line for
    each of them would bury the lines about the mailboxes that exist."""
    _connect(settings, TWO)

    assert _run() == 0

    captured = capsys.readouterr()
    assert captured.err == ""
    lines = captured.out.strip().splitlines()
    assert len(lines) == 1
    assert _line(TWO, TWO) in lines[0]


def test_a_revoked_mailbox_is_its_own_line_and_the_next_space_still_runs(
    settings: Settings, cron: FakeGmail, capsys: pytest.CaptureFixture[str]
) -> None:
    """A consent revoked or expired is a state, not a crash: the sentence `gmail-sync`
    has always printed for the root, on stderr, now with the space in front of it; the
    space after it is synchronised in the same run, and the exit status says that one
    mailbox could not be."""
    _connect(settings, ONE, status="revoked")
    _connect(settings, TWO)

    assert _run() == 1

    captured = capsys.readouterr()
    assert _line(ONE, ONE) in captured.err
    assert "revocato" in captured.err
    assert _line(TWO, TWO) in captured.out
    assert _watermark(settings, ONE) is None
    assert _watermark(settings, TWO) is not None


@pytest.mark.parametrize(
    ("table", "where"),
    [
        # Read before any mailbox is listed: the whole space is one line.
        ("space_settings", "space"),
        # Read inside the cycle: that mailbox is one line, after the lock is handed back.
        ("gmail_known_addresses", "mailbox"),
    ],
)
def test_a_space_whose_schema_lags_is_skipped_on_its_own_line(
    settings: Settings,
    cron: FakeGmail,
    capsys: pytest.CaptureFixture[str],
    table: str,
    where: str,
) -> None:
    """A space migrates only when the API boots (`ensure-space-defaults`), and this
    command migrates nothing, like the digest. A space whose schema is behind the image
    is one `saltato` line naming the exception's type and never its text (a psycopg
    error can carry the URL), and the space after it is synchronised all the same.

    Inside a mailbox's cycle the failure is one nobody foresaw, so the frames of its
    traceback follow the line, for whoever has to find where it broke. Only the frames:
    the message is the text the line leaves out, for the same reason."""
    _connect(settings, ONE)
    _connect(settings, TWO)
    engine = _engine(settings, ONE)
    try:
        with engine.begin() as connection:
            connection.execute(text(f"alter table {table} rename to {table}_future"))
        try:
            assert _run() == 1
        finally:
            with engine.begin() as connection:
                connection.execute(text(f"alter table {table}_future rename to {table}"))
    finally:
        engine.dispose()

    captured = capsys.readouterr()
    skipped = (
        f"gmail-sync {ONE}: saltato (ProgrammingError)"
        if where == "space"
        else f"{_line(ONE, ONE)}saltato (ProgrammingError)"
    )
    assert skipped in captured.err
    if where == "mailbox":
        assert 'File "' in captured.err
        assert "in sync" in captured.err
    else:
        assert 'File "' not in captured.err
    # `relation "gmail_known_addresses" does not exist`, or the same for the other table:
    # the message, which is what can carry a URL or a statement's parameters.
    assert "does not exist" not in captured.err
    assert _line(TWO, TWO) in captured.out
    assert _watermark(settings, TWO) is not None


def test_a_space_whose_database_is_gone_does_not_take_the_others_with_it(
    settings: Settings,
    cron: FakeGmail,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The digest's own test, for the same contract: `tenant_database_url` is patched on
    the module the command resolves it from, so the space's row names a database that
    was never created, and this file's own `_engine` still reaches the real ones."""
    import pigrocrm.core.tenants.database as tenants_database

    _connect(settings, ONE)
    _connect(settings, TWO)
    real = tenants_database.tenant_database_url
    monkeypatch.setattr(
        tenants_database,
        "tenant_database_url",
        lambda settings_, db_name: real(
            settings_,
            "pigrocrm_space_never_created" if db_name == tenant_database_name(ONE) else db_name,
        ),
    )

    assert _run() == 1

    captured = capsys.readouterr()
    assert captured.err.strip().endswith(f"gmail-sync {ONE}: saltato (OperationalError)")
    assert _line(TWO, TWO) in captured.out
    assert _watermark(settings, TWO) is not None


def test_an_unreachable_registry_still_synchronises_the_root(
    settings: Settings,
    cron: FakeGmail,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The root is not in the registry, so a registry that does not answer is one line
    and the root's mailbox is synchronised all the same, as the digest still sends it."""
    import pigrocrm.core.tenants as tenants

    def unreachable(_settings: Settings) -> Engine:
        raise RuntimeError("postgres://user:hunter2@elsewhere/pigrocrm_tenants")

    _connect(settings, None)
    monkeypatch.setattr(tenants, "ensure_tenants_database", unreachable)

    assert _run() == 1

    captured = capsys.readouterr()
    assert captured.err.strip().endswith(
        "gmail-sync: registro degli spazi non raggiungibile (RuntimeError)"
    )
    assert "hunter2" not in captured.err
    assert _line(cli.ROOT_LABEL, None) in captured.out


def test_email_names_one_mailbox_wherever_it_is(
    settings: Settings, cron: FakeGmail, capsys: pytest.CaptureFixture[str]
) -> None:
    """`--email` keeps working for a cron line written before REB-404 with one mailbox
    in it, and it now finds that mailbox in whichever installation holds it."""
    for slug in (None, *SPACES):
        _connect(settings, slug)

    assert _run("--email", MAILBOXES[TWO][0]) == 0

    captured = capsys.readouterr()
    assert captured.err == ""
    assert len(captured.out.strip().splitlines()) == 1
    assert _line(TWO, TWO) in captured.out
    assert _watermark(settings, ONE) is None
    assert _watermark(settings, TWO) is not None


@pytest.mark.parametrize("unread", ["registry", "space"])
def test_no_mailbox_is_not_claimed_while_a_space_could_not_be_read(
    settings: Settings,
    cron: FakeGmail,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    unread: str,
) -> None:
    """«nessuna casella Google collegata» is a claim about every installation, and a run
    that could not read the registry, or one space, cannot make it: the mailbox may be
    in the part it did not read. Its own line has said what went wrong already."""
    import pigrocrm.core.tenants as tenants
    import pigrocrm.core.tenants.database as tenants_database

    if unread == "registry":

        def unreachable(_settings: Settings) -> Engine:
            raise RuntimeError("the registry does not answer")

        monkeypatch.setattr(tenants, "ensure_tenants_database", unreachable)
    else:
        real = tenants_database.tenant_database_url
        monkeypatch.setattr(
            tenants_database,
            "tenant_database_url",
            lambda settings_, db_name: real(
                settings_,
                "pigrocrm_space_never_created" if db_name == tenant_database_name(ONE) else db_name,
            ),
        )

    assert _run() == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert len(captured.err.strip().splitlines()) == 1
    assert "nessuna casella" not in captured.err


def test_an_unmatched_email_says_the_match_may_be_incomplete(
    settings: Settings,
    cron: FakeGmail,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The same claim, narrowed to one address: with a space unread, «non è una casella
    collegata» would be a guess. The line names what was read and says a space was not."""
    import pigrocrm.core.tenants.database as tenants_database

    _connect(settings, TWO)
    real = tenants_database.tenant_database_url
    monkeypatch.setattr(
        tenants_database,
        "tenant_database_url",
        lambda settings_, db_name: real(
            settings_,
            "pigrocrm_space_never_created" if db_name == tenant_database_name(ONE) else db_name,
        ),
    )

    assert _run("--email", "nobody@example.it") == 1

    captured = capsys.readouterr()
    last = captured.err.strip().splitlines()[-1]
    assert "nobody@example.it" in last
    assert MAILBOXES[TWO][0] in last
    assert "non è stato letto" in last
    assert "non è una casella collegata" not in last
    assert _watermark(settings, TWO) is None
