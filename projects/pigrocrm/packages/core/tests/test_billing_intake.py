"""The `billing` read of a space mailbox files attachments as inbox items (REB-1016, § 2.2 of
`2026-11-09-incoming-documents-inbox-design.md`), against the fake IMAP server of this suite
(a real socket and a real TLS handshake, never a real account) and a real local store."""

import base64
import datetime as dt
from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import pytest
from fakes.fake_imap import (
    HOST,
    PASSWORD,
    USERNAME,
    FakeImap,
    Msg,
    Part,
    ServerState,
    loopback,
)
from sqlalchemy import select
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.auth.models import User
from pigrocrm.core.config import Settings
from pigrocrm.core.inbox.mail_intake import PART_MAX_BYTES, BillingSink, wanted
from pigrocrm.core.inbox.models import InboxItem
from pigrocrm.core.mailboxes.imap_client import BodyPart, ImapFailure, MessageHead
from pigrocrm.core.mailboxes.imap_service import ImapMailboxService
from pigrocrm.core.mailboxes.imap_sync import ImapSyncService
from pigrocrm.core.mailboxes.models import Mailbox, MailboxPurpose
from pigrocrm.core.mailboxes.schemas import ImapMailboxCreate
from pigrocrm.core.storage.local import LocalFileStorage

NOW = dt.datetime(2026, 11, 10, 12, 0, tzinfo=dt.UTC)
KEY = base64.b64encode(b"m" * 32).decode()
XML = b'<?xml version="1.0"?><p:FatturaElettronica xmlns:p="x"><a/></p:FatturaElettronica>'
PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\n%%EOF"
PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 64


def _settings() -> Settings:
    return Settings(_env_file=None, jwt_secret="x" * 32, mailbox_token_key=KEY)  # type: ignore[call-arg]


@pytest.fixture
def server() -> Iterator[FakeImap]:
    fake = FakeImap(ServerState())
    fake.start()
    yield fake
    fake.stop()


@pytest.fixture
def storage(tmp_path: Path) -> LocalFileStorage:
    return LocalFileStorage(tmp_path)


def _admin(session: Session) -> Actor:
    user = User(
        email=f"u-{uuid4().hex[:8]}@example.it",
        name="U",
        password_hash="x",
        role="admin",
        active=True,
    )
    session.add(user)
    session.flush()
    return Actor(id=user.id, type="user", role="admin")


def _connect(session: Session, server: FakeImap, *, space_owned: bool = True) -> Mailbox:
    service = ImapMailboxService(
        session, settings=_settings(), resolve=loopback, context=server.client_context()
    )
    body = ImapMailboxCreate(
        address=USERNAME,
        host=HOST,
        port=server.port,
        security="tls",
        username=USERNAME,
        password=PASSWORD,
        purposes=["billing"] if space_owned else ["correspondence"],
        space_owned=space_owned,
    )
    read = service.connect(_admin(session), body)
    mailbox = session.get(Mailbox, read.id)
    assert mailbox is not None
    return mailbox


def _sync(session: Session, server: FakeImap, storage: LocalFileStorage, mailbox: Mailbox):
    sink = BillingSink(session, lambda: storage)
    return ImapSyncService(
        session,
        settings=_settings(),
        sinks=[sink],
        resolve=loopback,
        context=server.client_context(),
    ).sync(mailbox.id, now=NOW)


def _items(session: Session) -> list[InboxItem]:
    return list(session.scalars(select(InboxItem).order_by(InboxItem.created_at, InboxItem.id)))


def _invoice(uid: int = 1, **overrides: object) -> Msg:
    fields: dict[str, object] = {
        "uid": uid,
        "date": NOW - dt.timedelta(days=2),
        "sender": "fornitore@acme.test",
        "subject": "Fattura 12/2026",
        "parts": (
            Part("text/plain", content=b"the body, which is never read"),
            Part("application/xml", "IT01234567890_00001.xml", content=XML),
        ),
    }
    return Msg(**{**fields, **overrides})  # type: ignore[arg-type]


def test_an_xml_attachment_becomes_an_inbox_item_with_its_sender_subject_and_date(
    db_session: Session, server: FakeImap, storage: LocalFileStorage
) -> None:
    server.state.messages = [_invoice()]
    mailbox = _connect(db_session, server)

    report = _sync(db_session, server, storage, mailbox)

    assert report.health == "active"
    (item,) = _items(db_session)
    assert (item.source, item.state, item.file_type, item.uploaded_by_id) == (
        "mailbox",
        "new",
        "xml",
        None,
    )
    assert item.original_name == "IT01234567890_00001.xml"
    assert (item.mail_sender, item.mail_subject, item.mail_date) == (
        "fornitore@acme.test",
        "Fattura 12/2026",
        NOW - dt.timedelta(days=2),
    )
    assert (item.mailbox_id, item.mail_uid, item.mail_uidvalidity) == (mailbox.id, 1, 7)
    assert item.storage_key is not None and storage.get(item.storage_key) == XML


def test_the_body_of_the_message_is_never_fetched_nor_stored(
    db_session: Session, server: FakeImap, storage: LocalFileStorage
) -> None:
    server.state.messages = [_invoice()]
    mailbox = _connect(db_session, server)

    _sync(db_session, server, storage, mailbox)

    asked = [c for c in server.state.commands if "FETCH" in c]
    parts = [c for c in asked if "BODY.PEEK[" in c and "HEADER.FIELDS" not in c]
    assert [c.split("BODY.PEEK[")[1] for c in parts] == ["2])"], "only the attachment's part"
    stored = b"".join(p.read_bytes() for p in storage_root_files(storage))
    assert b"the body" not in stored
    columns = {c.name for c in InboxItem.__table__.columns}
    assert not {"body", "mail_body", "snippet", "body_text"} & columns


def storage_root_files(storage: LocalFileStorage) -> list[Path]:
    root = Path(getattr(storage, "root", getattr(storage, "_root", ".")))
    return [p for p in root.rglob("*") if p.is_file()]


def test_the_same_message_in_a_personal_mailbox_yields_nothing(
    db_session: Session, server: FakeImap, storage: LocalFileStorage
) -> None:
    server.state.messages = [_invoice()]
    mailbox = _connect(db_session, server, space_owned=False)

    report = _sync(db_session, server, storage, mailbox)

    assert report.health == "active"
    assert _items(db_session) == []
    assert not any("BODY.PEEK[2]" in c for c in server.state.commands)


def test_the_sink_refuses_a_personal_mailbox_even_if_it_were_handed_one(
    db_session: Session, server: FakeImap, storage: LocalFileStorage
) -> None:
    mailbox = _connect(db_session, server, space_owned=False)
    head = MessageHead(
        1,
        7,
        NOW,
        "<a>",
        "a@b.test",
        (),
        (),
        "s",
        (BodyPart("1", "application/xml", "a.xml", 10, None, None),),
    )

    def fetch(*_: object) -> bytes:
        raise AssertionError("a personal mailbox's attachment must never be fetched")

    BillingSink(db_session, lambda: storage).accept(mailbox, [head], fetch)
    assert _items(db_session) == []


def test_disconnecting_keeps_the_items_and_their_files(
    db_session: Session, server: FakeImap, storage: LocalFileStorage
) -> None:
    server.state.messages = [_invoice()]
    mailbox = _connect(db_session, server)
    _sync(db_session, server, storage, mailbox)
    admin = _admin(db_session)

    ImapMailboxService(
        db_session, settings=_settings(), resolve=loopback, context=server.client_context()
    ).disconnect(admin, mailbox.id)

    (item,) = _items(db_session)
    assert item.mailbox_id == mailbox.id and item.state == "new"
    assert item.storage_key is not None and storage.get(item.storage_key) == XML


def test_inline_parts_small_images_other_types_nameless_parts_and_own_mail_are_not_taken() -> None:
    def part(mime: str, name: str | None, size: int, **kw: object) -> BodyPart:
        return BodyPart("2", mime, name, size, kw.get("disposition"), kw.get("cid"))  # type: ignore[arg-type]

    assert wanted(part("application/xml", "a.xml", 100))
    assert wanted(part("image/png", "scan.png", 50_000))
    assert not wanted(part("image/png", "logo.png", 2_000)), "under the image floor"
    assert not wanted(part("image/png", "scan.png", 50_000, disposition="inline"))
    assert not wanted(part("image/png", "scan.png", 50_000, cid="<c1>"))
    assert wanted(part("application/pdf", "a.pdf", 50_000, disposition="attachment", cid="<c1>"))
    assert not wanted(part("application/msword", "a.doc", 50_000))
    assert not wanted(part("application/xml", None, 100))


def test_a_message_with_nothing_to_take_costs_no_part_fetch(
    db_session: Session, server: FakeImap, storage: LocalFileStorage
) -> None:
    server.state.messages = [
        Msg(
            1,
            NOW - dt.timedelta(days=1),
            parts=(Part("text/plain"), Part("application/msword", "a.doc", 500)),
        ),
        Msg(
            2,
            NOW - dt.timedelta(days=1),
            parts=(
                Part("text/html"),
                Part("image/png", "logo.png", 900, inline=True, content_id="<c>"),
            ),
        ),
        _invoice(3, sender=USERNAME),
    ]
    mailbox = _connect(db_session, server)

    _sync(db_session, server, storage, mailbox)

    assert _items(db_session) == []
    assert not [c for c in server.state.commands if "BODY.PEEK[" in c and "HEADER" not in c]


def test_a_message_read_twice_is_one_message(
    db_session: Session, server: FakeImap, storage: LocalFileStorage
) -> None:
    server.state.messages = [_invoice()]
    mailbox = _connect(db_session, server)

    _sync(db_session, server, storage, mailbox)
    fetches = len([c for c in server.state.commands if "BODY.PEEK[2]" in c])
    _sync(db_session, server, storage, mailbox)

    assert len(_items(db_session)) == 1
    assert len([c for c in server.state.commands if "BODY.PEEK[2]" in c]) == fetches, (
        "not fetched again"
    )


def test_an_attachment_over_the_cap_is_kept_as_a_discarded_item_and_never_fetched(
    db_session: Session, server: FakeImap, storage: LocalFileStorage
) -> None:
    server.state.messages = [
        _invoice(parts=(Part("application/pdf", "grande.pdf", PART_MAX_BYTES + 1),))
    ]
    mailbox = _connect(db_session, server)

    _sync(db_session, server, storage, mailbox)

    (item,) = _items(db_session)
    assert (item.state, item.storage_key, item.file_type) == ("discarded", None, None)
    assert item.reason is not None and "troppo grande" in item.reason
    assert item.mail_sender == "fornitore@acme.test"
    assert not [c for c in server.state.commands if "BODY.PEEK[1]" in c]


def test_bytes_that_disagree_with_the_name_are_kept_as_a_discarded_item_with_no_bytes(
    db_session: Session, server: FakeImap, storage: LocalFileStorage
) -> None:
    server.state.messages = [
        _invoice(parts=(Part("application/pdf", "finta.pdf", content=b"this is not a pdf"),))
    ]
    mailbox = _connect(db_session, server)

    _sync(db_session, server, storage, mailbox)

    (item,) = _items(db_session)
    assert (item.state, item.storage_key) == ("discarded", None)
    assert item.reason is not None and "pdf" in item.reason.lower()


def test_a_zip_is_opened_and_every_item_names_the_message(
    db_session: Session, server: FakeImap, storage: LocalFileStorage
) -> None:
    import io
    import zipfile

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("uno.xml", XML)
        archive.writestr("due.pdf", PDF)
    server.state.messages = [
        _invoice(parts=(Part("application/zip", "fatture.zip", content=buffer.getvalue()),))
    ]
    mailbox = _connect(db_session, server)

    _sync(db_session, server, storage, mailbox)

    items = _items(db_session)
    assert sorted(i.original_name for i in items) == [
        "fatture.zip",
        "fatture.zip/due.pdf",
        "fatture.zip/uno.xml",
    ]
    assert {(i.source, i.mail_subject, i.mailbox_id) for i in items} == {
        ("mailbox", "Fattura 12/2026", mailbox.id)
    }


def test_a_connection_that_drops_mid_message_leaves_nothing_of_it(
    db_session: Session, server: FakeImap, storage: LocalFileStorage
) -> None:
    mailbox = _connect(db_session, server)
    head = MessageHead(
        1,
        7,
        NOW,
        "<a>",
        "a@b.test",
        (),
        (),
        "s",
        (
            BodyPart("1", "application/xml", "a.xml", 100, None, None),
            BodyPart("2", "application/pdf", "b.pdf", 100, None, None),
        ),
    )
    calls: list[str] = []

    def fetch(_head: MessageHead, part: BodyPart) -> bytes:
        calls.append(part.path)
        if part.path == "2":
            raise ImapFailure("transient", "la connessione si è interrotta")
        return XML

    with pytest.raises(ImapFailure):
        BillingSink(db_session, lambda: storage).accept(mailbox, [head], fetch)

    assert calls == ["1", "2"]
    assert _items(db_session) == [], "the first file was not written before the second was fetched"


def test_a_sink_that_breaks_rolls_the_chunk_back_and_deletes_the_bytes_it_wrote(
    db_session: Session,
    server: FakeImap,
    storage: LocalFileStorage,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server.state.messages = [_invoice(1), _invoice(2, date=NOW - dt.timedelta(days=1))]
    mailbox = _connect(db_session, server)
    real = BillingSink._write
    calls: list[int] = []

    def write(self: BillingSink, plan: object) -> None:
        calls.append(1)
        if len(calls) == 2:
            raise RuntimeError("a sink bug")
        real(self, plan)  # type: ignore[arg-type]

    monkeypatch.setattr(BillingSink, "_write", write)

    with pytest.raises(RuntimeError):
        _sync(db_session, server, storage, mailbox)

    assert _items(db_session) == []
    assert storage_root_files(storage) == []


def test_a_cycle_commits_a_few_messages_at_a_time_and_moves_the_watermark_forward(
    db_session: Session, server: FakeImap, storage: LocalFileStorage
) -> None:
    server.state.messages = [
        _invoice(uid, date=NOW - dt.timedelta(days=20 - uid), subject=f"f{uid}")
        for uid in range(1, 13)
    ]
    mailbox = _connect(db_session, server)

    report = _sync(db_session, server, storage, mailbox)

    assert report.heads_read == 12
    assert sorted(i.mail_subject for i in _items(db_session)) == sorted(
        f"f{uid}" for uid in range(1, 13)
    )
    purpose = db_session.scalar(
        select(MailboxPurpose).where(MailboxPurpose.mailbox_id == mailbox.id)
    )
    assert purpose is not None and purpose.sync_watermark == NOW - dt.timedelta(days=8)


def _plain_head(*parts: BodyPart, sender: str = "a@b.test", message_id: str = "<a>") -> MessageHead:
    return MessageHead(1, 7, NOW, message_id, sender, (), (), "s", tuple(parts))


def test_a_zip_that_opens_into_more_than_it_may_is_not_opened(
    db_session: Session, server: FakeImap, storage: LocalFileStorage
) -> None:
    import io
    import zipfile

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("zeri.xml", b"<a>" + b"0" * (30 * 1024 * 1024))
    assert len(buffer.getvalue()) < 100_000, "a small file that opens into a great one"
    mailbox = _connect(db_session, server)
    head = _plain_head(
        BodyPart("1", "application/zip", "bomba.zip", len(buffer.getvalue()), None, None)
    )

    BillingSink(db_session, lambda: storage).accept(mailbox, [head], lambda *_: buffer.getvalue())

    (item,) = _items(db_session)
    assert (item.state, item.storage_key) == ("discarded", None)
    assert item.reason is not None and "troppo grande" in item.reason


def test_a_zip_with_too_many_entries_is_not_opened(
    db_session: Session, server: FakeImap, storage: LocalFileStorage
) -> None:
    import io
    import zipfile

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for index in range(150):
            archive.writestr(f"{index}.xml", XML)
    mailbox = _connect(db_session, server)
    head = _plain_head(
        BodyPart("1", "application/zip", "tanti.zip", len(buffer.getvalue()), None, None)
    )

    BillingSink(db_session, lambda: storage).accept(mailbox, [head], lambda *_: buffer.getvalue())

    (item,) = _items(db_session)
    assert item.state == "discarded"


def test_many_refused_parts_leave_a_few_rows_and_not_one_each(
    db_session: Session, server: FakeImap, storage: LocalFileStorage
) -> None:
    mailbox = _connect(db_session, server)
    parts = [
        BodyPart(str(i), "application/pdf", f"g{i}.pdf", PART_MAX_BYTES + 1, None, None)
        for i in range(1, 31)
    ]

    BillingSink(db_session, lambda: storage).accept(mailbox, [_plain_head(*parts)], lambda *_: b"")

    items = _items(db_session)
    assert len(items) == 5
    assert any("altri" in (i.reason or "") for i in items)


def test_a_header_with_a_nul_or_a_lone_surrogate_does_not_stop_the_mailbox(
    db_session: Session, server: FakeImap, storage: LocalFileStorage
) -> None:
    mailbox = _connect(db_session, server)
    head = _plain_head(
        BodyPart("1", "application/xml", "a.xml", 100, None, None),
        sender="x\x00y@b.test",
        message_id="<a\ud800b>",
    )

    BillingSink(db_session, lambda: storage).accept(mailbox, [head], lambda *_: XML)

    (item,) = _items(db_session)
    assert (item.state, item.mail_sender, item.mail_message_id) == ("new", "xy@b.test", "<ab>")


def test_a_message_that_cannot_be_written_is_one_discarded_item_and_the_rest_go_on(
    db_session: Session,
    server: FakeImap,
    storage: LocalFileStorage,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sqlalchemy.exc import DataError

    mailbox = _connect(db_session, server)
    first = _plain_head(BodyPart("1", "application/xml", "a.xml", 100, None, None))
    second = MessageHead(
        2,
        7,
        NOW,
        "<b>",
        "b@b.test",
        (),
        (),
        "s2",
        (BodyPart("1", "application/xml", "b.xml", 100, None, None),),
    )
    real = BillingSink._write_items

    def write_items(self: BillingSink, plan: object) -> None:
        if plan.origin.uid == 1:  # type: ignore[attr-defined]
            self.uploads.upload([("a.xml", XML)], self.actor, origin=plan.origin, commit=False)  # type: ignore[attr-defined]
            raise DataError("insert", {}, Exception("boom"))
        real(self, plan)  # type: ignore[arg-type]

    monkeypatch.setattr(BillingSink, "_write_items", write_items)

    BillingSink(db_session, lambda: storage).accept(mailbox, [first, second], lambda *_: XML)

    by_uid = {i.mail_uid: i for i in _items(db_session)}
    assert by_uid[1].state == "discarded" and by_uid[1].mail_sender is None
    assert by_uid[2].state == "new" and by_uid[2].mail_subject == "s2"
    assert len(storage_root_files(storage)) == 1, "the first message's bytes did not stay"
