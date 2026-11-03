"""`DocumentService.import_bytes`: bytes that arrived from somewhere else, filed as a
document in one transaction (slice 9C task 4).

Every other way a `documents` row gets a file is two commits -- `create` commits the
row, `add_version` commits the file -- and that is right for a person clicking upload,
because the row they just made is theirs to see even if the upload then fails. It is
wrong for an import: the caller of `import_bytes` is registering a fattura whose PDF
comes from Drive, and a document row committed on its own would outlive the refusal of
the invoice it was fetched for -- an orphan PDF filed against a customer, with no
invoice pointing at it and nothing to say why it is there.

So the two halves compose without a commit between them (`commit=False` leaves the
whole thing to the caller's transaction), and the tests below pin exactly that: the
document and its version are one unit of work, a refused `content_type` never leaves a
row behind, and `commit=False` really does mean the caller can still roll it all back.
"""

from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from pigrocrm.core.activities.models import Activity
from pigrocrm.core.actor import Actor
from pigrocrm.core.customers.models import Customer
from pigrocrm.core.documents.models import Document, DocumentVersion
from pigrocrm.core.documents.service import DocumentService
from pigrocrm.core.errors import PermissionDenied, ValidationFailed
from pigrocrm.core.storage.local import LocalFileStorage

ADMIN = Actor(id=None, type="system", role="admin")
READONLY = Actor(id=None, type="system", role="readonly")

PDF = b"%PDF-1.4 l'originale che il cliente ha in mano"
ORIGIN = {"drive_file_id": "1PreventivoPdfXXXX", "nome": "Fattura 7.pdf"}


def _customer_id(session: Session) -> UUID:
    customer = Customer(legal_name=f"Acme {uuid4()}")
    session.add(customer)
    session.flush()
    return customer.id


def _docs(session: Session, tmp_path: Any) -> DocumentService:
    from pigrocrm.core.config import get_settings

    return DocumentService(session, LocalFileStorage(tmp_path), get_settings())


def _documents(session: Session) -> list[Document]:
    return list(session.execute(select(Document)).scalars())


def test_import_bytes_files_the_document_and_its_first_version_together(
    db_session: Session, tmp_path: Any
) -> None:
    docs = _docs(db_session, tmp_path)
    cid = _customer_id(db_session)

    read = docs.import_bytes(
        customer_id=cid,
        type="invoice",
        title="Fattura 2026/7 (originale del gestionale precedente)",
        data=PDF,
        content_type="application/pdf",
        actor=ADMIN,
        origin=ORIGIN,
    )

    assert (read.customer_id, read.type, read.title) == (
        cid,
        "invoice",
        "Fattura 2026/7 (originale del gestionale precedente)",
    )
    assert read.current_version == 1
    # The bytes are really in storage, readable through the ordinary download path: an
    # import that filed a row and lost the file would be worse than no import.
    data, content_type, filename = docs.download(read.id, None, ADMIN)
    assert (data, content_type) == (PDF, "application/pdf")
    assert filename.endswith(".pdf")
    versions = docs.versions(read.id, ADMIN)
    assert [(v.number, v.content_type, v.size) for v in versions] == [
        (1, "application/pdf", len(PDF))
    ]


def test_the_import_records_where_the_bytes_came_from(db_session: Session, tmp_path: Any) -> None:
    """`origin` is the whole point of the activity: a PDF nobody in this CRM produced
    has to be able to answer "where did this come from?" years later, and the answer is
    the Drive id it was fetched from -- not merely "a file was uploaded"."""
    docs = _docs(db_session, tmp_path)
    read = docs.import_bytes(
        customer_id=_customer_id(db_session),
        type="invoice",
        title="Fattura 2026/7 (originale del gestionale precedente)",
        data=PDF,
        content_type="application/pdf",
        actor=ADMIN,
        origin=ORIGIN,
    )

    rows = list(
        db_session.execute(
            select(Activity.kind, Activity.payload)
            .where(Activity.entity_type == "document", Activity.entity_id == read.id)
            .order_by(Activity.occurred_at)
        )
    )
    kinds = [kind for kind, _ in rows]
    assert "document.importato" in kinds
    payload = next(payload for kind, payload in rows if kind == "document.importato")
    assert payload["origin"] == ORIGIN


def test_a_content_type_outside_the_allowed_set_leaves_no_row_behind(
    db_session: Session, tmp_path: Any
) -> None:
    """The refusal has to come *before* the `documents` row is flushed. A check that
    fired inside `add_version` would leave a flushed, uncommitted document in the
    caller's session for every later statement of that transaction to see -- the exact
    failure `InvoiceService.import_issued` documents at length and orders its checks to
    avoid."""
    docs = _docs(db_session, tmp_path)
    cid = _customer_id(db_session)

    with pytest.raises(ValidationFailed) as caught:
        docs.import_bytes(
            customer_id=cid,
            type="invoice",
            title="Uno zip travestito",
            data=b"PK\x03\x04",
            content_type="application/zip",
            actor=ADMIN,
            origin=ORIGIN,
        )

    assert caught.value.details["field"] == "content_type"
    assert _documents(db_session) == []


def test_empty_bytes_are_refused_before_any_row_is_flushed(
    db_session: Session, tmp_path: Any
) -> None:
    docs = _docs(db_session, tmp_path)
    with pytest.raises(ValidationFailed):
        docs.import_bytes(
            customer_id=_customer_id(db_session),
            type="invoice",
            title="Zero byte",
            data=b"",
            content_type="application/pdf",
            actor=ADMIN,
            origin=ORIGIN,
        )
    assert _documents(db_session) == []


def test_one_owner_exactly_neither_none_nor_both(db_session: Session, tmp_path: Any) -> None:
    docs = _docs(db_session, tmp_path)
    cid = _customer_id(db_session)
    for owners in ({}, {"customer_id": cid, "deal_id": uuid4()}):
        with pytest.raises(ValidationFailed):
            docs.import_bytes(
                type="invoice",
                title="Senza padrone",
                data=PDF,
                content_type="application/pdf",
                actor=ADMIN,
                origin=ORIGIN,
                **owners,  # type: ignore[arg-type]
            )
    assert _documents(db_session) == []


def test_a_deal_can_own_an_imported_document(
    db_session: Session, tmp_path: Any, seeded_deal_id: UUID
) -> None:
    docs = _docs(db_session, tmp_path)
    read = docs.import_bytes(
        deal_id=seeded_deal_id,
        type="document",
        title="Contratto firmato (da Drive)",
        data=b"%PDF-1.4 contratto",
        content_type="application/pdf",
        actor=ADMIN,
        origin=ORIGIN,
    )
    assert (read.deal_id, read.customer_id) == (seeded_deal_id, None)


def test_an_import_is_a_write_and_a_readonly_actor_cannot(
    db_session: Session, tmp_path: Any
) -> None:
    docs = _docs(db_session, tmp_path)
    with pytest.raises(PermissionDenied):
        docs.import_bytes(
            customer_id=_customer_id(db_session),
            type="invoice",
            title="Non mio",
            data=PDF,
            content_type="application/pdf",
            actor=READONLY,
            origin=ORIGIN,
        )
    assert _documents(db_session) == []


def test_commit_false_leaves_the_whole_import_to_the_callers_transaction(
    db_session: Session, tmp_path: Any
) -> None:
    """The property `InvoiceService.import_issued` depends on: with `commit=False`
    nothing is committed, so the caller's rollback takes the document *and* its version
    with it. Both halves are asserted, because a `create` that still committed on its
    own would leave the row and only lose the version -- an orphan document filed
    against a customer with no file in it, which is precisely the outcome this
    parameter exists to prevent.
    """
    docs = _docs(db_session, tmp_path)
    read = docs.import_bytes(
        customer_id=_customer_id(db_session),
        type="invoice",
        title="Da annullare",
        data=PDF,
        content_type="application/pdf",
        actor=ADMIN,
        origin=ORIGIN,
        commit=False,
    )
    # Visible inside the transaction that made it...
    assert db_session.get(Document, read.id) is not None

    db_session.rollback()

    # ...and gone with it.
    assert db_session.get(Document, read.id) is None
    assert (
        db_session.execute(
            select(DocumentVersion).where(DocumentVersion.document_id == read.id)
        ).first()
        is None
    )


def test_by_default_the_import_commits_on_its_own(db_session: Session, tmp_path: Any) -> None:
    """The mirror of the test above, so the default is pinned too: an ordinary caller
    that does not manage a transaction gets a document that survives."""
    docs = _docs(db_session, tmp_path)
    read = docs.import_bytes(
        customer_id=_customer_id(db_session),
        type="invoice",
        title="Da tenere",
        data=PDF,
        content_type="application/pdf",
        actor=ADMIN,
        origin=ORIGIN,
    )

    db_session.rollback()

    assert db_session.get(Document, read.id) is not None


def test_the_public_create_and_add_version_still_commit_one_at_a_time(
    db_session: Session, tmp_path: Any
) -> None:
    """The refactor that made `import_bytes` possible split `create` and `add_version`
    into non-committing cores. This is the guard on that split: the two public methods
    must still each commit on their own, because that is what every existing caller --
    every router, every MCP tool -- relies on.
    """
    from pigrocrm.core.documents.schemas import DocumentCreate

    docs = _docs(db_session, tmp_path)
    doc = docs.create(
        DocumentCreate(customer_id=_customer_id(db_session), type="invoice", title="A mano"),
        ADMIN,
    )
    db_session.rollback()
    assert db_session.get(Document, doc.id) is not None

    version = docs.add_version(doc.id, PDF, "application/pdf", ADMIN)
    db_session.rollback()
    assert db_session.get(DocumentVersion, version.id) is not None
    assert db_session.get(Document, doc.id).current_version == 1
