"""The default backend. A directory on disk, and no external dependency at all --
which is what makes the product genuinely self-hostable (spec 5)."""

import os
import tempfile
from datetime import timedelta
from pathlib import Path

from pigrocrm.core.errors import NotFound
from pigrocrm.core.storage.base import validate_storage_key
from pigrocrm.core.storage.errors import STORAGE_ENTITY, unusable_storage_key


class LocalFileStorage:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def _resolve_or_refuse(self, candidate: Path) -> Path:
        """Resolves `candidate`, refusing it if resolution fails for any reason other
        than the path (or a suffix of it) simply not existing yet -- which is normal
        and expected for a `put` target that has never been written before.

        Two distinct failures land here, both confirmed against a real reproduction on
        this host, not assumed from documentation:

        - A self-referential or looping symlink planted among the key's own segments
          makes `resolve()` raise `OSError` (errno ELOOP, "too many levels of symbolic
          links"). The *non-strict* `resolve()` this class used to call unconditionally
          swallows that internally and falls back to treating the loop as a literal,
          not-yet-existing path -- which let a still-broken destination through
          containment-checking, only to raise that same raw `OSError` later, deeper
          inside `mkdir`/`read_bytes`/`is_dir`, on all three of `put`/`get`/`delete`.
        - A segment that already exists as a plain *file* where the key needs a
          directory raises `NotADirectoryError` -- also not `FileNotFoundError`, and
          also, before this method existed, reached the caller raw from all three
          methods, not only `put`.

        Trying the strict resolution first, and falling back to the lenient one only
        for the single error that means "does not exist yet", catches both failures
        here, uniformly, instead of leaking either one from deeper in the call.
        """
        try:
            return candidate.resolve(strict=True)
        except FileNotFoundError:
            return candidate.resolve(strict=False)
        except OSError as exc:
            raise unusable_storage_key(
                "impossibile risolvere il percorso di storage",
                "una chiave la cui destinazione sia risolvibile e resti nella radice",
            ) from exc

    def _ensure_contained(self, candidate: Path) -> Path:
        """Refuses `candidate` if its real destination is not inside `root`.

        A key can be a perfectly safe *string* (`validate_storage_key`) and still
        resolve outside root if a symlink was already planted at one of its segments
        before this call -- the string check has no way to see that, since it never
        touches the filesystem. Comparing resolved paths, rather than trusting the
        join, is what catches it.

        Used both for the key's own target path (`_path`) and, in `put`, for the
        temporary file's path: a symlink or hard link planted at the temp name is
        exactly as dangerous as one planted at the real key, and was reachable there
        until `put` started routing its temp path through this same check too.
        """
        if not self._resolve_or_refuse(candidate).is_relative_to(self.root.resolve()):
            raise unusable_storage_key(
                "il percorso risolto punta fuori dalla radice di storage",
                "una chiave la cui destinazione reale resti dentro la radice",
            )
        return candidate

    def _path(self, key: str) -> Path:
        """The one place every method maps a key to a real filesystem location.

        Two independent gates, in order: `validate_storage_key` rejects an unsafe
        *string* before any filesystem call -- traversal, absolute paths, reserved
        names, length, case. `_ensure_contained` then rejects a safe string whose real,
        on-disk destination is not inside `root` anyway.
        """
        return self._ensure_contained(self.root / validate_storage_key(key))

    def put(self, key: str, data: bytes, content_type: str) -> None:
        """`content_type` is accepted and ignored: a filesystem has no place to record
        it, and the authoritative copy is `document_versions.content_type` in Postgres
        -- which is where every reader already looks. Storing it in a sidecar file
        would create a second answer that can disagree with the first.

        The temporary file's name is never a function of `key`: a derived name
        (`key + ".tmp"`) is itself an ordinary storage key, so an unrelated upload at
        that key would be destroyed, a symlink or hard link planted at it would redirect
        the write outside root or onto another file, and two concurrent puts to one key
        would race over it. `tempfile.mkstemp` creates a randomly named file with
        `O_CREAT|O_EXCL`, which fails outright if anything already sits at the name. The
        path still goes through `_ensure_contained`, so no path this class acts on
        bypasses the containment check.
        """
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                self._ensure_contained(temporary)
                handle.write(data)
            # Write-then-rename: os.replace is atomic on the same filesystem, so a
            # crash mid-write can never leave a truncated PDF readable under the real
            # key, and no reader ever observes a partially written file under it either.
            os.replace(temporary, path)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise

    def get(self, key: str) -> bytes:
        path = self._path(key)
        try:
            return path.read_bytes()
        except (FileNotFoundError, IsADirectoryError) as exc:
            # IsADirectoryError alongside FileNotFoundError: a key that collides with
            # an existing directory (a bug upstream, or a wrong id reused) is just as
            # much "no such document blob" as a key nothing was ever written under --
            # neither is the raw OSError this project has already let reach a caller
            # six times for other input shapes.
            raise NotFound(STORAGE_ENTITY, key) from exc

    def delete(self, key: str) -> None:
        """Idempotent: a retried request must not fail because the first one worked.

        Checks `is_dir()` before unlinking rather than catching the error `unlink`
        raises for a directory, because that error is not portable: the same
        directory target raises `IsADirectoryError` (EISDIR) on Linux but
        `PermissionError` (EPERM) on macOS/BSD -- confirmed against the Python
        installed here, not assumed. Catching only the Linux shape would let the
        macOS one through as a raw, unrelated-looking `PermissionError`; checking the
        actual filesystem type first sidesteps the platform difference entirely.
        """
        path = self._path(key)
        if path.is_dir():
            raise unusable_storage_key(
                "la chiave indica una directory, non un singolo blob",
                "una chiave che identifica un unico file",
            )
        path.unlink(missing_ok=True)

    def signed_url(self, key: str, ttl: timedelta) -> str | None:
        """Always `None`. The download passes through the API, which is the only place
        authorisation exists (spec 5)."""
        validate_storage_key(key)
        return None
