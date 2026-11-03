"""The only place a Google Drive URL is born.

Spec 9C names the failure to avoid: reading a person's Drive. A personal Drive holds
the children's photos, the rental agreement, the folder from another job. The import
instead always starts from a folder the owner has indicated
(`google_drive_accounts.root_folder_ids`), and "list the children of this folder" is
the only query the system needs.

So the constraint is enforced *server-side*, inside the `q`, and this module is the only
place where a Drive URL can be built. Two guards, because each alone is one edit away
from being defeated:

  * `files_list_url` rejects a `q` lacking a well-formed `'<id>' in parents` clause,
    rejects the free-search operator `contains`, and rejects `or`, `not` and
    parentheses -- because the `in parents` clause limits the listing only if it is the
    only thing deciding where to look: `'<id>' in parents or mimeType='application/pdf'`
    contains it and still lists every PDF in the Drive. Together they make the bug
    unbuildable rather than merely forbidden. The two operator checks run on a skeleton
    with the quoted literals emptied, because `contains` inside a customer's name is
    not an operator;
  * `tests/test_drive_query.py` walks the AST of every source file and fails if a
    literal contains the Drive host, the upload host or a `/files` path, so that anyone
    wanting to bypass this module no longer has a place to write the URL. Its exemption
    list is empty: `storage/gdrive.py`, which used to write the URLs by hand, has been
    migrated here.

Everything in here is a pure function on validated input. No function of this module
reads the database, none accepts free text, and in particular there is deliberately no
constructor that takes a Drive search string: no surface of this slice offers one, and
the cheapest way to keep that true is to have nothing to call.

**Two strictness levels for ids, not an oversight.** An id that arrives from outside --
the root folder the owner pasted into `google_drive_accounts.root_folder_ids`, or the
file someone asked to read -- goes through `checked_outside_id`, which demands the shape
of a real Drive id (10-128 characters of Google's opaque alphabet) and which reports the
*name of the parameter* that carried it (`folder_id`, `file_id`), because that name is
nearly the whole content of the message. An id that Drive itself has just returned to
us -- the child of a listing, the folder that `files.create` created -- goes through
`_checked_id`, which has the same alphabet without a minimum length: here the threat is
not a search expression chosen by someone, it is path traversal toward another
endpoint, and the alphabet is enough against that. Demanding ten characters from these
too would mean demanding that Drive ids have a *documented* minimum length, which they
do not.

There is a third source of ids, and it also goes through the lax check: the storage
root folder (`Settings.gdrive_root_folder_id`, i.e.
`PIGROCRM_GDRIVE_ROOT_FOLDER_ID`), which `GDriveStorage` interpolates as the parent of
every folder it creates and passes to `file_meta_url` in `verify_root_accessible`. It
comes from an operator, not from a Drive listing, so the stricter check would suit it --
but it is a configuration value read once at startup, not a request parameter, and it
is `verify_root_accessible` itself that tells, at startup and with an understandable
message, that it is not reachable. Raising the bar now would break the installations
whose root is written in a form that Drive accepts and this regex does not.
"""

import re
from urllib.parse import urlencode

from pigrocrm.core.errors import ValidationFailed

DRIVE_API_ROOT = "https://www.googleapis.com/drive/v3"
# An upload does not go through the normal endpoint: it is a different host path, with
# a mandatory `uploadType`. It lives here and not in `storage/gdrive.py` because of the
# second guard.
DRIVE_UPLOAD_ROOT = "https://www.googleapis.com/upload/drive/v3"
_FILES = f"{DRIVE_API_ROOT}/files"
_UPLOAD_FILES = f"{DRIVE_UPLOAD_ROOT}/files"

FOLDER_MIME = "application/vnd.google-apps.folder"

# The shape of a Drive id as it arrives from outside: real ids are between 28 and 44
# characters, so a minimum of ten cuts nothing legitimate and cuts every word someone
# wrote while thinking of something else. `re.fullmatch`, never `re.match` with `$` --
# `$` also accepts a trailing newline, which is exactly the character used to attempt an
# injection.
_SAFE_FOLDER_ID = re.compile(r"[A-Za-z0-9_\-]{10,128}")
# The same shape for the ids Drive returns to us, without the minimum: see the module
# docstring.
_SAFE_ID = re.compile(r"[A-Za-z0-9_\-]{1,128}")
# The key of an `appProperties` entry: a code constant, validated because validating
# costs one line and makes it impossible for it to become an input by distraction.
_SAFE_PROPERTY_KEY = re.compile(r"[A-Za-z0-9_.\-]{1,64}")
# A media type, for `files.export`. Restricted because it ends up in a query parameter
# and because export accepts a closed list of conversions: anything without this shape
# is a caller error, not an exotic conversion.
_SAFE_MIME = re.compile(r"[a-z]+/[A-Za-z0-9.+\-]+")
# What makes a listing legitimate: a clause naming a specific folder. The shape of the
# id is load-bearing, not decoration -- `in parents` as a substring would also appear in
# a `q` that limits nothing, and that is the broad search this module exists to prevent.
_SCOPED_TO_A_FOLDER = re.compile(r"'[A-Za-z0-9_\-]{1,128}' in parents")
# Drive's free-search operator, in every form (`name contains`, `fullText contains`).
# Rejected even inside a folder: the import lists and decides afterwards, and admitting
# `contains` here would mean admitting that a search string arrives from outside.
# Looked for on the *skeleton* (see `_skeleton`), never on the raw `q`: the word inside
# a quoted value is not an operator, and mistaking it for one means failing every upload
# of a customer named "Contains S.r.l.".
_FULL_TEXT_SEARCH = re.compile(r"\bcontains\b", re.IGNORECASE)
# The three ways to *widen* a filter instead of narrowing it. `or` and `not` are
# obvious; parentheses are less so, but they serve to group an `or` and no query this
# module builds needs them, so forbidding them costs nothing and closes the loophole of
# nesting the `or`. These too are checked on the skeleton: a folder name may
# legitimately contain a `(` or the word "or".
_WIDENS_THE_FILTER = re.compile(r"\bor\b|\bnot\b|[()]", re.IGNORECASE)
# A quoted literal, with the escaping of `escape_query_value` already applied: `\\.`
# covers both `\'` and `\\`, so the scan does not lose count of the quotes even on the
# most hostile value.
_QUOTED = re.compile(r"'(?:[^'\\]|\\.)*'")


def escape_query_value(value: str) -> str:
    """Escape `\\` and `'` for a Drive `q` filter, backslash first.

    The order is load-bearing for the same reason it is in the template escapers:
    doing the quote first would mean seeing its own backslash doubled by the second
    pass, leaving a trailing backslash able to eat the closing quote of the filter.

    Every value this module puts inside a filter -- the folder names and the
    `appProperties` value -- is today a segment of a storage key validated by
    `validate_storage_key`, whose character class (`[a-z0-9._-]`) contains neither of
    the two, so this function is unreachable through any call the CRM makes today. It
    stays anyway, for the same reason `base.py` keeps its already-redundant checks: a
    future widening of that class must not silently reopen query injection just
    because nobody exercises it today.
    """
    return value.replace("\\", "\\\\").replace("'", "\\'")


# The `checked_outside_id` rule written as a JSON Schema `pattern`, for the one kind of
# caller that must *publish* it rather than merely apply it: the Drive tools of
# `apps/mcp` declare it on the `folder_id`/`file_id` parameters, so that a search
# expression passed by an agent is rejected by the tool's schema before a body runs, and
# the rejection names the parameter the agent wrote instead of surfacing from three
# levels below.
#
# It lives here, next to the function, and not in the module that publishes it: it is
# the same rule, and the reason `checked_outside_id` is public instead of being
# rewritten by every caller applies equally to its JSON Schema spelling. Derived from
# the compiled regex, never retyped by hand: two spellings of a rule are a rule that
# will diverge, and the divergence is silent in both directions -- a laxer schema turns
# a readable rejection into a `ValidationFailed` from deep down, a stricter one rejects
# ids the owner configured legitimately.
#
# The anchors are explicit because `pattern` in JSON Schema is a *search*, not a full
# match: without `^…$` it would accept `'x' in parents and 1ClientsRootAAAA`, i.e.
# exactly the string that exists to be rejected. `re.fullmatch` below is what makes the
# Python side equivalent.
OUTSIDE_ID_PATTERN = f"^{_SAFE_FOLDER_ID.pattern}$"


def checked_outside_id(value: str, *, field: str) -> str:
    """The strict shape of an id that arrives **from outside**, under the name of the
    parameter that carried it.

    An external id is the root folder the owner pasted into
    `google_drive_accounts.root_folder_ids` *or* the file someone -- a route, an MCP
    tool -- asked to read: the strictness is the same (see the module docstring), the
    parameter name is not. `field` exists because that name is nearly the whole content
    of the message: reading "not a Drive folder id" after passing the id of a *file*
    sends one to check the roots configuration, which is fine, instead of the parameter
    that was written. For the same reason the sentence names neither of the two kinds:
    it is the *shape* of the id that is wrong.

    Public, and not a `_checked_folder_id` that every caller imports on the sly:
    `drive/reader.py` must apply exactly this check to its `file_id`s, and two regexes
    for a single rule are a rule that sooner or later will diverge.
    """
    if not _SAFE_FOLDER_ID.fullmatch(value):
        raise ValidationFailed(
            "drive_query",
            field,
            "non è un id di Drive interpolabile in una query",
            expected="10-128 caratteri fra lettere, cifre, - e _",
        )
    return value


def _checked_id(file_id: str) -> str:
    if not _SAFE_ID.fullmatch(file_id):
        raise ValidationFailed(
            "drive_query", "file_id", "un id di Drive contiene solo lettere, cifre, - e _"
        )
    return file_id


def children_query(folder_id: str) -> str:
    """The only query the import needs to make: the non-trashed children of a folder
    the owner has indicated.

    The trash is excluded here and not downstream: a trashed file is a file someone
    decided to throw away, and importing it only to filter it afterwards means it went
    through the process anyway.
    """
    return f"'{checked_outside_id(folder_id, field='folder_id')}' in parents and trashed = false"


def folder_by_name_query(parent_id: str, name: str) -> str:
    """The folder named `name` directly under `parent_id`.

    It serves the *placement* of `GDriveStorage` (one folder per customer, like the
    system it replaces), not identity: identity lives in `appProperties`. The name is
    a storage-key segment generated by the CRM, not user text, and it is escaped
    anyway -- see `escape_query_value`.
    """
    clauses = [
        "trashed=false",
        f"name='{escape_query_value(name)}'",
        f"'{_checked_id(parent_id)}' in parents",
        f"mimeType='{FOLDER_MIME}'",
    ]
    return " and ".join(clauses)


def _skeleton(q: str) -> str:
    """The `q` with every quoted literal emptied, i.e. its structure alone.

    This is what is inspected for operators, because an operator inside a value is not
    an operator. A customer's legal name becomes a storage-key segment, hence a folder
    name, hence the value of a `name='...'`: looking for `contains` in the raw `q`
    would mean failing every put and get of the customer "Contains S.r.l." with a
    `ValidationFailed` on a query the CRM itself had built. Emptying instead of
    removing keeps the quotes in place, so that `name='' and '' in parents` stays
    readable as a structure.
    """
    return _QUOTED.sub("''", q)


def _with_params(base: str, **params: str) -> str:
    """Every call declares it can handle a Shared Drive.

    This is not a detail: a service account's root folder *must* live on a Shared Drive
    (a service account has no quota of its own), and the folder indicated by an owner
    may live on one. Without this parameter Drive answers 404 to both.
    """
    return f"{base}?{urlencode({'supportsAllDrives': 'true', **params})}"


def files_list_url(q: str, *, page_token: str | None = None, fields: str) -> str:
    """The only way to build a `files.list` URL in this codebase.

    Three rejections, which together are the spec 9C mechanism -- a listing that leaves
    the folder indicated by the owner, or a text search in their Drive, cannot be
    built, hence cannot be shipped by mistake:

    1. a `q` without a well-formed `'<id>' in parents` clause is not limited to any
       folder;
    2. a `q` containing `contains` searches a person's Drive;
    3. a `q` containing `or`, `not` or a parenthesis *widens* instead of narrowing, and
       the mere presence of the `in parents` clause does not prevent it:
       `'<id>' in parents or mimeType='application/pdf'` contains it and still lists
       every PDF in the Drive. This third check is what makes the phrase "only the
       children of that folder" true; without it the module docstring promises
       something the code does not prove.

    Points 2 and 3 look at the *skeleton* (`_skeleton`), not the raw `q`: a quoted
    value contains no operators by definition, and inspecting it would mean rejecting a
    customer's folder because of what it is called. Point 1 looks at the real `q`,
    because it is precisely the `'<id>'` literal that must be there.

    The only exemption is `files_by_app_property_url`, and it is another function with
    another name instead of a `strict=False` parameter, so that whoever reads it sees
    from the name what it filters on.
    """
    structure = _skeleton(q)
    if _FULL_TEXT_SEARCH.search(structure):
        raise ValidationFailed(
            "drive_query",
            "q",
            "l'operatore contains cerca nel Drive di una persona: l'import elenca una "
            "cartella indicata e decide dopo",
            expected="un elenco dei figli di una cartella, senza contains",
        )
    if _WIDENS_THE_FILTER.search(structure):
        raise ValidationFailed(
            "drive_query",
            "q",
            "or, not e le parentesi allargano il filtro oltre la cartella indicata: un "
            "elenco può solo restringere",
            expected="clausole unite da and, senza or, not o parentesi",
        )
    if not _SCOPED_TO_A_FOLDER.search(q):
        raise ValidationFailed(
            "drive_query",
            "q",
            "un elenco non limitato a una cartella indicata dal titolare è un bug, "
            "non una ricerca ampia",
            expected="una clausola '<id cartella>' in parents",
        )
    params: dict[str, str] = {"q": q, "fields": fields, "includeItemsFromAllDrives": "true"}
    if page_token:
        params["pageToken"] = page_token
    return _with_params(_FILES, **params)


def files_by_app_property_url(key: str, value: str, *, fields: str) -> str:
    """The only listing not limited to a folder, and it is so because it is *more*
    specific, not less: it names a value the CRM itself wrote into the `appProperties`
    of a file it uploaded itself, not a search of someone's Drive. It is the same form
    of exemption as Gmail's `rfc822msgid`.

    It is not limited to a folder *per project*: this is how `GDriveStorage` finds a
    file without walking the tree, which would be ambiguous (see the docstring of
    `storage/gdrive.py`). It runs across every Shared Drive the credential sees, which
    is safe because a PigroCRM installation has a single Drive credential and every
    file it writes carries this property.
    """
    if not _SAFE_PROPERTY_KEY.fullmatch(key):
        raise ValidationFailed(
            "drive_query",
            "key",
            "una chiave appProperties è una costante del codice, non un input",
            expected="1-64 caratteri fra lettere, cifre, ., - e _",
        )
    clauses = [
        "trashed=false",
        f"appProperties has {{ key='{key}' and value='{escape_query_value(value)}' }}",
    ]
    return _with_params(
        _FILES,
        q=" and ".join(clauses),
        fields=fields,
        includeItemsFromAllDrives="true",
        corpora="allDrives",
    )


def files_create_url(*, fields: str) -> str:
    """`files.create` without a binary body: this is how a folder is born."""
    return _with_params(_FILES, fields=fields)


def file_url(file_id: str) -> str:
    """The URL of a single file, with no parameters beyond Shared Drive support:
    what a `DELETE` needs."""
    return _with_params(f"{_FILES}/{_checked_id(file_id)}")


def file_meta_url(file_id: str, *, fields: str) -> str:
    """The metadata of a file or a folder. `fields` is mandatory because without it
    Drive answers with a fixed subset that never contains what the caller wanted --
    `driveId`, to name one, which is the entire question of
    `verify_root_accessible`."""
    return _with_params(
        f"{_FILES}/{_checked_id(file_id)}", fields=fields, includeItemsFromAllDrives="true"
    )


def file_media_url(file_id: str) -> str:
    """The bytes of an uploaded file, as they are."""
    return _with_params(f"{_FILES}/{_checked_id(file_id)}", alt="media")


def file_export_url(file_id: str, mime: str = "text/plain") -> str:
    """The conversion of a native Google document (Doc, Sheet) into a readable format:
    a Google Doc has no bytes of its own to download, `alt=media` on it answers 403.

    The only endpoint here without `supportsAllDrives`: it is not one of its
    parameters -- Google's generated client does not expose it for `files.export` -- and
    passing the API a parameter it does not document is a 400 waiting to happen. A
    document on a Shared Drive can be exported anyway, because access is by `fileId`.
    """
    if not _SAFE_MIME.fullmatch(mime):
        raise ValidationFailed(
            "drive_query", "mime", "non è un media type", expected="tipo/sottotipo"
        )
    return f"{_FILES}/{_checked_id(file_id)}/export?{urlencode({'mimeType': mime})}"


def upload_create_url(*, fields: str) -> str:
    """The creation of a file with its bytes: metadata and content in a single
    `multipart/related` body, so that a file cannot exist without the properties that
    make it findable."""
    return _with_params(_UPLOAD_FILES, uploadType="multipart", fields=fields)


def upload_media_url(file_id: str) -> str:
    """The replacement of only the bytes of an already existing file: the file stays in
    the right folder, with the right name and the right properties, and only the
    content changes."""
    return _with_params(f"{_UPLOAD_FILES}/{_checked_id(file_id)}", uploadType="media")
