"""The logo and the signature an azienda uploads (REB-627, spec 2026-10-03 §1.8, §3):
sniffed by content, bounded, stored under the azienda's own key, read back for the
Typst job and for the panel, removable; and the SVG hygiene the API's serving needs.
"""

import zlib
from uuid import UUID

import pytest
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.emitter.assets import _PNG_SIGNATURE, MAX_IMAGE_BYTES, AziendaAssets, sniff_image
from pigrocrm.core.emitter.models import Azienda
from pigrocrm.core.emitter.repository import AziendaRepository
from pigrocrm.core.errors import NotFound, PermissionDenied, ValidationFailed
from pigrocrm.core.render.pdf import BLANK_PNG
from pigrocrm.core.storage.local import LocalFileStorage

ADMIN = Actor(id=None, type="system", role="admin")
COLLABORATOR = Actor(id=None, type="user", role="collaboratore")
SVG = (
    b'<svg xmlns="http://www.w3.org/2000/svg" width="8" height="8">'
    b'<rect width="8" height="8"/></svg>'
)
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 32


def _default(session: Session) -> Azienda:
    row = AziendaRepository(session).default()
    assert row is not None
    return row


def test_the_bytes_decide_the_kind_never_a_name_and_only_a_whole_image() -> None:
    assert sniff_image(BLANK_PNG) == "png"
    assert sniff_image(SVG) == "svg"
    assert sniff_image(b'<?xml version="1.0"?>\n<svg xmlns="x"></svg>') == "svg"
    assert sniff_image(JPEG) is None
    assert sniff_image(b"<html><svg></svg></html>") is None
    assert sniff_image(b"") is None
    # Cut short: a PNG without its trailer, an SVG that does not parse, an XML whose
    # root is not an svg. Each would be stored under the size limit and fail every
    # later render instead of this upload.
    assert sniff_image(BLANK_PNG[:-12]) is None
    # Wearing the signature and the trailer with nothing valid in between.
    assert sniff_image(_PNG_SIGNATURE + b"\0\0\0\0IHDR\0\0\0\0" + BLANK_PNG[-12:]) is None
    # A chunk whose CRC does not match its bytes.
    assert sniff_image(BLANK_PNG[:-5] + b"\0" + BLANK_PNG[-4:]) is None
    # Every CRC right and the image stream not zlib at all: a file no renderer opens.
    assert sniff_image(_png(b"IDAT", b"not a zlib stream")) is None
    # And the same shape with a real stream passes, so the check is about the stream.
    assert sniff_image(_png(b"IDAT", zlib.compress(b"\0\0\0\0\0"))) == "png"


def _chunk(kind: bytes, body: bytes) -> bytes:
    return (
        len(body).to_bytes(4, "big")
        + kind
        + body
        + (zlib.crc32(kind + body) & 0xFFFFFFFF).to_bytes(4, "big")
    )


def _png(kind: bytes, body: bytes) -> bytes:
    """A 1x1 PNG whose data chunk is `body` under `kind`, every CRC correct."""
    ihdr = (1).to_bytes(4, "big") + (1).to_bytes(4, "big") + bytes([8, 6, 0, 0, 0])
    return _PNG_SIGNATURE + _chunk(b"IHDR", ihdr) + _chunk(kind, body) + _chunk(b"IEND", b"")
    assert sniff_image(b'<svg xmlns="x"><rect></svg>') is None
    assert sniff_image(b'<?xml version="1.0"?><not-svg><svg/></not-svg>') is None


def test_a_logo_is_stored_under_the_azienda_s_key_and_read_back_for_the_job(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    assets = AziendaAssets(db_session, local_storage)
    azienda = _default(db_session)
    read = assets.set_logo(BLANK_PNG, ADMIN)
    first_key = read.logo_key
    assert first_key is not None
    assert first_key.startswith(f"aziende/{azienda.id}/logo-") and first_key.endswith(".png")
    assert assets.logo() == (BLANK_PNG, "image/png")
    assert assets.media_for(azienda.id) == {"logo.png": BLANK_PNG}

    # An SVG replaces it under a key of its own, and the PNG leaves the storage.
    read = assets.set_logo(SVG, ADMIN, azienda.id)
    assert read.logo_key != first_key and read.logo_key.endswith(".svg")  # type: ignore[union-attr]
    assert assets.logo(azienda.id) == (SVG, "image/svg+xml")
    assert assets.media_for(azienda.id) == {"logo.svg": SVG}
    with pytest.raises(NotFound):
        local_storage.get(first_key)

    removed = assets.remove_logo(ADMIN, azienda.id)
    assert removed.logo_key is None
    assert assets.logo(azienda.id) is None
    assert assets.media_for(azienda.id) == {}
    # Removing twice is nothing, not an error.
    assert assets.remove_logo(ADMIN, azienda.id).logo_key is None


def test_the_signature_is_a_png_under_the_name_the_offer_template_reads(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    assets = AziendaAssets(db_session, local_storage)
    azienda = _default(db_session)
    read = assets.set_firma(BLANK_PNG, ADMIN)
    assert read.firma_key is not None
    assert read.firma_key.startswith(f"aziende/{azienda.id}/firma-")
    assert read.firma_key.endswith(".png")
    assert assets.firma() == (BLANK_PNG, "image/png")
    assert assets.media_for() == {"sign_is.png": BLANK_PNG}
    with pytest.raises(ValidationFailed) as refused:
        assets.set_firma(SVG, ADMIN)
    assert refused.value.details["field"] == "file"
    assert "PNG" in str(refused.value)


def test_what_is_refused_at_upload(db_session: Session, local_storage: LocalFileStorage) -> None:
    assets = AziendaAssets(db_session, local_storage)
    for bad, words in (
        (JPEG, "PNG"),
        (BLANK_PNG[:-12], "PNG"),
        (b'<svg xmlns="x"><rect></svg>', "PNG"),
        (b"\x89PNG\r\n\x1a\n" + b"\x00" * MAX_IMAGE_BYTES, "KiB"),
        (b'<svg xmlns="x"><script>alert(1)</script></svg>', "script"),
        (b'<svg xmlns="x" onload="alert(1)"></svg>', "script"),
        (b'<svg xmlns="x"><a href="javascript:alert(1)"><rect/></a></svg>', "script"),
        (b'<svg xmlns="x"><foreignObject><body/></foreignObject></svg>', "script"),
    ):
        with pytest.raises(ValidationFailed) as refused:
            assets.set_logo(bad, ADMIN)
        assert refused.value.details["field"] == "file"
        assert words in str(refused.value)
    assert _default(db_session).logo_key is None
    with pytest.raises(PermissionDenied):
        assets.set_logo(BLANK_PNG, COLLABORATOR)
    with pytest.raises(PermissionDenied):
        assets.remove_firma(COLLABORATOR)
    with pytest.raises(NotFound):
        assets.set_logo(BLANK_PNG, ADMIN, UUID(int=1))


def test_a_storage_that_refuses_the_cleanup_does_not_fail_a_committed_change(
    db_session: Session, local_storage: LocalFileStorage, monkeypatch: pytest.MonkeyPatch
) -> None:
    assets = AziendaAssets(db_session, local_storage)
    first = assets.set_logo(BLANK_PNG, ADMIN).logo_key

    def refuse(key: str) -> None:
        raise RuntimeError("drive down")

    monkeypatch.setattr(local_storage, "delete", refuse)
    replaced = assets.set_logo(SVG, ADMIN)
    assert replaced.logo_key != first and replaced.logo_key.endswith(".svg")  # type: ignore[union-attr]
    assert assets.logo() == (SVG, "image/svg+xml")
    assert assets.remove_logo(ADMIN).logo_key is None
    assert assets.logo() is None


def test_a_key_the_storage_no_longer_has_reads_as_no_image(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    """The row says there is a logo and the file is gone: the PDF sets the name in type
    and the panel shows nothing, rather than either failing on a missing blob."""
    azienda = _default(db_session)
    azienda.logo_key = f"aziende/{azienda.id}/logo.png"
    db_session.flush()
    assets = AziendaAssets(db_session, local_storage)
    assert assets.logo(azienda.id) is None
    assert assets.media_for(azienda.id) == {}
