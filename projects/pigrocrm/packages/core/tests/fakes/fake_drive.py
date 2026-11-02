"""An in-memory Google Drive that speaks the same HTTP surface `GDriveStorage` uses.

Deliberately a fake of the *transport*, not of `GDriveStorage` itself: the URL
building, the query escaping, the multipart body, the `appProperties` filter and the
error handling are exactly the parts most likely to be wrong, so they must run for
real. What it replaces is the network, nothing above it.

Implements: files.get (single file/folder metadata, used by `verify_root_accessible`
and by a read-only caller that needs a specific file's metadata), files.list with a
`q` filter -- `name=` + parent for folder lookup, `appProperties has {...}` for
identity lookup, and a bare `'<id>' in parents` for listing a folder's children, with
`pageToken`/`pageSize` pagination -- files.create for a folder, multipart create and
media-only update for a file, media download, `files/{id}/export` for a Google Doc
exported as text, and delete -- all with `supportsAllDrives=true`, because a service
account's files live on a Shared Drive.

`requests` is the point of the read surface, on the model of `fakes/fake_gmail.py`:
every request is recorded with its parsed query string, so a test can assert on *what
was asked of Google* -- in particular, that every `files.list` carried `'...' in
parents` and never a `contains` clause, which this fake refuses outright (see `_list`).
"""

import json
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, urlparse

FOLDER_MIME = "application/vnd.google-apps.folder"
# Google's own "native" formats -- Docs, Sheets, Slides, folders -- carry no binary
# `size` in files.list/files.get: they have no bytes on Drive to measure, only an
# export. A fake that invented a size for them would let a caller that (wrongly)
# trusts `size` for a Google Doc pass a test it should fail.
_GOOGLE_NATIVE_MIME_PREFIX = "application/vnd.google-apps."
GOOGLE_DOC_MIME = "application/vnd.google-apps.document"


@dataclass
class _File:
    id: str
    name: str
    parent: str
    mime: str
    data: bytes = b""
    app_properties: dict[str, str] = field(default_factory=dict)
    # New, and additive only: every existing call site constructs a `_File`
    # positionally with at most six arguments, so this has to have a default and come
    # last, or `test_storage_conformance.py` / `test_drive_transport.py` -- which build
    # `_File`s directly and are owned by a different task in this same checkout --
    # would break. Empty means "this file has exactly the one parent named by
    # `.parent`"; see `all_parents`.
    parents: list[str] = field(default_factory=list)
    modified_time: str = "2026-01-01T00:00:00.000Z"

    @property
    def all_parents(self) -> list[str]:
        return self.parents or [self.parent]


@dataclass(frozen=True)
class RecordedRequest:
    """One HTTP call, with its query string already parsed.

    On the model of `fakes/fake_gmail.py`'s `RecordedRequest`: a test should be able to
    assert on what was actually asked of Google, not merely on what the fake happened
    to answer.
    """

    method: str
    path: str
    params: dict[str, list[str]]

    @property
    def q(self) -> str | None:
        """The Drive search expression, if this was a `files.list` call."""
        values = self.params.get("q")
        return values[0] if values else None

    @property
    def is_files_list(self) -> bool:
        return self.method == "GET" and self.path.endswith("/files") and "q" in self.params


@dataclass
class FakeDrive:
    root_id: str = "ROOT"
    # Whether `files.get` on `root_id` itself reports a `driveId` -- i.e. whether the
    # configured root behaves as if it were on a Shared Drive. Set to False to
    # exercise `verify_root_accessible`'s second failure mode.
    root_on_shared_drive: bool = True
    files: dict[str, _File] = field(default_factory=dict)
    calls: list[tuple[str, str]] = field(default_factory=list)
    # Every request, with its query string parsed -- see `RecordedRequest`. Kept
    # alongside `calls` rather than replacing it: `calls` is already depended on by
    # `test_storage_conformance.py` (`[url for _, url in drive.calls if ...]`), and a
    # fake changing the shape of state another task's tests already read is exactly
    # the kind of collision this slice is trying to avoid.
    requests: list[RecordedRequest] = field(default_factory=list)
    next_id: int = 1
    token_requests: int = 0
    # A queue of statuses to return, consumed one per call (FIFO), before falling
    # through to the real handling below. A single-shot failure is `fail_with = [403]`;
    # `fail_with = [429, 429]` simulates two transient failures followed by a normal
    # response, for testing retry-then-succeed. Every call while this is non-empty is
    # intercepted -- including the OAuth token request -- since a real transient
    # failure does not know or care which endpoint it happens to hit.
    fail_with: list[int] = field(default_factory=list)

    def _new_id(self) -> str:
        self.next_id += 1
        return f"id{self.next_id}"

    # ---- seeding, for a read-only Drive a test wants to build without touching a
    # single `_File` field by hand -------------------------------------------------

    def add_folder(self, name: str, *, parent: str, file_id: str | None = None) -> str:
        """Adds a folder under `parent` (a folder id, or `self.root_id`) and returns
        its id."""
        new_id = file_id or self._new_id()
        self.files[new_id] = _File(new_id, name, parent, FOLDER_MIME, parents=[parent])
        return new_id

    def add_file(
        self,
        name: str,
        *,
        parent: str,
        mime: str = "application/octet-stream",
        content: bytes = b"",
        file_id: str | None = None,
    ) -> str:
        """Adds a file under `parent` and returns its id. `content` is served back
        verbatim by `alt=media`, and -- for a file whose `mime` is
        `application/vnd.google-apps.document` -- by `/export` as well."""
        new_id = file_id or self._new_id()
        self.files[new_id] = _File(new_id, name, parent, mime, content, parents=[parent])
        return new_id

    def __call__(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        body: bytes | None,
        *,
        max_bytes: int | None = None,
    ) -> tuple[int, bytes, dict[str, str]]:
        """The seam: what Drive answers, with the body cut at `max_bytes + 1` as the real
        one reads it, and no response headers (Drive's `Retry-After` is not modelled)."""
        status, payload = self._handle(method, url, headers, body)
        return status, payload if max_bytes is None else payload[: max_bytes + 1], {}

    def _handle(
        self, method: str, url: str, headers: dict[str, str], body: bytes | None
    ) -> tuple[int, bytes]:
        self.calls.append((method, url))
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        self.requests.append(RecordedRequest(method=method, path=parsed.path, params=query))
        if self.fail_with:
            status = self.fail_with.pop(0)
            return status, json.dumps({"error": {"message": "boom"}}).encode()
        if parsed.netloc == "oauth2.googleapis.com":
            self.token_requests += 1
            return 200, json.dumps({"access_token": "at-1", "expires_in": 3600}).encode()
        if method == "GET" and parsed.path.endswith("/files") and "q" in query:
            return self._list(query)
        if method == "GET" and query.get("alt") == ["media"]:
            return self._download(parsed.path)
        if method == "GET" and parsed.path.endswith("/export"):
            return self._export(parsed.path)
        if method == "GET" and re.search(r"/files/[^/]+$", parsed.path):
            return self._get_metadata(parsed.path.rsplit("/", 1)[-1])
        if method == "POST" and "upload" in parsed.path:
            return self._create(headers, body or b"")
        if method == "PATCH" and "upload" in parsed.path:
            return self._update_media(parsed.path, body or b"")
        if method == "POST" and parsed.path.endswith("/files"):
            return self._create_folder(json.loads((body or b"{}").decode()))
        if method == "DELETE":
            self.files.pop(parsed.path.rsplit("/", 1)[-1], None)
            return 204, b""
        return 404, json.dumps({"error": {"message": f"unhandled {method} {url}"}}).encode()

    def _metadata(self, f: _File) -> dict[str, Any]:
        """The `files.get`/`files.list` shape a read-only caller needs: id, name,
        mimeType, modifiedTime and parents always; size only for a file that has
        actual bytes on Drive to measure (see `_GOOGLE_NATIVE_MIME_PREFIX`)."""
        meta: dict[str, Any] = {
            "id": f.id,
            "name": f.name,
            "mimeType": f.mime,
            "modifiedTime": f.modified_time,
            "parents": f.all_parents,
        }
        if not f.mime.startswith(_GOOGLE_NATIVE_MIME_PREFIX):
            # Google's own API returns `size` as a string, not a number.
            meta["size"] = str(len(f.data))
        return meta

    def _get_metadata(self, file_id: str) -> tuple[int, bytes]:
        if file_id == self.root_id:
            payload: dict[str, Any] = {"id": self.root_id}
            if self.root_on_shared_drive:
                payload["driveId"] = "shared-drive-1"
            return 200, json.dumps(payload).encode()
        found = self.files.get(file_id)
        if found is None:
            return 404, json.dumps({"error": {"message": "File not found"}}).encode()
        return 200, json.dumps(self._metadata(found)).encode()

    def _list(self, query: dict[str, list[str]]) -> tuple[int, bytes]:
        q = query.get("q", [""])[0]
        if re.search(r"contains", q, re.IGNORECASE):
            # `contains` is a substring/prefix match on Drive, not an exact one, and
            # every query this fake's callers are allowed to build is exact: a folder
            # lookup by full name, an `appProperties` identity lookup, or "children of
            # this id". A caller reaching for `contains` is reaching for something none
            # of those are, and a fake that answered it anyway would hide the mistake.
            raise AssertionError(q)
        app_property = re.search(
            r"appProperties has \{ key='([^']*)' and value='((?:[^'\\]|\\.)*)' \}", q
        )
        if app_property:
            wanted_value = app_property.group(2).replace("\\'", "'").replace("\\\\", "\\")
            matches = [
                {"id": f.id}
                for f in self.files.values()
                if f.app_properties.get(app_property.group(1)) == wanted_value
            ]
            return 200, json.dumps({"files": matches}).encode()
        name = re.search(r"name='((?:[^'\\]|\\.)*)'", q)
        parent = re.search(r"'([^']+)' in parents", q)
        if name is None and parent is None:
            raise AssertionError(q)
        if name:
            assert parent, q
            wanted = name.group(1).replace("\\'", "'")
            matches = [
                {"id": f.id, "name": f.name}
                for f in self.files.values()
                if f.name == wanted and f.parent == parent.group(1) and f.mime == FOLDER_MIME
            ]
            return 200, json.dumps({"files": matches}).encode()
        assert parent
        return self._list_children(parent.group(1), query)

    def _list_children(self, parent_id: str, query: dict[str, list[str]]) -> tuple[int, bytes]:
        """The page of a folder's children named by `pageToken` (an offset into a
        stable, insertion order listing), `pageSize` entries long."""
        children = [f for f in self.files.values() if parent_id in f.all_parents]
        page_size = int(query["pageSize"][0]) if "pageSize" in query else len(children) or 1
        start = int(query["pageToken"][0]) if "pageToken" in query else 0
        page = children[start : start + page_size]
        payload: dict[str, Any] = {"files": [self._metadata(f) for f in page]}
        next_start = start + len(page)
        if next_start < len(children):
            payload["nextPageToken"] = str(next_start)
        return 200, json.dumps(payload).encode()

    def _create_folder(self, payload: dict[str, Any]) -> tuple[int, bytes]:
        new = _File(self._new_id(), payload["name"], payload["parents"][0], FOLDER_MIME)
        self.files[new.id] = new
        return 200, json.dumps({"id": new.id, "name": new.name}).encode()

    def _create(self, headers: dict[str, str], body: bytes) -> tuple[int, bytes]:
        boundary = re.search(r'boundary="?([^";]+)"?', headers["Content-Type"])
        assert boundary, headers
        parts = body.split(b"--" + boundary.group(1).encode())
        metadata = json.loads(parts[1].split(b"\r\n\r\n", 1)[1].rstrip(b"\r\n").decode())
        # The trailing `\r\n` before the closing boundary is part of the multipart
        # framing, not the payload -- and must be removed as a fixed two-byte suffix,
        # not with `.rstrip(b"\r\n")`: `rstrip` deletes every trailing byte that is
        # *either* `\r` or `\n`, so a payload legitimately ending in its own `\n` (an
        # ordinary text/PDF byte) would have that byte eaten too. A real PDF written by
        # `put()` and read back by `get()` caught this the first time this fake ran for
        # real, which is exactly why this fake exists.
        data_with_framing = parts[2].split(b"\r\n\r\n", 1)[1]
        data = data_with_framing[:-2] if data_with_framing.endswith(b"\r\n") else data_with_framing
        new = _File(
            self._new_id(),
            metadata["name"],
            metadata["parents"][0],
            "application/octet-stream",
            data,
            dict(metadata.get("appProperties") or {}),
        )
        self.files[new.id] = new
        return 200, json.dumps({"id": new.id}).encode()

    def _update_media(self, path: str, data: bytes) -> tuple[int, bytes]:
        file_id = path.rsplit("/", 1)[-1]
        found = self.files.get(file_id)
        if found is None:
            return 404, json.dumps({"error": {"message": "File not found"}}).encode()
        found.data = data
        return 200, json.dumps({"id": found.id}).encode()

    def _download(self, path: str) -> tuple[int, bytes]:
        file_id = path.rsplit("/", 1)[-1]
        found = self.files.get(file_id)
        return (200, found.data) if found else (404, b'{"error":{"message":"not found"}}')

    def _export(self, path: str) -> tuple[int, bytes]:
        """`GET /files/{id}/export?mimeType=text/plain`: the text of a Google Doc.

        Only `mimeType=text/plain` is exercised by any caller of this fake, so that is
        all it models -- but which *export* format was asked for is beside the point
        the 400 below exists to make: Drive's `export` endpoint only exists at all for
        its native formats (Docs, Sheets, Slides), and answers 400 for anything else,
        including an ordinary PDF or Word file that a caller mistakenly tried to export
        rather than download.
        """
        file_id = path.rsplit("/export", 1)[0].rsplit("/", 1)[-1]
        found = self.files.get(file_id)
        if found is None:
            return 404, json.dumps({"error": {"message": "File not found"}}).encode()
        if found.mime != GOOGLE_DOC_MIME:
            return (
                400,
                json.dumps(
                    {"error": {"message": "Export only supports Google Docs Editors files."}}
                ).encode(),
            )
        return 200, found.data
