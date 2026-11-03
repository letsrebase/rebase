"""`FakeDrive`'s own read surface: listing a folder's children, downloading media,
exporting a Google Doc as text, and refusing what it does not model.

Not `test_drive_reader.py`: that name is reserved for the tests of the production
Drive reader (a parallel task in this same checkout). This file is purely about the
fake itself -- proof that it answers the read-only shapes a reader will need before
anything is built on top of it, driven exactly as production code would: through
`DriveTransport`, never by calling `FakeDrive` directly (except for the one shape that
has to raise before `_call`'s retry loop ever sees a status code).
"""

from urllib.parse import urlencode

import pytest
from fakes.fake_drive import GOOGLE_DOC_MIME, FakeDrive

from pigrocrm.core.drive.transport import DriveTransport
from pigrocrm.core.errors import Conflict

FOLDER_MIME = "application/vnd.google-apps.folder"
FILES_URL = "https://www.googleapis.com/drive/v3/files"


class StubTokens:
    """The minimal `TokenProvider` this file needs: one fixed token, `forget` a no-op.
    None of these tests exercise credential refresh -- that is `test_drive_transport.py`'s
    job -- so nothing more elaborate is needed here.
    """

    def access_token(self) -> str:
        return "at-1"

    def forget(self) -> None:
        pass


def _transport(drive: FakeDrive) -> DriveTransport:
    return DriveTransport(tokens=StubTokens(), http=drive, sleep=lambda _: None)


def _list_url(
    parent_id: str, *, page_size: int | None = None, page_token: str | None = None
) -> str:
    params: dict[str, str] = {
        "q": f"'{parent_id}' in parents and trashed = false",
        "supportsAllDrives": "true",
        "fields": "nextPageToken, files(id, name, mimeType, size, modifiedTime, parents)",
    }
    if page_size is not None:
        params["pageSize"] = str(page_size)
    if page_token is not None:
        params["pageToken"] = page_token
    return f"{FILES_URL}?{urlencode(params)}"


def _metadata_url(file_id: str) -> str:
    params = {
        "fields": "id, name, mimeType, size, modifiedTime, parents",
        "supportsAllDrives": "true",
    }
    return f"{FILES_URL}/{file_id}?{urlencode(params)}"


def _media_url(file_id: str) -> str:
    return f"{FILES_URL}/{file_id}?{urlencode({'alt': 'media', 'supportsAllDrives': 'true'})}"


def _export_url(file_id: str) -> str:
    return f"{FILES_URL}/{file_id}/export?{urlencode({'mimeType': 'text/plain'})}"


def _build_tree() -> tuple[FakeDrive, dict[str, str]]:
    """`Offerte/{a.pdf, sub/b.docx}`, plus a Google Doc that lives outside `Offerte`
    entirely -- so that "children of `Offerte` are exactly `a.pdf` and `sub`" is not
    accidentally true only because nothing else was ever added to the drive."""
    drive = FakeDrive()
    offers = drive.add_folder("Offerte", parent=drive.root_id)
    a_pdf = drive.add_file("a.pdf", parent=offers, mime="application/pdf", content=b"%PDF-a\n")
    sub = drive.add_folder("sub", parent=offers)
    b_docx = drive.add_file(
        "b.docx",
        parent=sub,
        mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        content=b"docx-bytes",
    )
    doc = drive.add_file(
        "Preventivo",
        parent=drive.root_id,
        mime=GOOGLE_DOC_MIME,
        content=b"Testo del preventivo, in chiaro.",
    )
    return drive, {"offerte": offers, "a_pdf": a_pdf, "sub": sub, "b_docx": b_docx, "doc": doc}


def test_lists_the_children_of_a_folder() -> None:
    drive, ids = _build_tree()
    transport = _transport(drive)

    result = transport.json("GET", _list_url(ids["offerte"]), what="list Offerte")

    by_name = {f["name"]: f for f in result["files"]}
    assert set(by_name) == {"a.pdf", "sub"}
    assert by_name["sub"]["mimeType"] == FOLDER_MIME
    assert by_name["a.pdf"]["mimeType"] == "application/pdf"
    assert by_name["a.pdf"]["size"] == str(len(b"%PDF-a\n"))
    assert by_name["a.pdf"]["parents"] == [ids["offerte"]]
    assert "modifiedTime" in by_name["a.pdf"]
    assert "nextPageToken" not in result


def test_lists_the_children_of_a_subfolder() -> None:
    drive, ids = _build_tree()
    transport = _transport(drive)

    result = transport.json("GET", _list_url(ids["sub"]), what="list sub")

    names = {f["name"] for f in result["files"]}
    assert names == {"b.docx"}


def test_pagination_with_page_size_one_yields_two_pages_covering_both_children() -> None:
    drive, ids = _build_tree()
    transport = _transport(drive)

    first = transport.json(
        "GET", _list_url(ids["offerte"], page_size=1), what="list Offerte page 1"
    )
    assert len(first["files"]) == 1
    assert "nextPageToken" in first

    second = transport.json(
        "GET",
        _list_url(ids["offerte"], page_size=1, page_token=first["nextPageToken"]),
        what="list Offerte page 2",
    )
    assert len(second["files"]) == 1
    assert "nextPageToken" not in second

    seen = {first["files"][0]["name"], second["files"][0]["name"]}
    assert seen == {"a.pdf", "sub"}


def test_get_metadata_for_a_single_file() -> None:
    drive, ids = _build_tree()
    transport = _transport(drive)

    result = transport.json("GET", _metadata_url(ids["a_pdf"]), what="get a.pdf")

    assert result["id"] == ids["a_pdf"]
    assert result["name"] == "a.pdf"
    assert result["parents"] == [ids["offerte"]]


def test_alt_media_serves_the_bytes() -> None:
    drive, ids = _build_tree()
    transport = _transport(drive)

    data = transport.bytes("GET", _media_url(ids["a_pdf"]), what="download a.pdf")

    assert data == b"%PDF-a\n"


def test_export_serves_a_google_docs_text() -> None:
    drive, ids = _build_tree()
    transport = _transport(drive)

    text = transport.bytes("GET", _export_url(ids["doc"]), what="export Preventivo")

    assert text == b"Testo del preventivo, in chiaro."


def test_export_of_a_non_google_doc_is_refused_with_400() -> None:
    drive, ids = _build_tree()
    transport = _transport(drive)

    with pytest.raises(Conflict) as excinfo:
        transport.bytes("GET", _export_url(ids["a_pdf"]), what="export a.pdf")

    assert excinfo.value.details["upstream_status"] == 400


def test_a_contains_clause_is_refused_outright() -> None:
    """The fake is as severe about `q` as `fakes/gmail_query.py` is: a `contains`
    clause is a substring match, and no caller this fake models is entitled to one."""
    drive, ids = _build_tree()
    url = f"{FILES_URL}?" + urlencode(
        {
            "q": f"'{ids['offerte']}' in parents and name contains 'a'",
            "supportsAllDrives": "true",
        }
    )

    with pytest.raises(AssertionError):
        drive("GET", url, {}, None)


def test_unknown_id_is_404() -> None:
    drive, _ids = _build_tree()
    transport = _transport(drive)

    with pytest.raises(Conflict) as excinfo:
        transport.json("GET", _metadata_url("does-not-exist"), what="get missing file")

    assert excinfo.value.details["upstream_status"] == 404
