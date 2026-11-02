"""Google Drive as a `DocumentStorage`: where a document goes, and how it is found.

The HTTP layer and the credential are not here. They live in `drive/transport.py`
(`DriveTransport` plus a `TokenProvider`), because slice 9 needs the same HTTP client
driven by a *user* OAuth token as well as by today's service account, and only the
credential differs between the two. Neither are the URLs: every one of them is built by
`drive/query.py`, which is the only module in the repository allowed to name a Drive
host at all -- a guard slice 9C needs because reading a person's Drive must be
impossible to construct, not merely discouraged, and a guard worth nothing if this
module keeps its own two hosts in a corner. This module is the part that does not
change: placement, identity, and the four `DocumentStorage` methods.

**Operational requirement, not a detail** -- and one that applies to the service-account
setup specifically: a service account has no Drive storage quota of its own.
`PIGROCRM_GDRIVE_ROOT_FOLDER_ID` must name a folder on a *Shared Drive*, or a folder
explicitly shared with the service account's address, or `files.create` fails with
`storageQuotaExceeded`. Every call below carries `supportsAllDrives=true` for that
reason, and `verify_root_accessible` (called once by `storage_from_settings`, not by
anything in this class automatically) turns a misconfigured root into one refusal that
names it -- at start-up on the MCP adapter, and inside the first request that needs a
document backend on the API -- instead of a `storageQuotaExceeded` on a real upload.

**Placement vs. identity.** The key is a path, and `a/b/c.pdf` is filed under nested
folders `a` then `b` beneath the configured root, with a file `c.pdf` inside -- one
folder per customer, matching how the system this replaces already organised things,
so a person migrating finds their documents where they expect (spec 5).

But Drive does not enforce name-uniqueness among siblings: two concurrent uploads for
a brand-new customer can each fail to see the other's just-created folder and each
create one of the same name under the same parent. From that point on the *tree* is
ambiguous -- a lookup that walks it by name can land on either folder, including one
that does not contain the file being looked for. So the tree is for placement and
human navigation only, never for identity: every file this class writes carries the
full storage key in its `appProperties`, and `get`/`delete` locate it with a
property-filtered query that does not care which folder it ended up in. `_ensure_folder`
still has to cope with the same raciness for *placement* (two `put()`s for a new
customer's first document must not scatter their files across two duplicate folders
forever) by re-querying after a create and adopting one candidate deterministically.
"""

import json
from datetime import timedelta
from typing import Any

from pigrocrm.core.drive.query import (
    FOLDER_MIME,
    file_media_url,
    file_meta_url,
    file_url,
    files_by_app_property_url,
    files_create_url,
    files_list_url,
    folder_by_name_query,
    upload_create_url,
    upload_media_url,
)
from pigrocrm.core.drive.transport import DriveTransport, ServiceAccountTokens, SignAssertion
from pigrocrm.core.errors import Conflict, NotFound
from pigrocrm.core.http import HttpCall, SleepFn
from pigrocrm.core.storage.base import validate_storage_key
from pigrocrm.core.storage.errors import STORAGE_ENTITY

# Where a Drive URL comes from: `drive/query.py`, and nowhere else. This module used
# to hold the two hosts and its own `q` escaper, which meant "only one place can build
# a Drive URL" was a sentence rather than a fact -- and this was the one file anybody
# would add the next Drive call to. `test_drive_query.py` walks the AST of every source
# file and fails on a Drive host or a `/files` path in any literal outside that module,
# with an empty exemption list, so the sentence is now checked. `FOLDER_MIME` came along
# with the query that filters on it.

# The custom property every file this class writes carries, holding the full storage
# key. This -- not the folder it happens to sit in -- is what `get`/`delete` search
# for; see the module docstring.
APP_PROPERTY_KEY = "pigrocrm_key"
_MULTIPART_BOUNDARY = "pigrocrm-boundary-7f3c1a"
# What each call tells the transport it is doing. Named, because obtaining the access
# token is itself a call through the same transport, so a failure has to be attributed
# before it is interpreted: a 404 from the *token* endpoint is not a missing document,
# and a 403 there is not an unshared folder. `_decode` puts this string in
# `Conflict.details["what"]` for exactly that comparison.
_WHAT_API = "richiesta a Google Drive"
_WHAT_DOWNLOAD = "download da Google Drive"
_WHAT_VERIFY = "verifica della cartella radice"


class GDriveStorage:
    def __init__(self, *, transport: DriveTransport, root_folder_id: str) -> None:
        self._transport = transport
        self._root_folder_id = root_folder_id

    @classmethod
    def from_service_account(
        cls,
        *,
        service_account_json: str,
        root_folder_id: str,
        http: HttpCall | None = None,
        sign_assertion: SignAssertion | None = None,
        sleep: SleepFn | None = None,
    ) -> "GDriveStorage":
        """The constructor every installation configured before slice 9 uses, and the
        one `storage_from_settings` still calls when a service account is configured.

        It exists so that "which credential" stays a decision made once, at the edge,
        rather than a `service_account_json` parameter threaded through a class that no
        longer has any use for one. The user-token equivalent builds a `UserTokens`
        provider the same way (slice 9D).
        """
        return cls(
            transport=DriveTransport(
                tokens=ServiceAccountTokens(
                    service_account_json,
                    http=http,
                    sign_assertion=sign_assertion,
                    sleep=sleep,
                ),
                http=http,
                sleep=sleep,
            ),
            root_folder_id=root_folder_id,
        )

    def _api(
        self, method: str, url: str, *, body: bytes | None = None, content_type: str | None = None
    ) -> dict[str, Any]:
        return self._transport.json(
            method, url, body=body, content_type=content_type, what=_WHAT_API
        )

    # ---- folders: placement and human navigation only, never identity ---------

    def _find_folder(self, parent_id: str, name: str) -> str | None:
        """The id of a folder named `name` directly under `parent_id`, or `None`.

        Returns the *lowest* id when more than one matches -- the deterministic
        convergence rule `_ensure_folder` needs for the race it documents -- rather
        than an arbitrary "first" result Drive's own ordering happens to return.
        """
        url = files_list_url(folder_by_name_query(parent_id, name), fields="files(id)")
        files = self._api("GET", url).get("files") or []
        return str(min((f["id"] for f in files), key=str)) if files else None

    def _ensure_folder(self, parent_id: str, name: str) -> str:
        """Finds-or-creates, race-tolerant.

        Two concurrent uploads for a customer's *first* document can both miss the
        folder in `_find_folder` (neither has created it yet) and both call
        `files.create`: Drive does not enforce unique names among siblings, so this
        legitimately produces two folders named alike under the same parent.
        Re-querying after the create -- rather than trusting the id `files.create`
        just handed back -- lets every racing caller see the same full candidate set
        and adopt the same one (lowest id), so a second `put()` for that customer
        files into the folder the first one converged on, not into a third one.
        """
        existing = self._find_folder(parent_id, name)
        if existing:
            return existing
        self._api(
            "POST",
            files_create_url(fields="id"),
            body=json.dumps(
                {"name": name, "mimeType": FOLDER_MIME, "parents": [parent_id]}
            ).encode(),
            content_type="application/json",
        )
        converged = self._find_folder(parent_id, name)
        if converged is None:
            raise Conflict(
                STORAGE_ENTITY,
                "la cartella appena creata su Google Drive non risulta ancora: riprova",
            )
        return converged

    def _ensure_folder_chain(self, segments: list[str]) -> str:
        parent = self._root_folder_id
        for name in segments:
            parent = self._ensure_folder(parent, name)
        return parent

    # ---- files: identity lives in appProperties, never in the folder path -----

    def _find_all_file_ids_by_key(self, key: str) -> list[str]:
        """Every file currently carrying `key` in its `appProperties`, found without
        walking the folder tree (see the module docstring for why a name-based walk
        would be ambiguous), sorted low-to-high id.

        Not scoped to any folder: the query runs across every Shared Drive this
        credential can see, which is safe because a PigroCRM deployment has one Drive
        credential per instance and every file it writes carries this property.

        Normally returns at most one id. More than one means a create-race: Drive
        offers no atomic create-if-absent, so two concurrent `put()`s for a key that
        does not yet exist can each miss the other and each create a file. `put`
        converges on the lowest id and reaps the rest (see `put`'s own docstring for
        why); `delete` cannot make the same simplification -- it must remove every id
        this returns, or a document the caller was told was deleted could still be
        read back through the survivor (see `delete`'s docstring).
        """
        url = files_by_app_property_url(APP_PROPERTY_KEY, key, fields="files(id)")
        files = self._api("GET", url).get("files") or []
        return sorted((str(f["id"]) for f in files), key=str)

    def _find_file_by_key(self, key: str) -> str | None:
        """The single, canonical id for `key` -- the lowest, when more than one file
        matches (see `_find_all_file_ids_by_key`). Used by `get`, for which reading
        any one coherent, converged-upon file is correct; `delete` uses
        `_find_all_file_ids_by_key` directly instead, because leaving a second one
        behind is exactly the bug this class was fixed to not have.
        """
        ids = self._find_all_file_ids_by_key(key)
        return ids[0] if ids else None

    # ---- DocumentStorage --------------------------------------------------------

    def put(self, key: str, data: bytes, content_type: str) -> None:
        validated = validate_storage_key(key)
        existing_ids = self._find_all_file_ids_by_key(validated)
        if existing_ids:
            # Media-only update: the file already lives in the right folder under the
            # right name, only its bytes change. When a create-race (module
            # docstring) has left more than one file carrying this key, the lowest id
            # is the canonical one -- the same one `get` would already be reading --
            # and every other id is a duplicate nothing should still be pointing at.
            # Deleting them here, on the next write, heals the exact situation that
            # created the bug `delete` was fixed for: it is what stops "leftover
            # storage" from also becoming "delete doesn't delete" the moment a caller
            # writes to the key again. `delete` does not depend on `put` ever having
            # run, though -- it removes every matching id itself, so a key that is
            # only ever read after a race is still deleted correctly.
            canonical, *duplicates = existing_ids
            self._api(
                "PATCH",
                upload_media_url(canonical),
                body=data,
                content_type=content_type,
            )
            for duplicate_id in duplicates:
                self._api("DELETE", file_url(duplicate_id))
            return
        *folders, filename = validated.split("/")
        parent = self._ensure_folder_chain(folders)
        metadata = json.dumps(
            {"name": filename, "parents": [parent], "appProperties": {APP_PROPERTY_KEY: validated}}
        ).encode()
        body = b"".join(
            [
                f"--{_MULTIPART_BOUNDARY}\r\n".encode(),
                b"Content-Type: application/json; charset=UTF-8\r\n\r\n",
                metadata,
                f"\r\n--{_MULTIPART_BOUNDARY}\r\n".encode(),
                f"Content-Type: {content_type}\r\n\r\n".encode(),
                data,
                f"\r\n--{_MULTIPART_BOUNDARY}--\r\n".encode(),
            ]
        )
        self._api(
            "POST",
            upload_create_url(fields="id"),
            body=body,
            content_type=f"multipart/related; boundary={_MULTIPART_BOUNDARY}",
        )

    def get(self, key: str) -> bytes:
        validated = validate_storage_key(key)
        file_id = self._find_file_by_key(validated)
        if file_id is None:
            raise NotFound(STORAGE_ENTITY, key)
        url = file_media_url(file_id)
        try:
            return self._transport.bytes("GET", url, what=_WHAT_DOWNLOAD)
        except Conflict as failed:
            if failed.details.get("what") == _WHAT_DOWNLOAD and failed.details.get("status") == 404:
                # Found a moment ago by `_find_file_by_key`, gone now: a concurrent
                # delete, not a bug in the query. Either way, "no such document" is the
                # honest answer to give this caller.
                raise NotFound(STORAGE_ENTITY, key) from failed
            # A 404 from anywhere else in this call -- the token endpoint, notably --
            # is not a missing document and must not be reported as one.
            raise

    def delete(self, key: str) -> None:
        """Removes every file carrying `key`, not only the one a lookup would
        currently resolve to.

        A create-race (module docstring) can leave two files sharing the same
        `appProperties` value after concurrent first-writers, and `put` only reaps
        the extras when a write happens to land on the key afterwards. Deleting just
        the canonical one and leaving a duplicate behind would mean the *next* `get`
        resolves to that duplicate and returns content the caller was told was gone
        -- a document a customer asked to have erased coming back is a correctness
        failure, not a storage-hygiene one, so this removes all of them. Reproduced
        directly against a planted duplicate, not only reasoned about -- see
        test_storage_conformance.py's
        `test_delete_removes_every_file_left_by_a_create_race`.
        """
        validated = validate_storage_key(key)
        for file_id in self._find_all_file_ids_by_key(validated):
            self._api("DELETE", file_url(file_id))

    def signed_url(self, key: str, ttl: timedelta) -> str | None:
        """Always `None`, exactly like `LocalFileStorage`.

        Drive can mint a shareable link on demand, and that is precisely why this
        method must not: such a link is a bearer credential on a clock Google
        controls, not PigroCRM's, and it keeps granting access after the document is
        archived, the customer is deleted, or a user is offboarded -- entirely outside
        this system's own permission checks and audit trail. Routing every download
        through the API instead means there is exactly one place authorisation is
        decided, on both backends, and exactly one place it shows up in the audit
        log. "Drive could mint a link" is true and is not a reason to let it; anyone
        tempted to add one back should re-read this paragraph first, the same way
        `config.py` asks for `cookie_secure`.
        """
        validate_storage_key(key)
        return None

    def verify_root_accessible(self) -> None:
        """Confirms the configured root folder exists, this credential can see it, and
        it lives on a Shared Drive -- so a misconfiguration surfaces once, with a
        message that says what to fix, instead of showing up later as a bare 404 or a
        `storageQuotaExceeded` on some customer's first upload.

        Not called by anything else in this class: `storage_from_settings` calls it
        once, right after constructing a `GDriveStorage`, which is what makes this a
        per-process check rather than a per-request one. *When* in the process depends
        on the adapter: the MCP server builds its backend in `build_server`, so this
        runs at start-up there, while `pigrocrm_api.deps.get_storage` builds on first
        use, so on the API it runs inside the first request that needs a document
        backend -- and its failure is that request's, not the boot's. A script or a
        test that only needs the type is free to skip the network round trip entirely.
        """
        url = file_meta_url(self._root_folder_id, fields="id,driveId")
        try:
            parsed = self._transport.json("GET", url, what=_WHAT_VERIFY)
        except Conflict as failed:
            if failed.details.get("what") != _WHAT_VERIFY:
                # The credential was refused before the folder was ever asked about.
                # Telling an operator to re-share a folder when the private key is
                # wrong is precisely the wrong-thing-to-fix this method exists to
                # prevent, so an authentication failure is reported as itself.
                raise
            raise RuntimeError(
                "PIGROCRM_GDRIVE_ROOT_FOLDER_ID non e' raggiungibile dal service "
                f"account (HTTP {failed.details.get('status')}). Verificare che la "
                "cartella esista e sia stata condivisa con l'indirizzo email del "
                "service account, direttamente o tramite uno Shared Drive."
            ) from failed
        if "driveId" not in parsed:
            raise RuntimeError(
                "PIGROCRM_GDRIVE_ROOT_FOLDER_ID non si trova su uno Shared Drive: un "
                "service account non ha una propria quota di storage su Google Drive, "
                "quindi ogni upload fallirebbe con storageQuotaExceeded. Spostare la "
                "cartella su uno Shared Drive (o su una sua sottocartella)."
            )
