import argparse
import getpass
import sys
from collections.abc import Sequence
from datetime import UTC, date, datetime
from typing import cast

from sqlalchemy import Engine
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session

from pigrocrm.core import telemetry
from pigrocrm.core.actor import Actor, Role
from pigrocrm.core.auth.repository import UserRepository
from pigrocrm.core.auth.schemas import UserCreate
from pigrocrm.core.auth.service import UserService
from pigrocrm.core.config import get_settings
from pigrocrm.core.db import create_engine_from_settings, session_factory
from pigrocrm.core.digest.run import DigestOutcome, DigestRun
from pigrocrm.core.digest.service import previous_week, week_containing
from pigrocrm.core.errors import Conflict, DomainError, ValidationFailed
from pigrocrm.core.gmail.errors import GoogleCallFailed
from pigrocrm.core.gmail.models import GoogleAccount
from pigrocrm.core.gmail.repository import GmailRepository
from pigrocrm.core.gmail.schemas import SyncReport
from pigrocrm.core.gmail.sync import GmailSyncService
from pigrocrm.core.gmail.tokens import GoogleTokenClient
from pigrocrm.core.gmail.transport import GmailTransport
from pigrocrm.core.mail import sender_from_settings

# Both forms of the same module, deliberately. `telemetry` as a module is what
# `digest` calls `shutdown()` through, so the call resolves the attribute at run time and
# a test that replaces it is obeyed; `tracker_from_settings` is imported by name because
# that is the seam the tests patch on *this* module (`monkeypatch.setattr(cli,
# "tracker_from_settings", ...)`), which only works on a name this module owns.
from pigrocrm.core.telemetry import tracker_from_settings

# What `pigrocrm digest` prints in front of the root installation's line (REB-263). Not
# the root slug: the log names the installation apart from its spaces, and the runbook,
# which can never carry a real company's name, documents this line as it is.
RADICE = "root"


def createadmin(email: str | None, nome: str | None) -> int:
    """Bootstrap the first administrator. There is no default account and no known
    default password — the direct lesson from the previous system's hardcoded credentials."""
    email = email or input("Email: ").strip()
    nome = nome or input("Nome: ").strip()
    password = _read_password("Password: ")
    if sys.stdin.isatty() and password != getpass.getpass("Conferma password: "):
        print("Le password non coincidono.", file=sys.stderr)
        return 1

    engine = create_engine_from_settings(get_settings())
    with session_factory(engine)() as session:
        service = UserService(session)
        try:
            user = service.create(
                UserCreate(email=email, password=password, nome=nome, ruolo="admin"),
                Actor.system(),
            )
        except DomainError as exc:
            # A short password is the single most likely first mistake a new operator
            # will make with this tool; a raw traceback here is a bad first impression.
            print(exc.message, file=sys.stderr)
            return 1
    print(f"Creato amministratore {user.email}")
    return 0


def _read_password(prompt: str) -> str:
    """From the terminal when there is one, hidden; from stdin when there is not.

    `getpass` needs a tty and raises `EOFError` without one -- which is what happens
    inside `docker compose exec` without `-t`, and in any shell that pipes into this.
    The non-tty branch reads one line, so `echo "$PW" | pigrocrm resetpassword --email x`
    works from a script the operator controls; the password still never appears on a
    command line or in `ps`."""
    if sys.stdin.isatty():
        return getpass.getpass(prompt)
    return sys.stdin.readline().rstrip("\n")


def resetpassword(email: str | None) -> int:
    """`pigrocrm resetpassword --email chi@dove.it`: a new password for an account that
    exists. The product has no e-mail flow for this on purpose; the operator at the
    server is the reset."""
    email = email or input("Email: ").strip()
    password = _read_password("Nuova password: ")
    if sys.stdin.isatty() and password != getpass.getpass("Conferma password: "):
        print("Le password non coincidono.", file=sys.stderr)
        return 1

    engine = create_engine_from_settings(get_settings())
    with session_factory(engine)() as session:
        try:
            user = UserService(session).reset_password(email, password, Actor.system())
        except DomainError as exc:
            print(exc.message, file=sys.stderr)
            return 1
    print(f"Password aggiornata per {user.email}")
    return 0


def seed_templates() -> int:
    """`pigrocrm seed-templates`. Idempotent, so it is safe on every deploy -- which is
    the point: the timesheet template has to exist before anyone presses Scarica, and
    requiring a manual step there is how a feature ships broken."""
    from pigrocrm.core.actor import Actor
    from pigrocrm.core.config import get_settings
    from pigrocrm.core.db import create_engine_from_settings, session_factory
    from pigrocrm.core.templates.service import TemplateService

    with session_factory(create_engine_from_settings(get_settings()))() as session:
        created = TemplateService(session).seed_defaults(Actor.system())
    for template in created:
        print(f"creato: {template.nome} ({template.tipo})")
    if not created:
        print("nessun template da creare: sono già presenti")
    return 0


def _schema_revision(engine: Engine) -> str | None:
    """The Alembic revision a space's database is at, `None` before its first migration
    (no `alembic_version` table yet). A database that cannot be reached raises: that is
    the caller's line on stderr, not a missing table."""
    from sqlalchemy import text
    from sqlalchemy.exc import ProgrammingError

    try:
        with engine.connect() as connection:
            return connection.execute(text("select version_num from alembic_version")).scalar()
    except ProgrammingError:
        return None


def ensure_space_defaults() -> int:
    """`pigrocrm ensure-space-defaults`: every space in the registry is brought to the
    image's schema, then gets the stages, templates and categories it lacks, table by
    table, only where the table is empty (spec 2026-09-12 §6.5). Runs in the API
    image's CMD after the root's own `alembic upgrade head`.

    **The migration comes first, and it is the reason this command visits every space
    at all** (ORB-189). A space's database was migrated once, when the space was
    provisioned, and never again: `alembic upgrade head` in the CMD knows only the root
    database. `pigrocrm-v0.13.0` shipped migration 0034, which every read of `users`
    depends on, to eight spaces still at 0031 and 0033, and the login of every space
    answered 500 until somebody ran the migration by hand. So each registry row is
    upgraded here with the same `migrate_to_head` provisioning uses, one line per space
    saying from which revision to which, and only then furnished.

    Always answers 0, whatever happens: this runs before uvicorn, and a problem in one
    space must never keep the API down for the others. A registry that cannot be
    reached, a space that cannot be migrated (then it is not furnished either) or one
    that cannot be furnished is one line on stderr with the exception's type and never
    its text (a psycopg error can carry the URL, password included). The root
    installation is not in the registry and is not touched."""
    from sqlalchemy import create_engine, select

    from pigrocrm.core.tenants import Tenant, ensure_defaults, ensure_tenants_database
    from pigrocrm.core.tenants.database import tenant_database_url
    from pigrocrm.core.tenants.service import migrate_to_head

    settings = get_settings()
    try:
        registry = ensure_tenants_database(settings)
        try:
            with session_factory(registry)() as session:
                spaces = [
                    (row.slug, row.db_name)
                    for row in session.scalars(select(Tenant).order_by(Tenant.created_at)).all()
                ]
        finally:
            registry.dispose()
    except Exception as exc:  # noqa: BLE001 - never a boot failure
        print(f"registro degli spazi non raggiungibile ({type(exc).__name__})", file=sys.stderr)
        return 0
    if not spaces:
        print("nessuno spazio nel registro")
        return 0
    for slug, db_name in spaces:
        url = tenant_database_url(settings, db_name)
        engine = create_engine(url, future=True)
        try:
            try:
                before = _schema_revision(engine)
                migrate_to_head(settings, url.render_as_string(hide_password=False))
                after = _schema_revision(engine)
            except Exception as exc:  # noqa: BLE001 - one space must not stop the others
                print(f"{slug}: non migrato ({type(exc).__name__})", file=sys.stderr)
                continue
            if before == after:
                print(f"{slug}: schema già a {after}")
            else:
                print(f"{slug}: schema migrato da {before or 'zero'} a {after}")
            try:
                with session_factory(engine)() as space:
                    report = ensure_defaults(space)
            except Exception as exc:  # noqa: BLE001 - one space must not stop the others
                print(f"{slug}: non arredato ({type(exc).__name__})", file=sys.stderr)
                continue
        finally:
            engine.dispose()
        if report.seeded:
            print(
                f"{slug}: stati {report.stages}, template {report.templates}, "
                f"categorie {report.categories}"
            )
        else:
            print(f"{slug}: già a posto")
    return 0


def rebuild_identity_index() -> int:
    """`pigrocrm rebuild-identity-index`: a day-one backfill for an installation that
    wants "my spaces" complete immediately, never a correctness requirement --
    `identities` already grows to completeness on its own as people log back in
    (`IdentityService.upsert_and_issue`, called from `login`, `enter_with_link` and
    `accept_invite`). Follows the exact shape `ensure_space_defaults` above already
    uses (design 2026-09-23 §6, §8; REB-379): read every `Tenant` row from the
    registry, `select(Tenant)` ordered by `created_at`, then visit each space in
    turn. For each space it reads every `users` row -- not only `owner_email`, since
    a space may already hold people invited after it was created -- and upserts one
    `identities` row per distinct address found, through
    `IdentityService.get_or_create`, which never mints a session or a token: a
    backfill records that an address exists, it does not pretend anyone has just
    logged in.

    Always answers 0, whatever happens, the same discipline `ensure_space_defaults`
    follows: a registry that cannot be reached is one line on stderr and nothing
    raised, and one space that cannot be reached -- or one space's identities that
    fail to write -- costs a line on stderr with the exception's type, never its
    text (a psycopg error can carry the URL, password included), and never the
    whole command."""
    from sqlalchemy import create_engine, select

    from pigrocrm.core.auth.models import User
    from pigrocrm.core.identity.models import Identity
    from pigrocrm.core.identity.service import IdentityService
    from pigrocrm.core.tenants import Tenant, ensure_tenants_database
    from pigrocrm.core.tenants.database import tenant_database_url

    settings = get_settings()
    try:
        registry = ensure_tenants_database(settings)
    except Exception as exc:  # noqa: BLE001 - never fails the whole command
        print(f"registro degli spazi non raggiungibile ({type(exc).__name__})", file=sys.stderr)
        return 0
    try:
        try:
            with session_factory(registry)() as registry_session:
                spaces = [
                    (row.slug, row.db_name)
                    for row in registry_session.scalars(
                        select(Tenant).order_by(Tenant.created_at)
                    ).all()
                ]
        except Exception as exc:  # noqa: BLE001 - never fails the whole command
            print(f"registro degli spazi non raggiungibile ({type(exc).__name__})", file=sys.stderr)
            return 0
        if not spaces:
            print("nessuno spazio nel registro")
            return 0
        for slug, db_name in spaces:
            url = tenant_database_url(settings, db_name)
            engine = create_engine(url, future=True)
            try:
                try:
                    with session_factory(engine)() as space:
                        emails = sorted(
                            {row.strip().lower() for row in space.scalars(select(User.email)).all()}
                            - {""}
                        )
                except Exception as exc:  # noqa: BLE001 - one space must not stop the others
                    print(f"{slug}: non raggiungibile ({type(exc).__name__})", file=sys.stderr)
                    continue
            finally:
                engine.dispose()
            if not emails:
                print(f"{slug}: nessun utente")
                continue
            try:
                with session_factory(registry)() as registry_session:
                    existing = set(
                        registry_session.scalars(
                            select(Identity.email).where(Identity.email.in_(emails))
                        ).all()
                    )
                    service = IdentityService(registry_session, settings)
                    for email in emails:
                        service.get_or_create(email)
            except Exception as exc:  # noqa: BLE001 - one space must not stop the others
                print(f"{slug}: identità non aggiornate ({type(exc).__name__})", file=sys.stderr)
                continue
            new_count = len(emails) - len(existing)
            if new_count:
                print(f"{slug}: {len(emails)} indirizzi, {new_count} nuove identità")
            else:
                print(f"{slug}: {len(emails)} indirizzi, già indicizzati")
    finally:
        registry.dispose()
    return 0


def digest(*, slug: str | None, data: date | None, forza: bool, dry_run: bool) -> int:
    """`pigrocrm digest`: the weekly report of the root installation and of every space in
    the registry, for cron.

    Spec 2026-09-16 §3.4. One run a week, Monday morning (the runbook is
    `docs/superpowers/notes/2026-09-09-gmail-cron-runbook.md`), one line per space, and
    exit 0 whatever happens: the spaces are independent, so one that fails must not take
    the exit status -- and with it the operator's attention -- away from the ones that
    worked. What decides anything about a space's week is `DigestRun`, not this function;
    here there is a registry to walk, a session to open per space and a line to print.

    **The root installation is one more space, and the first line** (REB-263). It is not
    in the registry, so the walk alone never reached it -- and it is the one installation
    with a titolare's real data in it. Its database is `settings.database_url`, its
    titolare the first active admin (`DigestRun` resolves it, from `owner_email=None`),
    and its links carry `PIGROCRM_ROOT_SLUG` the way a space's carry its own slug:
    `/<root_slug>/app` *is* the root. Its line reads `root: ...` whatever the slug, so the
    log tells the installation from its spaces, and `--slug root` visits it alone, as does
    `--slug <PIGROCRM_ROOT_SLUG>`: both names are reserved against signups, so neither can
    also be a space, and a run for the root alone never opens the registry. An
    unreachable registry no longer ends the run either: it is one line, and the root is
    still sent.

    **This command migrates nothing.** `ensure-space-defaults` at boot is the only
    migrator (ORB-189), and that separation is the point: a cron job that ran Alembic on
    eight databases at eight o'clock on a Monday would be the riskiest thing this product
    does, and would do it unattended. So a space whose schema is behind -- no `digests`
    table, no `users.digest_settimanale` -- fails here, once, as one `saltato` line naming
    the exception's type, and is picked up by the next deploy's boot command.

    **One sender, one tracker, one week, for the whole run.** Resend's client and
    PostHog's are per process, not per space, and `previous_week` asked once is what makes
    a run that starts at 07:59:59 on a Monday send *one* week to every space rather than
    two different ones side by side. Only `public_url` is per space, because only it
    differs: `space_base_settings` gives a space the root's URL plus its slug, which is
    what every link in the mail is built from. The week comes from the *root* settings and
    not from `space_base_settings`: there is one week per run, and that function only adds
    a space's slug to the public URL -- it does not change the timezone, so asking it per
    space would be the same answer computed eight times, with eight chances to straddle
    midnight.

    **`telemetry.shutdown()` in a `finally`.** The PostHog SDK queues captures on a
    background thread and flushes them on its own schedule; a process that exits without
    shutting it down loses whatever was still in the queue -- which, for a command that
    runs for a few seconds once a week, is potentially every event it just produced. It
    is a no-op on an installation with no key, which is most of them.
    """
    from sqlalchemy import create_engine, select

    from pigrocrm.core.tenants import Tenant, ensure_tenants_database, space_base_settings
    from pigrocrm.core.tenants.database import tenant_database_url

    settings = get_settings()
    radice_sola = slug is not None and slug in {RADICE, settings.root_slug}
    # (the line's label, the database, the registry's owner or `None` for the root, the
    # base of every link in the mail)
    spaces: list[tuple[str, str | URL, str | None, str]] = []
    if slug is None or radice_sola:
        # `rstrip`: with no root slug this is `public_url` untouched, and a trailing slash
        # on it would put `//app` in every link.
        root_url = space_base_settings(settings, settings.root_slug or None).public_url
        spaces.append((RADICE, settings.database_url, None, root_url.rstrip("/")))
    registro_letto = False
    if not radice_sola:
        try:
            registry = ensure_tenants_database(settings)
            try:
                with session_factory(registry)() as session:
                    spaces.extend(
                        (
                            row.slug,
                            tenant_database_url(settings, row.db_name),
                            row.owner_email,
                            space_base_settings(settings, row.slug).public_url,
                        )
                        for row in session.scalars(select(Tenant).order_by(Tenant.created_at)).all()
                        if slug is None or row.slug == slug
                    )
                registro_letto = True
            finally:
                registry.dispose()
        except Exception as exc:  # noqa: BLE001 - a cron line, never a traceback
            # The type and never the text: a psycopg error can carry the URL, password
            # included, and this line is appended to a file on the host. Not a `return`:
            # the root is not in the registry, and it goes on being sent.
            print(f"registro degli spazi non raggiungibile ({type(exc).__name__})", file=sys.stderr)
    if not spaces:
        # A typo in a cron line, said only when the registry was really read: an
        # unreachable one has had its own line already. Never as a week that went out.
        if slug is not None and registro_letto:
            print(f"{slug}: non nel registro", file=sys.stderr)
        return 0

    settimana = week_containing(data) if data is not None else previous_week(settings)
    try:
        # Both clients are built *inside* this `try`, so that the `finally` below reaches
        # them: PostHog's constructor is the one that can fail here -- a malformed key, a
        # host it refuses -- and built above, its failure would leave the command with a
        # traceback instead of a line and, worse, with whatever the SDK had already
        # started never shut down. The inner `except` is narrow on purpose: a space's own
        # failure is caught per space below, with the slug in front of it.
        try:
            sender = sender_from_settings(settings)
            tracker = tracker_from_settings(settings)
        except Exception as exc:  # noqa: BLE001 - a cron line, never a traceback
            print(f"invio non configurabile ({type(exc).__name__})", file=sys.stderr)
            return 0
        for space_slug, database_url, owner_email, public_url in spaces:
            engine = create_engine(database_url, future=True)
            try:
                # A session of its own per space, with no transaction open: `DigestRun`
                # opens the dashboard's snapshot itself and hands the session back clean
                # on every path.
                with session_factory(engine)() as space:
                    esito = DigestRun(
                        space,
                        settings,
                        sender=sender,
                        tracker=tracker,
                        public_url=public_url,
                    ).send_for_space(
                        space_slug, owner_email, settimana, forza=forza, dry_run=dry_run
                    )
            except Exception as exc:  # noqa: BLE001 - one space must not stop the others
                # `send_for_space` answers `saltato` for everything that fails once it has
                # started; what is left for here is what fails before that -- a database
                # that cannot be reached, a schema behind the image's. The same line
                # either way, because it is the same thing for the operator reading it.
                print(f"{space_slug}: saltato ({type(exc).__name__})", file=sys.stderr)
                continue
            finally:
                engine.dispose()
            _stampa_esito(esito, dry_run=dry_run)
    finally:
        telemetry.shutdown()
    return 0


def _stampa_esito(esito: DigestOutcome, *, dry_run: bool) -> None:
    """One space, one line, counters only -- never an address and never a figure out of
    the report. `saltato` goes to stderr, so `2>&1` in the cron line keeps the order and a
    log split by stream keeps the failures on their own.

    `(prova)` on a rehearsal: `--dry-run` answers `inviato` with the people it *would*
    have written to, and a log where the rehearsal and the real Monday read identically is
    a log that cannot answer whether last week went out.
    """
    if esito.esito == "inviato":
        print(f"{esito.slug}: inviato a {esito.destinatari}{' (prova)' if dry_run else ''}")
    elif esito.esito == "vuoto":
        print(f"{esito.slug}: vuoto")
    elif esito.esito == "gia_inviato":
        print(f"{esito.slug}: già inviato per {esito.settimana}")
    elif esito.esito == "nessun_destinatario":
        print(f"{esito.slug}: nessun destinatario")
    else:
        print(f"{esito.slug}: saltato ({esito.motivo})", file=sys.stderr)


def gmail_sync(email: str | None) -> int:
    """`pigrocrm gmail-sync [--email casella@dove.it]`: one cycle, for cron.

    There is no daemon and no queue in this product (see `gmail/sync.py`), so the
    fifteen minutes are cron's to keep. This is the whole contract with it: one line on
    stdout, exit 0 when the cycle ran, exit 1 when it could not. The runbook is
    `docs/superpowers/notes/2026-09-09-gmail-cron-runbook.md`.

    **Which actor, and why it is not `Actor.system()`.** `createadmin` passes
    `Actor.system()`, which has no id -- and `GmailSyncService.sync` refuses an actor
    with no id, correctly: there is one mailbox per user, and a sync that read "the
    first row in the table" would spend one person's Google quota under another's
    consent. So this builds a *system* actor carrying the mailbox owner's id and the
    owner's own role (`_cron_actor`): the id says *whose* credential is being spent, and
    `type="system"` says nobody pressed anything, which is what the timeline entry then
    records. The alternative -- the owner's own `user` actor -- would write a timeline
    that says a person synchronised at 03:15, and the timeline exists not to say that.

    It widens nothing. `AGENT_FORBIDDEN_ACTIONS` is checked against `type == "mcp"`
    only, so a system actor was never subject to it and a personal access token gains
    nothing from this command existing; the credential an agent presents still resolves
    to `type="mcp"` in `PatService.resolve`, whatever this process does.

    **Why the failures are caught here.** A traceback in a log nobody is watching is a
    failure that gets read as noise. Every foreseeable one -- no mailbox, an ambiguous
    one, a deactivated owner, a revoked or expired consent, Gmail refusing the call --
    is a sentence and a 1. Anything else still raises, because an unforeseen failure
    deserves its stack.
    """
    settings = get_settings()
    engine = create_engine_from_settings(settings)
    with session_factory(engine)() as session:
        try:
            account = _gmail_account(session, email)
            actor = _cron_actor(session, account)
            transport = GmailTransport()
            service = GmailSyncService(
                session,
                settings=settings,
                transport=transport,
                tokens=GoogleTokenClient(
                    client_id=settings.google_client_id,
                    client_secret=settings.google_client_secret,
                    transport=transport,
                ),
            )
            report = service.sync(actor)
        except (DomainError, GoogleCallFailed) as exc:
            print(f"{_now()} gmail-sync: {_reason(exc)}", file=sys.stderr)
            return 1
        print(f"{_now()} gmail-sync {account.email_address}: {_outcome(report)}")
    return 0


def _reason(exc: DomainError | GoogleCallFailed) -> str:
    """The sentence, without the machine-readable prefix.

    `DomainError` carries structured details and composes `message` for the adapters
    that render them -- `Conflict` as `f"{entity}: {reason}"`. In a log an operator
    reads, `google_account:` in front of «nessuna casella Google collegata» says
    nothing they can act on, so the bare `reason` is printed when there is one.
    """
    if isinstance(exc, DomainError):
        reason = exc.details.get("reason")
        return reason if isinstance(reason, str) else exc.message
    return str(exc)


def _cron_actor(session: Session, account: GoogleAccount) -> Actor:
    """Who the cron acts as: the mailbox's owner, with their own role, as the system.

    Two refusals rather than an escalation, and they are different questions.

    **A deactivated owner.** Deactivating somebody is, everywhere else in this product,
    the moment they stop being able to make the CRM do anything: `deps.py`'s `get_actor`
    turns their session down through this same `get_active`. A cron that kept
    synchronising would go on spending the Google consent of a person the titolare has
    just switched off, quietly, every fifteen minutes.

    **The owner's real role.** `role="admin"` was convenient and wrong: it would let a
    `readonly` owner's mailbox synchronise from cron while the Sincronizza button in
    their own settings page refuses them. Whether the sync may run is a decision this
    installation already made about that person, so the actor carries their role and
    `sync`'s own `require_write` decides. The check below only exists to say it in
    Italian first -- `PermissionDenied`'s message is written for an API, not for a log.
    """
    try:
        user = UserRepository(session).get_active(account.user_id)
    except ValidationFailed as exc:
        raise Conflict(
            "google_account",
            f"la casella {account.email_address} appartiene a un utente disattivato: "
            "il sync resta fermo finché non viene riattivato",
        ) from exc
    # `cast` and not a runtime check: `Actor` is a pydantic model and validates `role`
    # against the same literal on construction, so a column holding something else
    # raises there rather than travelling on unnoticed.
    actor = Actor(id=user.id, type="system", role=cast(Role, user.ruolo))
    if not actor.can_write:
        raise Conflict(
            "google_account",
            f"{user.email} ha il ruolo {user.ruolo} e non può sincronizzare la casella "
            f"{account.email_address}: il cron non agisce con più diritti del titolare",
        )
    return actor


def _now() -> str:
    """Every line starts with the time. Cron adds none, and a log of bare sentences
    cannot answer the first question anybody asks it: when did this stop working."""
    return datetime.now(UTC).isoformat(timespec="seconds")


def _outcome(report: SyncReport) -> str:
    """Counters only -- never a subject, an address or a body. `SyncReport` has no room
    for one, which is what makes this line safe to append to a file on the host."""
    if report.already_running:
        # Not a failure: a cron every fifteen minutes and a human pressing Sincronizza
        # is exactly the collision the advisory lock exists for, and the second caller
        # spent nothing. Exiting 1 here would put an error in the log for the system
        # working as designed.
        since = report.running_since
        return f"già in corso da {since.isoformat(timespec='seconds') if since else 'poco fa'}"
    return (
        f"{report.messages_stored} messaggi nuovi, "
        f"{report.messages_skipped} già presenti, "
        f"{report.threads_fetched} conversazioni lette, "
        f"{report.links_created} collegamenti, "
        f"{report.reconciled} invii riconciliati "
        f"({report.queries_issued} query)"
    )


def _gmail_account(session: Session, email: str | None) -> GoogleAccount:
    """The mailbox to synchronise, or a `Conflict` naming the ones there are.

    Never a guess. On the single-user install this is the only row and `--email` is
    noise; on an install with two, picking one would leave the other quietly
    unsynchronised and the log would look identical either way.

    A mailbox somebody disconnected is not one of them, in the choice or in the list:
    `usable` refuses it with «nessuna casella Google collegata», so listing it as
    connected would name a mailbox that answers, one line later, that it does not
    exist -- and would turn a single-mailbox install into an ambiguous one the day its
    owner unhooked a second, older account.
    """
    accounts = [
        account
        for account in GmailRepository(session).all_accounts()
        if account.status != "disconnected"
    ]
    if not accounts:
        raise Conflict("google_account", "nessuna casella Google collegata")
    if email is not None:
        for account in accounts:
            if account.email_address.casefold() == email.casefold():
                return account
        raise Conflict(
            "google_account",
            f"{email} non è una casella collegata: {_mailbox_list(accounts)}",
        )
    if len(accounts) > 1:
        raise Conflict(
            "google_account",
            f"più di una casella collegata, indica --email: {_mailbox_list(accounts)}",
        )
    return accounts[0]


def _mailbox_list(accounts: Sequence[GoogleAccount]) -> str:
    return ", ".join(account.email_address for account in accounts)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pigrocrm")
    sub = parser.add_subparsers(dest="command", required=True)
    admin = sub.add_parser("createadmin", help="Crea il primo utente amministratore")
    admin.add_argument("--email")
    admin.add_argument("--nome")
    reset = sub.add_parser("resetpassword", help="Imposta una nuova password a un utente esistente")
    reset.add_argument("--email")
    sub.add_parser("seed-templates", help="Crea i template predefiniti, se mancano")
    sub.add_parser(
        "ensure-space-defaults",
        help="Dà a ogni spazio del registro stati, template e categorie predefiniti, se mancano",
    )
    sub.add_parser(
        "rebuild-identity-index",
        help="Indicizza in identities ogni indirizzo trovato negli users di ogni spazio",
    )
    sync = sub.add_parser("gmail-sync", help="Sincronizza la casella Google collegata (per cron)")
    sync.add_argument("--email", help="La casella da sincronizzare, se ne è collegata più di una")
    settimanale = sub.add_parser(
        "digest", help="Manda a ogni spazio il resoconto della settimana (per cron)"
    )
    settimanale.add_argument("--slug", help="Un solo spazio, invece di tutto il registro")
    settimanale.add_argument(
        "--data",
        # `type=` rather than a parse in the dispatch below: a mistyped date then comes
        # back as argparse's own usage message and an exit 2, not as a traceback at the
        # bottom of a cron log.
        type=date.fromisoformat,
        metavar="YYYY-MM-DD",
        help="Un giorno della settimana da mandare; senza, la settimana appena chiusa",
    )
    settimanale.add_argument(
        "--forza", action="store_true", help="Manda di nuovo una settimana già inviata"
    )
    settimanale.add_argument(
        "--dry-run",
        action="store_true",
        help="Prova: prepara il resoconto, dice a quanti andrebbe e non manda niente",
    )

    args = parser.parse_args(argv)
    if args.command == "createadmin":
        return createadmin(args.email, args.nome)
    if args.command == "resetpassword":
        return resetpassword(args.email)
    if args.command == "seed-templates":
        return seed_templates()
    if args.command == "ensure-space-defaults":
        return ensure_space_defaults()
    if args.command == "rebuild-identity-index":
        return rebuild_identity_index()
    if args.command == "gmail-sync":
        return gmail_sync(args.email)
    if args.command == "digest":
        return digest(slug=args.slug, data=args.data, forza=args.forza, dry_run=args.dry_run)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
