"""Read-only Drive, confined to the folders the titolare pointed at.

Spec 9C names the failure to avoid: reading somebody's Drive. A personal Drive holds
the photos of their children, their rent contract, the folder of another job. So this
module can do exactly three things -- list the children of a folder, fetch the bytes of
one file, turn those bytes into text -- and each of them refuses outright unless the
subject is inside `google_drive_accounts.root_folder_ids`.

**Where the confinement lives.** `drive/query.py` is the first guard: it will not build
a `files.list` that is not scoped to one folder, and it has no search-string builder at
all. That is not enough on its own, because `children_query("<a folder of somebody's
Drive>")` is perfectly well-formed. The second guard is here, and it is
`is_within_roots`: `files.get?fields=id,parents` walked upward until a configured root
is reached, the Drive's own root is reached, or `MAX_ROOT_WALK_LEVELS` levels have been
spent. Outside the roots, `list_children` and the two `read_*` raise `NotFound` --
**before** any `files.list` is issued, so there is no window in which Google was asked
about a folder nobody configured. The third guard is
`tests/test_drive_reader.py`'s closing test, which re-reads every request the fake
transport received across the whole module and checks each listing named a folder of
the hierarchy.

**One sentence for three different situations.** A file that does not exist, a file in
an unconfigured corner of the titolare's own Drive and a file in a stranger's Drive all
answer `NotFound("drive_file", <id>)`, in the same words. Distinguishing them would
answer "is there a file with this id?" for anybody who can reach the tool, which is an
existence oracle over all of Google Drive.

**Two severities for an id, from `query.py`.** Every id a *caller* supplies -- the
`folder_id` of a listing, the `file_id` of a read -- is checked against the strict
pattern before anything happens, and that check is `query.py`'s own rather than a
second regex here: two patterns for one rule is one pattern that will drift. Ids that
came out of Drive's own answers (a child of a listing, a parent of a metadata call)
pass only the builders' laxer check, for the reason that module's docstring gives at
length.

**Nothing here writes.** Not a `files.create`, not a `files.delete`, not an upload:
`storage/gdrive.py` remains the only writer of Drive, on its own credential, and 9D is
what imports a file into a document. A reader that could also write would make "read
these folders" a promise held up by nobody checking.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, TypeVar

from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.config import TEXT_MAX_BYTES_DEFAULT, Settings
from pigrocrm.core.drive.account import GoogleDriveAccountService
from pigrocrm.core.drive.errors import DriveCredentialRevoked
from pigrocrm.core.drive.query import (
    FOLDER_MIME,
    # Re-exported, not used here: `checked_outside_id`'s rule as a JSON Schema
    # `pattern`. It lives in `query.py`, beside the check it spells (one rule, one
    # place), and is visible from this module too because the Drive reads an adapter
    # publishes are reached through the reader rather than through the query builder.
    OUTSIDE_ID_PATTERN,  # noqa: F401
    # The strict shape of an id that arrives from outside, imported rather than
    # rewritten: `query.py` is where that shape is defined, and a copy here would be a
    # second pattern to keep in step with it. The `field=` is what makes it usable for
    # a file as well as a folder -- see that function's docstring.
    checked_outside_id,
    children_query,
    file_export_url,
    file_media_url,
    file_meta_url,
    files_list_url,
)
from pigrocrm.core.drive.schemas import DRIVE_SCOPE_READONLY
from pigrocrm.core.drive.transport import TRANSPORT_ENTITY, DriveTransport, user_transport_for
from pigrocrm.core.errors import Conflict, NotFound
from pigrocrm.core.text import PLAIN_TEXT_MIME, FileText, file_text

# The entity every refusal of this module is reported under. One name for a folder and
# a file alike, deliberately: the entity is part of what a caller reads, and saying
# `drive_folder` for one and `drive_file` for the other would tell apart the two cases
# the identical message above exists to keep indistinguishable.
#
# *Every* refusal, including the ones the transport raises: a Google failure mid-read
# arrives under `TRANSPORT_ENTITY` (the storage's subject) and `_guarded` re-stamps it
# here, so this constant is a property of the module rather than of the call sites that
# happen to remember it.
ENTITY = "drive_file"

# How far up the parent chain a file may be from a configured root. Drive lets a file
# have several parents, so "walk upward" is a graph and not a path: without a bound, a
# cycle (which a Drive bug can produce, and which this code must survive rather than
# trust away) would spin against Google forever. Twenty is far past any real filing --
# a folder nested twenty deep under a root is a folder nobody navigates by hand.
MAX_ROOT_WALK_LEVELS = 20

# And how many `files.get` calls that walk may spend in total. The level bound is not
# enough on its own: a file may have many parents, so one level can be three hundred
# folders wide, and twenty levels of that is a walk that spends somebody's Drive quota
# to answer one question. A file whose ancestry fans out past this is reported outside
# the roots -- the safe direction, and a filing with two hundred distinct ancestors is
# not one anybody navigates.
MAX_ROOT_WALK_REQUESTS = 200

# The ceiling on the bytes of a single file, and the reason it is not the text ceiling:
# a 4 MB PDF is an ordinary contract whose text is eight kilobytes, so sharing one
# number with `read_text` would refuse the documents this slice exists to read. Above
# it a read is refused rather than cut, because half a PDF is not a PDF -- truncation
# is meaningful for text and meaningless for bytes. Same 20 MB as
# `gmail_attachment_max_bytes`, which is the same question about the same kind of file.
DOWNLOAD_MAX_BYTES = 20_971_520

# `application/vnd.google-apps.document` and its siblings. A native Google file has no
# bytes of its own on Drive -- `alt=media` on one answers 403 -- so a Doc is read
# through `files.export`, and a Sheet or a Slide deck has no text export this slice
# would know what to do with.
GOOGLE_DOC_MIME = "application/vnd.google-apps.document"
_GOOGLE_NATIVE_PREFIX = "application/vnd.google-apps."

# What each call asks for, in the words a failure will carry (`DriveTransport` puts
# `what` into the `Conflict` a person reads). Italian, like every other user-facing
# sentence in this codebase.
_WHAT_LIST = "elenco dei file della cartella Drive"
_WHAT_META = "lettura dei dati del file Drive"
_WHAT_DOWNLOAD = "download del file da Drive"
_WHAT_EXPORT = "conversione in testo del documento Google"

_ENTRY_FIELDS = "id, name, mimeType, size, modifiedTime, parents"
_LIST_FIELDS = f"nextPageToken, files({_ENTRY_FIELDS})"
# The walk upward needs nothing but the edges of the graph.
_PARENT_FIELDS = "id, parents"

_NOT_A_FOLDER = "una cartella non ha byte da leggere: elencane i figli"
_NO_BYTES = "un file {mime} non ha byte da scaricare né un export in testo"
_TOO_BIG = "il file supera {max_bytes} byte: aprilo su Drive invece di importarlo"

# The entity a refusal *about the account* is reported under, as opposed to `ENTITY`,
# which is the file or folder being read. Same string `GoogleDriveAccountService` uses
# for its own gates, so an adapter that routes on the entity sends a person to the same
# settings screen whichever of the refusals they met.
_ACCOUNT_ENTITY = "google_drive_account"

# Connected, but pointed at nothing. Named as a configuration step and not as an error,
# because that is what it is -- and it says which folders, because "configure Drive" is
# advice somebody who already connected Drive has no way to act on.
_NO_ROOTS = (
    "{feature} non è disponibile: non c'è nessuna cartella radice configurata. "
    "Indica da Impostazioni → Drive le cartelle che il CRM può leggere."
)

_REVOKED_REASON = (
    "Il consenso Google Drive per {email} è stato revocato: la lettura dei documenti "
    "è sospesa finché non ricolleghi Drive da Impostazioni → Drive."
)

_T = TypeVar("_T")


@dataclass(frozen=True)
class DriveEntry:
    """One child of a folder. Italian field names, like every other schema a person or
    an agent reads.

    `dimensione` is `None` and not `0` for a folder or a native Google file: those have
    no bytes on Drive to measure, and `0` would say "an empty file", which is a
    different and false statement. Same for `modificato_il`, which Drive can omit.
    """

    id: str
    nome: str
    mime: str
    dimensione: int | None
    modificato_il: datetime | None
    cartella: bool


@dataclass(frozen=True)
class DriveListing:
    """One page of children. `next_page_token` is Drive's own: the reader never chooses
    a page size (`files_list_url` cannot even express one), so how a folder is split
    into pages is Drive's business and this token is how the caller asks for the rest.
    """

    items: list[DriveEntry]
    next_page_token: str | None


class DriveReader:
    """Three operations on the configured roots, and no way to reach anything else.

    Holds a `DriveTransport` and a list of root folder ids -- no session, no settings,
    no `Actor`: whether this credential may be used at all was decided before it was
    built (`GoogleDriveAccountService.usable`, through `drive_reader_for`), and a reader
    that could re-decide it would be a second gate to keep in step with the first.

    `on_revoked` is the one callback: a revoked grant is learned here, deep in an HTTP
    call, and recorded by whoever owns the account row. See `drive_reader_for`.
    """

    def __init__(
        self,
        transport: DriveTransport,
        *,
        roots: Sequence[str],
        text_max_bytes: int = TEXT_MAX_BYTES_DEFAULT,
        download_max_bytes: int = DOWNLOAD_MAX_BYTES,
        on_revoked: Callable[[], None] | None = None,
    ) -> None:
        self._transport = transport
        self._roots = tuple(roots)
        self._text_max_bytes = text_max_bytes
        self._download_max_bytes = download_max_bytes
        self._on_revoked = on_revoked

    # ---- the three operations --------------------------------------------------------

    def list_children(self, folder_id: str, *, page_token: str | None = None) -> DriveListing:
        """The children of a folder inside the roots, one Drive page at a time."""
        checked = checked_outside_id(folder_id, field="folder_id")
        return self._guarded(lambda: self._list_children(checked, page_token))

    def describe(self, file_id: str) -> DriveEntry:
        """The metadata of one file or folder inside the roots, as a listing would show
        it.

        Not a fourth capability over Drive: it is the `files.get` `list_children` and
        `read_bytes` already make, answered to the caller instead of consumed
        internally, and it is confined by the same `is_within_roots` check they are --
        including the same single sentence for a file that does not exist, a file in an
        unconfigured corner of the titolare's Drive and a file in a stranger's Drive
        (see the module docstring: telling those apart is an existence oracle over all
        of Google Drive).

        It exists because two callers need a *name* and there was no way to ask for
        one. An import records where a document came from -- `{"drive_file_id": ...,
        "mime": ..., "nome": ...}` -- and "a file was uploaded" is not an answer to
        "where did this come from?" years later; and `describe_roots` below needs the
        name of a folder that has no listable parent to have appeared in.
        """
        checked = checked_outside_id(file_id, field="file_id")
        return self._guarded(lambda: self._describe(checked))

    def describe_roots(self) -> list[DriveEntry]:
        """The configured roots themselves, as entries.

        Spec 9C §4.1's entry point: an agent that must name a folder before it can see
        one has no way in, and the roots are the only folders it may be told about
        without being told about everything above them. So this is one `files.get` per
        root -- deliberately *not* a listing, because the folder above a root is the
        titolare's Drive and enumerating it is the failure this module exists to
        prevent.

        A root the titolare has since deleted, or that Drive answers 404 for, raises
        `NotFound` naming that id rather than being skipped: an entry point silently
        missing one of its folders would read as "this root is empty".
        """

        def work() -> list[DriveEntry]:
            # `folder_id`, because a root is a folder and that is the parameter whose
            # name a refusal has to send the reader to -- `google_drive_accounts.
            # root_folder_ids`, i.e. the settings screen, not a `file_id` they typed.
            return [
                self._describe(checked_outside_id(root, field="folder_id")) for root in self._roots
            ]

        return self._guarded(work)

    def is_within_roots(self, file_id: str) -> bool:
        """Whether a configured root is this file's ancestor -- or is this file.

        A folder the titolare named is inside the roots by definition, and answering
        that needs no call to Google at all. Everything else is `files.get` on the
        parents, level by level, with a cache for the duration of this one call: a file
        with several parents whose paths meet again is otherwise walked once per path.
        """
        # `folder_id`, because this is the one entry point that answers about a
        # *folder* as readily as about a file, and `list_children` is what calls it.
        checked = checked_outside_id(file_id, field="folder_id")
        return self._guarded(lambda: self._within_roots(checked, {}))

    def read_bytes(self, file_id: str, *, max_bytes: int | None = None) -> tuple[bytes, str]:
        """The bytes of one file and the mime they are in.

        A Google Doc has no bytes of its own, so what comes back is Drive's
        `text/plain` export and the mime says `text/plain` rather than repeating the
        native type -- the caller is being handed text, and telling it otherwise would
        make every extractor downstream guess.

        `max_bytes` can only *tighten* the reader's own ceiling (see `_ceiling`): it is
        a caller's budget, not a permission, and a tool that asked for 500 MB must not
        thereby raise a limit it does not decide.
        """
        checked = checked_outside_id(file_id, field="file_id")
        limit = self._ceiling(max_bytes)
        return self._guarded(lambda: self._read_bytes(checked, limit))

    def read_text(self, file_id: str, *, max_bytes: int | None = None) -> FileText:
        """The text of one file, cut at `max_bytes` and stamped with its provenance.

        Two ceilings, not one: the bytes are fetched under the download ceiling (a 4 MB
        PDF is an ordinary contract) and the *text* is cut at `max_bytes`, which
        defaults to `settings.drive_text_max_bytes`. See `DOWNLOAD_MAX_BYTES`.
        """
        checked = checked_outside_id(file_id, field="file_id")
        # A caller's `max_bytes` may raise the text limit above the configured default
        # (that is what it is for) but, like `read_bytes`', only up to the download
        # ceiling: `_ceiling` is the one bound on what a caller may ask this reader for.
        limit = self._text_max_bytes if max_bytes is None else self._ceiling(max_bytes)

        def work() -> FileText:
            content, mime = self._read_bytes(checked, self._ceiling(None))
            return file_text(content, mime=mime, max_bytes=limit)

        return self._guarded(work)

    # ---- internals -------------------------------------------------------------------

    def _ceiling(self, max_bytes: int | None) -> int:
        """The smaller of the caller's budget and this reader's own ceiling.

        `min`, not "the caller's if given": a `max_bytes` above the ceiling would let
        whoever calls this decide how many bytes of somebody's Drive may be pulled into
        this process, which is the one thing the ceiling exists to take away from them.
        """
        if max_bytes is None:
            return self._download_max_bytes
        return min(max_bytes, self._download_max_bytes)

    def _guarded(self, work: Callable[[], _T]) -> _T:
        """Every public operation: the exception that has a *reaction* attached, and the
        entity every other failure is reported under.

        `DriveCredentialRevoked` is not swallowed and not re-worded: the callback
        records the fact (and commits it, on its own transaction) and the exception
        continues, so the caller stops instead of carrying on against a dead
        credential. Wrapped here, once, rather than at the four call sites -- and the
        public methods call the private internals below precisely so that a nested
        operation cannot fire the callback twice for one failure. It is caught *before*
        the `Conflict` clause below because it is one, and the order of `except` clauses
        is what keeps the reaction attached to it.

        **The entity of a transport failure.** `DriveTransport` reports its own failures
        under `TRANSPORT_ENTITY` (`document_blob`), which is right for the caller it was
        written for -- `GDriveStorage` reaches Drive for the bytes of a CRM document --
        and wrong for every caller of this class. A Google 500 while listing a folder the
        titolare named arrived as «document_blob: elenco fallito (500)»: there is no CRM
        document in that operation, `_resolve_original_pdf` documents the opposite, and an
        adapter routing on the entity would send somebody to the documents screen. So the
        transport's entity, and *only* it, is re-stamped as `ENTITY` on the way out.

        Only it, deliberately: `google_drive_account` (the credential) and `google_drive`
        (an outage after every retry was spent) are already the truthful subject of their
        own refusals, and flattening them into `drive_file` would claim a file was the
        problem when the account was. The one-entity rule is also why this lives here and
        not as a constructor argument on the transport: it then holds for *any* transport
        a reader was handed, including the doubles composed in tests, rather than only for
        the one `drive_reader_for` builds.
        """
        try:
            return work()
        except DriveCredentialRevoked:
            if self._on_revoked is not None:
                self._on_revoked()
            raise
        except Conflict as refusal:
            if refusal.details.get("entity") != TRANSPORT_ENTITY:
                raise
            # Rebuilt rather than mutated: `Conflict` composes its own message from the
            # entity, so re-stamping `details` alone would leave a sentence that still
            # said `document_blob`. Every other detail travels -- `status` and `what` in
            # particular, which `storage/gdrive.py` reads and `_metadata` matches on.
            passthrough = {
                key: value
                for key, value in refusal.details.items()
                if key not in ("entity", "reason")
            }
            raise Conflict(ENTITY, refusal.details["reason"], **passthrough) from refusal

    def _list_children(self, folder_id: str, page_token: str | None) -> DriveListing:
        if not self._within_roots(folder_id, {}):
            # Before `files.list`, not after: a listing of a folder nobody configured
            # must never be *sent*, which is the whole point of this module.
            raise NotFound(ENTITY, folder_id)
        url = files_list_url(children_query(folder_id), page_token=page_token, fields=_LIST_FIELDS)
        payload = self._transport.json("GET", url, what=_WHAT_LIST)
        files = payload.get("files") or []
        token = payload.get("nextPageToken")
        return DriveListing(
            items=[_entry(item) for item in files if isinstance(item, dict)],
            next_page_token=str(token) if token else None,
        )

    def _describe(self, file_id: str) -> DriveEntry:
        """`files.get` plus the roots check, with the metadata call's own `parents`
        seeding the walk -- so describing a file costs one request and not two, the
        same saving `_read_bytes` already takes."""
        meta = self._metadata(file_id)
        if not self._within_roots(file_id, {file_id: _parents(meta)}):
            raise NotFound(ENTITY, file_id)
        return _entry(meta)

    def _read_bytes(self, file_id: str, max_bytes: int) -> tuple[bytes, str]:
        meta = self._metadata(file_id)
        # The metadata call already carries the file's parents, so seeding the walk's
        # cache with them spends no second `files.get` on the file itself.
        cache: dict[str, list[str]] = {file_id: _parents(meta)}
        if not self._within_roots(file_id, cache):
            raise NotFound(ENTITY, file_id)

        mime = str(meta.get("mimeType") or "")
        if mime == FOLDER_MIME:
            raise Conflict(ENTITY, _NOT_A_FOLDER, file_id=file_id, mime=mime)
        declared = _as_int(meta.get("size"))
        if declared is not None and declared > max_bytes:
            # Refused on what Drive says the size is, so a 900 MB video costs nothing
            # to turn down. The check is repeated on what arrives, below, because an
            # export has no declared size at all.
            raise Conflict(
                ENTITY, _TOO_BIG.format(max_bytes=max_bytes), file_id=file_id, size=declared
            )

        if mime == GOOGLE_DOC_MIME:
            content = self._transport.bytes(
                "GET",
                file_export_url(file_id, PLAIN_TEXT_MIME),
                what=_WHAT_EXPORT,
                max_bytes=max_bytes,
            )
            mime = PLAIN_TEXT_MIME
        elif mime.startswith(_GOOGLE_NATIVE_PREFIX):
            raise Conflict(ENTITY, _NO_BYTES.format(mime=mime), file_id=file_id, mime=mime)
        else:
            content = self._transport.bytes(
                "GET", file_media_url(file_id), what=_WHAT_DOWNLOAD, max_bytes=max_bytes
            )

        # The transport reads at most `max_bytes + 1` bytes off the socket, so an answer
        # longer than the ceiling is told apart from one that fits without ever being
        # buffered whole. The declared-size check above is the cheap refusal; this is the
        # one that holds for an export, which declares no size at all. The size of an
        # oversized answer is not reported: the read stopped one byte past the ceiling.
        if len(content) > max_bytes:
            raise Conflict(ENTITY, _TOO_BIG.format(max_bytes=max_bytes), file_id=file_id)
        return content, mime

    def _within_roots(self, file_id: str, cache: dict[str, list[str]]) -> bool:
        if file_id in self._roots:
            return True
        seen = {file_id}
        frontier = [file_id]
        spent = 0
        for _level in range(MAX_ROOT_WALK_LEVELS):
            above: list[str] = []
            for current in frontier:
                if spent >= MAX_ROOT_WALK_REQUESTS and current not in cache:
                    return False
                spent += 1
                for parent in self._parents_of(current, cache):
                    if parent in self._roots:
                        return True
                    if parent not in seen:
                        seen.add(parent)
                        above.append(parent)
            if not above:
                # The Drive's own root, or a file with no parents at all: there is
                # nothing above it, and no configured root was met on the way.
                return False
            frontier = above
        return False

    def _parents_of(self, file_id: str, cache: dict[str, list[str]]) -> list[str]:
        """The parents of one id, asked once per `is_within_roots` call.

        A 404 answers "no parents", not an error: the walk's question is only ever
        "does a configured root sit above this?", and an id that does not exist has no
        root above it. Turning it into a refusal here would also make the refusal
        *different* from the one an unconfigured folder gets, which is exactly the
        distinction this module withholds.

        Only a 404, deliberately, and it is a trade rather than a free choice: a 403
        ("you may not see this file") also means no root can be proven above it, and it
        propagates as a `Conflict` instead. That is the right answer for the caller --
        an outage or a permission change is worth reporting as itself, and swallowing
        it would make an unshared parent look like an unconfigured one -- but it does
        mean a 403 on a *parent* of a file the titolare can read surfaces as a Drive
        error rather than as `NotFound`. It has not been seen, and inventing a
        translation for it would be inventing which of the two it was.
        """
        cached = cache.get(file_id)
        if cached is not None:
            return cached
        try:
            payload = self._transport.json(
                "GET", file_meta_url(file_id, fields=_PARENT_FIELDS), what=_WHAT_META
            )
        except Conflict as refusal:
            if refusal.details.get("status") != 404:
                raise
            payload = {}
        parents = _parents(payload)
        cache[file_id] = parents
        return parents

    def _metadata(self, file_id: str) -> dict[str, Any]:
        try:
            return self._transport.json(
                "GET", file_meta_url(file_id, fields=_ENTRY_FIELDS), what=_WHAT_META
            )
        except Conflict as refusal:
            if refusal.details.get("status") == 404:
                # Same sentence as a file outside the roots: see the module docstring.
                raise NotFound(ENTITY, file_id) from refusal
            raise


class _AlreadyAuthorized:
    """The type of `ALREADY_AUTHORIZED`, and the only reason it is a type rather than a
    string: mypy can then tell the two apart, and no `action` a caller composes from
    data can accidentally *be* it."""

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - diagnostics only
        return "ALREADY_AUTHORIZED"


# What a caller passes as `action` to say "the agent ban for this operation has already
# been applied, under a name this call cannot know".
#
# There is exactly one such caller -- `InvoiceService._drive_reader`, whose operation is
# `import_issued_invoice`, checked by `import_issued` under that name before any of this
# runs. Passing the name again here would check the same ban twice; passing anything else
# would check the wrong one.
#
# It exists as a named constant because it replaced `action=None`, i.e. a *default*. The
# ban was then skipped by any caller who simply did not think about it -- a new REST route
# or MCP tool composing a reader inherited the exemption in silence, which is the one
# mistake in this file that has no symptom until somebody reads a Drive they should not
# have. `action` is now required, and the exemption has to be spelled out at the call
# site, where a reviewer sees the words «already authorized» and asks by whom.
ALREADY_AUTHORIZED = _AlreadyAuthorized()

# `str` for a real operation, the sentinel above for the one caller that has already
# applied the ban itself. Not `str | None`: see `ALREADY_AUTHORIZED`.
DriveAction = str | _AlreadyAuthorized


def drive_reader_for(
    session: Session,
    actor: Actor,
    settings: Settings,
    *,
    feature: str,
    action: DriveAction,
) -> DriveReader:
    """A reader on this actor's connected Drive account, or the refusal that says why not.

    The order is the point, and there are now four gates in it, each of which must come
    before the next has anything to say:

    1. `action`: the operation the caller is about, checked against
       `FULL_ACCESS_ACTIONS` before this function touches the database at all. An
       agent credential on an installation that has not opted in is refused here, so it
       never learns whether a Drive is even connected;
    2. `usable`, which holds no transport and therefore *cannot* have called Google, so
       a revoked grant, an expired consent or a missing `drive.readonly` is learnt at
       the ask rather than from a failed Drive call;
    3. the roots, which must not be empty -- see below;
    4. only then the credential, composed by `user_transport_for` -- the row's refresh
       token unsealed, wrapped in `UserTokens`, handed to a `DriveTransport`. The roots
       come from the row, so a reader cannot be pointed at a folder the titolare did not
       configure.

    `feature` is the name the refusal uses ("la lettura dei documenti da Drive"), so a
    missing scope reads as a feature that is off rather than as a broken credential.

    `action` is the name `FULL_ACCESS_ACTIONS` lists the operation under -- the MCP
    tool's own name, which is also what a REST route would pass. It is here, at the one
    point every reader of Drive is built from, rather than in each caller: the tool being
    unregistered protects the MCP transport only, and a personal access token is not
    confined to it (see `core/actor.py`, and the commit its comment names). A route that
    composes a reader inherits the ban by passing its action.

    It is **required**, and one caller must not pass a real one:
    `InvoiceService._drive_reader`, whose operation is `import_issued_invoice` -- already
    on the list, and already checked by `import_issued` under that name. Passing it here
    too would check the same ban twice, and passing anything else would check the wrong
    one. That caller passes `ALREADY_AUTHORIZED` instead, by name.

    It used to default to `None` with the same meaning, and the default was the bug: an
    exemption nobody had to ask for. A new route or tool composing a reader and not
    thinking about the ban inherited it silently, and this is the one omission in this
    module with no symptom -- the reader works, the roots are honoured, and an agent
    credential the installation never opted in for reads the titolare's Drive. Spelling
    it at the call site is what makes a reviewer ask "already authorized by whom?";
    nothing here can answer that, which is also why the three MCP tools are tested
    against a closed agent credential one by one.

    **Empty roots are guidance, not an empty answer.** A connected credential with no
    `root_folder_ids` is a real state -- `DriveRootsUpdate` requires at least one, so
    the only way to have none is never to have configured them -- and it is a
    configuration step, not a fault. Without this the reader would compose happily and
    every operation would answer "not found", or list nothing: an agent would report
    that the titolare's Drive is empty, and the person would go looking for a bug
    instead of opening the settings screen.

    `on_revoked` closes the last gap: a revocation can only be *learned* by a refresh
    that answers `invalid_grant`, which happens deep inside a read. `mark_revoked`
    commits on its own behalf, so the fact outlives the rollback of whatever operation
    discovered it, and then the exception continues.

    The `GoogleTokenClient` `user_transport_for` builds lives as long as the reader.
    Unlike the API's per-process cache (`pigrocrm_api/google_tokens.py`), that means one
    token exchange per reader rather than one per hour per process -- the price of
    `packages/core` not owning a process-wide cache, and the same choice `apps/mcp`'s
    privileged tools already make.
    """
    if isinstance(action, str):
        # Before the session is touched: a refusal here must not double as "there is a
        # Drive account for this user".
        actor.require_agent_allowed(action)
    accounts = GoogleDriveAccountService(session, settings=settings)
    account = accounts.usable(actor, scope=DRIVE_SCOPE_READONLY, feature=feature)
    if not account.root_folder_ids:
        raise Conflict(
            _ACCOUNT_ENTITY,
            _NO_ROOTS.format(feature=feature),
            email_address=account.email_address,
        )
    # The three steps -- unseal the row's refresh token, wrap it in a `UserTokens`
    # provider over the slice 5 token client, hand that to a `DriveTransport` -- written
    # once, in `transport/user_transport_for`, and shared with the document storage. Not
    # repeated here: a second copy would be a second place the plaintext refresh token
    # exists as a local variable, and a second thing to fix the day the composition
    # changes.
    transport = user_transport_for(account, settings)

    def on_revoked() -> None:
        accounts.mark_revoked(
            account,
            # Google revoked this, nobody in this CRM did.
            Actor.system(),
            # The sentence a person reads, written here: no upstream prose, no error
            # code, no token.
            _REVOKED_REASON.format(email=account.email_address),
        )

    return DriveReader(
        transport,
        roots=account.root_folder_ids,
        text_max_bytes=settings.drive_text_max_bytes,
        on_revoked=on_revoked,
    )


def _entry(item: dict[str, Any]) -> DriveEntry:
    mime = str(item.get("mimeType") or "")
    return DriveEntry(
        id=str(item.get("id") or ""),
        nome=str(item.get("name") or ""),
        mime=mime,
        dimensione=_as_int(item.get("size")),
        modificato_il=_moment(item.get("modifiedTime")),
        cartella=mime == FOLDER_MIME,
    )


def _parents(payload: dict[str, Any]) -> list[str]:
    raw = payload.get("parents") or []
    return [str(parent) for parent in raw if isinstance(parent, str)]


def _as_int(value: Any) -> int | None:
    """Drive reports `size` as a *string*, and omits it entirely for anything without
    bytes. Anything unparseable is `None` for the reason `gmail/parse.py` gives: a
    strange answer from Google is not worth failing a whole read over."""
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _moment(value: Any) -> datetime | None:
    """Drive's RFC 3339 timestamp, always UTC-aware.

    `fromisoformat` reads the trailing `Z` from Python 3.11 on. A naive result cannot
    happen with Drive's own format, but is stamped UTC rather than returned naive: a
    naive datetime compared against an aware one raises, and a listing is not the place
    to discover that.
    """
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
