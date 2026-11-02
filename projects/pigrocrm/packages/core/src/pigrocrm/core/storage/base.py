"""One interface, two implementations. The metadata always lives in Postgres.

Search, permissions, timeline and versions work identically on both backends, and
changing backend loses nothing but the bytes already uploaded -- for which an explicit
migration is needed, not a change of environment variable (spec 5).
"""

import re
from datetime import timedelta
from typing import Protocol

from pigrocrm.core.storage.errors import unusable_storage_key

MAX_KEY_LENGTH: int = 255

# A key is a relative POSIX-style path: segments of `[a-z0-9._-]`, separated by a
# single `/`, each segment starting with a lowercase alphanumeric. `re.fullmatch` below,
# never `re.match` with `$`, because `$` matches before a trailing newline -- which would
# let "a.pdf\n" through and reach the filesystem, and reach the `String(255)` column, as
# something neither had agreed to.
#
# Lowercase-only is deliberate. Two keys differing only in
# case are different Python strings but can name the *same* file on a case-insensitive
# filesystem (macOS/APFS and Windows/NTFS by default): `put("ACME/V1.PDF", b)` after
# `put("acme/v1.pdf", a)` silently leaves one file containing `b`, with no exception on
# either call -- reproduced through the real API, not assumed. The keys this system
# actually generates -- a slug plus hex UUID fragments -- are already lowercase, so
# nothing real is lost by refusing the rest outright. It also keeps the two backends the
# next task must make interchangeable from disagreeing about identity: Google Drive is
# case-sensitive, macOS is not, and a key that is safe (unambiguous) for one and not the
# other is not safe -- the same principle the rest of this function already follows for
# Windows-reserved names below.
#
# The character class is otherwise ASCII-only by construction (no `\w`, no Unicode
# letters), which is also what makes Unicode normalisation a non-issue: none of
# `a-z0-9._-` or `/` has more than one Unicode Normalization Form, so any key this
# pattern accepts is already identical under NFC and NFD. A key that is *not* ASCII is
# refused outright, not silently re-encoded.
_KEY_RE = re.compile(r"[a-z0-9][a-z0-9._-]*(?:/[a-z0-9][a-z0-9._-]*)*")

# Windows reserves these as device names regardless of extension or case: "con",
# "CON" and "con.pdf" are all illegal there, even though ext4/APFS do not care. This
# backend runs fine without the check, but `validate_storage_key` is the one gate a
# future remote backend shares (see the module docstring), and a key that is safe for
# one and not the other is not safe. Compared against the segment's stem upper-cased
# even though the charset above is now lowercase-only: a defensive normalisation, not a
# necessary one today, kept for the same reason the pattern rejects ".." explicitly
# below -- so a future widening of the character class does not silently reopen this
# hole too.
_WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{digit}" for digit in range(1, 10)),
    *(f"LPT{digit}" for digit in range(1, 10)),
}


_INVALID_KEY_REASON = "chiave di storage non valida"
_INVALID_KEY_EXPECTED = "segmenti alfanumerici separati da '/', max 255 caratteri"


def _unsafe_segment(segment: str) -> bool:
    """A segment the character-class regex above lets through but that is still unsafe
    to hand to *some* real filesystem.

    A trailing dot: Windows' own API layer silently strips it, so "a." and "a" can name
    the same file there even though POSIX treats them as different names -- exactly the
    kind of divergence `validate_storage_key` exists to prevent (module docstring).

    A reserved device name, matched on the segment's stem (before the first dot) so
    both "aux" and "aux.pdf" are caught: Windows refuses to create either, in any
    directory, regardless of case.
    """
    if segment.endswith("."):
        return True
    stem = segment.split(".", 1)[0]
    return stem.upper() in _WINDOWS_RESERVED_NAMES


def validate_storage_key(key: str) -> str:
    """The one gate every backend calls before touching anything.

    Checked here rather than inside each implementation so `LocalFileStorage` and
    `GDriveStorage` cannot drift: a key that is safe for one and not the other is a
    key that is not safe.

    `".."` as an exact path *segment* is refused explicitly on top of the pattern,
    compared per segment rather than as a substring of the whole key -- a substring
    check also refuses the ordinary segment `"a..b"`, which contains `".."` but is not
    a traversal attempt, and did so in an earlier version of this function. The pattern
    alone already rejects a `".."` segment today (a segment must start with an
    alphanumeric), but a future widening of the character class would silently reopen
    path traversal, and this line will still be here.

    Pure string checking, no filesystem access: this function has no way to see a
    symlink planted at the destination, so `LocalFileStorage` layers its own
    containment check on top after this one passes.
    """
    if not _KEY_RE.fullmatch(key) or len(key) > MAX_KEY_LENGTH:
        raise unusable_storage_key(_INVALID_KEY_REASON, _INVALID_KEY_EXPECTED)
    segments = key.split("/")
    if any(segment == ".." for segment in segments) or any(
        _unsafe_segment(segment) for segment in segments
    ):
        raise unusable_storage_key(_INVALID_KEY_REASON, _INVALID_KEY_EXPECTED)
    return key


class DocumentStorage(Protocol):
    """`signed_url` returns `None` on a backend that has no such concept.

    On `LocalFileStorage` that is the whole design: the download goes through the
    API, which is the only place authorisation exists.

    Consistency this Protocol requires, deliberately narrower than "every reader sees
    every write immediately": a `get(key)` that follows a successful `put(key, ...)`
    for the same key, from the same caller, must return the data just written. This is
    read-*your-own*-writes, not global strong consistency -- a backend is free to take
    longer for some *other* reader or region to observe the same write. The distinction
    matters because a remote object store may not promise the stronger property
    unconditionally the instant an upload response returns, and this Protocol must not
    quietly assume it does. It does not promise durability either: a successful `put`
    means the call did not raise, not that the bytes have survived a concurrent power
    loss or reached every replica -- `LocalFileStorage` writes atomically (see `put`)
    but does not `fsync`, which is a deliberate match to what an honest remote backend
    can promise, not an oversight.
    """

    def put(self, key: str, data: bytes, content_type: str) -> None: ...

    def get(self, key: str) -> bytes: ...

    def delete(self, key: str) -> None: ...

    def signed_url(self, key: str, ttl: timedelta) -> str | None: ...
