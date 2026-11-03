"""The read-only review step (REB-365): `import_review.review_content` and
`InvoiceService.review_import`, exercised end to end with a real `LegalEntity`
row, a real `documents` row and the FPR12 fixture REB-363/364 already use.

The issue's own "Done when" is the one property every scenario below serves:
reviewing the same document twice never changes database state and always returns
the same verdict.
"""

import hashlib
from datetime import date
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import pytest
from fakes.customer_fixtures import make_customer_with_address
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.customers.models import Customer
from pigrocrm.core.documents.schemas import DocumentCreate
from pigrocrm.core.documents.service import DocumentService
from pigrocrm.core.errors import AgentForbidden, PermissionDenied, ValidationFailed
from pigrocrm.core.invoices.models import Invoice
from pigrocrm.core.invoices.service import InvoiceService
from pigrocrm.core.legal_entities.models import LegalEntity
from pigrocrm.core.legal_entities.repository import LegalEntityRepository
from pigrocrm.core.storage import LocalFileStorage

FIXTURES = Path(__file__).parent / "fixtures" / "fatturapa"
CONSULTING = "fpr-12-march-consulting.xml"
ADMIN = Actor(id=None, type="user", role="admin")

# The FPR12 fixture's own `CedentePrestatore` (fornitore): matching this on an
# `LegalEntity` is what makes the fixture "outgoing" for the account holder.
SUPPLIER_VAT_NUMBER = "01234567890"
SUPPLIER_CF = "BNCCHR85M41H501Z"
# Its `CessionarioCommittente` (cliente): matching this on a `Customer` is what
# turns an otherwise-`"ready"` outcome into a real match instead of `"needs_
# customer_confirmation"`.
CUSTOMER_VAT_NUMBER = "09876543210"


def _fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def _legal_entity(session: Session, **overrides: object) -> LegalEntity:
    base: dict[str, object] = {
        "legal_name": "Chiara Bianchi",
        "vat_number": SUPPLIER_VAT_NUMBER,
        "tax_code": SUPPLIER_CF,
        "country": "IT",
        "is_default": True,
    }
    base.update(overrides)
    existing = LegalEntityRepository(session).default()
    if existing is not None:
        for key, value in base.items():
            setattr(existing, key, value)
        session.flush()
        return existing
    profile = LegalEntity(**base)
    session.add(profile)
    session.flush()
    return profile


def _document(
    session: Session,
    storage: LocalFileStorage,
    content: bytes,
    *,
    content_type: str = "application/xml",
    owner: Customer | None = None,
) -> UUID:
    """A `documents` row carrying `content` as its current version -- the same door
    (`POST /api/documents/upload`, in production) the design record names as the
    only way a caller ever gets a `document_id` to hand this tool."""
    owner = owner if owner is not None else make_customer_with_address(session)
    service = DocumentService(session, storage)
    document = service.create(DocumentCreate(customer_id=owner.id, title="Fattura ricevuta"), ADMIN)
    service.add_version(document.id, content, content_type, ADMIN)
    return document.id


def _invoice_count(session: Session) -> int:
    return int(session.execute(select(func.count()).select_from(Invoice)).scalar_one())


def _seed_register_row(session: Session, *, xml_hash_sha256: str | None) -> None:
    """A register row at the fixture's own natural key (2026/6), so `_natural_key`
    finds it through `InvoiceRepository.existing_by_number`."""
    row = Invoice(
        customer_id=make_customer_with_address(session).id,
        type="invoice",
        status="issued",
        year=2026,
        number=6,
        issue_date=date(2026, 3, 15),
        imported_from="fatturapa",
        xml_hash_sha256=xml_hash_sha256,
        taxable_amount=Decimal("1000.00"),
        vat_amount=Decimal("0.00"),
        stamp_duty=Decimal("2.00"),
        total=Decimal("1000.00"),
    )
    session.add(row)
    session.flush()


def test_reviewing_the_same_document_twice_never_changes_state_and_returns_the_same_verdict(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    _legal_entity(db_session)
    customer = make_customer_with_address(db_session, vat_number=CUSTOMER_VAT_NUMBER)
    document_id = _document(db_session, local_storage, _fixture(CONSULTING), owner=customer)
    service = InvoiceService(db_session, local_storage)

    before = _invoice_count(db_session)
    first = service.review_import([document_id], ADMIN)
    after_first = _invoice_count(db_session)
    second = service.review_import([document_id], ADMIN)
    after_second = _invoice_count(db_session)

    assert before == after_first == after_second == 0
    assert [row.model_dump(mode="json") for row in first] == [
        row.model_dump(mode="json") for row in second
    ]
    [row] = first
    assert row.outcome == "ready"
    assert row.matched_customer_id == customer.id


def test_review_never_adds_dirties_or_deletes_anything_in_the_session(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    """The stronger form of "no database write of any kind": the session's own
    pending-write state is empty right after the call, not merely unflushed."""
    _legal_entity(db_session)
    customer = make_customer_with_address(db_session, vat_number=CUSTOMER_VAT_NUMBER)
    document_id = _document(db_session, local_storage, _fixture(CONSULTING), owner=customer)
    db_session.flush()

    InvoiceService(db_session, local_storage).review_import([document_id], ADMIN)

    assert not db_session.new
    assert not db_session.dirty
    assert not db_session.deleted


def test_no_matching_customer_reports_needs_customer_confirmation(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    _legal_entity(db_session)
    document_id = _document(db_session, local_storage, _fixture(CONSULTING))
    [row] = InvoiceService(db_session, local_storage).review_import([document_id], ADMIN)
    assert row.outcome == "needs_customer_confirmation"
    assert row.matched_customer_id is None


def test_a_suppliers_invoice_is_incoming_skipped_and_the_register_is_never_checked(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    _legal_entity(db_session, vat_number="99999999999", tax_code="ZZZZZZ00A01H501Z")
    document_id = _document(db_session, local_storage, _fixture(CONSULTING))
    [row] = InvoiceService(db_session, local_storage).review_import([document_id], ADMIN)
    assert row.outcome == "incoming_skipped"
    assert row.matched_customer_id is None


def test_a_matching_stored_hash_is_already_present(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    _legal_entity(db_session)
    content = _fixture(CONSULTING)
    _seed_register_row(db_session, xml_hash_sha256=hashlib.sha256(content).hexdigest())
    document_id = _document(db_session, local_storage, content)

    before = _invoice_count(db_session)
    [row] = InvoiceService(db_session, local_storage).review_import([document_id], ADMIN)
    assert row.outcome == "already_present"
    assert _invoice_count(db_session) == before


def test_a_different_stored_hash_is_conflict_not_already_present(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    _legal_entity(db_session)
    _seed_register_row(
        db_session, xml_hash_sha256=hashlib.sha256(b"a previous, different document").hexdigest()
    )
    document_id = _document(db_session, local_storage, _fixture(CONSULTING))
    [row] = InvoiceService(db_session, local_storage).review_import([document_id], ADMIN)
    assert row.outcome == "conflict"


def test_a_numbered_row_with_no_stored_hash_is_conflict_never_already_present(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    """The `external`/`lotto`-batch rule REB-364 already enforces at the classifier
    level, proven again here through the whole wiring: nothing to compare against
    is never treated as a safe match."""
    _legal_entity(db_session)
    _seed_register_row(db_session, xml_hash_sha256=None)
    document_id = _document(db_session, local_storage, _fixture(CONSULTING))
    [row] = InvoiceService(db_session, local_storage).review_import([document_id], ADMIN)
    assert row.outcome == "conflict"


def test_a_document_no_adapter_recognises_is_unclaimed_not_a_silent_drop(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    _legal_entity(db_session)
    document_id = _document(db_session, local_storage, b"not xml at all", content_type="text/plain")
    [row] = InvoiceService(db_session, local_storage).review_import([document_id], ADMIN)
    assert row.outcome == "unclaimed"
    assert row.invoice is None
    assert row.matched_customer_id is None


def test_to_number_this_crm_never_prints_carries_no_natural_key_and_is_to_conflict(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    """A document printing a free-text id (the previous system's own `"900142/..."`
    shape, not PigroCRM's `number_complete`) resolves to no PigroCRM natural key at
    all, so the conservative default applies -- never assumed safe just because
    nothing was found to compare against."""
    _legal_entity(db_session)
    xml = _fixture(CONSULTING).replace(b"2026/6", b"900142-A")
    document_id = _document(db_session, local_storage, xml)
    [row] = InvoiceService(db_session, local_storage).review_import([document_id], ADMIN)
    assert row.outcome == "conflict"


def test_to_bare_digit_number_pairs_with_the_documents_own_emission_year(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    """The other shape `_natural_key` recognises, never exercised by the fixture's
    own `number_complete`-shaped `"2026/6"` elsewhere in this file: a plain
    invoicing tool's bare `"6"`, paired with `issue_date`'s year (2026, the
    fixture's own `<Data>2026-03-15</Data>`) rather than any other field."""
    _legal_entity(db_session)
    xml = _fixture(CONSULTING).replace(b"2026/6", b"6")
    _seed_register_row(db_session, xml_hash_sha256=hashlib.sha256(xml).hexdigest())
    document_id = _document(db_session, local_storage, xml)
    [row] = InvoiceService(db_session, local_storage).review_import([document_id], ADMIN)
    assert row.outcome == "already_present"


def test_to_number_no_int_can_parse_is_to_conflict_never_to_crash(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    """Regression: `"\u00b2".isdigit()` is `True` but `int("\u00b2")` raises
    `ValueError` -- a `str.isdigit()` pre-check on a FatturaPA `Numero` (free
    text, supplier-controlled, `SafeStr` restricts no character set beyond a
    NUL) would have let a document like this one crash the whole review call
    instead of reporting an ordinary, resolvable outcome for the document."""
    _legal_entity(db_session)
    xml = _fixture(CONSULTING).replace(b"2026/6", "\u00b2".encode())
    document_id = _document(db_session, local_storage, xml)
    [row] = InvoiceService(db_session, local_storage).review_import([document_id], ADMIN)
    assert row.outcome == "conflict"


def test_multiple_document_ids_in_one_call_produce_one_row_each(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    _legal_entity(db_session)
    customer = make_customer_with_address(db_session, vat_number=CUSTOMER_VAT_NUMBER)
    ready_id = _document(db_session, local_storage, _fixture(CONSULTING), owner=customer)
    unclaimed_id = _document(db_session, local_storage, b"garbage", content_type="text/plain")

    rows = InvoiceService(db_session, local_storage).review_import([ready_id, unclaimed_id], ADMIN)

    by_document = {row.document_id: row for row in rows}
    assert set(by_document) == {ready_id, unclaimed_id}
    assert by_document[ready_id].outcome == "ready"
    assert by_document[unclaimed_id].outcome == "unclaimed"


def test_a_readonly_actor_is_refused(db_session: Session, local_storage: LocalFileStorage) -> None:
    _legal_entity(db_session)
    document_id = _document(db_session, local_storage, _fixture(CONSULTING))
    readonly = Actor(id=None, type="user", role="readonly")
    with pytest.raises(PermissionDenied):
        InvoiceService(db_session, local_storage).review_import([document_id], readonly)


def test_an_agent_credential_without_full_access_is_forbidden(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    """The credential-level half of the ban (`agent_actions.py::FULL_ACCESS_ACTIONS`),
    proven at the service the tool actually calls -- the tool's own absence on a
    default installation is `test_mcp_invoice_ban.py`'s guarantee, not this one."""
    _legal_entity(db_session)
    document_id = _document(db_session, local_storage, _fixture(CONSULTING))
    agent = Actor(id=None, type="mcp", role="admin", full_access=False)
    with pytest.raises(AgentForbidden):
        InvoiceService(db_session, local_storage).review_import([document_id], agent)


def test_no_legal_entities_configured_refuses_rather_than_guesses(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    # Since REB-615 every space has its azienda from the first boot, so «not configured»
    # is an azienda with neither fiscal id, and the refusal names that field rather
    # than a missing row: the import never guesses which P.IVA is ours.
    document_id = _document(db_session, local_storage, _fixture(CONSULTING))
    with pytest.raises(ValidationFailed) as caught:
        InvoiceService(db_session, local_storage).review_import([document_id], ADMIN)
    assert caught.value.details["entity"] == "legal_entity"
    assert caught.value.details["field"] == "vat_number"
