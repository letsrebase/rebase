"""`/api/aziende/{id}/logo` and `/firma` (REB-628, spec 2026-10-03 §3): an admin uploads,
every role reads the bytes back with their content type, the server sniffs the kind and
refuses what the renderer and a browser must not get.
"""

from uuid import uuid4

from aziende_helpers import azienda_url
from fastapi.testclient import TestClient

from pigrocrm.core.render.pdf import BLANK_PNG

SVG = (
    b'<svg xmlns="http://www.w3.org/2000/svg" width="8" height="8">'
    b'<rect width="8" height="8"/></svg>'
)


def _put(client: TestClient, url: str, data: bytes, name: str = "x.bin") -> object:
    return client.put(url, files={"file": (name, data, "application/octet-stream")})


def test_an_admin_uploads_reads_and_removes_the_logo_and_the_signature(
    logged_in: TestClient,
) -> None:
    logo = azienda_url(logged_in, "/logo")
    firma = azienda_url(logged_in, "/firma")
    assert logged_in.get(logo).status_code == 404
    assert logged_in.get(firma).status_code == 404

    put = _put(logged_in, logo, SVG, "mark.svg")
    assert put.status_code == 200, put.text
    assert put.json()["logo_key"].endswith(".svg")
    served = logged_in.get(logo)
    assert served.status_code == 200
    assert served.headers["content-type"].startswith("image/svg+xml")
    assert served.content == SVG
    assert served.headers["x-content-type-options"] == "nosniff"
    assert "default-src 'none'" in served.headers["content-security-policy"]
    assert served.headers["content-disposition"] == 'inline; filename="logo.svg"'

    # A PNG replaces the SVG, under its own name.
    assert _put(logged_in, logo, BLANK_PNG, "mark.png").json()["logo_key"].endswith(".png")
    assert logged_in.get(logo).headers["content-type"].startswith("image/png")

    put = _put(logged_in, firma, BLANK_PNG)
    assert put.status_code == 200, put.text
    assert put.json()["firma_key"].endswith(".png")
    assert logged_in.get(firma).content == BLANK_PNG

    row = logged_in.get(azienda_url(logged_in)).json()
    assert row["logo_key"] and row["firma_key"]

    assert logged_in.delete(logo).json()["logo_key"] is None
    assert logged_in.delete(firma).json()["firma_key"] is None
    assert logged_in.get(logo).status_code == 404
    assert logged_in.get(firma).status_code == 404
    # Removing again is nothing.
    assert logged_in.delete(logo).status_code == 200


def test_the_bytes_decide_and_what_is_refused_is_a_422_naming_the_file(
    logged_in: TestClient,
) -> None:
    logo = azienda_url(logged_in, "/logo")
    firma = azienda_url(logged_in, "/firma")
    for url, data, name in (
        (logo, b"\xff\xd8\xff\xe0" + b"\x00" * 16, "foto.jpg"),
        (logo, b"\xff\xd8\xff\xe0" + b"\x00" * 16, "foto.png"),
        (logo, b"\x89PNG\r\n\x1a\n" + b"\x00" * (1024 * 1024), "grande.png"),
        (logo, b"<html><svg></svg></html>", "pagina.svg"),
        (logo, b'<svg xmlns="x"><script>1</script></svg>', "cattivo.svg"),
        (firma, SVG, "firma.svg"),
    ):
        response = _put(logged_in, url, data, name)
        assert response.status_code == 422, (name, response.text)
        assert response.json()["field"] == "file"
    assert logged_in.get(logo).status_code == 404


def test_a_collaboratore_is_refused_on_the_writes_and_served_on_the_reads(
    collaborator_client: TestClient,
) -> None:
    """The role fixtures log in on the one client, so the admin's upload cannot precede
    this: the read is proved as a 404 (served, nothing there) rather than a 403, and the
    admin's own round trip is the first test above."""
    logo = azienda_url(collaborator_client, "/logo")
    assert _put(collaborator_client, logo, BLANK_PNG).status_code == 403
    assert collaborator_client.delete(logo).status_code == 403
    assert collaborator_client.get(logo).status_code == 404


def test_an_unknown_azienda_is_404_on_every_image_route(logged_in: TestClient) -> None:
    url = f"/api/aziende/{uuid4()}/logo"
    assert logged_in.get(url).status_code == 404
    assert _put(logged_in, url, BLANK_PNG).status_code == 404
    assert logged_in.delete(url).status_code == 404


def test_the_openapi_document_declares_the_six_routes(logged_in: TestClient) -> None:
    paths = logged_in.get("/openapi.json").json()["paths"]
    for slot in ("logo", "firma"):
        operations = paths[f"/api/aziende/{{azienda_id}}/{slot}"]
        assert set(operations) == {"get", "put", "delete"}
        assert "multipart/form-data" in operations["put"]["requestBody"]["content"]
