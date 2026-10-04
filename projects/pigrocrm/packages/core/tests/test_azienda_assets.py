"""The logo and the signature an azienda uploads (REB-627, spec 2026-10-03 §1.8, §3):
sniffed by content, bounded, stored under the azienda's own key, read back for the
Typst job and for the panel, removable; and the SVG hygiene the API's serving needs.
"""

from uuid import UUID

import pytest
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.emitter.assets import MAX_IMAGE_BYTES, AziendaAssets, sniff_image
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


def test_the_bytes_decide_the_kind_never_a_name() -> None:
    assert sniff_image(BLANK_PNG) == "png"
    assert sniff_image(SVG) == "svg"
    assert sniff_image(b'<?xml version="1.0"?>\n<svg xmlns="x"></svg>') == "svg"
    assert sniff_image(JPEG) is None
    assert sniff_image(b"<html><svg></svg></html>") is None
    assert sniff_image(b"") is None


def test_a_logo_is_stored_under_the_azienda_s_key_and_read_back_for_the_job(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    assets = AziendaAssets(db_session, local_storage)
    azienda = _default(db_session)
    read = assets.set_logo(BLANK_PNG, ADMIN)
    assert read.logo_key == f"aziende/{azienda.id}/logo.png"
    assert assets.logo() == (BLANK_PNG, "image/png")
    assert assets.media_for(azienda.id) == {"logo.png": BLANK_PNG}

    # An SVG replaces it under its own name, and the PNG leaves the storage.
    read = assets.set_logo(SVG, ADMIN, azienda.id)
    assert read.logo_key == f"aziende/{azienda.id}/logo.svg"
    assert assets.logo(azienda.id) == (SVG, "image/svg+xml")
    assert assets.media_for(azienda.id) == {"logo.svg": SVG}
    with pytest.raises(NotFound):
        local_storage.get(f"aziende/{azienda.id}/logo.png")

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
    assert read.firma_key == f"aziende/{azienda.id}/firma.png"
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
