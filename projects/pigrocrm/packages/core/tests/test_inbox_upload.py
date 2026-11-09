"""The upload road of the inbox (REB-971): items, deduplication, the automatic discards, the
limits, and who may upload and see what. Postgres is real; the suite's user bypasses the
row-level policy, which `test_legal_entity_scope.py` exercises as the application role."""

import io
import zipfile
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.errors import AgentForbidden, NotFound, PermissionDenied, ValidationFailed
from pigrocrm.core.inbox import uploads as uploads_module
from pigrocrm.core.inbox.models import InboxItem
from pigrocrm.core.inbox.schemas import InboxItemRead, InboxListQuery
from pigrocrm.core.inbox.service import InboxService
from pigrocrm.core.inbox.uploads import InboxUploads
from pigrocrm.core.invoices import import_upload
from pigrocrm.core.storage.local import LocalFileStorage

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "fatturapa"
INVOICE = (FIXTURES / "fpr-12-march-consulting.xml").read_bytes()
LOTTO = (FIXTURES / "fpr-12-batch-due-invoices.xml").read_bytes()
SIGNED = (FIXTURES / "fpr-12-march-consulting.xml.p7m").read_bytes()
BROKEN = b"\x30\x84\x00\x00\x00\x07garbage"
PDF = b"%PDF-1.7\n1 0 obj\n<<>>\nendobj\n"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


def _zip(entries: dict[str, bytes]) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    return out.getvalue()


@pytest.fixture
def storage(tmp_path: Path) -> LocalFileStorage:
    return LocalFileStorage(tmp_path / "store")


@pytest.fixture
def admin(seeded_user_id: UUID) -> Actor:
    return Actor(id=seeded_user_id, type="user", role="admin")


@pytest.fixture
def inbox(db_session: Session, storage: LocalFileStorage) -> InboxUploads:
    return InboxUploads(db_session, storage)


def _rows(session: Session) -> list[InboxItem]:
    return list(session.execute(select(InboxItem).order_by(InboxItem.id)).scalars())


def _stored(storage: LocalFileStorage) -> list[str]:
    root = Path(storage.root)
    return sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file())


def _by_name(items: list[InboxItemRead], name: str) -> InboxItemRead:
    return next(i for i in items if i.original_name == name)


# ---- the card's acceptance ---------------------------------------------------------------


def test_the_same_invoice_as_xml_as_p7m_and_inside_a_zip_is_one_item_and_two_duplicates(
    db_session: Session, inbox: InboxUploads, admin: Actor
) -> None:
    xml = inbox.upload([("fattura.xml", INVOICE)], admin).items[0]
    signed = inbox.upload([("fattura.xml.p7m", SIGNED)], admin).items[0]
    packed = inbox.upload([("cassetto.zip", _zip({"IT01234567890_1.xml": INVOICE}))], admin).items

    assert xml.state == "new" and xml.duplicate_of_id is None
    assert signed.state == "duplicate" and signed.duplicate_of_id == xml.id
    archive, entry = packed
    assert (archive.kind, archive.state) == ("archive", "filed")
    assert entry.state == "duplicate" and entry.duplicate_of_id == xml.id
    assert entry.parent_id == archive.id

    invoices = [r for r in _rows(db_session) if r.kind != "archive"]
    assert [r.state for r in invoices].count("new") == 1
    assert [r.state for r in invoices].count("duplicate") == 2


def test_the_three_roads_in_one_request_end_the_same_way_whatever_the_order(
    db_session: Session, inbox: InboxUploads, admin: Actor
) -> None:
    items = inbox.upload(
        [
            ("cassetto.zip", _zip({"IT01234567890_1.xml": INVOICE})),
            ("fattura.xml.p7m", SIGNED),
            ("fattura.xml", INVOICE),
        ],
        admin,
    ).items
    invoices = [i for i in items if i.kind != "archive"]
    assert [i.state for i in invoices] == ["new", "duplicate", "duplicate"]
    assert {i.duplicate_of_id for i in invoices[1:]} == {invoices[0].id}


def test_the_same_invoice_in_two_files_with_other_bytes_is_found_by_its_natural_key(
    inbox: InboxUploads, admin: Actor
) -> None:
    reformatted = INVOICE.replace(
        b"<?xml version='1.0' encoding='UTF-8'?>", b"<?xml version='1.0'?>\n"
    )
    assert reformatted != INVOICE
    first = inbox.upload([("a.xml", INVOICE)], admin).items[0]
    second = inbox.upload([("b.xml", reformatted)], admin).items[0]
    assert first.content_sha256 != second.content_sha256
    assert second.state == "duplicate" and second.duplicate_of_id == first.id
    assert second.reason is not None and "numero e data" in second.reason


def test_two_different_invoices_are_two_items(inbox: InboxUploads, admin: Actor) -> None:
    items = inbox.upload([("a.xml", INVOICE), ("lotto.xml", LOTTO)], admin).items
    assert [i.state for i in items] == ["new", "new"]
    assert items[0].natural_key != items[1].natural_key


# ---- what is kept ---------------------------------------------------------------------------


def test_an_item_keeps_its_bytes_under_a_key_of_the_inboxs_own(
    inbox: InboxUploads, admin: Actor, storage: LocalFileStorage
) -> None:
    item = inbox.upload([("Fattura n. 12 — Società & C.xml", INVOICE)], admin).items[0]
    assert item.has_file and item.size_bytes == len(INVOICE)
    assert _stored(storage) == [f"inbox/{item.id}/fattura-n-12-societa-c.xml"]
    data, content_type, name = inbox.download(item.id, admin)
    assert (data, content_type, name) == (INVOICE, "application/xml", item.original_name)


def test_a_duplicate_with_the_same_bytes_keeps_none_and_one_with_other_bytes_keeps_them(
    inbox: InboxUploads, admin: Actor, storage: LocalFileStorage
) -> None:
    original = inbox.upload([("a.xml", INVOICE)], admin).items[0]
    same = inbox.upload([("b.xml", INVOICE)], admin).items[0]
    signed = inbox.upload([("c.xml.p7m", SIGNED)], admin).items[0]
    assert same.state == signed.state == "duplicate"
    assert not same.has_file and signed.has_file
    assert len(_stored(storage)) == 2
    with pytest.raises(NotFound, match="originale"):
        inbox.download(same.id, admin)
    assert inbox.download(signed.id, admin)[1] == "application/pkcs7-mime"
    assert original.has_file


def test_a_signed_copy_is_read_as_the_invoice_it_signs(inbox: InboxUploads, admin: Actor) -> None:
    item = inbox.upload([("c.xml.p7m", SIGNED)], admin).items[0]
    assert item.file_type == "p7m" and item.state == "new"
    assert item.natural_key is not None
    assert item.sha256 != item.content_sha256


def test_a_p7m_whose_envelope_cannot_be_opened_is_kept_as_other_with_the_reason(
    inbox: InboxUploads, admin: Actor
) -> None:
    item = inbox.upload([("rotto.xml.p7m", BROKEN)], admin).items[0]
    assert (item.state, item.kind) == ("new", "other")
    assert item.reason is not None and "non si legge" in item.reason
    assert item.has_file


def test_every_accepted_type_becomes_an_item_of_its_type(inbox: InboxUploads, admin: Actor) -> None:
    items = inbox.upload(
        [
            ("estratto.pdf", PDF),
            ("scontrino.png", PNG),
            ("movimenti.csv", b"data;importo\n"),
            ("estratto.xml", b"<Document xmlns='urn:iso:std:iso:20022:tech:xsd:camt.053.001.02'/>"),
        ],
        admin,
    ).items
    assert [i.file_type for i in items] == ["pdf", "png", "csv", "camt053"]
    assert {i.state for i in items} == {"new"}
    assert {i.kind for i in items} == {None}


# ---- ZIP ------------------------------------------------------------------------------------


def test_a_zip_is_the_parent_of_its_entries_and_is_not_waiting_for_anybody(
    db_session: Session, inbox: InboxUploads, admin: Actor
) -> None:
    items = inbox.upload(
        [("cassetto.zip", _zip({"a.xml": INVOICE, "sub/b.pdf": PDF, "__MACOSX/._a.xml": b"junk"}))],
        admin,
    ).items
    archive, first, second = items
    assert (archive.kind, archive.state, archive.reason) == (
        "archive",
        "filed",
        "Archivio aperto: 2 file",
    )
    assert [first.parent_id, second.parent_id] == [archive.id, archive.id]
    assert [first.original_name, second.original_name] == [
        "cassetto.zip/a.xml",
        "cassetto.zip/sub/b.pdf",
    ]
    page = InboxService(db_session).list(InboxListQuery(parent_id=archive.id), Actor.system())
    assert {i.id for i in page.items} == {first.id, second.id}


def test_a_zip_uploaded_twice_is_one_duplicate_and_is_not_opened_again(
    db_session: Session, inbox: InboxUploads, admin: Actor
) -> None:
    archive = _zip({"a.xml": INVOICE})
    inbox.upload([("cassetto.zip", archive)], admin)
    again = inbox.upload([("copia.zip", archive)], admin).items
    assert [i.state for i in again] == ["duplicate"]
    assert len(_rows(db_session)) == 3  # the zip, its entry, the duplicate zip


def test_the_same_invoice_twice_inside_one_zip_is_an_item_and_a_duplicate(
    inbox: InboxUploads, admin: Actor
) -> None:
    _, first, second = inbox.upload(
        [("c.zip", _zip({"a.xml": INVOICE, "b.xml": INVOICE}))], admin
    ).items
    assert (first.state, second.state) == ("new", "duplicate")
    assert second.duplicate_of_id == first.id


def test_an_entry_the_inbox_does_not_take_is_a_discarded_item_with_its_reason_and_no_bytes(
    inbox: InboxUploads, admin: Actor, storage: LocalFileStorage
) -> None:
    items = inbox.upload(
        [
            (
                "c.zip",
                _zip({"leggimi.txt": b"hello", "falso.pdf": b"MZ not a pdf", "a.xml": INVOICE}),
            )
        ],
        admin,
    ).items
    note = _by_name(items, "c.zip/leggimi.txt")
    fake = _by_name(items, "c.zip/falso.pdf")
    assert (note.state, note.file_type, note.has_file) == ("discarded", None, False)
    assert note.reason is not None and "tipo di file non accettato" in note.reason
    assert (fake.state, fake.has_file) == ("discarded", False)
    assert fake.reason is not None and "il contenuto non e' quello" in fake.reason
    assert _by_name(items, "c.zip/a.xml").state == "new"
    assert len(_stored(storage)) == 2  # the zip and the invoice


def test_a_zip_inside_a_zip_is_kept_and_discarded_and_not_opened(
    inbox: InboxUploads, admin: Actor
) -> None:
    inner = _zip({"a.xml": INVOICE})
    items = inbox.upload([("outer.zip", _zip({"inner.zip": inner}))], admin).items
    nested = _by_name(items, "outer.zip/inner.zip")
    assert (nested.state, nested.file_type, nested.has_file) == ("discarded", "zip", True)
    assert nested.reason is not None and "Archivio dentro un archivio" in nested.reason
    assert len(items) == 2


def test_a_zip_over_the_limits_of_the_importer_is_refused_with_nothing_written(
    db_session: Session,
    inbox: InboxUploads,
    admin: Actor,
    storage: LocalFileStorage,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(import_upload, "UPLOAD_MAX_FILES", 2)
    with pytest.raises(ValidationFailed, match="3 file"):
        inbox.upload(
            [("c.zip", _zip({"a.xml": b"<a/>", "b.xml": b"<b/>", "c.xml": b"<c/>"}))], admin
        )
    monkeypatch.setattr(import_upload, "UPLOAD_MAX_FILES", 2000)
    monkeypatch.setattr(import_upload, "UPLOAD_MAX_FILE_BYTES", 10)
    with pytest.raises(ValidationFailed, match="il file supera"):
        inbox.upload([("c.zip", _zip({"a.xml": INVOICE}))], admin)
    assert _rows(db_session) == [] and _stored(storage) == []


def test_a_zip_that_cannot_be_read_is_refused(inbox: InboxUploads, admin: Actor) -> None:
    with pytest.raises(ValidationFailed, match="non si legge"):
        inbox.upload([("rotto.zip", b"PK\x03\x04 truncated")], admin)


# ---- the automatic discards ------------------------------------------------------------------


def test_a_zip_kept_as_nested_does_not_swallow_the_same_zip_sent_loose(
    inbox: InboxUploads, admin: Actor
) -> None:
    inner = _zip({"a.xml": INVOICE})
    inbox.upload([("outer.zip", _zip({"inner.zip": inner}))], admin)
    loose = inbox.upload([("inner.zip", inner)], admin).items
    assert (loose[0].kind, loose[0].state) == ("archive", "filed")
    assert loose[1].state == "new"
    assert loose[1].file_type == "xml"


def test_two_people_uploading_the_same_file_each_have_an_item_of_their_own(
    db_session: Session, inbox: InboxUploads
) -> None:
    first_user = Actor(id=_user(db_session, "collaborator"), type="user", role="collaborator")
    second_user = Actor(id=_user(db_session, "collaborator"), type="user", role="collaborator")
    first = inbox.upload([("a.pdf", PDF)], first_user).items[0]
    second = inbox.upload([("a.pdf", PDF)], second_user).items[0]
    assert second.state == "new" and second.duplicate_of_id is None and second.has_file
    assert first.id != second.id
    # An unscoped administrator sees both, and the next upload of theirs is a duplicate of the
    # earliest.
    admin = Actor(id=_user(db_session, "admin"), type="user", role="admin")
    third = inbox.upload([("a.pdf", PDF)], admin).items[0]
    assert (third.state, third.duplicate_of_id) == ("duplicate", first.id)


def test_the_bytes_of_a_file_whose_twin_kept_none_are_kept(
    inbox: InboxUploads, admin: Actor
) -> None:
    note = inbox.upload([("c.zip", _zip({"x.txt": b"a;b\n1;2\n"}))], admin).items[1]
    assert (note.state, note.has_file) == ("discarded", False)
    csv = inbox.upload([("x.csv", b"a;b\n1;2\n")], admin).items[0]
    assert (csv.state, csv.has_file) == ("duplicate", True)


def test_a_marker_in_the_name_of_a_pdf_is_not_an_sdi_file(
    inbox: InboxUploads, admin: Actor
) -> None:
    assert inbox.upload([("scontrino_MT_2.pdf", PDF)], admin).items[0].state == "new"


def test_a_request_whose_zips_hold_too_many_files_in_all_is_refused(
    db_session: Session, inbox: InboxUploads, admin: Actor, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(uploads_module, "UPLOAD_MAX_FILES", 4)
    archives = [(f"{n}.zip", _zip({"a.xml": b"<a/>", "b.xml": b"<b/>"})) for n in range(2)]
    with pytest.raises(ValidationFailed, match="piu' di 4"):
        inbox.upload(archives, admin)
    assert _rows(db_session) == []


def test_the_sdi_metadata_files_and_receipts_are_discarded_and_stay_as_items(
    inbox: InboxUploads, admin: Actor
) -> None:
    receipt = b"<?xml version='1.0'?><ns2:RicevutaConsegna xmlns:ns2='urn:x'/>"
    items = inbox.upload(
        [
            ("IT01234567890_00001_MT_001.xml", b"<a/>"),
            ("IT01234567890_00001_RC_001.xml", receipt),
            ("fattura.xml", INVOICE),
        ],
        admin,
    ).items
    assert [i.state for i in items] == ["discarded", "discarded", "new"]
    assert items[0].reason == "File di metadati dello SdI: non e' un documento"
    assert items[1].reason == "Ricevuta o notifica dello SdI: non e' un documento"
    assert items[0].has_file and items[1].has_file


def test_a_file_whose_hash_matches_a_discarded_item_is_a_duplicate_of_it_and_it_stays_discarded(
    db_session: Session, inbox: InboxUploads, admin: Actor
) -> None:
    discarded = inbox.upload([("IT0_MT_001.xml", b"<a/>")], admin).items[0]
    again = inbox.upload([("copia.xml", b"<a/>")], admin).items[0]
    assert again.state == "duplicate" and again.duplicate_of_id == discarded.id
    assert again.reason is not None and "scartato" in again.reason
    assert db_session.get(InboxItem, discarded.id).state == "discarded"  # type: ignore[union-attr]


# ---- the limits --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "content", "reason"),
    [
        ("virus.exe", b"MZ", "tipo di file non accettato"),
        ("fattura.xml", b"MZ\x90\x00", "il contenuto non e' quello"),
        ("vuoto.pdf", b"", "vuoto"),
        ("estratto.pdf", INVOICE, "il contenuto non e' quello"),
    ],
)
def test_a_loose_file_the_inbox_does_not_take_refuses_the_whole_request_with_nothing_written(
    db_session: Session,
    inbox: InboxUploads,
    admin: Actor,
    storage: LocalFileStorage,
    name: str,
    content: bytes,
    reason: str,
) -> None:
    with pytest.raises(ValidationFailed, match=reason):
        inbox.upload([("buono.xml", INVOICE), (name, content)], admin)
    assert _rows(db_session) == [] and _stored(storage) == []


def test_an_empty_request_is_refused(inbox: InboxUploads, admin: Actor) -> None:
    with pytest.raises(ValidationFailed, match="nessun file"):
        inbox.upload([], admin)


def test_a_file_over_the_size_ceiling_is_refused(
    inbox: InboxUploads, admin: Actor, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(uploads_module, "SIZE_MAX", len(PDF) - 1)
    with pytest.raises(ValidationFailed, match="il file supera"):
        inbox.upload([("estratto.pdf", PDF)], admin)


def test_a_loose_file_is_not_held_to_the_ten_mebibytes_of_a_zip_entry(
    inbox: InboxUploads, admin: Actor, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Design § 2.1: a statement PDF above the importer's per-entry limit arrives loose."""
    monkeypatch.setattr(import_upload, "UPLOAD_MAX_FILE_BYTES", 10)
    assert inbox.upload([("estratto.pdf", PDF)], admin).items[0].state == "new"


def test_a_request_over_the_total_ceiling_is_refused(
    db_session: Session, inbox: InboxUploads, admin: Actor, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(uploads_module, "UPLOAD_MAX_TOTAL_BYTES", len(PDF) + len(PNG) - 1)
    with pytest.raises(ValidationFailed, match="in tutto"):
        inbox.upload([("a.pdf", PDF), ("b.png", PNG)], admin)
    assert _rows(db_session) == []


def test_a_request_with_too_many_files_is_refused(
    inbox: InboxUploads, admin: Actor, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(uploads_module, "UPLOAD_MAX_FILES", 1)
    with pytest.raises(ValidationFailed, match="piu' di 1"):
        inbox.upload([("a.pdf", PDF), ("b.png", PNG)], admin)


def test_a_name_with_control_characters_is_stored_without_them(
    inbox: InboxUploads, admin: Actor
) -> None:
    item = inbox.upload([("fat\x00tura\n.pdf", PDF)], admin).items[0]
    assert item.original_name == "fattura.pdf"


def test_a_failing_storage_leaves_no_row_and_no_file(
    db_session: Session,
    admin: Actor,
    storage: LocalFileStorage,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_put = storage.put
    calls: list[str] = []

    def failing_put(key: str, data: bytes, content_type: str) -> None:
        calls.append(key)
        if len(calls) == 2:
            raise OSError("disk full")
        real_put(key, data, content_type)

    monkeypatch.setattr(storage, "put", failing_put)
    with pytest.raises(OSError, match="disk full"):
        InboxUploads(db_session, storage).upload([("a.pdf", PDF), ("b.png", PNG)], admin)
    assert len(calls) == 2
    assert _rows(db_session) == [] and _stored(storage) == []


# ---- who may upload ---------------------------------------------------------------------------


def test_a_readonly_user_cannot_upload(inbox: InboxUploads, seeded_user_id: UUID) -> None:
    with pytest.raises(PermissionDenied):
        inbox.upload([("a.pdf", PDF)], Actor(id=seeded_user_id, type="user", role="readonly"))


def test_a_collaborator_can_upload(inbox: InboxUploads, seeded_user_id: UUID) -> None:
    item = inbox.upload(
        [("a.pdf", PDF)], Actor(id=seeded_user_id, type="user", role="collaborator")
    ).items[0]
    assert item.uploaded_by_id == seeded_user_id


def test_an_agent_token_cannot_upload_unless_it_carries_full_access(
    inbox: InboxUploads, seeded_user_id: UUID
) -> None:
    with pytest.raises(AgentForbidden):
        inbox.upload([("a.pdf", PDF)], Actor(id=seeded_user_id, type="mcp", role="admin"))
    full = Actor(id=seeded_user_id, type="mcp", role="admin", full_access=True)
    assert inbox.upload([("a.pdf", PDF)], full).items[0].state == "new"


def test_a_system_actor_can_upload_and_the_item_has_no_uploader(
    inbox: InboxUploads,
) -> None:
    assert inbox.upload([("a.pdf", PDF)], Actor.system()).items[0].uploaded_by_id is None


# ---- who sees what ----------------------------------------------------------------------------


def _user(session: Session, role: str) -> UUID:
    from pigrocrm.core.auth.models import User

    user = User(email=f"{uuid4()}@example.test", password_hash="x", name="Altro", role=role)
    session.add(user)
    session.flush()
    return user.id


def test_an_unclassified_item_is_seen_by_its_uploader_and_an_unscoped_admin_only(
    db_session: Session, inbox: InboxUploads, admin: Actor
) -> None:
    other = Actor(id=_user(db_session, "collaborator"), type="user", role="collaborator")
    mine = Actor(id=_user(db_session, "collaborator"), type="user", role="collaborator")
    scoped_admin = Actor(
        id=_user(db_session, "admin"), type="user", role="admin", legal_entities=(uuid4(),)
    )
    readonly = Actor(id=_user(db_session, "readonly"), type="user", role="readonly")
    item = inbox.upload([("a.pdf", PDF)], mine).items[0]
    service = InboxService(db_session)

    for allowed in (mine, admin, Actor.system()):
        assert service.get(item.id, allowed).id == item.id
        assert [i.id for i in service.list(InboxListQuery(), allowed).items] == [item.id]
        assert inbox.download(item.id, allowed)[0] == PDF
    for refused in (other, scoped_admin, readonly):
        with pytest.raises(NotFound):
            service.get(item.id, refused)
        assert service.list(InboxListQuery(), refused).items == []
        with pytest.raises(NotFound):
            inbox.download(item.id, refused)


def test_a_classified_item_is_seen_by_whoever_the_entity_scope_lets_through(
    db_session: Session, inbox: InboxUploads, admin: Actor
) -> None:
    from pigrocrm.core.legal_entities.repository import LegalEntityRepository

    other = Actor(id=_user(db_session, "collaborator"), type="user", role="collaborator")
    legal_entity = LegalEntityRepository(db_session).default()
    assert legal_entity is not None
    item = inbox.upload([("a.pdf", PDF)], admin).items[0]
    db_session.get(InboxItem, item.id).legal_entity_id = legal_entity.id  # type: ignore[union-attr]
    db_session.flush()
    assert InboxService(db_session).get(item.id, other).id == item.id


def test_an_item_that_does_not_exist_is_not_found(db_session: Session, admin: Actor) -> None:
    with pytest.raises(NotFound):
        InboxService(db_session).get(uuid4(), admin)


# ---- listing ----------------------------------------------------------------------------------


def test_the_list_is_newest_first_filterable_and_pages(
    db_session: Session, inbox: InboxUploads, admin: Actor
) -> None:
    uploaded = inbox.upload(
        [("a.pdf", PDF), ("b.png", PNG), ("IT0_MT_001.xml", b"<a/>")], admin
    ).items
    service = InboxService(db_session)
    everything = service.list(InboxListQuery(), admin)
    assert [i.id for i in everything.items] == [i.id for i in reversed(uploaded)]
    assert everything.next_cursor is None
    discarded = service.list(InboxListQuery(state="discarded"), admin)
    assert [i.original_name for i in discarded.items] == ["IT0_MT_001.xml"]
    first = service.list(InboxListQuery(limit=2), admin)
    assert len(first.items) == 2 and first.next_cursor == first.items[-1].id
    second = service.list(InboxListQuery(limit=2, cursor=first.next_cursor), admin)
    assert [i.id for i in second.items] == [uploaded[0].id] and second.next_cursor is None


def test_a_read_carries_no_storage_key(
    db_session: Session, inbox: InboxUploads, admin: Actor
) -> None:
    item = inbox.upload([("a.pdf", PDF)], admin).items[0]
    assert "storage_key" not in item.model_dump()
    assert "storage_key" not in InboxService(db_session).get(item.id, admin).model_dump()


def test_the_database_refuses_a_duplicate_without_an_original(db_session: Session) -> None:
    from sqlalchemy.exc import IntegrityError

    db_session.add(
        InboxItem(
            original_name="a.pdf",
            file_type="pdf",
            size_bytes=1,
            sha256="0" * 64,
            content_sha256="0" * 64,
            state="duplicate",
        )
    )
    with pytest.raises(IntegrityError, match="ck_inbox_items_duplicate_of"):
        db_session.flush()


def test_three_identical_files_in_one_request_are_one_original_and_two_duplicates(
    db_session: Session, inbox: InboxUploads, admin: Actor
) -> None:
    inbox.upload([("a.xml", INVOICE), ("b.xml", INVOICE), ("c.xml", INVOICE)], admin)
    total = db_session.execute(select(func.count(InboxItem.id))).scalar_one()
    originals = db_session.execute(
        select(func.count(InboxItem.id)).where(InboxItem.state != "duplicate")
    ).scalar_one()
    assert (total, originals) == (3, 1)
