"""The `billing` read of a space mailbox: attachments of an accepted type become inbox items
(REB-1016, § 2.2 of `2026-11-09-incoming-documents-inbox-design.md`).

`BillingSink` is the sink `mailboxes/imap_sync.py` hands the headers of a `billing` mailbox to.
It is the one place a part of a message is fetched, and the rules are the design's:

- **Space mailboxes only.** A personal mailbox cannot hold the purpose (the schema refuses it)
  and the sink refuses one again, so the same message in a personal mailbox yields nothing.
- **Only attachments of a type the inbox takes**, decided from BODYSTRUCTURE before anything is
  fetched: the extension of the part's name is in the inbox's allowlist. A part of any other
  kind, a part with no name, an inline part (Content-ID or inline disposition without an
  attachment disposition), an image under `IMAGE_FLOOR_BYTES` (a signature's logo) and the
  mailbox's own sent mail are never downloaded. The body of the message never is.
- **Capped before storage.** A part over `PART_MAX_BYTES` is not fetched, a message holds at
  most `MAX_PARTS` and `MAX_MESSAGE_BYTES`, and what arrives is type-checked by its bytes
  (`detect_file_type`) by the inbox itself. What is not taken is kept as a discarded item with
  its reason and no bytes, so nothing a message carried goes unaccounted for.
- **Never the body.** An item keeps the sender, subject and date of its message and nothing of
  its text (`MailOrigin`).
- **One message is one message.** The read hands a boundary message over again, so a message
  one of whose files is already an item is skipped. The bytes of every message of a chunk are
  fetched before any item is written, so a connection that drops mid-chunk leaves nothing of it
  behind, and the database's locks are not held while the server is asked. A message that cannot
  be written (a poisoned header, say) is recorded as one discarded item in a savepoint and does not
  stop the mailbox.
- **A ZIP is weighed opened.** Its entries and expanded bytes count against the message's budget,
  and one that is over it is not opened: a small ZIP must not become gigabytes of items.

It acts as the system: `Actor.system()` has no person to be the uploader, so the items are an
unscoped administrator's until classified (`inbox/access.py`), and they belong to the space: they
stay when the mailbox is disconnected.
"""

import contextlib
import io
import zipfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Final

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.errors import ValidationFailed
from pigrocrm.core.inbox.filetypes import (
    UnsupportedFile,
    basename,
    detect_file_type,
    extension_of,
    is_accepted_name,
)
from pigrocrm.core.inbox.repository import InboxRepository
from pigrocrm.core.inbox.uploads import InboxUploads, MailOrigin
from pigrocrm.core.mailboxes.imap_client import BodyPart, MessageHead, PartUnreadable
from pigrocrm.core.mailboxes.imap_sync import PartFetcher
from pigrocrm.core.mailboxes.models import Mailbox
from pigrocrm.core.storage.base import DocumentStorage

PURPOSE: Final = "billing"
# An image smaller than this is a logo or a tracking pixel in a signature, not a receipt. The
# size is BODYSTRUCTURE's, which is the encoded one.
IMAGE_FLOOR_BYTES: Final = 10 * 1024
# What one part may weigh encoded, fetched, and what one message may hold. Under the reader's own
# ceiling on a response (`imap_client.MAX_LITERAL_BYTES`).
PART_MAX_BYTES: Final = 12 * 1024 * 1024
MESSAGE_MAX_BYTES: Final = 25 * 1024 * 1024
MAX_PARTS: Final = 20
# What a ZIP may hold once opened, and how many items a message may leave behind as «refused».
ZIP_MAX_ENTRIES: Final = 100
MAX_REFUSED: Final = 5
_IMAGES: Final = frozenset({".png", ".jpg", ".jpeg"})


@dataclass(frozen=True, slots=True)
class _Taken:
    name: str
    content: bytes


@dataclass(slots=True)
class _Plan:
    """What one message will write, fetched and checked, before anything is written."""

    origin: MailOrigin
    taken: list[_Taken]
    refused: list[tuple[str, int, str]]


def wanted(part: BodyPart, *, image_floor: int = IMAGE_FLOOR_BYTES) -> bool:
    """Whether a part is an attachment the `billing` read takes, from its structure alone."""
    if part.filename is None or not is_accepted_name(part.filename):
        return False
    if part.disposition == "inline":
        return False
    if part.disposition is None and part.content_id is not None:
        return False
    return not (extension_of(part.filename) in _IMAGES and part.size < image_floor)


def _expanded_bytes(content: bytes, budget: int) -> int | None:
    """The bytes a ZIP holds once opened, read with `budget` as the ceiling (the headers' own
    sizes are not trusted), or `None` when it holds more than it may or more entries than
    `ZIP_MAX_ENTRIES`. A file that is not a readable ZIP weighs what it weighs: the upload says
    why it cannot be opened, and that is the item's reason."""
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            infos = [info for info in archive.infolist() if not info.is_dir()]
            if len(infos) > ZIP_MAX_ENTRIES:
                return None
            total = 0
            for info in infos:
                if info.flag_bits & 0x1:
                    continue  # encrypted: the upload refuses it, nothing is read
                with archive.open(info) as handle:
                    total += len(handle.read(budget - total + 1))
                if total > budget:
                    return None
            return total
    except (zipfile.BadZipFile, NotImplementedError, RuntimeError, ValueError, OSError):
        return len(content)


class BillingSink:
    """`MessageSink` for `billing`. `session` is the cycle's, and nothing here commits it."""

    purpose = PURPOSE

    def __init__(self, session: Session, storage: Callable[[], DocumentStorage]) -> None:
        """`storage` is a factory, called the first time a file is kept: a mailbox with nothing
        to take never needs the space's store, and a store that is misconfigured must not stop
        the cycle of a mailbox that does not use it."""
        self.session = session
        self.repo = InboxRepository(session)
        self._storage = storage
        self._uploads: InboxUploads | None = None
        self.actor = Actor.system()

    @property
    def uploads(self) -> InboxUploads:
        if self._uploads is None:
            self._uploads = InboxUploads(self.session, self._storage())
        return self._uploads

    def accept(self, mailbox: Mailbox, heads: Sequence[MessageHead], fetch: PartFetcher) -> None:
        if not mailbox.space_owned:
            return
        own = (mailbox.address or "").strip().lower()
        plans: list[_Plan] = []
        for head in heads:
            if own and head.from_address == own:
                continue
            candidates = [part for part in head.parts if wanted(part)]
            if not candidates:
                continue
            if self.repo.mail_seen(mailbox.id, head.uidvalidity, head.uid):
                continue
            plans.append(self._plan(mailbox, head, candidates, fetch))
        # Everything is fetched, and then written: the writes take the inbox's locks.
        for plan in plans:
            self._write(plan)

    def committed(self) -> None:
        if self._uploads is not None:
            self._uploads.written_keys.clear()

    def discard(self) -> None:
        if self._uploads is not None:
            self._uploads.discard_written()

    def _plan(
        self, mailbox: Mailbox, head: MessageHead, parts: list[BodyPart], fetch: PartFetcher
    ) -> _Plan:
        origin = MailOrigin(
            mailbox_id=mailbox.id,
            uidvalidity=head.uidvalidity,
            uid=head.uid,
            message_id=head.message_id,
            sender=head.from_address,
            subject=head.subject,
            date=head.internal_date,
        )
        refused: list[tuple[str, int, str]] = []
        taken: list[_Taken] = []
        total = 0
        for index, part in enumerate(parts):
            name = basename(part.filename or "")
            if index >= MAX_PARTS:
                refused.append((name, part.size, f"oltre i {MAX_PARTS} allegati per messaggio"))
            elif part.size > PART_MAX_BYTES or total + part.size > MESSAGE_MAX_BYTES:
                refused.append((name, part.size, "l'allegato è troppo grande per essere preso"))
            else:
                try:
                    content = fetch(head, part)
                except PartUnreadable as exc:
                    refused.append((name, part.size, str(exc)))
                    continue
                weight = len(content)
                if content.startswith(b"PK\x03\x04"):
                    opened = _expanded_bytes(content, MESSAGE_MAX_BYTES - total)
                    if opened is None:
                        refused.append((name, part.size, "l'archivio, aperto, è troppo grande"))
                        continue
                    weight = max(weight, opened)
                total += weight
                taken.append(_Taken(name, content))
        if len(refused) > MAX_REFUSED:
            rest = len(refused) - (MAX_REFUSED - 1)
            refused = refused[: MAX_REFUSED - 1] + [
                ("altri allegati", 0, f"altri {rest} allegati del messaggio non sono stati presi")
            ]
        return _Plan(origin, taken, refused)

    def _write(self, plan: _Plan) -> None:
        """One message's items, in a savepoint: one that cannot be written is one discarded item
        with what is safe to say of it, and the bytes it had put in the store go."""
        before = len(self.uploads.written_keys)
        try:
            with self.session.begin_nested():
                self._write_items(plan)
        except SQLAlchemyError:
            for key in self.uploads.written_keys[before:]:
                # The failure being handled is the one to report, not a cleanup's.
                with contextlib.suppress(Exception):
                    self.uploads.storage.delete(key)
            del self.uploads.written_keys[before:]
            origin = plan.origin
            blank = MailOrigin(
                origin.mailbox_id, origin.uidvalidity, origin.uid, "", "", "", origin.date
            )
            with self.session.begin_nested():
                self.uploads.record_refused(
                    "messaggio", 0, "il messaggio non si è potuto registrare", blank
                )

    def _write_items(self, plan: _Plan) -> None:
        for name, size, reason in plan.refused:
            self.uploads.record_refused(name, size, reason, plan.origin)
        for file in plan.taken:
            try:
                detect_file_type(file.name, file.content)
                self.uploads.upload(
                    [(file.name, file.content)], self.actor, origin=plan.origin, commit=False
                )
            except (UnsupportedFile, ValidationFailed) as exc:
                reason = exc.details["reason"] if isinstance(exc, ValidationFailed) else str(exc)
                self.uploads.record_refused(file.name, len(file.content), reason, plan.origin)
