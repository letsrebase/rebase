"""REST surface of the inbox (REB-971): `POST /api/inbox/upload`, the list, one item and its
file. `test_role_matrix.py` owns the role gate of the upload; this file proves what the inbox
page will rely on through the real HTTP surface: the same invoice by three roads ends as one
item and two duplicates, a refused request writes nothing, and an unclassified item is seen by
its uploader and an unscoped administrator alone."""

import io
import zipfile
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.auth.schemas import UserCreate
from pigrocrm.core.auth.service import UserService
from pigrocrm.core.inbox import uploads as uploads_module

FIXTURES = (
    Path(__file__).resolve().parents[3] / "packages" / "core" / "tests" / "fixtures" / "fatturapa"
)
INVOICE = (FIXTURES / "fpr-12-march-consulting.xml").read_bytes()
SIGNED = (FIXTURES / "fpr-12-march-consulting.xml.p7m").read_bytes()
PDF = b"%PDF-1.7\n1 0 obj\n<<>>\nendobj\n"
PASSWORD = "supersegreta1"

UPLOAD = "/api/inbox/upload"


def _zip(entries: dict[str, bytes]) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    return out.getvalue()


def _files(*files: tuple[str, bytes]) -> list[tuple[str, tuple[str, bytes, str]]]:
    return [("file", (name, content, "application/octet-stream")) for name, content in files]


def _login_as(client: TestClient, session: Session, email: str, role: str) -> TestClient:
    UserService(session).create(
        UserCreate(email=email, password=PASSWORD, name="Test", role=role),  # type: ignore[arg-type]
        Actor.system(),
    )
    response = client.post("/api/auth/login", json={"email": email, "password": PASSWORD})
    assert response.status_code == 200, response.text
    return client


def _upload(client: TestClient, *files: tuple[str, bytes]) -> list[dict[str, Any]]:
    response = client.post(UPLOAD, files=_files(*files))
    assert response.status_code == 201, response.text
    items: list[dict[str, Any]] = response.json()["items"]
    return items


def _listed(client: TestClient, **params: Any) -> list[dict[str, Any]]:
    response = client.get("/api/inbox/items", params=params)
    assert response.status_code == 200, response.text
    items: list[dict[str, Any]] = response.json()["items"]
    return items


def test_the_same_invoice_as_xml_as_p7m_and_inside_a_zip_is_one_item_and_two_duplicates(
    logged_in: TestClient,
) -> None:
    xml = _upload(logged_in, ("fattura.xml", INVOICE))[0]
    signed = _upload(logged_in, ("fattura.xml.p7m", SIGNED))[0]
    packed = _upload(logged_in, ("cassetto.zip", _zip({"IT01234567890_1.xml": INVOICE})))

    assert xml["state"] == "new"
    assert (signed["state"], signed["duplicate_of_id"]) == ("duplicate", xml["id"])
    archive, entry = packed
    assert (archive["kind"], archive["state"]) == ("archive", "filed")
    assert (entry["state"], entry["duplicate_of_id"]) == ("duplicate", xml["id"])
    assert entry["parent_id"] == archive["id"]

    invoices = [i for i in _listed(logged_in, limit=50) if i["kind"] != "archive"]
    assert sorted(i["state"] for i in invoices) == ["duplicate", "duplicate", "new"]


def test_a_request_with_several_files_answers_one_item_per_file_in_order(
    logged_in: TestClient,
) -> None:
    items = _upload(logged_in, ("a.pdf", PDF), ("fattura.xml", INVOICE))
    assert [i["original_name"] for i in items] == ["a.pdf", "fattura.xml"]
    assert [i["file_type"] for i in items] == ["pdf", "xml"]
    assert all("storage_key" not in i for i in items)


def test_a_refused_request_writes_nothing(logged_in: TestClient) -> None:
    response = logged_in.post(UPLOAD, files=_files(("buono.xml", INVOICE), ("virus.exe", b"MZ")))
    assert response.status_code == 422, response.text
    assert "tipo di file non accettato" in response.text
    assert _listed(logged_in) == []


@pytest.mark.parametrize(
    ("files", "reason"),
    [
        ([("falso.pdf", INVOICE)], "il contenuto non e' quello"),
        ([("vuoto.pdf", b"")], "vuoto"),
        ([("rotto.zip", b"PK\x03\x04 truncated")], "non si legge"),
    ],
)
def test_a_file_that_is_not_what_it_says_is_a_422_with_the_reason(
    logged_in: TestClient, files: list[tuple[str, bytes]], reason: str
) -> None:
    response = logged_in.post(UPLOAD, files=_files(*files))
    assert response.status_code == 422, response.text
    assert reason in response.text


def test_a_request_with_no_file_is_a_422(logged_in: TestClient) -> None:
    response = logged_in.post(UPLOAD, files=[("altro", ("x.pdf", PDF, "application/pdf"))])
    assert response.status_code == 422, response.text
    assert "nessun file" in response.text


def test_a_file_over_the_size_ceiling_is_a_422(
    logged_in: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(uploads_module, "SIZE_MAX", len(PDF) - 1)
    response = logged_in.post(UPLOAD, files=_files(("estratto.pdf", PDF)))
    assert response.status_code == 422, response.text
    assert "il file supera" in response.text


def test_the_file_of_an_item_downloads_as_it_was_uploaded(logged_in: TestClient) -> None:
    item = _upload(logged_in, ("estratto.pdf", PDF))[0]
    response = logged_in.get(f"/api/inbox/items/{item['id']}/file")
    assert response.status_code == 200
    assert response.content == PDF
    assert response.headers["content-type"] == "application/pdf"
    assert "estratto.pdf" in response.headers["content-disposition"]
    assert response.headers["x-content-type-options"] == "nosniff"


def test_a_duplicate_with_the_same_bytes_has_no_file_of_its_own(logged_in: TestClient) -> None:
    _upload(logged_in, ("a.pdf", PDF))
    duplicate = _upload(logged_in, ("b.pdf", PDF))[0]
    assert (duplicate["state"], duplicate["has_file"]) == ("duplicate", False)
    assert logged_in.get(f"/api/inbox/items/{duplicate['id']}/file").status_code == 404


def test_the_list_filters_by_state_and_pages(logged_in: TestClient) -> None:
    _upload(logged_in, ("a.pdf", PDF), ("IT0_MT_001.xml", b"<a/>"), ("fattura.xml", INVOICE))
    assert [i["original_name"] for i in _listed(logged_in, state="discarded")] == ["IT0_MT_001.xml"]
    first = logged_in.get("/api/inbox/items", params={"limit": 2}).json()
    assert len(first["items"]) == 2 and first["next_cursor"] is not None
    second = logged_in.get(
        "/api/inbox/items", params={"limit": 2, "cursor": first["next_cursor"]}
    ).json()
    assert [i["original_name"] for i in second["items"]] == ["a.pdf"]
    assert second["next_cursor"] is None
    assert logged_in.get("/api/inbox/items", params={"state": "nonsense"}).status_code == 422


def test_an_unknown_item_is_a_404(logged_in: TestClient) -> None:
    missing = "0199aaaa-0000-7000-8000-000000000000"
    assert logged_in.get(f"/api/inbox/items/{missing}").status_code == 404
    assert logged_in.get(f"/api/inbox/items/{missing}/file").status_code == 404


def test_a_readonly_user_cannot_upload_and_a_collaborator_can(
    client: TestClient, api_session: Session
) -> None:
    _login_as(client, api_session, "readonly@pigro.it", "readonly")
    refused = client.post(UPLOAD, files=_files(("a.pdf", PDF)))
    assert refused.status_code == 403
    assert refused.json()["code"] == "permission_denied"
    assert _listed(client) == []

    _login_as(client, api_session, "collaboratore@pigro.it", "collaborator")
    assert _upload(client, ("a.pdf", PDF))[0]["state"] == "new"


def test_an_unclassified_item_is_seen_by_its_uploader_and_an_unscoped_admin_alone(
    client: TestClient, api_session: Session, admin_user: Any
) -> None:
    _login_as(client, api_session, "uno@pigro.it", "collaborator")
    mine = _upload(client, ("mio.pdf", PDF))[0]
    assert [i["id"] for i in _listed(client)] == [mine["id"]]

    _login_as(client, api_session, "due@pigro.it", "collaborator")
    assert _listed(client) == []
    assert client.get(f"/api/inbox/items/{mine['id']}").status_code == 404
    assert client.get(f"/api/inbox/items/{mine['id']}/file").status_code == 404

    _login_as(client, api_session, "lettore@pigro.it", "readonly")
    assert _listed(client) == []

    response = client.post(
        "/api/auth/login", json={"email": admin_user.email, "password": PASSWORD}
    )
    assert response.status_code == 200, response.text
    assert [i["id"] for i in _listed(client)] == [mine["id"]]
    assert client.get(f"/api/inbox/items/{mine['id']}/file").content == PDF


def test_the_inbox_routes_are_in_the_openapi_document(logged_in: TestClient) -> None:
    paths = logged_in.get("/openapi.json").json()["paths"]
    body = paths[UPLOAD]["post"]["requestBody"]["content"]["multipart/form-data"]["schema"]
    assert body["properties"]["file"]["items"] == {"type": "string", "format": "binary"}
    assert {
        "/api/inbox/items",
        "/api/inbox/items/{item_id}",
        "/api/inbox/items/{item_id}/file",
    } <= set(paths)
