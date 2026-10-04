"""The two images an azienda owns: its logo and its signature (REB-627, spec 2026-10-03
§1.8, §3, §6).

Until this module existed every PDF drew one person's logo and one person's signature
copied from the bundle (REB-48 called that a defect), and `logo_key` and `firma_key` on
`emitter_profile` were columns nobody read. Now each azienda uploads its own, stored in
`DocumentStorage` under `aziende/{id}/`, and the Typst job of a document or an invoice
copies the right one into its own directory under the name the templates expect
(`render/pdf.py`). An azienda with no logo sets its name in type; one with no signature
leaves a transparent placeholder where the seeded offer template draws it.

The keys are server-managed: `AziendaUpsert` no longer takes them, so a whole-row `PUT`
can neither clear them nor point them at somebody else's file. The only writes are the
four here, admin-only like every other write on the row.
"""

import logging
import re
import zlib
from typing import Any, Final, Literal
from uuid import UUID

from lxml import etree
from sqlalchemy.orm import Session

from pigrocrm.core.activities.service import ActivityService
from pigrocrm.core.actor import Actor
from pigrocrm.core.db.base import uuid7
from pigrocrm.core.emitter.models import Azienda
from pigrocrm.core.emitter.repository import AziendaRepository
from pigrocrm.core.emitter.schemas import AziendaRead
from pigrocrm.core.errors import NotFound, ValidationFailed
from pigrocrm.core.storage.base import DocumentStorage

logger = logging.getLogger(__name__)

ENTITY: Final = "emitter_profile"
DEFAULT_LABEL: Final = "predefinita"

# Spec §3: «a PNG or SVG under 1 MiB».
MAX_IMAGE_BYTES: Final = 1024 * 1024

ImageKind = Literal["png", "svg"]
Slot = Literal["logo", "firma"]

CONTENT_TYPES: Final[dict[ImageKind, str]] = {"png": "image/png", "svg": "image/svg+xml"}

# The names the Typst job writes the images under, which are the names the header
# templates and the seeded offer template reference (`./media/...`). The signature keeps
# the name the bundled file had, so an offer template a space already seeded keeps
# compiling without an edit.
LOGO_FILES: Final[dict[ImageKind, str]] = {"png": "logo.png", "svg": "logo.svg"}
FIRMA_FILE: Final = "sign_is.png"

_PNG_SIGNATURE: Final = b"\x89PNG\r\n\x1a\n"
# How much of the inflated image stream is held at a time while checking it, and how
# much of it a single upload may produce in all: a 1 MiB file can inflate to a gigabyte,
# and this check runs on a request worker. 32 MiB is a 2900-pixel square in RGBA, far
# past anything a 60pt logo or a signature needs (Greptile and CodeRabbit, PR #510).
_INFLATE_PIECE: Final = 1 << 16
_INFLATE_BUDGET: Final = 32 << 20
# What an SVG served back to a browser must not carry: a script, an event handler, a
# `javascript:` link or an embedded HTML document. Typst ignores all of them, but the
# API serves the file to the admin's own browser. Refused at upload rather than
# sanitised: an admin can save the file again without the offending part, and nothing
# here rewrites what a person made. This text match is a courtesy to the admin, not the
# guarantee: an entity-encoded `javascript:` passes it. What holds the line is the
# serving route's `Content-Security-Policy: default-src 'none'` (`routers/aziende.py`),
# which blocks inline script, handlers and `javascript:` navigation when the file is
# opened in a tab, while the panel's preview goes through a blob `<img>`, which never
# scripts. Nobody relaxes those headers on the strength of this check.
_SVG_FORBIDDEN: Final = ("<script", "javascript:", "<foreignobject", "<iframe", "<embed")


def sniff_image(data: bytes) -> ImageKind | None:
    """`png` or `svg` from the bytes themselves, never from a name or a declared
    content type, and only a whole image: a PNG carries its header chunk and ends on
    its trailer, an SVG parses and its root is `svg`. `None` for anything else."""
    if data.startswith(_PNG_SIGNATURE):
        return "png" if _png_is_well_formed(data) else None
    head = data[:4096].decode("utf-8", "ignore").lstrip("\ufeff").lstrip().lower()
    if head.startswith(("<?xml", "<svg", "<!doctype svg", "<!--")) and "<svg" in head:
        return "svg" if _parses_as_svg(data) else None
    return None


# Adam7: the x and y of each pass's first pixel and its steps.
_ADAM7: Final = (
    (0, 0, 8, 8),
    (4, 0, 8, 8),
    (0, 4, 4, 8),
    (2, 0, 4, 4),
    (0, 2, 2, 4),
    (1, 0, 2, 2),
    (0, 1, 1, 2),
)
_CHANNELS: Final = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}


def _png_rows(
    width: int, height: int, depth: int, colour: int, interlace: int
) -> list[tuple[int, int]]:
    """The scanlines a PNG's inflated stream must hold, as `(rows, bytes per row)` runs:
    one run for a plain image, up to seven for an interlaced one, each row one filter
    byte plus its pixels. From this the exact inflated size follows, which is what
    tells a complete image from framing around missing pixels (CodeRabbit, PR #510)."""
    bits = depth * _CHANNELS[colour]
    if not interlace:
        return [(height, 1 + (width * bits + 7) // 8)]
    runs = []
    for x0, y0, dx, dy in _ADAM7:
        columns = (width - x0 + dx - 1) // dx if width > x0 else 0
        rows = (height - y0 + dy - 1) // dy if height > y0 else 0
        if columns and rows:
            runs.append((rows, 1 + (columns * bits + 7) // 8))
    return runs


class _Scanlines:
    """Walks the inflated bytes row by row: every row must open with a filter byte of
    0 to 4 and the stream must end exactly on the last row."""

    def __init__(self, runs: list[tuple[int, int]]) -> None:
        self.runs = runs
        self.run = 0
        self.rows_left = runs[0][0] if runs else 0
        self.in_row = 0
        self.ok = True

    def feed(self, chunk: bytes) -> None:
        position = 0
        while position < len(chunk) and self.ok:
            if self.run >= len(self.runs):
                self.ok = False  # bytes past the last row
                return
            row_bytes = self.runs[self.run][1]
            if self.in_row == 0 and chunk[position] > 4:
                self.ok = False
                return
            take = min(row_bytes - self.in_row, len(chunk) - position)
            self.in_row += take
            position += take
            if self.in_row == row_bytes:
                self.in_row = 0
                self.rows_left -= 1
                if self.rows_left == 0:
                    self.run += 1
                    self.rows_left = self.runs[self.run][0] if self.run < len(self.runs) else 0

    @property
    def complete(self) -> bool:
        return self.ok and self.run == len(self.runs) and self.in_row == 0


def _png_is_well_formed(data: bytes) -> bool:
    """Every chunk in place with its CRC: an `IHDR` of thirteen bytes first with a
    size, a depth, a colour type and an interlace the format defines, at least one
    `IDAT` whose stream inflates to exactly the scanlines that header announces, each
    opening with a legal filter byte, an empty `IEND` last and nothing after it. A file
    that only wears the framing around missing or malformed pixels would be stored
    under the size limit and fail every later render instead of this upload
    (CodeRabbit and Greptile, PR #510). The inflated bytes are discarded as they are
    checked, in bounded pieces, within a budget: a small file can inflate to a very
    large image, and this is a check, not a decode."""
    offset, first, idat, ended = len(_PNG_SIGNATURE), True, False, False
    stream = zlib.decompressobj()
    scanlines: _Scanlines | None = None
    while offset + 12 <= len(data):
        length = int.from_bytes(data[offset : offset + 4], "big")
        kind = data[offset + 4 : offset + 8]
        body_start, body_end = offset + 8, offset + 8 + length
        if body_end + 4 > len(data):
            return False
        declared = int.from_bytes(data[body_end : body_end + 4], "big")
        if zlib.crc32(data[offset + 4 : body_end]) & 0xFFFFFFFF != declared:
            return False
        if first:
            if kind != b"IHDR" or length != 13:
                return False
            width = int.from_bytes(data[body_start : body_start + 4], "big")
            height = int.from_bytes(data[body_start + 4 : body_start + 8], "big")
            depth, colour, compression, filtering, interlace = data[
                body_start + 8 : body_start + 13
            ]
            if (
                width == 0
                or height == 0
                or depth not in (1, 2, 4, 8, 16)
                or colour not in _CHANNELS
                or compression != 0
                or filtering != 0
                or interlace not in (0, 1)
            ):
                return False
            runs = _png_rows(width, height, depth, colour, interlace)
            if sum(rows * row_bytes for rows, row_bytes in runs) > _INFLATE_BUDGET:
                return False
            scanlines = _Scanlines(runs)
            first = False
        elif kind == b"IDAT":
            idat = True
            assert scanlines is not None
            if not _inflates(stream, data[body_start:body_end], scanlines):
                return False
        elif kind == b"IEND":
            ended = length == 0 and body_end + 4 == len(data)
            break
        offset = body_end + 4
    return (
        not first and idat and ended and stream.eof and scanlines is not None and scanlines.complete
    )


def _inflates(stream: Any, piece: bytes, scanlines: _Scanlines) -> bool:
    """Feed one `IDAT` payload to the decompressor, handing the output to the scanline
    walk in bounded pieces; `False` when the stream is not zlib or the rows do not add
    up. `Any`: the decompressor's class lives only in the type stubs, not in `zlib` at
    runtime."""
    try:
        scanlines.feed(stream.decompress(piece, _INFLATE_PIECE))
        while stream.unconsumed_tail and scanlines.ok:
            scanlines.feed(stream.decompress(stream.unconsumed_tail, _INFLATE_PIECE))
    except zlib.error:
        return False
    return scanlines.ok


def _parses_as_svg(data: bytes) -> bool:
    # No entities resolved, no DTD loaded, no network, no huge tree: the parser reads
    # the bytes it was given and nothing they point at.
    parser = etree.XMLParser(
        resolve_entities=False, no_network=True, huge_tree=False, load_dtd=False
    )
    try:
        root = etree.fromstring(data, parser=parser)
    except etree.XMLSyntaxError:
        return False
    return isinstance(root.tag, str) and etree.QName(root).localname == "svg"


_EVENT_HANDLER: Final = re.compile(r"\son[a-z]+\s*=")


def _check_svg(data: bytes) -> None:
    text = data.decode("utf-8", "ignore").lower()
    if any(marker in text for marker in _SVG_FORBIDDEN) or _EVENT_HANDLER.search(text):
        raise ValidationFailed(
            ENTITY,
            "file",
            "l'SVG contiene script, gestori di eventi o contenuto esterno",
            expected="un SVG con sole forme e testo",
        )


def logo_file_in(media: dict[str, bytes]) -> str | None:
    """Which logo file a media set carries, for the header's conditional."""
    for name in LOGO_FILES.values():
        if name in media:
            return name
    return None


class AziendaAssets:
    """Reads and writes of an azienda's logo and signature, on top of `DocumentStorage`."""

    def __init__(self, session: Session, storage: DocumentStorage) -> None:
        self.session = session
        self.storage = storage
        self.repo = AziendaRepository(session)
        self.activities = ActivityService(session)

    # -- reads -----------------------------------------------------------------

    def _row(self, azienda_id: UUID | None) -> Azienda:
        row = self.repo.default() if azienda_id is None else self.repo.get(azienda_id)
        if row is None:
            raise NotFound(ENTITY, DEFAULT_LABEL if azienda_id is None else str(azienda_id))
        return row

    def _read(self, key: str | None) -> tuple[bytes, str] | None:
        if not key:
            return None
        kind: ImageKind = "svg" if key.endswith(".svg") else "png"
        try:
            return self.storage.get(key), CONTENT_TYPES[kind]
        except NotFound:
            # The row names a file the storage no longer has: the same as no image,
            # which is what the PDF and the panel can honestly show.
            return None

    def logo(self, azienda_id: UUID | None = None) -> tuple[bytes, str] | None:
        """`(bytes, content_type)` of the logo, or `None` when the azienda has none."""
        return self._read(self._row(azienda_id).logo_key)

    def firma(self, azienda_id: UUID | None = None) -> tuple[bytes, str] | None:
        return self._read(self._row(azienda_id).firma_key)

    def media_for(self, azienda_id: UUID | None = None) -> dict[str, bytes]:
        """The files a Typst job writes under `media/` for this azienda, by name: the
        logo under `logo.png` or `logo.svg` and the signature under `sign_is.png`,
        each only when the azienda has one. `render_pdf` supplies the placeholder for
        a missing signature."""
        row = self._row(azienda_id)
        media: dict[str, bytes] = {}
        logo = self._read(row.logo_key)
        if logo is not None:
            data, content_type = logo
            media[LOGO_FILES["svg" if content_type == CONTENT_TYPES["svg"] else "png"]] = data
        firma = self._read(row.firma_key)
        if firma is not None:
            media[FIRMA_FILE] = firma[0]
        return media

    # -- writes ----------------------------------------------------------------

    @staticmethod
    def _check(data: bytes, *, slot: Slot) -> ImageKind:
        if len(data) > MAX_IMAGE_BYTES:
            raise ValidationFailed(
                ENTITY,
                "file",
                f"il file supera {MAX_IMAGE_BYTES // 1024} KiB",
                expected="un'immagine sotto 1 MiB",
            )
        kind = sniff_image(data)
        if kind is None:
            raise ValidationFailed(
                ENTITY,
                "file",
                "il file non è un PNG né un SVG",
                expected="un PNG o un SVG",
            )
        if kind == "svg":
            if slot == "firma":
                # The offers draw the signature through a Markdown image whose name is
                # fixed in the template (`sign_is.png`), and Typst reads the format
                # from the name: an SVG there would not compile.
                raise ValidationFailed(
                    ENTITY,
                    "file",
                    "la firma deve essere un PNG: i template delle offerte la leggono per nome",
                    expected="un PNG",
                )
            _check_svg(data)
        return kind

    def _set(
        self, azienda_id: UUID | None, data: bytes, actor: Actor, *, slot: Slot
    ) -> AziendaRead:
        actor.require_admin("update_azienda_assets")
        row = self._row(azienda_id)
        kind = self._check(data, slot=slot)
        attribute = f"{slot}_key"
        key = f"aziende/{row.id}/{slot}-{uuid7()}.{kind}"
        previous = getattr(row, attribute)
        self.storage.put(key, data, CONTENT_TYPES[kind])
        setattr(row, attribute, key)
        self.activities.record(ENTITY, row.id, "updated", actor, {"changed": [attribute]})
        self.session.commit()
        # The old file goes after the commit, never before: a commit that fails must
        # leave the row pointing at a file that still exists. Every upload writes a
        # fresh key, so two admins replacing the same image at once each delete only
        # the file they found, never the one the other just committed; the loser's own
        # file is an orphan, which costs nothing (CodeRabbit and Greptile, PR #510).
        if previous:
            self._forget(previous)
        return AziendaRead.model_validate(row)

    def _remove(self, azienda_id: UUID | None, actor: Actor, *, slot: Slot) -> AziendaRead:
        actor.require_admin("update_azienda_assets")
        row = self._row(azienda_id)
        attribute = f"{slot}_key"
        key = getattr(row, attribute)
        if key:
            setattr(row, attribute, None)
            self.activities.record(ENTITY, row.id, "updated", actor, {"changed": [attribute]})
            self.session.commit()
            # The file goes last, for the same reason as in `_set`.
            self._forget(key)
        return AziendaRead.model_validate(row)

    def _forget(self, key: str) -> None:
        """Best effort after a commit: the row no longer names `key`, and a storage that
        refuses the delete (Drive down) must not turn a committed change into an error
        the admin would retry against a state that already moved. The orphan is logged."""
        try:
            self.storage.delete(key)
        except Exception:  # noqa: BLE001  # whatever the backend raised, the row is right
            logger.warning("azienda image %s was replaced or removed but its file stays", key)

    def set_logo(self, data: bytes, actor: Actor, azienda_id: UUID | None = None) -> AziendaRead:
        return self._set(azienda_id, data, actor, slot="logo")

    def set_firma(self, data: bytes, actor: Actor, azienda_id: UUID | None = None) -> AziendaRead:
        return self._set(azienda_id, data, actor, slot="firma")

    def remove_logo(self, actor: Actor, azienda_id: UUID | None = None) -> AziendaRead:
        return self._remove(azienda_id, actor, slot="logo")

    def remove_firma(self, actor: Actor, azienda_id: UUID | None = None) -> AziendaRead:
        return self._remove(azienda_id, actor, slot="firma")
