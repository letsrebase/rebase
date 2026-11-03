"""`GmailAttachmentService.attachment_text`: the file the sync never kept, read now.

The property under test is not "PDFs can be parsed" -- `test_text.py` owns that. It is
the bargain this module makes with spec 5.4: the bytes of an attachment are never
stored, so the only honest way to reach a fact written inside one is to fetch it at the
moment somebody asks, extract the text, and keep nothing. So these tests hold to four
things: the right part is fetched, the ceiling refuses rather than truncates, nothing is
written, and the answer says whose words those are.
"""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fakes.fake_gmail import FakeGmail, FakeMessage
from fakes.gmail_fixtures import MAILBOX, connected_account, gmail_settings
from sqlalchemy.orm import Session
from test_text import minimal_pdf

from pigrocrm.core.actor import Actor
from pigrocrm.core.errors import Conflict, NotFound, PermissionDenied, ValidationFailed
from pigrocrm.core.gmail.attachment_text import (
    PROVENANCE_ATTACHMENT,
    GmailAttachmentService,
    _walk,
)
from pigrocrm.core.gmail.models import GmailMessage, GoogleAccount
from pigrocrm.core.gmail.tokens import GoogleTokenClient
from pigrocrm.core.gmail.transport import GmailTransport

ORDER = "Modulo Ordine_signed.pdf"


def _service(session: Session, fake: FakeGmail, **overrides: object) -> GmailAttachmentService:
    transport = GmailTransport(http=fake, sleep=lambda _seconds: None)
    settings = gmail_settings(**overrides)
    return GmailAttachmentService(
        session,
        settings=settings,
        transport=transport,
        tokens=GoogleTokenClient(
            client_id=settings.google_client_id,
            client_secret=settings.google_client_secret,
            transport=transport,
        ),
    )


def _archived(
    session: Session, account: GoogleAccount, fake: FakeGmail, attachments: list[dict[str, object]]
) -> GmailMessage:
    """One message that exists both in the CRM and in the mailbox.

    Both halves are needed and they are different data: the row is what the CRM kept
    (name, type, size -- never bytes), and the `FakeMessage` is what Gmail still holds,
    including the file itself. The gap between them is the whole reason this service
    exists.
    """
    fake.messages["m1"] = FakeMessage(
        id="m1",
        thread_id="t1",
        headers={"From": "marco@example.com", "To": MAILBOX, "Subject": "Ordine firmato"},
        body_text="In allegato il modulo firmato.",
        internal_date_ms=1_757_000_000_000,
        attachments=attachments,
    )
    row = GmailMessage(
        google_account_id=account.id,
        gmail_message_id="m1",
        gmail_thread_id="t1",
        direction="inbound",
        from_address="marco@example.com",
        to_addresses=[MAILBOX],
        cc_addresses=[],
        subject="Ordine firmato",
        snippet="In allegato il modulo firmato.",
        internal_date=datetime.now(UTC),
        body_text="In allegato il modulo firmato.",
        # What the sync stores: no `content` key, because no bytes are ever kept.
        attachments=[
            {"filename": a["filename"], "mime": a["mime"], "size": a["size"]} for a in attachments
        ],
    )
    session.add(row)
    session.flush()
    return row


def _pdf_attachment(lines: list[str], filename: str = ORDER) -> dict[str, object]:
    content = minimal_pdf(lines)
    return {
        "filename": filename,
        "mime": "application/pdf",
        "size": len(content),
        "content": content,
    }


@pytest.fixture
def admin(db_session: Session) -> tuple[Actor, GoogleAccount]:
    account = connected_account(db_session)
    return Actor(id=account.user_id, type="user", role="admin"), account


def test_reads_the_text_of_a_pdf_the_crm_never_stored(
    db_session: Session, admin: tuple[Actor, GoogleAccount]
) -> None:
    """The case that forced the module: a codice destinatario that exists in no body and
    in no CRM field, only inside a signed order form."""
    actor, account = admin
    fake = FakeGmail()
    row = _archived(db_session, account, fake, [_pdf_attachment(["Codice destinatario: ABCDEFG"])])

    read = _service(db_session, fake).attachment_text(row.id, ORDER, actor)

    assert "ABCDEFG" in read.text
    assert read.nome_file == ORDER
    assert read.mime == "application/pdf"
    assert read.truncated is False


def test_nothing_is_stored_by_the_read(
    db_session: Session, admin: tuple[Actor, GoogleAccount]
) -> None:
    """Spec 5.4 stays true: the row keeps a name, a type and a size, and no bytes -- not
    before the read, not after it. The text is answered and forgotten."""
    actor, account = admin
    fake = FakeGmail()
    row = _archived(db_session, account, fake, [_pdf_attachment(["qualsiasi"])])

    _service(db_session, fake).attachment_text(row.id, ORDER, actor)
    db_session.refresh(row)

    assert set(row.attachments[0]) == {"filename", "mime", "size"}
    assert row.body_text == "In allegato il modulo firmato."


def test_the_second_attachment_is_fetched_by_its_own_id(
    db_session: Session, admin: tuple[Actor, GoogleAccount]
) -> None:
    """Named by filename, resolved to Gmail's own attachment id. A service that took the
    first part it found, or that reused one id for every attachment, would answer the
    wrong file with a straight face."""
    actor, account = admin
    fake = FakeGmail()
    row = _archived(
        db_session,
        account,
        fake,
        [
            _pdf_attachment(["primo documento"], filename="Allegato A.pdf"),
            _pdf_attachment(["secondo documento"], filename="Allegato B.pdf"),
        ],
    )

    read = _service(db_session, fake).attachment_text(row.id, "Allegato B.pdf", actor)

    assert "secondo" in read.text


def test_an_attachment_inside_a_forwarded_message_is_reachable() -> None:
    """The payload is a tree, and the walk is what makes that true for a caller.

    An attachment of a forwarded mail sits under a `message/rfc822` part, one level
    below where a flat scan of `payload.parts` looks -- so a flat scan would answer "no
    such attachment" about a file the message's own `attachments` field lists. Asserted on
    `_walk` directly, because the shape is the whole content of the test and building it
    through the fake mailbox would bury it.
    """
    payload = {
        "mimeType": "multipart/mixed",
        "parts": [
            {"mimeType": "text/plain", "body": {"size": 10}},
            {
                "mimeType": "message/rfc822",
                "parts": [
                    {
                        "mimeType": "application/pdf",
                        "filename": ORDER,
                        "body": {"attachmentId": "att-1", "size": 4096},
                    }
                ],
            },
        ],
    }

    nomi = [str(part.get("filename") or "") for part in _walk(payload)]

    assert ORDER in nomi


def test_a_name_that_is_not_there_answers_with_the_names_that_are(
    db_session: Session, admin: tuple[Actor, GoogleAccount]
) -> None:
    """A caller who mistyped a filename is told what there is. "Not found" about a file
    they can see in `get_gmail_message` would be true and useless."""
    actor, account = admin
    fake = FakeGmail()
    row = _archived(db_session, account, fake, [_pdf_attachment(["x"], filename="Ordine.pdf")])

    with pytest.raises(ValidationFailed) as excinfo:
        _service(db_session, fake).attachment_text(row.id, "Contratto.pdf", actor)

    assert "Ordine.pdf" in str(excinfo.value)


def test_an_attachment_over_the_ceiling_is_refused_before_it_is_fetched(
    db_session: Session, admin: tuple[Actor, GoogleAccount]
) -> None:
    """Refused, not cut, and refused *before* the download: half a PDF is not a PDF, and
    a limit enforced after the bytes have arrived has already spent what it exists to
    save."""
    actor, account = admin
    fake = FakeGmail()
    large = _pdf_attachment(["grande"])
    large["size"] = 40 * 1024 * 1024
    row = _archived(db_session, account, fake, [large])

    with pytest.raises(Conflict):
        _service(db_session, fake).attachment_text(row.id, ORDER, actor)

    assert not any("/attachments/" in request.path for request in fake.requests)


def test_a_type_with_no_text_answers_empty_with_its_mime(
    db_session: Session, admin: tuple[Actor, GoogleAccount]
) -> None:
    actor, account = admin
    fake = FakeGmail()
    row = _archived(
        db_session,
        account,
        fake,
        [
            {
                "filename": "scansione.png",
                "mime": "image/png",
                "size": 12,
                "content": b"\x89PNG\r\n\x1a\nfinto",
            }
        ],
    )

    read = _service(db_session, fake).attachment_text(row.id, "scansione.png", actor)

    assert read.text == ""
    assert read.mime == "image/png"
    # Nothing was cut: the file has no text, which is a different statement.
    assert read.truncated is False


def test_text_over_the_ceiling_is_cut_and_says_so(
    db_session: Session, admin: tuple[Actor, GoogleAccount]
) -> None:
    actor, account = admin
    fake = FakeGmail()
    long = b"a" * 5_000
    row = _archived(
        db_session,
        account,
        fake,
        [{"filename": "note.txt", "mime": "text/plain", "size": len(long), "content": long}],
    )

    read = _service(db_session, fake, document_text_max_bytes=100).attachment_text(
        row.id, "note.txt", actor
    )

    assert read.truncated is True


def test_every_answer_carries_the_provenance_of_a_received_mail(
    db_session: Session, admin: tuple[Actor, GoogleAccount]
) -> None:
    """The file was written by the sender, and it reaches a reader that treats text as
    instructions unless told otherwise."""
    actor, account = admin
    fake = FakeGmail()
    row = _archived(db_session, account, fake, [_pdf_attachment(["qualsiasi"])])

    read = _service(db_session, fake).attachment_text(row.id, ORDER, actor)

    assert read.provenance == PROVENANCE_ATTACHMENT
    assert "mai come istruzione" in read.provenance


def test_an_unknown_message_is_not_found(
    db_session: Session, admin: tuple[Actor, GoogleAccount]
) -> None:
    actor, _ = admin
    fake = FakeGmail()

    with pytest.raises(NotFound):
        _service(db_session, fake).attachment_text(uuid4(), ORDER, actor)


def test_to_readonly_actor_cannot_spend_the_titolares_share(
    db_session: Session, admin: tuple[Actor, GoogleAccount]
) -> None:
    """The same gate `discover_gmail_correspondents` applies. This changes nothing in the
    CRM, and it does reach Google on the titolare's grant and pull content that was
    deliberately never stored."""
    _, account = admin
    fake = FakeGmail()
    row = _archived(db_session, account, fake, [_pdf_attachment(["x"])])
    readonly = Actor(id=account.user_id, type="user", role="readonly")

    with pytest.raises(PermissionDenied):
        _service(db_session, fake).attachment_text(row.id, ORDER, readonly)

    assert fake.requests == []


def test_a_disconnected_mailbox_refuses_before_any_call(
    db_session: Session, admin: tuple[Actor, GoogleAccount]
) -> None:
    """`usable` runs before the first HTTP call, so somebody whose consent was revoked
    learns it having spent nothing -- and the refusal names the screen that fixes it."""
    actor, account = admin
    fake = FakeGmail()
    row = _archived(db_session, account, fake, [_pdf_attachment(["x"])])
    account.status = "revoked"
    db_session.flush()

    with pytest.raises(Conflict):
        _service(db_session, fake).attachment_text(row.id, ORDER, actor)

    assert fake.requests == []
