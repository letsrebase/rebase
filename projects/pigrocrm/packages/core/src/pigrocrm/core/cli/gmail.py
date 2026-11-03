"""`pigrocrm gmail-sync`: one cycle per connected mailbox, for cron."""

import sys
import traceback
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.orm import Session, sessionmaker

from pigrocrm.core.actor import Actor
from pigrocrm.core.auth.repository import UserRepository
from pigrocrm.core.auth.scope import actor_for
from pigrocrm.core.cli.spaces_walk import read_spaces, record_job, space_engine
from pigrocrm.core.config import Settings
from pigrocrm.core.db import session_factory
from pigrocrm.core.db.scope import bind_scope
from pigrocrm.core.errors import Conflict, DomainError, ValidationFailed
from pigrocrm.core.gmail.models import GoogleAccount
from pigrocrm.core.gmail.repository import GmailRepository
from pigrocrm.core.gmail.schemas import SyncReport
from pigrocrm.core.gmail.sync import GmailSyncService
from pigrocrm.core.gmail.tokens import GoogleTokenClient
from pigrocrm.core.gmail.transport import GmailTransport
from pigrocrm.core.labels import USER_ROLE, label
from pigrocrm.core.reminders.jobs import GMAIL_SYNC_JOB
from pigrocrm.core.tenants import SpaceRegistry

# What `_sync_mailbox` answers for a cycle another run holds: neither a completed cycle nor a
# refusal, which is not an exception class and so cannot be mistaken for one.
_CYCLE_RUNNING = "cycle running"


def gmail_sync(settings: Settings, email: str | None) -> int:
    """`pigrocrm gmail-sync [--email casella@dove.it]`: one cycle per connected mailbox of
    the root installation and of every space in the registry, for cron.

    There is no daemon and no queue in this product (see `gmail/sync.py`), so the
    fifteen minutes are cron's to keep. This is the whole contract with it: one line per
    mailbox, stdout when its cycle ran, stderr with a sentence when it could not. Exit 0
    when every line went to stdout, which is every cycle ran or found another already
    running; exit 1 when at least one went to stderr, which is a mailbox that could not
    be synchronised, the registry or a space that could not be read, or no mailbox found.
    The runbook is `docs/superpowers/notes/2026-09-09-gmail-cron-runbook.md`. Like the
    other cron commands it migrates nothing (REB-404, ORB-189).

    **Counters and ids only, never an address.** The line names the mailbox by its account
    id, and a refusal says the class or a sentence that names no address: the log is
    appended to a file on the host (B-16). A space whose settings or mailboxes cannot be
    read, or a failure nobody foresaw, is `skipped` with the exception's type, followed by
    the frames of its traceback and never its message, which can carry the URL (a psycopg
    error) or a statement's parameters.

    Each space syncs with **its own effective settings**, `SpaceRegistry.effective_settings`,
    the same answer the API gives a request to that space: the root's client borrowed or
    the space's own, and the token key derived for that space, which is the only key that
    opens the refresh tokens its callback sealed. And its own `GoogleTokenClient`: the
    client caches access tokens by account id alone, so one shared across spaces could hand
    a row copied from another space's database a live token.

    **Every connected mailbox, not one.** Each mailbox gets its cycle, in a session of its
    own. `--email` narrows the run to the mailboxes with that address, wherever they are. A
    space with no connected mailbox prints nothing: most spaces never connect one. A run
    that read every installation and found no mailbox says so once; a run that could not
    read the registry or a space does not, since the mailbox may be in the part it did not
    read.

    **Which actor, and why it is not `Actor.system()`.** `GmailSyncService.sync` refuses an
    actor with no id, correctly: there is one mailbox per user, and a sync that read "the
    first row in the table" would spend one person's Google quota under another's consent.
    So this builds a *system* actor carrying the mailbox owner's id and the owner's own
    role (`_cron_actor`): the id says *whose* credential is being spent, and
    `type="system"` says nobody pressed anything. It widens nothing: a personal access
    token still resolves to `type="mcp"`, whatever this process does.

    **The space's clock** (REB-764). After each space that has a connected mailbox the run
    writes its row in `reminder_job_run`: a success moves `ran_at`, a failure records the
    first refusal's exception class. A run narrowed with `--email` writes nothing, and a
    clock that cannot be written after a cycle that worked is one line on `stderr` and so
    exit 1."""
    walk = read_spaces(settings)
    failed = unread = walk.unreadable is not None
    if walk.unreadable is not None:
        # Not a `return`: the root is not in the registry and is still synced.
        print(
            f"{_now()} gmail-sync: registro degli spazi non raggiungibile ({walk.unreadable})",
            file=sys.stderr,
        )

    # Only `effective_settings` is asked of it, on each space's own session.
    settings_of = SpaceRegistry(settings)
    connected: list[str] = []
    attempted = 0
    for target in walk.targets:
        label = target.label
        with space_engine(target.url) as engine:
            factory = session_factory(engine)
            try:
                with factory() as session:
                    space_settings = settings_of.effective_settings(target.slug, session)
                    # A mailbox somebody disconnected is not one: `usable` would refuse it
                    # with «nessuna casella Google collegata» one line later.
                    mailboxes = [
                        (account.id, account.email_address)
                        for account in GmailRepository(session).all_accounts()
                        if account.status != "disconnected"
                    ]
            except Exception as exc:  # noqa: BLE001 - one space must not stop the others
                print(
                    f"{_now()} gmail-sync {label}: saltato ({type(exc).__name__})", file=sys.stderr
                )
                # Which read failed, the space's settings or its mailboxes: the frames say
                # it and the type alone does not. The message stays out.
                traceback.print_tb(exc.__traceback__, file=sys.stderr)
                failed = unread = True
                if email is None:
                    record_job(engine, GMAIL_SYNC_JOB, label, type(exc).__name__, quiet=True)
                continue
            connected.extend(address for _, address in mailboxes)
            if email is not None:
                mailboxes = [
                    (account_id, address)
                    for account_id, address in mailboxes
                    if address.casefold() == email.casefold()
                ]
            if not mailboxes:
                continue
            transport = GmailTransport()
            tokens = GoogleTokenClient(
                client_id=space_settings.google_client_id,
                client_secret=space_settings.google_client_secret,
                transport=transport,
            )
            # What each mailbox's cycle said, `None` when it ran. The space's clock is one
            # row, so it records the first failure of the space, which is what the panel
            # shows next to «Gmail non riesce».
            refusals: list[str] = []
            skipped = False
            for account_id, _ in mailboxes:
                attempted += 1
                outcome = _sync_mailbox(
                    factory, space_settings, transport, tokens, f"{label} {account_id}", account_id
                )
                if outcome == _CYCLE_RUNNING:
                    skipped = True
                elif outcome is not None:
                    failed = True
                    refusals.append(outcome)
            # A run narrowed to one address by `--email` is not the space's sync: it would
            # clear a failure of a mailbox it did not visit. A mailbox whose cycle another
            # run still holds did not complete one either, so a space with one of those and
            # no refusal records nothing, and a sync stuck for good then goes late instead
            # of reading «in regola».
            if (
                email is None
                and (refusals or not skipped)
                and not record_job(engine, GMAIL_SYNC_JOB, label, refusals[0] if refusals else None)
            ):
                failed = True

    if not attempted:
        reason = _nothing_to_sync(email, len(connected), unread=unread)
        if reason is not None:
            print(f"{_now()} gmail-sync: {reason}", file=sys.stderr)
        failed = True
    return 1 if failed else 0


def _nothing_to_sync(email: str | None, connected: int, *, unread: bool) -> str | None:
    """What a run that synchronised nothing says about it, or `None` when it cannot say.

    «nessuna casella Google collegata» is a claim about every installation, and with the
    registry or a space unread the mailbox may be exactly in the part that was not read:
    that line has already said what went wrong, and a second one guessing the rest
    would send the operator to connect a mailbox that may well be connected. An
    `--email` that matched nothing is the same claim narrowed to one address."""
    if email is None:
        return None if unread else "nessuna casella Google collegata"
    if unread:
        return (
            f"{email} non è fra le caselle degli spazi letti ({connected} collegate): "
            "uno spazio non è stato letto, e potrebbe essere lì"
        )
    if connected:
        return f"{email} non è una casella collegata ({connected} collegate)"
    return "nessuna casella Google collegata"


def _sync_mailbox(
    factory: sessionmaker[Session],
    settings: Settings,
    transport: GmailTransport,
    tokens: GoogleTokenClient,
    label: str,
    account_id: UUID,
) -> str | None:
    """One mailbox's cycle and its line: `None` when the cycle ran, `_CYCLE_RUNNING` when
    another run held it and nothing was done, and otherwise the name of the exception class
    that stopped it (what the space's clock records). Its own session, opened and closed
    inside the guard: a cycle that fails halfway leaves nothing open for the next mailbox,
    and a session that cannot be opened, or closed, costs this mailbox its line and not the
    rest of the run."""
    try:
        with factory() as session:
            account = GmailRepository(session).account(account_id)
            if account is None:
                # Removed between the listing and now: the owner disconnected it.
                raise Conflict("google_account", "nessuna casella Google collegata")
            actor = _cron_actor(session, account)
            # The owner's own scope, never «tutte» (spec 2026-10-03 §4): a scoped
            # owner's mailbox is matched against the customers and people their scope
            # can see, so the sync writes no link on another legal entity's customer.
            bind_scope(session, actor)
            report = GmailSyncService(
                session, settings=settings, transport=transport, tokens=tokens
            ).sync(actor)
    except DomainError as exc:
        print(f"{_now()} gmail-sync {label}: {_reason(exc)}", file=sys.stderr)
        # The class and not the sentence: the clock is read by every admin and by agents.
        # The panel words each class.
        return type(exc).__name__
    except Exception as exc:  # noqa: BLE001 - one mailbox must not stop the others
        print(f"{_now()} gmail-sync {label}: saltato ({type(exc).__name__})", file=sys.stderr)
        traceback.print_tb(exc.__traceback__, file=sys.stderr)
        return type(exc).__name__
    # `flush`: under `docker compose exec -T` stdout is a pipe, so Python buffers it by
    # the block while stderr goes out at once, and `2>&1` in the cron line would put every
    # failure of the run ahead of the root's line in the log.
    print(f"{_now()} gmail-sync {label}: {_outcome(report)}", flush=True)
    return _CYCLE_RUNNING if report.already_running else None


def _reason(exc: DomainError) -> str:
    """The sentence, without the machine-readable prefix.

    `DomainError` carries structured details and composes `message` for the adapters
    that render them -- `Conflict` as `f"{entity}: {reason}"`. In a log an operator
    reads, `google_account:` in front of «nessuna casella Google collegata» says
    nothing they can act on, so the bare `reason` is printed when there is one."""
    reason = exc.details.get("reason")
    return reason if isinstance(reason, str) else exc.message


def _cron_actor(session: Session, account: GoogleAccount) -> Actor:
    """Who the cron acts as: the mailbox's owner, with their own role, as the system.

    Two refusals rather than an escalation, and they are different questions. Neither
    sentence names an address: the label of the line already names the mailbox.

    **A deactivated owner.** Deactivating somebody is, everywhere else in this product,
    the moment they stop being able to make the CRM do anything. A cron that kept
    synchronising would go on spending the Google consent of a person the owner has
    just switched off, quietly, every fifteen minutes.

    **The owner's real role.** Whether the sync may run is a decision this installation
    already made about that person, so the actor carries their role and `sync`'s own
    `require_write` decides: a `readonly` owner's mailbox must not synchronise from cron
    while the Sincronizza button in their own settings page refuses them. The check below
    only exists to say it in Italian first -- `PermissionDenied`'s message is written for
    an API, not for a log."""
    try:
        user = UserRepository(session).get_active(account.user_id)
    except ValidationFailed as exc:
        raise Conflict(
            "google_account",
            "la casella appartiene a un utente disattivato: "
            "il sync resta fermo finché non viene riattivato",
        ) from exc
    actor = actor_for(user, "system")
    if not actor.can_write:
        raise Conflict(
            "google_account",
            f"il titolare della casella ha il ruolo {label(USER_ROLE, user.role)} e non può "
            f"sincronizzarla: "
            "il cron non agisce con più diritti del titolare",
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
