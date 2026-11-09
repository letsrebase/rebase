"""Bytes in, and bytes out: the upload road of the inbox (design § 2.1) and the download of an
item's file. Over REST and the web only: bytes never travel over MCP (§ 6), which is why this
is not a `*Service` and has no tool.

A request is **all or nothing**, in two steps. First everything that is true or false of the
files is checked with nothing written: the request's ceilings, every loose file's type and
signature, every ZIP opened through `invoices.import_upload.expand_uploads` with its limits
(2000 files, 10 MiB per entry, 100 MiB in all, no password) and one level deep. A loose file
the inbox does not take refuses the request with the sentence that says why. An entry of a ZIP
that it does not take is different: the ZIP was one thing the person sent, so the entry is
kept as a discarded item with its reason, and nothing a ZIP held goes unaccounted for.

Then the items are written in one transaction, under the dedup lock: each file is read once
(`reading.read_file`), compared with what the inbox already holds by content hash and natural
key (design § 3.3), and becomes a new item, a duplicate of the item that came first, or a
discarded one. A ZIP is kept as the **parent item** (state `filed`, kind `archive`) and each
entry points at it; a ZIP that is itself a duplicate is one duplicate and is not opened again.
The bytes of a duplicate are kept only when they differ from its original's (a signed `.p7m`
is not the bare XML it signs, and which one is wanted is not for the inbox to say).
"""

import contextlib
from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.db import uuid7
from pigrocrm.core.documents.naming import slugify_folder
from pigrocrm.core.documents.schemas import SIZE_MAX
from pigrocrm.core.errors import NotFound, ValidationFailed
from pigrocrm.core.inbox.access import ENTITY, visible_item, visible_to
from pigrocrm.core.inbox.filetypes import (
    ACCEPTED,
    ACCEPTED_DESCRIPTION,
    FileType,
    UnsupportedFile,
    basename,
    detect_file_type,
)
from pigrocrm.core.inbox.models import FILE_NAME_MAX_LENGTH, InboxItem
from pigrocrm.core.inbox.reading import Reading, read_file, sha256_of
from pigrocrm.core.inbox.repository import InboxRepository
from pigrocrm.core.inbox.schemas import InboxItemRead, InboxUploadResult
from pigrocrm.core.invoices.import_upload import (
    UPLOAD_MAX_FILES,
    UPLOAD_MAX_TOTAL_BYTES,
    expand_uploads,
)
from pigrocrm.core.storage.base import DocumentStorage

UPLOAD_ACTION = "upload_inbox_file"
"""The action `upload` refuses a non-writer under; the route checks it before reading the body."""

_SLUG_MAX_LENGTH = 80
_NESTED_ARCHIVE = "Archivio dentro un archivio: non viene aperto"
_SAME_CONTENT = "Stesso contenuto di un file gia' arrivato"
_SAME_INVOICE = "Stessa fattura (emittente, numero e data) di un file gia' arrivato"
_ORIGINAL_DISCARDED = ", che era stato scartato"


@dataclass(slots=True)
class _Entry:
    """One file to write: a loose upload or one entry of a ZIP."""

    name: str
    content: bytes
    file_type: FileType | None
    rejection: str | None = None
    """Why the inbox will not take it, for an entry of a ZIP. A loose file is refused before."""
    reading: Reading | None = None
    """What is read of it, once, before the dedup lock is taken."""


@dataclass(slots=True)
class _Upload:
    entry: _Entry
    members: list[_Entry] | None = None
    """The entries, when `entry` is a ZIP."""


def _read(entry: _Entry) -> _Entry:
    if entry.file_type is None:
        entry.reading = Reading(content_sha256=sha256_of(entry.content))
    else:
        entry.reading = read_file(entry.name, entry.file_type, entry.content)
    return entry


def _clean_name(name: str) -> str:
    """A name a person can read and Postgres can store: no control characters, at most the
    column's width (the end kept, where the extension is)."""
    cleaned = "".join(ch for ch in name if ch.isprintable()).strip() or "senza-nome"
    return cleaned[-FILE_NAME_MAX_LENGTH:]


def _refuse(reason: str, *, expected: str | None = None) -> ValidationFailed:
    return ValidationFailed(ENTITY, "files", reason, expected=expected)


def _storage_key(item_id: UUID, name: str, file_type: FileType) -> str:
    """`inbox/{id}/{slug}{ext}`: the key rule allows lowercase `[a-z0-9._-]` only, so the name
    is slugified and the extension comes from the type, never from the uploaded name."""
    stem = basename(name).rsplit(".", 1)[0]
    slug = slugify_folder(stem)[:_SLUG_MAX_LENGTH].strip("-") or "senza-nome"
    return f"inbox/{item_id}/{slug}{ACCEPTED[file_type].extension}"


class InboxUploads:
    def __init__(self, session: Session, storage: DocumentStorage) -> None:
        self.session = session
        self.storage = storage
        self.repo = InboxRepository(session)

    # ---- checking, with nothing written --------------------------------------------

    def _expand(self, entry: _Entry) -> list[_Entry]:
        try:
            files = expand_uploads([(entry.name, entry.content)])
        except ValidationFailed as exc:
            raise _refuse(exc.details["reason"], expected=exc.details.get("expected")) from exc
        members: list[_Entry] = []
        for file in files:
            name = _clean_name(file.name)
            try:
                file_type: FileType | None = detect_file_type(name, file.content)
            except UnsupportedFile as exc:
                members.append(_read(_Entry(name, file.content, None, str(exc))))
                continue
            rejection = _NESTED_ARCHIVE if file_type == "zip" else None
            members.append(_read(_Entry(name, file.content, file_type, rejection)))
        return members

    def _prepare(self, files: Sequence[tuple[str, bytes]]) -> list[_Upload]:
        if not files:
            raise _refuse(
                "nessun file da caricare", expected=f"almeno un file: {ACCEPTED_DESCRIPTION}"
            )
        if len(files) > UPLOAD_MAX_FILES:
            raise _refuse(
                f"i file sono piu' di {UPLOAD_MAX_FILES}",
                expected=f"al massimo {UPLOAD_MAX_FILES} file per richiesta",
            )
        uploads: list[_Upload] = []
        total = 0
        items = 0
        for raw_name, content in files:
            name = _clean_name(raw_name)
            if len(content) > SIZE_MAX:
                raise _refuse(
                    f"{basename(name)}: il file supera {SIZE_MAX} byte",
                    expected=f"al massimo {SIZE_MAX} byte per file",
                )
            try:
                file_type = detect_file_type(name, content)
            except UnsupportedFile as exc:
                raise _refuse(
                    str(exc),
                    expected=f"un file {ACCEPTED_DESCRIPTION}, con il contenuto del suo tipo",
                ) from exc
            entry = _read(_Entry(name, content, file_type))
            upload = _Upload(entry, self._expand(entry) if file_type == "zip" else None)
            items += 1 if upload.members is None else 1 + len(upload.members)
            if items > UPLOAD_MAX_FILES:
                raise _refuse(
                    f"i file, con quelli degli archivi, sono piu' di {UPLOAD_MAX_FILES}",
                    expected=f"al massimo {UPLOAD_MAX_FILES} file per richiesta, archivi compresi",
                )
            total += (
                len(content)
                if upload.members is None
                else sum(len(member.content) for member in upload.members)
            )
            if total > UPLOAD_MAX_TOTAL_BYTES:
                raise _refuse(
                    f"i file superano {UPLOAD_MAX_TOTAL_BYTES} byte in tutto",
                    expected=f"al massimo {UPLOAD_MAX_TOTAL_BYTES} byte in tutto",
                )
            uploads.append(upload)
        return uploads

    # ---- writing --------------------------------------------------------------------

    def _put(self, item: InboxItem, entry: _Entry, written: list[str]) -> None:
        """Keeps the bytes of `entry` for `item`, after its row is in the transaction: the
        row first so a failed insert never leaves bytes behind, as `DocumentUploads` orders it."""
        assert entry.file_type is not None
        key = _storage_key(item.id, entry.name, entry.file_type)
        self.storage.put(key, entry.content, ACCEPTED[entry.file_type].content_type)
        written.append(key)
        item.storage_key = key

    def _duplicate_reason(self, original: InboxItem, reading: Reading) -> str:
        same_content = original.content_sha256 == reading.content_sha256
        reason = _SAME_CONTENT if same_content else _SAME_INVOICE
        return reason + (_ORIGINAL_DISCARDED if original.state == "discarded" else "")

    def _write(
        self,
        entry: _Entry,
        reading: Reading,
        actor: Actor,
        written: list[str],
        *,
        parent_id: UUID | None,
        kind: str | None = None,
        state: str = "new",
        reason: str | None = None,
    ) -> InboxItem:
        digest = sha256_of(entry.content)
        original = self.repo.find_original(
            reading.content_sha256,
            reading.natural_key,
            visible_to(actor),
            archive=kind == "archive",
        )
        item = InboxItem(
            id=uuid7(),
            source="upload",
            original_name=entry.name,
            file_type=entry.file_type,
            size_bytes=len(entry.content),
            sha256=digest,
            content_sha256=reading.content_sha256,
            natural_key=reading.natural_key,
            parent_id=parent_id,
            uploaded_by_id=actor.id,
            kind=kind,
            state=state,
            reason=reason,
        )
        keep_bytes = entry.file_type is not None
        if original is not None:
            item.state = "duplicate"
            item.kind = None
            item.duplicate_of_id = original.id
            item.reason = self._duplicate_reason(original, reading)
            # The original's bytes are the same file's, unless it never kept any.
            keep_bytes = keep_bytes and (digest != original.sha256 or original.storage_key is None)
        self.repo.add(item)
        if keep_bytes:
            self._put(item, entry, written)
        return item

    def _file(
        self, entry: _Entry, actor: Actor, written: list[str], parent_id: UUID | None
    ) -> InboxItem:
        reading = entry.reading
        assert reading is not None
        discard = entry.rejection or reading.discard_reason
        if discard is not None:
            return self._write(
                entry,
                reading,
                actor,
                written,
                parent_id=parent_id,
                state="discarded",
                reason=discard,
            )
        return self._write(
            entry,
            reading,
            actor,
            written,
            parent_id=parent_id,
            kind=reading.kind,
            reason=reading.note,
        )

    def _archive(self, upload: _Upload, actor: Actor, written: list[str]) -> list[InboxItem]:
        """The ZIP as the parent item, then its entries pointing at it."""
        assert upload.members is not None
        entry = upload.entry
        parent = self._write(
            entry,
            Reading(content_sha256=sha256_of(entry.content)),
            actor,
            written,
            parent_id=None,
            kind="archive",
            state="filed",
            reason=f"Archivio aperto: {len(upload.members)} file",
        )
        items = [parent]
        if parent.state == "duplicate":
            return items
        for member in upload.members:
            items.append(self._file(member, actor, written, parent.id))
        return items

    def upload(self, files: Sequence[tuple[str, bytes]], actor: Actor) -> InboxUploadResult:
        """`(name, bytes)` for every file of the request. Refused whole, with nothing written,
        for a request or a loose file the inbox does not take; otherwise every file ends as an
        item, however it ended (new, duplicate, discarded)."""
        actor.require_write(UPLOAD_ACTION)
        uploads = self._prepare(files)
        written: list[str] = []
        items: list[InboxItem] = []
        try:
            self.repo.lock_for_dedup()
            for upload in uploads:
                if upload.members is not None:
                    items.extend(self._archive(upload, actor, written))
                else:
                    items.append(self._file(upload.entry, actor, written, None))
            result = InboxUploadResult(items=[InboxItemRead.model_validate(i) for i in items])
            self.session.commit()
        except BaseException:
            self.session.rollback()
            for key in written:
                # The failure being handled is the one to raise, not a cleanup's.
                with contextlib.suppress(Exception):
                    self.storage.delete(key)
            raise
        return result

    def download(self, item_id: UUID, actor: Actor) -> tuple[bytes, str, str]:
        """`(bytes, content type, file name)` of an item the actor may see. An item that holds
        no bytes (a duplicate of the same bytes, a filed one) has the file of its original and
        not its own: `NotFound`, which says so."""
        item = visible_item(self.session, item_id, actor)
        if item.storage_key is None or item.file_type is None:
            raise NotFound(
                ENTITY,
                item_id,
                message="questo elemento non conserva il file: e' quello dell'elemento originale",
            )
        return (
            self.storage.get(item.storage_key),
            ACCEPTED[item.file_type].content_type,
            basename(item.original_name),
        )
