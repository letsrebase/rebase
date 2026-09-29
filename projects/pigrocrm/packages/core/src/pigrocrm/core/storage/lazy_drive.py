"""Drive as a `DocumentStorage`, on the titolare's own credential, resolved late.

**Why late.** The service-account backend is fully described by two environment
variables, so `storage_from_settings` can build it, verify its root folder, and refuse
the configuration by name the moment it is asked for one -- start-up on the MCP adapter,
the first document operation on the API (`factory.py` says which and why). This one is
described by a *row*: the connected Google account and the folder its owner chose in
Impostazioni → Drive. That row does not exist on a fresh installation, and the API has
to start anyway -- otherwise the only screen that could create it is unreachable, and
`PIGROCRM_STORAGE_BACKEND=gdrive` becomes a setting nobody can ever finish configuring.
So construction reads nothing at all, and the first `put`/`get`/`delete` is where the
answer is either found or refused with `StorageNotConfigured`.

**Why it re-reads the row.** The folder is a settings field a person edits while the
process is running. A storage that resolved once and cached forever would keep writing
into the folder that was configured at the time of the first upload -- after the
titolare moved it, after they disconnected Drive, after Google revoked the grant -- with
nothing on any screen to say why the documents are not where they should be. So every
operation reads the row (one indexed statement on a single-tenant table) and rebuilds
the transport only when `updated_at` says the row changed. That distinction is what
keeps the `GoogleTokenClient`'s access-token cache alive across uploads: rebuilding it
per call would turn one upload into one OAuth round-trip per Drive request.

**What it does not do.** No placement, no keys, no HTTP: it holds a `GDriveStorage` and
delegates. The only behaviour of its own is resolution, and reacting to the one failure
that invalidates it -- a revoked grant, recorded on the row so the Drive settings page can say
so, then re-raised **as `StorageUnreachable`** (REB-562 fix round 3): the holder's own
`DriveCredentialRevoked`/`DriveConsentExpired` names that admin's email, and every
operation through here can be reached by any actor with access to the document, not
only the holder. See `_run`'s own docstring.
"""

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TypeVar
from uuid import UUID

from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.config import Settings
from pigrocrm.core.drive.account import GoogleDriveAccountService, missing_scope_conflict
from pigrocrm.core.drive.errors import DriveConsentExpired, DriveCredentialRevoked
from pigrocrm.core.drive.models import GoogleDriveAccount
from pigrocrm.core.drive.repository import DriveRepository
from pigrocrm.core.drive.schemas import DRIVE_SCOPE_FILE
from pigrocrm.core.drive.transport import HttpCall, user_transport_for
from pigrocrm.core.gmail.tokens import GoogleTokenClient
from pigrocrm.core.storage.errors import StorageNotConfigured, StorageUnreachable
from pigrocrm.core.storage.gdrive import GDriveStorage

logger = logging.getLogger(__name__)

# A callable that opens a `Session` the storage then owns and closes -- in production
# the `sessionmaker` the adapter already builds. Not a `Session`: this object outlives
# any single request (it is built once per process, like the storage it replaces),
# while a session must not.
SessionFactory = Callable[[], Session]

_T = TypeVar("_T")

# What the user reads on the settings page after Google drops the grant. Written here,
# at the call site, rather than derived from the exception -- the same rule
# `gmail/sync.py` follows when it records a revoked mailbox: no error code, no upstream
# prose, no token, and a sentence that says what to do.
_REVOKED_REASON = (
    "Il consenso Google Drive per {email} è stato revocato: la scrittura dei documenti "
    "è sospesa finché non ricolleghi Drive da Impostazioni → Drive."
)

# The names the missing-scope refusal gives what it is refusing. A *feature*, not a
# call: `missing_scope_conflict` writes "<feature> non è disponibile: manca
# l'autorizzazione ...", so the noun has to be one the person reading it recognises as
# the thing they were trying to do.
#
# Two of them, because `drive.file` gates every operation of this backend while the
# operations are not the same thing to whoever met the refusal. On a `put` or a `delete`
# "la scrittura dei documenti" is exactly right and more precise than any generality. On
# a `get` -- somebody opening a proforma -- it names an operation nobody asked for, and
# invites the reader to conclude that reading is fine and something else is broken. So a
# read is refused over the archive as a whole, which is what is actually unavailable;
# the guidance is identical either way, because the fix is.
_FEATURE_WRITE = "la scrittura dei documenti su Drive"
_FEATURE_READ = "l'archivio documenti su Drive"


@dataclass(frozen=True)
class _Resolution:
    """One answer to "which account, which folder", and the stamp that dates it.

    `updated_at` is the whole reason this is a class and not just the storage: it is
    compared against the row on every operation, and a difference means the person
    changed something and the transport has to be rebuilt.
    """

    account_id: UUID
    email_address: str
    updated_at: datetime
    storage: GDriveStorage


class LazyUserDriveStorage:
    """A `DocumentStorage` that finds its Drive account at the first operation.

    `session_factory` rather than a `Session`, and `settings` rather than pre-built
    credentials, because this object is constructed once per process while both the
    session and the credential are things it has to obtain again later.

    **Resolution is serialised, because this object is shared.** The instance that
    reaches production is built once per process and used by every request (FastAPI runs
    sync endpoints in a thread pool, and the MCP adapter dispatches concurrently too),
    so two operations can meet a cold -- or newly invalidated -- cache at the same
    instant. Nothing here would corrupt without a lock: `_resolution` is replaced
    wholesale by a single assignment, never mutated in place. What would happen instead
    is waste and a double record: each thread would resolve, each would build its own
    transport, and one of the two `GoogleTokenClient`s would be discarded along with the
    access token it had just paid an OAuth round-trip for -- and a revoked grant
    discovered by both would be written to the row twice. So `_lock` guards `_resolve`
    end to end, and guards the moment `_record_revocation` claims -- by identity -- the
    very resolution its caller's failure came out of (the same shape, and the same
    reason, as `pigrocrm_api.deps`'s own `_engine_lock`) -- which makes "one resolution,
    one transport, one token, one revocation recorded against the credential that
    actually failed" true by construction rather than by timing.

    The lock is held across the row read, not merely across the assignment, and that is
    a deliberate trade whose full price is worth stating. It costs, per storage
    operation: a session opened from `session_factory` -- which in production checks a
    connection out of the pool and *can block* there if every connection is busy -- and
    one indexed statement on a single-tenant table, both serialised across every thread
    doing document work. What it buys is a check-then-build that cannot interleave. A
    lock taken only around the assignment would let both threads build first and then
    argue about which build to keep, which is the whole cost this exists to avoid; a
    read taken before the lock would be a read whose answer may already be stale by the
    time the lock is acquired, so it would have to be taken again anyway.

    What is *not* held under it is the part that talks to Google: no Drive request and no
    token exchange happens inside `_resolve` (`user_transport_for` composes a transport
    and calls nothing), and `_run` resolves, releases, and only then uploads -- so
    concurrent uploads stay concurrent and the serialised window stays a database
    window, never a network one.
    """

    def __init__(
        self,
        session_factory: SessionFactory,
        settings: Settings,
        *,
        http: HttpCall | None = None,
        tokens: GoogleTokenClient | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._settings = settings
        # Both are seams for the tests, and both are the ones `user_transport_for`
        # already declares: `http` is the Drive network, `tokens` is Google's token
        # endpoint. Two, not one, because they are two different hosts behind two
        # different fakes -- and because a single fake for both would let a composition
        # that used the wrong credential still pass.
        self._http = http
        self._tokens = tokens
        self._resolution: _Resolution | None = None
        # Guards every read and every write of `_resolution` -- see the class
        # docstring. Not reentrant, and it does not need to be: `_resolve` and
        # `_record_revocation` are the only holders, `_run` calls them one after the
        # other rather than one inside the other, and nothing either of them calls
        # comes back into this object.
        self._lock = threading.Lock()

    # ---- resolution -------------------------------------------------------------

    def _resolve(self, feature: str) -> _Resolution:
        """The account that may be written into, right now.

        `feature` is what the one refusal raised from here -- the missing-scope
        `Conflict` -- calls the operation it is refusing, passed down from `_run` so that
        a download is not turned away in the words of an upload. It reaches nothing else:
        every other answer this method gives is the same for all four operations.

        Raises `StorageNotConfigured` when there is none -- which is the same answer for
        "Drive was never connected", "no folder was chosen yet", "the person
        disconnected it" and "the grant was revoked": all four are fixed on the same
        screen, and telling them apart here would only invite four sentences that
        describe the same next action.

        A connected, configured account whose grant is missing `drive.file` is the one
        state that gets its own refusal, because it is the one with a *different* next
        action: not "connect Drive and choose a folder" -- both are done -- but "grant
        the authorisation this one is short of". See the check below.

        Under `_lock` from the row read to the assignment (class docstring): the read is
        what decides whether to build, so a concurrent caller that slipped in between
        the two would build a second transport for an answer this one had already found.
        """
        with self._lock:
            with self._session_factory() as session:
                account = DriveRepository(session).storage_account()
                if account is None or account.storage_folder_id is None:
                    # Dropped, not kept: a stale resolution would otherwise keep writing
                    # into a folder the row no longer names if the account came back.
                    self._resolution = None
                    raise StorageNotConfigured()
                if DRIVE_SCOPE_FILE not in account.scopes_granted:
                    # The scope that lets this credential create a file at all, checked
                    # here because nothing between here and Google does. Without it the
                    # upload reaches `files.create` and comes back a 403, which
                    # `GDriveStorage` reports as «caricamento su Drive fallito (403)»:
                    # an outage-shaped sentence for a configuration problem, naming
                    # neither the missing authorisation nor the screen that grants it.
                    # A person can reach this state honestly -- the consent screen lets
                    # fewer scopes be ticked than were asked for, and a client whose
                    # scope list grows leaves older grants short of one.
                    #
                    # Not `StorageNotConfigured`, and the difference is the next action:
                    # nothing here is unconfigured (the account is connected, the folder
                    # is chosen), so "collega Drive e scegli la cartella" would send
                    # somebody to redo work that is already done. The resolution is
                    # dropped for the same reason it is dropped above, and the scope
                    # lives on the row this re-reads, so a re-authorisation takes effect
                    # at the next operation rather than at the next restart.
                    self._resolution = None
                    raise missing_scope_conflict(DRIVE_SCOPE_FILE, feature)
                current = self._resolution
                if current is not None and (
                    current.account_id == account.id and current.updated_at == account.updated_at
                ):
                    return current
                resolved = _Resolution(
                    account_id=account.id,
                    email_address=account.email_address,
                    updated_at=account.updated_at,
                    storage=GDriveStorage(
                        transport=user_transport_for(
                            account, self._settings, http=self._http, tokens=self._tokens
                        ),
                        root_folder_id=account.storage_folder_id,
                    ),
                )
            # Outside the session's `with`: the session is closed and the account row is
            # detached, and nothing above this line reads it again. Assigned after the
            # block for the same reason -- a failure while composing must not leave a
            # half-built resolution cached.
            self._resolution = resolved
            return resolved

    def _record_revocation(self, resolution: _Resolution) -> None:
        """Marks the row revoked, in its own session, and forgets the resolution.

        `resolution` is the one the failure came out of, passed in by `_run` rather than
        read from `self` -- because `self._resolution` means "whichever account is
        cached *now*", and this object is shared by every request in the process and
        re-resolves whenever the row changes. A settings change, a disconnect and
        reconnect, or a concurrent operation that met a newer row can all replace it
        between the failed refresh and this call, and what got written then was a
        revocation stamped on a credential that had never been asked for anything: a
        healthy Drive painted red on the Drive settings page, and the one that actually failed
        left `active`.

        Its own session because `mark_revoked` commits: the operation that discovered
        this is about to raise and its caller's transaction will be rolled back, so the
        fact has to be written on its own behalf or it does not survive the request
        that learned it. Same contract, same reason, as `GmailSyncService._access_token`.

        The row may already be gone (a user deleted between the failed refresh and
        here), in which case there is nothing to record and the revocation still
        propagates: the caller's failure does not depend on the bookkeeping succeeding.

        **And that sentence is enforced, not merely intended.** Everything about this
        second session can fail on its own account -- a pool with no connection left,
        a row somebody else is holding, a `commit` that loses a race -- and every one of
        those failures would, unguarded, escape from inside `except
        DriveCredentialRevoked` and *replace* the revocation. The caller would then read
        a database error instead of "il consenso è stato revocato" (a 500 where a 409
        belongs), and the row would be left `active` regardless, so the reader would
        lose both the sentence and the record. So the whole body is guarded and nothing
        is re-raised: the original refusal always wins.

        Swallowing is the right trade here and not merely the convenient one. The state
        this failed to write is re-derivable -- the next refresh meets the same
        `invalid_grant` and tries again, which is exactly the retry a momentarily
        unavailable database needs -- while the exception it would have replaced is not
        re-derivable at all, because it is what the caller is being told.
        """
        # Claimed under `_lock`, written outside it -- and claimed by *identity*, which
        # is what makes the record happen at most once and always about the right row.
        # Two threads that both met the revocation on the same resolution arrive here,
        # and the second finds it already cleared and returns without writing; a thread
        # whose resolution has since been replaced finds a different object and returns
        # too, leaving the newer account alone. Equality would not do: two resolutions of
        # the same row are two different attempts, and the second one has not failed.
        #
        # Nothing is written in that last case, deliberately. The state this skips is
        # re-derivable -- the next operation resolves again and meets the same
        # `invalid_grant`, which is the retry this bookkeeping is best effort for --
        # while a fact recorded against an account that was never asked for a token is
        # not correctable by anything.
        #
        # The write itself commits and must not be holding a lock every other
        # operation's resolution waits on -- and it does not need to, because the thread
        # that got here holds the only reference to that resolution.
        with self._lock:
            if self._resolution is not resolution:
                return
            self._resolution = None
        try:
            session = self._session_factory()
            try:
                account = session.get(GoogleDriveAccount, resolution.account_id)
                if account is not None:
                    GoogleDriveAccountService(session, settings=self._settings).mark_revoked(
                        account,
                        # Google revoked this, nobody in this CRM did -- the actor every
                        # `mark_revoked` call site passes.
                        Actor.system(),
                        _REVOKED_REASON.format(email=account.email_address),
                    )
            except Exception:
                # A half-applied `mark_revoked` (status set, activity not yet recorded,
                # commit not reached) must not be left pending on a session something
                # later could flush, and a pooled connection must go back clean rather
                # than mid-transaction. `close()` below would roll back anyway; saying
                # so here is what makes it a decision instead of a side effect.
                session.rollback()
                raise
            finally:
                session.close()
        except Exception:
            # Deliberately swallowed -- see the docstring. The outer `try` wraps the
            # factory call, the rollback and the close as well as the write, because a
            # guard that only covered the write would still let a failing `rollback()`
            # or a failing `close()` escape and do the exact harm this exists to
            # prevent.
            #
            # `Exception` and not a list of SQLAlchemy classes: the point is that
            # *nothing* raised in here may reach the caller in place of the revocation,
            # and a list is a list somebody has to keep complete. `BaseException` still
            # passes, so a `KeyboardInterrupt` or a task cancellation is not absorbed by
            # bookkeeping.
            pass

    def _run(self, operation: Callable[[GDriveStorage], _T], feature: str) -> _T:
        """Resolve, delegate, turn a revoked grant into a recorded fact, and never let
        the holder's own exception past this method (REB-562 fix round 3).

        `feature` names, in Italian, what the caller was doing, and is used by exactly
        one thing: the sentence `_resolve` refuses a scope-short grant with. It is a
        parameter rather than a constant because reading and writing are not the same
        operation to the person reading that sentence -- see `_FEATURE_WRITE` and
        `_FEATURE_READ`.

        One place, four methods: a revocation can surface from any Drive call (the token
        refresh happens inside each of them), so a per-method `try` would be four
        chances to forget one.

        **The exception is translated, not re-raised.** `DriveCredentialRevoked` and
        `DriveConsentExpired` both name the holder's account -- `email_address` in the
        sentence and in `details`, which `domain_error_handler`
        (`pigrocrm_api/errors.py`) spreads into the JSON body verbatim -- and this
        method is reached by *any* actor with access to the document, not only the
        admin whose credential this is. `GoogleDriveAccountService.usable`'s own
        callers keep the named version (the viewer *is* the account owner there,
        `drive/account.py`), but this shared path answers with `StorageUnreachable`
        instead, which names nobody. The email is not lost: it is logged, once, before
        the sanitised error replaces the original -- `from failed` keeps the real cause
        on the traceback for anyone reading the log, without putting it on the wire.

        `DriveConsentExpired` cannot reach here today -- nothing on this path predicts
        an expiry the way `GoogleDriveAccountService.usable` does, only Google's own
        `invalid_grant` ever surfaces from a real token refresh -- but it is caught
        anyway: the two are the same shape and the same leak, and a future call this
        method starts making would otherwise have to remember this rule a second time.
        """
        resolution = self._resolve(feature)
        try:
            return operation(resolution.storage)
        except (DriveCredentialRevoked, DriveConsentExpired) as failed:
            if isinstance(failed, DriveCredentialRevoked):
                # The resolution this operation actually used, not whatever is cached
                # by the time the failure surfaces -- see `_record_revocation`.
                self._record_revocation(resolution)
            logger.warning(
                "drive storage: holder %s failed with %s; the caller sees no account information",
                resolution.email_address,
                type(failed).__name__,
            )
            raise StorageUnreachable() from failed

    # ---- DocumentStorage --------------------------------------------------------

    def put(self, key: str, data: bytes, content_type: str) -> None:
        self._run(lambda storage: storage.put(key, data, content_type), _FEATURE_WRITE)

    def get(self, key: str) -> bytes:
        return self._run(lambda storage: storage.get(key), _FEATURE_READ)

    def delete(self, key: str) -> None:
        # A write, and named as one: removing a document from Drive is the operation a
        # missing `drive.file` most obviously stops.
        self._run(lambda storage: storage.delete(key), _FEATURE_WRITE)

    def signed_url(self, key: str, ttl: timedelta) -> str | None:
        """`None`, like both other backends -- but only after resolving.

        `GDriveStorage.signed_url` never calls Drive, so this is the one method that
        could have skipped resolution. It does not: `None` from a backend that is not
        configured is indistinguishable from `None` from one that is, so a caller
        checking whether a direct link exists would learn nothing and the
        misconfiguration would surface somewhere else entirely.
        """
        return self._run(lambda storage: storage.signed_url(key, ttl), _FEATURE_READ)
