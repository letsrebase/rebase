"""`DriveReader`: the children of the configured roots, the text of one file, nothing
else.

The failure spec 9C names is reading somebody's Drive -- the photos of their children,
their rent contract, the folder of another job. Three guards stand between this code
and that, and this file is the third: `drive/query.py` refuses to *build* a widened
listing, `test_drive_query.py` refuses to let a Drive URL be written anywhere else,
and the tests here inspect what the fake transport was actually *asked*. The first two
would both accept `children_query("<a folder in somebody's Drive>")`; only this one
can tell that the folder named in the request belongs to the hierarchy the owner
configured -- which is why `test_every_files_list_of_this_module_stayed_inside_the_roots`
at the bottom re-reads every request every scenario above made.

The other property under test is the one that has no message: a folder or a file
outside the roots is `NotFound`, and it is `NotFound` in the same words whether it does
not exist, exists in a part of the Drive the owner did not configure, or exists in
somebody else's Drive entirely. Telling those apart would answer the question "is
there a file with this id?" for anyone who can call the tool.
"""

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest
from fakes.fake_drive import GOOGLE_DOC_MIME, FakeDrive, RecordedRequest
from fakes.gmail_fixtures import TOKEN_KEY, gmail_settings
from sqlalchemy import select
from sqlalchemy.orm import Session
from test_text import minimal_docx, minimal_pdf

from pigrocrm.core.activities.models import Activity
from pigrocrm.core.actor import Actor
from pigrocrm.core.auth.models import User
from pigrocrm.core.config import TEXT_MAX_BYTES_DEFAULT, Settings
from pigrocrm.core.crypto import seal
from pigrocrm.core.drive import reader as reader_module
from pigrocrm.core.drive.errors import DriveCredentialRevoked
from pigrocrm.core.drive.models import GoogleDriveAccount
from pigrocrm.core.drive.reader import (
    ALREADY_AUTHORIZED,
    MAX_ROOT_WALK_LEVELS,
    MAX_ROOT_WALK_REQUESTS,
    DriveReader,
    drive_reader_for,
)
from pigrocrm.core.drive.schemas import DRIVE_SCOPE_FILE, DRIVE_SCOPE_READONLY
from pigrocrm.core.drive.transport import DriveTransport, UserTokens
from pigrocrm.core.errors import Conflict, NotFound, ValidationFailed
from pigrocrm.core.text import DOCX_MIME, PDF_MIME, PROVENANCE, TEXT_TRUNCATION_MARKER

FOLDER_MIME = "application/vnd.google-apps.folder"

# Ids of the shape Drive really uses, because `folder_id` and `file_id` arrive from
# outside and `query.py` holds them to the strict pattern (ten characters at least):
# `FakeDrive`'s own generated ids ("id2") are legitimate for an id *Drive* returned,
# but not for one a caller supplies.
ROOT_FOLDER = "1RadiceClientiAAAA"
SUB_FOLDER = "1SottocartellaACME"
PDF_FILE = "1PreventivoPdfXXXX"
DOC_FILE = "1NoteGoogleDocYYYY"
TXT_FILE = "1AppuntiTestoZZZZZ"
SCAN_FILE = "1ScansioneSenzaTxt"
OUTSIDE_FOLDER = "1CartellaPersonale"
OUTSIDE_FILE = "1FotoDeiFigliJpeg1"

PDF_WITH_TEXT = minimal_pdf(["Preventivo per ACME", "Totale 1.000 euro"])
SCAN_WITHOUT_TEXT = minimal_pdf([])
DOC_TEXT = "Chiamare il commercialista entro venerdì."
TXT_TEXT = "Appunti: rivedere il preventivo, poi mandarlo."

# Every `FakeDrive` any test in this module built. Read only by the guard test at the
# bottom, which is the reason it exists: the promise "no listing ever leaves the
# configured roots" is a property of the whole module's traffic, not of one scenario.
_DRIVES: list[FakeDrive] = []


class StubTokens:
    """One fixed token, `forget` a no-op: none of these tests exercises the credential,
    which is `test_drive_transport.py`'s job."""

    def access_token(self) -> str:
        return "at-1"

    def forget(self) -> None:
        pass


class RevokedTokens:
    """The provider of an account whose grant Google has dropped. What `UserTokens`
    raises when a refresh comes back `invalid_grant`."""

    def __init__(self, account_id: Any = None, email_address: str = "io@example.it") -> None:
        self._account_id = account_id
        self._email = email_address

    def access_token(self) -> str:
        raise DriveCredentialRevoked(self._account_id, self._email)

    def forget(self) -> None:
        pass


def _tree() -> FakeDrive:
    """One configured root with a subfolder in it, and a second top-level folder that
    is *not* configured -- so "inside the roots" is not accidentally true of everything
    on the drive."""
    drive = FakeDrive()
    _DRIVES.append(drive)
    drive.add_folder("Clienti", parent=drive.root_id, file_id=ROOT_FOLDER)
    drive.add_folder("ACME", parent=ROOT_FOLDER, file_id=SUB_FOLDER)
    drive.add_file(
        "Note",
        parent=ROOT_FOLDER,
        mime=GOOGLE_DOC_MIME,
        content=DOC_TEXT.encode(),
        file_id=DOC_FILE,
    )
    drive.add_file(
        "Preventivo.pdf", parent=SUB_FOLDER, mime=PDF_MIME, content=PDF_WITH_TEXT, file_id=PDF_FILE
    )
    drive.add_file(
        "Appunti.txt",
        parent=SUB_FOLDER,
        mime="text/plain",
        content=TXT_TEXT.encode(),
        file_id=TXT_FILE,
    )
    drive.add_file(
        "Scansione.pdf",
        parent=SUB_FOLDER,
        mime=PDF_MIME,
        content=SCAN_WITHOUT_TEXT,
        file_id=SCAN_FILE,
    )
    drive.add_folder("Personale", parent=drive.root_id, file_id=OUTSIDE_FOLDER)
    drive.add_file(
        "figli.jpg",
        parent=OUTSIDE_FOLDER,
        mime="image/jpeg",
        content=b"\xff\xd8\xff\xe0",
        file_id=OUTSIDE_FILE,
    )
    return drive


def _reader(
    drive: FakeDrive,
    *,
    roots: tuple[str, ...] = (ROOT_FOLDER,),
    tokens: Any = None,
    http: Any = None,
    **kwargs: Any,
) -> DriveReader:
    transport = DriveTransport(
        tokens=tokens or StubTokens(), http=http or drive, sleep=lambda _: None
    )
    return DriveReader(transport, roots=roots, **kwargs)


def _listings(drive: FakeDrive) -> list[RecordedRequest]:
    return [request for request in drive.requests if request.is_files_list]


def _paths(drive: FakeDrive) -> list[str]:
    return [request.path for request in drive.requests]


# --- listing -------------------------------------------------------------------------


def test_lists_the_children_of_a_configured_root() -> None:
    drive = _tree()

    listing = _reader(drive).list_children(ROOT_FOLDER)

    by_name = {entry.name: entry for entry in listing.items}
    assert set(by_name) == {"ACME", "Note"}
    assert by_name["ACME"].folder is True
    assert by_name["ACME"].mime == FOLDER_MIME
    # A folder and a Google Doc have no bytes on Drive, so they have no size -- and
    # `None` is not the same answer as `0`, which would say "an empty file".
    assert by_name["ACME"].size is None
    assert by_name["Note"].size is None
    assert by_name["Note"].folder is False
    assert by_name["Note"].id == DOC_FILE
    assert by_name["Note"].modified_at == datetime(2026, 1, 1, tzinfo=UTC)
    assert listing.next_page_token is None


def test_lists_the_children_of_a_folder_inside_a_root() -> None:
    """The subfolder is not itself configured: what makes it listable is that it is
    *under* a configured root."""
    drive = _tree()

    listing = _reader(drive).list_children(SUB_FOLDER)

    by_name = {entry.name: entry for entry in listing.items}
    assert set(by_name) == {"Preventivo.pdf", "Appunti.txt", "Scansione.pdf"}
    assert by_name["Preventivo.pdf"].size == len(PDF_WITH_TEXT)
    assert by_name["Preventivo.pdf"].mime == PDF_MIME
    assert by_name["Preventivo.pdf"].folder is False


def test_a_folder_outside_the_roots_is_not_found_and_no_listing_is_ever_issued() -> None:
    """The heart of the slice. `files.list` is not attempted and then filtered: it is
    never sent at all, so there is no window in which Google was asked about a folder
    the owner did not configure."""
    drive = _tree()

    with pytest.raises(NotFound) as excinfo:
        _reader(drive).list_children(OUTSIDE_FOLDER)

    assert excinfo.value.details["entity"] == "drive_file"
    assert excinfo.value.details["identifier"] == OUTSIDE_FOLDER
    assert _listings(drive) == []


def test_a_folder_that_does_not_exist_is_refused_in_exactly_the_same_words() -> None:
    """Deliberately indistinguishable from the test above: an answer that told the two
    apart would answer "does a file with this id exist?" for anybody who can call the
    tool."""
    drive = _tree()
    missing = "1QuestoNonEsiste00"

    with pytest.raises(NotFound) as absent:
        _reader(drive).list_children(missing)
    with pytest.raises(NotFound) as outside:
        _reader(drive).list_children(OUTSIDE_FOLDER)

    assert absent.value.details["entity"] == outside.value.details["entity"]
    assert absent.value.args[0].replace(missing, "X") == outside.value.args[0].replace(
        OUTSIDE_FOLDER, "X"
    )
    assert _listings(drive) == []


def test_a_page_token_is_forwarded_and_the_next_one_comes_back() -> None:
    """Pagination is Drive's, not the reader's: the reader never chooses a page size --
    `files_list_url` cannot even express one -- so this test makes Drive answer in
    pages of one, which is exactly what a folder of four thousand files does on its
    own.
    """
    drive = _tree()

    def one_at_a_time(
        method: str,
        url: str,
        headers: dict[str, str],
        body: bytes | None,
        *,
        max_bytes: int | None = None,
    ) -> Any:
        if "q=" in url:
            url = f"{url}&pageSize=1"
        return drive(method, url, headers, body, max_bytes=max_bytes)

    reader = _reader(drive, http=one_at_a_time)
    first = reader.list_children(ROOT_FOLDER)
    assert len(first.items) == 1
    assert first.next_page_token is not None

    second = reader.list_children(ROOT_FOLDER, page_token=first.next_page_token)
    assert len(second.items) == 1
    assert second.next_page_token is None
    assert {first.items[0].name, second.items[0].name} == {"ACME", "Note"}
    assert [request.params.get("pageToken") for request in _listings(drive)] == [
        None,
        [first.next_page_token],
    ]


# --- the walk upward -----------------------------------------------------------------


def test_a_file_in_a_subfolder_of_a_root_is_within_the_roots() -> None:
    drive = _tree()

    assert _reader(drive).is_within_roots(PDF_FILE) is True


def test_a_configured_root_is_itself_within_the_roots_without_asking_drive() -> None:
    """No request at all: the answer is in the configuration, and a folder the owner
    named does not need Google's opinion."""
    drive = _tree()

    assert _reader(drive).is_within_roots(ROOT_FOLDER) is True
    assert drive.requests == []


def test_a_file_in_another_top_level_folder_is_outside_the_roots() -> None:
    drive = _tree()

    assert _reader(drive).is_within_roots(OUTSIDE_FILE) is False


def test_a_file_at_the_drive_root_that_is_not_a_root_is_outside() -> None:
    """The walk ends at the Drive's own root, which has no parents. Reaching it is the
    answer "no configured root is above this file", not a reason to keep going."""
    drive = _tree()
    drive.add_file(
        "sparso.txt", parent=drive.root_id, mime="text/plain", file_id="1FileNellaRadice"
    )

    assert _reader(drive).is_within_roots("1FileNellaRadice") is False


def test_the_walk_upward_stops_after_twenty_levels() -> None:
    """A folder nested deeper than the bound is reported outside rather than walked
    forever: Drive lets a file have several parents, so "walk up" is a graph, and a
    cycle in it is a Drive bug this code must survive rather than trust away."""
    drive = _tree()
    parent = ROOT_FOLDER
    for level in range(MAX_ROOT_WALK_LEVELS + 5):
        parent = drive.add_folder(f"L{level}", parent=parent, file_id=f"1Livello{level:010d}")
    deep = drive.add_file(
        "in fondo.txt", parent=parent, mime="text/plain", file_id="1MoltoProfondo"
    )

    reader = _reader(drive)
    assert reader.is_within_roots(deep) is False
    assert len(drive.requests) <= MAX_ROOT_WALK_LEVELS + 1

    shallow = drive.add_file(
        "vicino.txt", parent="1Livello0000000002", mime="text/plain", file_id="1PocoProfondo1234"
    )
    assert _reader(drive).is_within_roots(shallow) is True


def test_the_walk_upward_stops_after_a_bounded_number_of_requests() -> None:
    """The level bound is not the only way the walk can grow: Drive lets a file have
    many parents, so one level can be three hundred folders wide. A file whose
    ancestry fans out past the request bound is reported outside the roots rather than
    spending three hundred calls on Google's quota to find out."""
    drive = _tree()
    fan = []
    for index in range(MAX_ROOT_WALK_REQUESTS + 50):
        fan.append(
            drive.add_folder(f"F{index}", parent=OUTSIDE_FOLDER, file_id=f"1Fan{index:013d}")
        )
    wide = drive.add_file(
        "molti genitori.txt", parent=fan[0], mime="text/plain", file_id="1MoltiGenitori000"
    )
    drive.files[wide].parents = fan

    assert _reader(drive).is_within_roots(wide) is False
    assert len(drive.requests) <= MAX_ROOT_WALK_REQUESTS + 1


def test_the_walk_asks_drive_about_each_folder_at_most_once_per_call() -> None:
    """A file with two parents that share an ancestor: the cache is what keeps the walk
    linear in the number of folders instead of exponential in the number of paths."""
    drive = _tree()
    left = drive.add_folder("sinistra", parent=OUTSIDE_FOLDER, file_id="1RamoSinistro0000")
    right = drive.add_folder("destra", parent=OUTSIDE_FOLDER, file_id="1RamoDestro000000")
    shared = drive.add_file(
        "condiviso.txt", parent=left, mime="text/plain", file_id="1FileCondiviso000"
    )
    drive.files[shared].parents = [left, right]

    assert _reader(drive).is_within_roots(shared) is False

    asked = [path.rsplit("/", 1)[-1] for path in _paths(drive)]
    assert sorted(asked) == sorted(set(asked)), asked
    assert OUTSIDE_FOLDER in asked


def _refusing(drive: FakeDrive, file_id: str, status: int) -> Any:
    """`drive`'s own transport, except that `files.get` on one id answers `status`.

    `FakeDrive.fail_with` is a FIFO queue over *every* call, so it cannot express "the
    second request fails": the case below needs the file itself to be readable and one of
    its ancestors not to be, which is the only shape in which the branch under test can
    be reached at all.
    """

    def call(
        method: str,
        url: str,
        headers: dict[str, str],
        body: bytes | None,
        *,
        max_bytes: int | None = None,
    ) -> tuple[int, bytes, dict[str, str]]:
        if f"/files/{file_id}" in url:
            return status, b'{"error": {"message": "insufficientFilePermissions"}}', {}
        return drive(method, url, headers, body, max_bytes=max_bytes)

    return call


def test_a_403_on_an_ancestor_is_reported_and_not_read_as_outside_the_roots() -> None:
    """The trade `_parents_of` documents, pinned so that it stays a decision.

    Only a **404** is read as "no parents": an id that does not exist has no configured
    root above it, and answering `NotFound` there is what keeps a file outside the roots
    indistinguishable from one that was never there. A 403 is not that. It also means no
    root can be *proven* above the file, so swallowing it would be easy and would look
    right -- and it would report an ancestor somebody un-shared, or a permission change
    mid-walk, as «non trovato»: the owner would be told their own file is outside the
    folders they configured, and would go looking for it in the wrong place.

    So it propagates as itself, with the status intact for whoever has to diagnose it.
    The cost of the trade, equally deliberate: a 403 on a parent of a file the owner
    *can* read surfaces as a Drive error rather than as `NotFound`. That has not been
    seen, and inventing a translation for it would be inventing which of the two it was.
    """
    drive = _tree()

    reader = _reader(drive, http=_refusing(drive, SUB_FOLDER, 403))
    with pytest.raises(Conflict) as excinfo:
        reader.is_within_roots(PDF_FILE)

    assert excinfo.value.details["upstream_status"] == 403
    # Re-stamped by `_guarded`, like every other transport failure out of this module.
    assert excinfo.value.details["entity"] == "drive_file"


def test_a_404_on_an_ancestor_is_the_end_of_the_walk_and_not_an_error() -> None:
    """The branch the one above is a trade against, so the pair reads as one decision:
    a parent Drive no longer has is «this file has no root above it», answered `False`
    rather than raised."""
    drive = _tree()

    reader = _reader(drive, http=_refusing(drive, SUB_FOLDER, 404))

    assert reader.is_within_roots(PDF_FILE) is False


# --- bytes ---------------------------------------------------------------------------


def test_read_bytes_returns_the_bytes_and_the_mime() -> None:
    drive = _tree()

    content, mime = _reader(drive).read_bytes(PDF_FILE)

    assert content == PDF_WITH_TEXT
    assert mime == PDF_MIME


def test_read_bytes_of_a_google_doc_exports_it_as_plain_text() -> None:
    """A Google Doc has no bytes of its own -- `alt=media` on one answers 403 -- so the
    only thing that can be read is Drive's own `text/plain` export, and the mime that
    comes back says so rather than repeating the native type."""
    drive = _tree()

    content, mime = _reader(drive).read_bytes(DOC_FILE)

    assert content == DOC_TEXT.encode()
    assert mime == "text/plain"
    assert any(path.endswith("/export") for path in _paths(drive))
    assert not any(request.params.get("alt") == ["media"] for request in drive.requests)


def test_read_bytes_of_a_folder_is_refused() -> None:
    drive = _tree()

    with pytest.raises(Conflict) as excinfo:
        _reader(drive).read_bytes(SUB_FOLDER)

    assert excinfo.value.details["entity"] == "drive_file"
    assert not any(request.params.get("alt") == ["media"] for request in drive.requests)


def test_read_bytes_outside_the_roots_downloads_nothing() -> None:
    drive = _tree()

    with pytest.raises(NotFound):
        _reader(drive).read_bytes(OUTSIDE_FILE)

    assert not any(request.params.get("alt") == ["media"] for request in drive.requests)
    assert _listings(drive) == []


def test_read_bytes_refuses_a_file_drive_says_is_too_big_before_downloading_it() -> None:
    """The size travels in the metadata, so a 900 MB video is refused without a byte of
    it crossing the network."""
    drive = _tree()
    big = drive.add_file(
        "enorme.txt",
        parent=SUB_FOLDER,
        mime="text/plain",
        content=b"x" * 500,
        file_id="1FileEnorme00000",
    )

    with pytest.raises(Conflict) as excinfo:
        _reader(drive).read_bytes(big, max_bytes=100)

    assert excinfo.value.details["entity"] == "drive_file"
    assert not any(request.params.get("alt") == ["media"] for request in drive.requests)


def test_read_bytes_max_bytes_can_tighten_the_download_ceiling_but_never_widen_it() -> None:
    """`max_bytes` is a caller's own limit, not a permission: a tool that asked for
    500 MB would otherwise raise the ceiling this reader was built with, and the
    ceiling exists precisely because the caller does not decide it."""
    drive = _tree()
    reader = _reader(drive, download_max_bytes=100)
    big = drive.add_file(
        "grosso.txt",
        parent=SUB_FOLDER,
        mime="text/plain",
        content=b"x" * 500,
        file_id="1FileGrosso000000",
    )

    with pytest.raises(Conflict):
        reader.read_bytes(big, max_bytes=10_000_000)
    assert not any(request.params.get("alt") == ["media"] for request in drive.requests)
    # Tightening still works, on a file the ceiling would have allowed.
    with pytest.raises(Conflict):
        reader.read_bytes(TXT_FILE, max_bytes=4)


def test_read_bytes_refuses_bytes_that_arrive_over_the_ceiling_even_undeclared() -> None:
    """An export has no declared size -- Drive cannot know how long the text will be
    before producing it -- so the ceiling is checked again on what actually arrived."""
    drive = _tree()

    with pytest.raises(Conflict):
        _reader(drive).read_bytes(DOC_FILE, max_bytes=4)


def test_an_undeclared_oversized_answer_is_not_read_past_one_byte_over_the_ceiling() -> None:
    """An export declares no size, so the ceiling is enforced on what arrives -- and what
    arrives is cut by the transport at `max_bytes + 1`, so a Google Doc of any length is
    never buffered whole. Asserted on the bytes the transport handed back."""
    drive = _tree()
    drive.files[DOC_FILE].data = b"x" * 10_000
    reads: list[int] = []
    real = DriveTransport.bytes

    def spy(self: DriveTransport, *args: Any, **kwargs: Any) -> bytes:
        payload = real(self, *args, **kwargs)
        reads.append(len(payload))
        return payload

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(DriveTransport, "bytes", spy)
        with pytest.raises(Conflict):
            _reader(drive).read_bytes(DOC_FILE, max_bytes=100)

    assert reads == [101]


# --- text ----------------------------------------------------------------------------


def test_read_text_of_a_pdf_with_a_text_layer() -> None:
    drive = _tree()

    result = _reader(drive).read_text(PDF_FILE)

    assert "Preventivo per ACME" in result.text
    assert result.mime == PDF_MIME
    assert result.truncated is False
    assert result.provenance == PROVENANCE


def test_read_text_of_a_scan_is_empty_and_not_truncated() -> None:
    drive = _tree()

    result = _reader(drive).read_text(SCAN_FILE)

    assert result.text == ""
    assert result.truncated is False


def test_read_text_of_a_google_doc_reports_the_exported_type() -> None:
    drive = _tree()

    result = _reader(drive).read_text(DOC_FILE)

    assert result.text == DOC_TEXT
    assert result.mime == "text/plain"


def test_read_text_of_a_docx() -> None:
    drive = _tree()
    docx = drive.add_file(
        "Contratto.docx",
        parent=SUB_FOLDER,
        mime=DOCX_MIME,
        content=minimal_docx([["Contratto ", "quadro"], ["Durata annuale"]]),
        file_id="1ContrattoDocx000",
    )

    assert _reader(drive).read_text(docx).text == "Contratto quadro\nDurata annuale"


def test_read_text_truncates_at_the_configured_ceiling_with_a_marker() -> None:
    drive = _tree()

    result = _reader(drive, text_max_bytes=10).read_text(TXT_FILE)

    assert result.truncated is True
    assert result.text == TXT_TEXT[:10] + TEXT_TRUNCATION_MARKER


def test_read_text_max_bytes_overrides_the_configured_ceiling() -> None:
    drive = _tree()
    reader = _reader(drive, text_max_bytes=10)

    assert reader.read_text(TXT_FILE, max_bytes=1_000).truncated is False


def test_the_default_ceiling_is_the_settings_default() -> None:
    drive = _tree()

    assert _reader(drive).read_text(TXT_FILE).text == TXT_TEXT
    assert TEXT_MAX_BYTES_DEFAULT == 262_144
    assert Settings(_env_file=None).drive_text_max_bytes == TEXT_MAX_BYTES_DEFAULT  # type: ignore[call-arg]


# --- the id itself -------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "corto",
        "1AbCdEfGhI' in parents or '1XyZ",
        "../../files/1AbCdEfGhIjKl",
        "1AbCdEfGhIjKl\n",
    ],
)
def test_an_id_that_is_not_a_drive_id_is_refused_before_any_request(bad: str) -> None:
    """Every caller-supplied id is checked against `query.py`'s strict pattern *first*.
    Not because the builders would let it through -- they would not -- but because the
    refusal has to happen before the metadata call, or a malformed id would already
    have been sent to Google once."""
    drive = _tree()
    reader = _reader(drive)

    for call in (reader.list_children, reader.is_within_roots, reader.read_bytes, reader.read_text):
        with pytest.raises(ValidationFailed):
            call(bad)

    assert drive.requests == []


def test_a_bad_file_id_is_refused_as_a_file_id_and_a_folder_id_as_a_folder_id() -> None:
    """Which parameter was wrong is the whole content of the message.
    "Not a Drive folder id" in answer to a *file* id sends the reader to check the
    configured roots, which are fine, instead of the id they typed."""
    reader = _reader(_tree())

    for call in (reader.read_bytes, reader.read_text):
        with pytest.raises(ValidationFailed) as caught:
            call("corto")
        assert caught.value.details["field"] == "file_id"

    for call in (reader.list_children, reader.is_within_roots):
        with pytest.raises(ValidationFailed) as caught:
            call("corto")
        assert caught.value.details["field"] == "folder_id"


# --- what a failure is about ------------------------------------------------------------


def test_a_google_failure_mid_read_is_about_the_drive_file_and_not_a_crm_document() -> None:
    """`DriveTransport` reports its own failures under `document_blob`, which is the
    truth for the caller it was written for -- `GDriveStorage` reaches Drive for the
    bytes of a CRM document -- and false for every caller of this class.

    A Google 500 while listing a folder the owner named used to arrive as
    «document_blob: elenco fallito (500)». There is no CRM document anywhere in that
    operation: `OriginalPdfResolver.resolve`'s docstring documents the
    opposite ("each already arrive as a `Conflict` under the entity `drive_file`"), and
    an adapter routing on the entity -- which is what `details["entity"]` is *for* --
    would send somebody to the documents screen to look for a row that was never
    involved. So `_guarded` re-stamps it.

    `status` and `what` are asserted alongside because they are load-bearing details
    that the re-stamp must carry through, not decoration: `storage/gdrive.py` matches on
    `what` and `_metadata` matches on `status`.
    """
    drive = _tree()
    drive.fail_with = [500, 500, 500, 500]

    with pytest.raises(Conflict) as excinfo:
        _reader(drive).list_children(ROOT_FOLDER)

    assert excinfo.value.details["entity"] == "drive_file"
    assert "document_blob" not in excinfo.value.message
    assert excinfo.value.details["upstream_status"] == 500
    assert excinfo.value.details["what"]


def test_every_public_read_reports_a_google_failure_under_the_same_entity() -> None:
    """The re-stamp is on `_guarded`, so it holds for each operation rather than for the
    one a test happened to pick -- which is the whole reason it is there and not at a
    call site."""
    for operation in (
        lambda reader: reader.list_children(ROOT_FOLDER),
        lambda reader: reader.describe(PDF_FILE),
        lambda reader: reader.read_bytes(PDF_FILE),
        lambda reader: reader.read_text(PDF_FILE),
        lambda reader: reader.describe_roots(),
    ):
        drive = _tree()
        drive.fail_with = [500] * 8

        with pytest.raises(Conflict) as excinfo:
            operation(_reader(drive))

        assert excinfo.value.details["entity"] == "drive_file", operation


def test_a_refusal_about_the_credential_keeps_its_own_entity() -> None:
    """The re-stamp names one entity and only one. `DriveCredentialRevoked` and the
    outage `Conflict` are `google_drive_account` and `google_drive` respectively, and
    both are already the truthful subject of their own sentence: flattening them into
    `drive_file` would claim a file was the problem when the account was, and send
    somebody to check an id instead of to Impostazioni → Drive.
    """
    drive = _tree()

    with pytest.raises(Conflict) as excinfo:
        _reader(drive, tokens=RevokedTokens()).list_children(ROOT_FOLDER)

    assert isinstance(excinfo.value, DriveCredentialRevoked)
    assert excinfo.value.details["entity"] == "google_drive_account"


# --- the credential -------------------------------------------------------------------


def test_a_revoked_credential_calls_back_once_and_re_raises() -> None:
    """`DriveCredentialRevoked` is not swallowed and not re-worded: the callback records
    the fact (that is `drive_reader_for`'s wiring) and the exception continues, so the
    caller stops rather than carrying on against a dead credential."""
    drive = _tree()
    seen: list[int] = []
    reader = _reader(drive, tokens=RevokedTokens(), on_revoked=lambda: seen.append(1))

    with pytest.raises(DriveCredentialRevoked):
        reader.list_children(ROOT_FOLDER)
    assert seen == [1]

    with pytest.raises(DriveCredentialRevoked):
        reader.read_text(PDF_FILE)
    assert seen == [1, 1]


def test_without_a_callback_a_revoked_credential_still_raises() -> None:
    drive = _tree()

    with pytest.raises(DriveCredentialRevoked):
        _reader(drive, tokens=RevokedTokens()).list_children(ROOT_FOLDER)


# --- drive_reader_for ------------------------------------------------------------------


REFRESH_TOKEN = "1//0gDriveRefreshToken"


def _account(
    session: Session,
    *,
    scopes: tuple[str, ...] = (DRIVE_SCOPE_READONLY, DRIVE_SCOPE_FILE),
    status: str = "active",
    roots: tuple[str, ...] = (ROOT_FOLDER,),
) -> GoogleDriveAccount:
    user = User(
        email=f"drive-{uuid4().hex[:8]}@example.it",
        name="Owner",
        password_hash="x",
        role="admin",
        active=True,
    )
    session.add(user)
    session.flush()
    ciphertext, nonce = seal(REFRESH_TOKEN, TOKEN_KEY)
    account = GoogleDriveAccount(
        user_id=user.id,
        google_sub=f"sub-{user.id}",
        email_address="io@example.it",
        refresh_token_ciphertext=ciphertext,
        refresh_token_nonce=nonce,
        scopes_granted=list(scopes),
        status=status,
        root_folder_ids=list(roots),
    )
    session.add(account)
    session.flush()
    return account


def _actor(account: GoogleDriveAccount) -> Actor:
    return Actor(id=account.user_id, type="user", role="admin")


def _inject(monkeypatch: pytest.MonkeyPatch, drive: FakeDrive, providers: list[Any]) -> None:
    """The transport seam, pointed at an in-memory Drive -- the same monkeypatch
    `apps/mcp/tests/test_gmail_discovery_tool.py` uses for `GmailTransport`, and the same
    name `storage/lazy_drive.py`'s own tests replace: `user_transport_for`, the one helper
    both users of a user-credentialled Drive compose through.

    The real helper still runs, so the row's refresh token is really unsealed and the
    provider it built is *captured* on the way past; only the transport handed back is a
    stub over the fake Drive, so the composition can be asserted without Google's token
    endpoint being called at all.
    """
    real = reader_module.user_transport_for

    def capture(account: Any, settings: Any, **_: Any) -> DriveTransport:
        # `_tokens` on purpose: the provider is what this asserts about, and it exists
        # nowhere else -- `user_transport_for` builds it and keeps it.
        providers.append(real(account, settings)._tokens)
        return DriveTransport(tokens=StubTokens(), http=drive, sleep=lambda _: None)

    monkeypatch.setattr(reader_module, "user_transport_for", capture)


def test_drive_reader_for_composes_the_reader_from_the_stored_account(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    account = _account(db_session)
    drive = _tree()
    providers: list[Any] = []
    _inject(monkeypatch, drive, providers)

    reader = drive_reader_for(
        db_session,
        _actor(account),
        gmail_settings(),
        feature="la lettura da Drive",
        action=ALREADY_AUTHORIZED,
    )
    listing = reader.list_children(ROOT_FOLDER)

    assert {entry.name for entry in listing.items} == {"ACME", "Note"}
    # The roots come from the row, so a reader cannot be pointed anywhere else.
    with pytest.raises(NotFound):
        reader.list_children(OUTSIDE_FOLDER)
    # And the credential is this account's, with the refresh token really unsealed.
    assert len(providers) == 1
    provider = providers[0]
    assert isinstance(provider, UserTokens)
    assert provider.account_id == account.id
    assert provider.email_address == "io@example.it"
    assert provider.refresh_token == REFRESH_TOKEN


def test_drive_reader_for_refuses_a_revoked_account_before_composing_anything(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    account = _account(db_session, status="revoked")
    drive = _tree()
    _inject(monkeypatch, drive, [])

    with pytest.raises(DriveCredentialRevoked):
        drive_reader_for(
            db_session,
            _actor(account),
            gmail_settings(),
            feature="la lettura",
            action=ALREADY_AUTHORIZED,
        )

    assert drive.requests == []


def test_a_named_action_applies_the_agent_ban_before_the_row_is_read(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The first of the four gates, and the only one that must not learn anything about
    the installation before it refuses. An agent credential on an installation that has
    not opted in is told the operation is closed to agents, full stop -- not "there is no
    Drive connected", not "the grant is missing a scope", both of which are facts about
    the owner's account that a refused caller has no business finding out.

    The account here is perfectly healthy, so any refusal other than `AgentForbidden`
    would mean the gate ran in the wrong order.
    """
    from pigrocrm.core.errors import AgentForbidden

    account = _account(db_session)
    drive = _tree()
    _inject(monkeypatch, drive, [])
    agent = Actor(id=account.user_id, type="mcp", role="admin")

    with pytest.raises(AgentForbidden) as excinfo:
        drive_reader_for(
            db_session, agent, gmail_settings(), feature="la lettura", action="read_drive_file"
        )

    assert excinfo.value.details["action"] == "read_drive_file"
    assert drive.requests == []


def test_already_authorized_is_the_only_way_past_that_gate(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`ALREADY_AUTHORIZED` replaced an `action=None` default, and the difference is the
    point of it: the exemption now has to be *written* at the call site.

    Its one legitimate user is `OriginalPdfResolver._drive_reader`, whose operation
    (`import_issued_invoice`) is itself on the ban list and was checked by `import_issued`
    under that name before this is reached -- so the same agent credential that is
    refused above composes a reader here, having already been refused, or allowed, once.
    Checking the ban twice under one name is harmless; checking it under the *wrong* name
    is what a required-and-explicit `action` prevents.
    """
    account = _account(db_session)
    drive = _tree()
    _inject(monkeypatch, drive, [])
    agent = Actor(id=account.user_id, type="mcp", role="admin")

    reader = drive_reader_for(
        db_session, agent, gmail_settings(), feature="la lettura", action=ALREADY_AUTHORIZED
    )

    assert reader.list_children(ROOT_FOLDER).items


def test_an_opted_in_installation_lets_a_named_action_through(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other half of the switch, here as much as anywhere: the ban is a setting the
    owner can open, not a wall, and a test that only proved the refusal would be
    satisfied by a gate that refuses every agent for ever."""
    account = _account(db_session)
    drive = _tree()
    _inject(monkeypatch, drive, [])
    open = Actor(id=account.user_id, type="mcp", role="admin", full_access=True)

    reader = drive_reader_for(
        db_session, open, gmail_settings(), feature="la lettura", action="read_drive_file"
    )

    assert reader.list_children(ROOT_FOLDER).items


def test_drive_reader_for_refuses_a_grant_without_the_read_scope(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    account = _account(db_session, scopes=(DRIVE_SCOPE_FILE,))
    drive = _tree()
    _inject(monkeypatch, drive, [])

    with pytest.raises(Conflict) as excinfo:
        drive_reader_for(
            db_session,
            _actor(account),
            gmail_settings(),
            feature="la lettura",
            action=ALREADY_AUTHORIZED,
        )

    assert excinfo.value.details["feature"] == "la lettura"
    assert excinfo.value.details["scope"] == DRIVE_SCOPE_READONLY
    assert drive.requests == []


def test_a_revocation_discovered_mid_read_marks_the_account_and_re_raises(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The one place the system can *learn* a Drive grant is gone is a refresh that
    answers `invalid_grant`, and `mark_revoked` commits on its own behalf so the fact
    outlives the rollback of whatever operation discovered it."""
    account = _account(db_session)
    drive = _tree()
    monkeypatch.setattr(
        reader_module,
        "user_transport_for",
        lambda *_, **__: DriveTransport(
            tokens=RevokedTokens(account.id), http=drive, sleep=lambda _: None
        ),
    )

    reader = drive_reader_for(
        db_session,
        _actor(account),
        gmail_settings(),
        feature="la lettura",
        action=ALREADY_AUTHORIZED,
    )
    with pytest.raises(DriveCredentialRevoked):
        reader.list_children(ROOT_FOLDER)

    db_session.refresh(account)
    assert account.status == "revoked"
    assert account.last_error is not None
    assert "Drive" in account.last_error
    assert REFRESH_TOKEN not in account.last_error
    kinds = db_session.execute(
        select(Activity.kind).where(Activity.entity_id == account.id)
    ).scalars()
    assert "drive.credential_revoked" in list(kinds)


def test_drive_reader_for_uses_the_configured_text_ceiling(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    account = _account(db_session)
    drive = _tree()
    _inject(monkeypatch, drive, [])

    reader = drive_reader_for(
        db_session,
        _actor(account),
        gmail_settings(drive_text_max_bytes=10),
        feature="la lettura",
        action=ALREADY_AUTHORIZED,
    )

    assert reader.read_text(TXT_FILE).truncated is True


# --- the guard over the whole module ---------------------------------------------------


def _within(drive: FakeDrive, roots: tuple[str, ...]) -> set[str]:
    """Every id under `roots`, walked downward through the fake's own state -- the set
    of folders a listing is entitled to name."""
    seen = set(roots)
    while True:
        found = {
            entry.id
            for entry in drive.files.values()
            if any(parent in seen for parent in entry.all_parents)
        }
        if found <= seen:
            return seen
        seen |= found


def test_every_files_list_of_this_module_stayed_inside_the_roots() -> None:
    """Guard three, and the one the other two cannot be: it reads the requests the fake
    transport actually received across every scenario above, and checks that each
    `files.list` named a folder of the configured hierarchy and nothing else.

    Depends on running after the tests above, which is how pytest orders a module. Run
    alone it proves nothing, so it says so rather than passing vacuously.
    """
    listings = [
        (drive, request) for drive in _DRIVES for request in drive.requests if request.is_files_list
    ]
    assert len(listings) >= 5, "the scenarios above issued almost no listing at all"
    for drive, request in listings:
        q = request.q or ""
        allowed = _within(drive, (ROOT_FOLDER,))
        named = [candidate for candidate in allowed if f"'{candidate}' in parents" in q]
        assert named, f"a files.list that names no folder of the roots: {q}"
        assert "contains" not in q.lower(), q
        assert " or " not in q.lower(), q
