"""Confirming a reviewed invoice import onto the register (REB-366):
`InvoiceService.confirm_import`, exercised end to end with a real
`LegalEntity` row, a real `FiscalProfile` row, a real `documents` row and
the FPR12 fixtures REB-363/364/365 already use.

The issue's own "Done when" is what every scenario below serves: confirming an
invoice review already flagged `already_present` is a no-op, never a
duplicate insert; confirming a fresh one produces exactly the row
`import_issued` would have produced by hand, plus a hash-verified
`xml_document_id` for a single-invoice source, `NULL` for a batch-sourced one.
"""

import hashlib
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fakes.customer_fixtures import make_customer_with_address
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.customers.models import Customer
from pigrocrm.core.customers.repository import CustomerRepository
from pigrocrm.core.documents.schemas import DocumentCreate
from pigrocrm.core.documents.service import DocumentService
from pigrocrm.core.errors import AgentForbidden, PermissionDenied, ValidationFailed
from pigrocrm.core.fiscal.models import FiscalProfile
from pigrocrm.core.fiscal.repository import FiscalProfileRepository
from pigrocrm.core.invoices.import_confirm import map_parsed_invoice_to_import
from pigrocrm.core.invoices.import_review import detect_adapter
from pigrocrm.core.invoices.import_schemas import (
    ParsedInvoice,
    ParsedInvoiceLine,
    ParsedInvoiceParty,
    ParsedInvoiceTaxSummary,
    ParsedInvoiceTransmission,
)
from pigrocrm.core.invoices.import_upload import UploadedPartyDecision, party_key
from pigrocrm.core.invoices.models import Invoice, InvoiceRegisterGap
from pigrocrm.core.invoices.repository import InvoiceRepository
from pigrocrm.core.invoices.service import InvoiceService
from pigrocrm.core.legal_entities.models import LegalEntity
from pigrocrm.core.legal_entities.repository import LegalEntityRepository
from pigrocrm.core.storage import LocalFileStorage

FIXTURES = Path(__file__).parent / "fixtures" / "fatturapa"
CONSULTING = "fpr-12-march-consulting.xml"
LOTTO = "fpr-12-batch-due-invoices.xml"
ADMIN = Actor(id=None, type="user", role="admin")

# The FPR12 fixtures' own `CedentePrestatore` (fornitore): matching this on an
# `LegalEntity` is what makes them "outgoing" for the account holder.
SUPPLIER_VAT_NUMBER = "01234567890"
SUPPLIER_CF = "BNCCHR85M41H501Z"
# Their `CessionarioCommittente` (customer).
CUSTOMER_VAT_NUMBER = "09876543210"


def _fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def _fiscal_profile(session: Session) -> FiscalProfile:
    # A fiscal profile belongs to an azienda since REB-615: the default one, created
    # here when a test asks for the profile before the emitter.
    legal_entity = LegalEntityRepository(session).default() or _legal_entity(session)
    existing = FiscalProfileRepository(session).get(legal_entity.id)
    if existing is not None:
        return existing
    profile = FiscalProfile(regime_code="RF19", legal_entity_id=legal_entity.id)
    session.add(profile)
    session.flush()
    return profile


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


def _svc(session: Session, storage: LocalFileStorage) -> InvoiceService:
    """`InvoiceService` with the fiscal and emitter profiles `import_issued` reads
    already in place, the emitter matching the fixtures' own `CedentePrestatore`
    so a confirmed invoice classifies "outgoing" by default."""
    _fiscal_profile(session)
    _legal_entity(session)
    return InvoiceService(session, storage)


def _document(
    session: Session,
    storage: LocalFileStorage,
    content: bytes,
    *,
    content_type: str = "application/xml",
    owner: Customer | None = None,
) -> UUID:
    owner = owner if owner is not None else make_customer_with_address(session)
    service = DocumentService(session, storage)
    document = service.create(DocumentCreate(customer_id=owner.id, title="Fattura ricevuta"), ADMIN)
    service.add_version(document.id, content, content_type, ADMIN)
    return document.id


def _seed_register_row(
    session: Session, *, year: int, number: int, xml_hash_sha256: str | None
) -> UUID:
    row = Invoice(
        customer_id=make_customer_with_address(session).id,
        type="invoice",
        status="issued",
        year=year,
        number=number,
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
    return row.id


def _invoice_count(session: Session) -> int:
    return int(session.execute(select(func.count()).select_from(Invoice)).scalar_one())


def test_confirming_a_fresh_invoice_produces_exactly_what_import_issued_would_by_hand(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    service = _svc(db_session, local_storage)
    customer = make_customer_with_address(db_session, vat_number=CUSTOMER_VAT_NUMBER)
    content = _fixture(CONSULTING)
    document_id = _document(db_session, local_storage, content, owner=customer)

    [row] = service.confirm_import(document_id, ADMIN)

    assert row.outcome == "imported"
    assert row.document_id == document_id
    assert row.invoice is not None
    invoice = row.invoice
    assert (invoice.year, invoice.number, invoice.status, invoice.type) == (
        2026,
        6,
        "issued",
        "invoice",
    )
    assert invoice.customer_id == customer.id
    assert invoice.issue_date == date(2026, 3, 15)
    assert invoice.taxable_amount == Decimal("1000.00")
    assert invoice.vat_amount == Decimal("0.00")
    assert invoice.stamp_duty == Decimal("2.00")
    assert invoice.total == Decimal("1000.00")
    # REB-368: `confirm_import` passes `imported_from="fatturapa"` to `import_issued`
    # as its own keyword-only override -- never through `InvoiceImport` itself, which
    # stays fixed to `"external"` so a hand-declare caller can never claim otherwise.
    assert invoice.imported_from == "fatturapa"
    assert invoice.payment_status == "unpaid"
    lines = service.repo.lines(invoice.id)
    assert [
        (line.description, line.quantity, line.unit_price, line.total_price) for line in lines
    ] == [
        (
            "Consulenza professionale - marzo 2026",
            Decimal("5.000000"),
            Decimal("200.000000"),
            Decimal("1000.00"),
        )
    ]
    # The register starts empty and this invoice is number 6: 1-5 are undeclared
    # gaps, exactly as `import_issued`'s own response would report for the same
    # hand-declared numero on an empty register.
    assert row.undeclared_gaps == [1, 2, 3, 4, 5]


def test_a_single_invoice_source_document_gets_a_hash_verified_xml_document_id(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    service = _svc(db_session, local_storage)
    content = _fixture(CONSULTING)
    customer = make_customer_with_address(db_session, vat_number=CUSTOMER_VAT_NUMBER)
    document_id = _document(db_session, local_storage, content, owner=customer)

    [row] = service.confirm_import(document_id, ADMIN)

    assert row.invoice is not None
    assert row.invoice.xml_document_id == document_id
    assert row.invoice.xml_hash_sha256 == hashlib.sha256(content).hexdigest()


def test_a_batch_sourced_document_leaves_xml_document_id_and_hash_null_on_every_invoice(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    service = _svc(db_session, local_storage)
    content = _fixture(LOTTO)
    customer = make_customer_with_address(db_session, vat_number=CUSTOMER_VAT_NUMBER)
    document_id = _document(db_session, local_storage, content, owner=customer)

    rows = service.confirm_import(document_id, ADMIN)

    assert len(rows) == 2
    assert {row.outcome for row in rows} == {"imported"}
    for row in rows:
        assert row.invoice is not None
        assert row.invoice.xml_document_id is None
        assert row.invoice.xml_hash_sha256 is None
    assert {row.invoice.number for row in rows if row.invoice is not None} == {6, 7}


def test_confirming_an_already_present_invoice_is_a_no_op_never_a_duplicate_insert(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    service = _svc(db_session, local_storage)
    content = _fixture(CONSULTING)
    existing_id = _seed_register_row(
        db_session, year=2026, number=6, xml_hash_sha256=hashlib.sha256(content).hexdigest()
    )
    document_id = _document(
        db_session,
        local_storage,
        content,
        owner=make_customer_with_address(db_session, vat_number=CUSTOMER_VAT_NUMBER),
    )

    before = _invoice_count(db_session)
    [row] = service.confirm_import(document_id, ADMIN)
    after = _invoice_count(db_session)

    assert before == after
    assert row.outcome == "already_present"
    assert row.invoice is not None
    assert row.invoice.id == existing_id
    assert row.undeclared_gaps is None


def test_a_conflicting_or_missing_hash_is_reported_and_never_written(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    service = _svc(db_session, local_storage)
    content = _fixture(CONSULTING)
    _seed_register_row(
        db_session,
        year=2026,
        number=6,
        xml_hash_sha256=hashlib.sha256(b"a different document").hexdigest(),
    )
    document_id = _document(db_session, local_storage, content)

    before = _invoice_count(db_session)
    [row] = service.confirm_import(document_id, ADMIN)

    assert row.outcome == "conflict"
    assert row.invoice is None
    assert _invoice_count(db_session) == before


def test_a_suppliers_invoice_is_incoming_skipped_and_never_written(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    _fiscal_profile(db_session)
    _legal_entity(db_session, vat_number="99999999999", tax_code="ZZZZZZ00A01H501Z")
    service = InvoiceService(db_session, local_storage)
    document_id = _document(db_session, local_storage, _fixture(CONSULTING))

    before = _invoice_count(db_session)
    [row] = service.confirm_import(document_id, ADMIN)

    assert row.outcome == "incoming_skipped"
    assert row.invoice is None
    assert _invoice_count(db_session) == before


def test_a_document_no_adapter_recognises_is_unclaimed_and_never_written(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    service = _svc(db_session, local_storage)
    document_id = _document(
        db_session, local_storage, b"not a fattura at all", content_type="text/plain"
    )

    before = _invoice_count(db_session)
    [row] = service.confirm_import(document_id, ADMIN)

    assert row.outcome == "unclaimed"
    assert row.invoice is None
    assert _invoice_count(db_session) == before


def test_no_matching_customer_refuses_and_never_writes(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    service = _svc(db_session, local_storage)
    document_id = _document(db_session, local_storage, _fixture(CONSULTING))

    before = _invoice_count(db_session)
    [row] = service.confirm_import(document_id, ADMIN)

    assert row.outcome == "needs_customer_confirmation"
    assert row.invoice is None
    assert _invoice_count(db_session) == before


def test_an_explicit_customer_id_overrides_the_automatic_match(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    """The human decision always wins over the automatic tax-id match: `customer_id`
    is exactly how a reviewer says which `Customer` this invoice attaches to, even
    when one already matches on its own."""
    service = _svc(db_session, local_storage)
    matched = make_customer_with_address(db_session, vat_number=CUSTOMER_VAT_NUMBER)
    chosen = make_customer_with_address(db_session)
    document_id = _document(db_session, local_storage, _fixture(CONSULTING), owner=matched)

    [row] = service.confirm_import(document_id, ADMIN, customer_id=chosen.id)

    assert row.outcome == "imported"
    assert row.invoice is not None
    assert row.invoice.customer_id == chosen.id


def test_to_customer_of_another_legal_entity_is_refused_and_nothing_is_written(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    """REB-783: `customer_id` is a human decision, but the register it lands on is the
    file issuer's azienda's. A customer on another azienda would put an invoice on one
    register for another's customer, so the confirmation answers `ValidationFailed`
    naming the party, and no invoice is written."""
    service = _svc(db_session, local_storage)
    other = LegalEntity(
        legal_name="Acme Ltd", vat_number="IE6388047V", country="IE", is_default=False
    )
    db_session.add(other)
    db_session.flush()
    foreign = make_customer_with_address(db_session, legal_entity_id=other.id)
    document_id = _document(db_session, local_storage, _fixture(CONSULTING))

    before = _invoice_count(db_session)
    with pytest.raises(ValidationFailed) as caught:
        service.confirm_import(document_id, ADMIN, customer_id=foreign.id)

    assert caught.value.details["field"] == "customer_id"
    assert "Esempio Servizi S.r.l." in caught.value.details["reason"]
    assert "altra azienda" in caught.value.details["reason"]
    assert _invoice_count(db_session) == before


def test_an_archived_customer_is_refused_and_one_of_the_right_legal_entity_still_imports(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    service = _svc(db_session, local_storage)
    archived = make_customer_with_address(db_session, deleted_at=datetime.now(UTC))
    chosen = make_customer_with_address(db_session)
    document_id = _document(db_session, local_storage, _fixture(CONSULTING))

    with pytest.raises(ValidationFailed) as caught:
        service.confirm_import(document_id, ADMIN, customer_id=archived.id)
    assert "archiviato" in caught.value.details["reason"]

    [row] = service.confirm_import(document_id, ADMIN, customer_id=chosen.id)
    assert row.outcome == "imported"
    assert row.invoice is not None
    assert row.invoice.customer_id == chosen.id


def test_a_refusal_in_a_later_file_leaves_the_earlier_files_unwritten(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    """Each invoice commits on its own, so a customer refused only when its file is
    reached would leave the files before it on the register behind a bare 422. The
    refusal is found before the first write: nothing of the request is written."""
    service = _svc(db_session, local_storage)
    other = LegalEntity(
        legal_name="Acme Ltd", vat_number="IE6388047V", country="IE", is_default=False
    )
    db_session.add(other)
    db_session.flush()
    own = make_customer_with_address(db_session)
    foreign = make_customer_with_address(db_session, legal_entity_id=other.id)
    first = _fixture(CONSULTING)
    second = (
        first.decode()
        .replace("<Numero>2026/6</Numero>", "<Numero>2026/9</Numero>")
        .replace("09876543210", "01112223334")
        .replace("Esempio Servizi S.r.l.", "Beta Servizi S.r.l.")
        .encode()
    )
    parties = []
    for xml in (first, second):
        adapter = detect_adapter(xml)
        assert adapter is not None
        parties.extend(party_key(invoice.customer) for invoice in adapter.parse(xml))
    assert len(set(parties)) == 2

    before = _invoice_count(db_session)
    with pytest.raises(ValidationFailed) as caught:
        service.confirm_upload(
            [("uno.xml", first), ("due.xml", second)],
            {
                parties[0]: UploadedPartyDecision(customer_id=own.id),
                parties[1]: UploadedPartyDecision(customer_id=foreign.id),
            },
            ADMIN,
        )

    assert "Beta Servizi S.r.l." in caught.value.details["reason"]
    assert _invoice_count(db_session) == before


def test_the_automatic_match_never_attaches_to_customer_of_another_legal_entity(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    """The same tax id on file under another legal entity is not this invoice's customer:
    the review does not propose it, and a confirmation that creates the counterparty
    makes one on the legal entity the file landed on instead of reusing the other's."""
    service = _svc(db_session, local_storage)
    other = LegalEntity(
        legal_name="Acme Ltd", vat_number="IE6388047V", country="IE", is_default=False
    )
    db_session.add(other)
    db_session.flush()
    foreign = make_customer_with_address(
        db_session, legal_entity_id=other.id, vat_number=CUSTOMER_VAT_NUMBER
    )
    document_id = _document(db_session, local_storage, _fixture(CONSULTING), owner=foreign)

    [reviewed] = service.review_import([document_id], ADMIN)
    assert reviewed.outcome == "needs_customer_confirmation"
    assert reviewed.matched_customer_id is None

    [row] = service.confirm_import(document_id, ADMIN, create_customer=True)

    assert row.outcome == "imported"
    assert row.invoice is not None
    assert row.invoice.customer_id != foreign.id
    created = db_session.get(Customer, row.invoice.customer_id)
    assert created is not None
    assert created.legal_entity_id == row.legal_entity_id


def test_create_customer_makes_exactly_one_new_customer_for_a_brand_new_counterparty_batch(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    """Design record §7 item 5's own acceptance: a batch (a `Lotto`) from a brand-new
    counterparty produces exactly one new `Customer` row, with every invoice in
    that batch attached to it -- never one per invoice, even though `invoice.
    customer` is parsed fresh for each `FatturaElettronicaBody` in the file."""
    service = _svc(db_session, local_storage)
    document_id = _document(db_session, local_storage, _fixture(LOTTO))
    before_customers = db_session.execute(select(func.count()).select_from(Customer)).scalar_one()

    rows = service.confirm_import(document_id, ADMIN, create_customer=True)

    assert [row.outcome for row in rows] == ["imported", "imported"]
    customer_ids = {row.invoice.customer_id for row in rows if row.invoice is not None}
    assert len(customer_ids) == 1
    after_customers = db_session.execute(select(func.count()).select_from(Customer)).scalar_one()
    # The document's own owner customer (the `_document` baseline) plus exactly one
    # new row for the counterparty every invoice in the batch shares.
    assert after_customers == before_customers + 1
    [customer_id] = customer_ids
    created = CustomerRepository(db_session).get(customer_id)
    assert created is not None
    assert created.legal_name == "Esempio Servizi S.r.l."
    assert created.vat_number == CUSTOMER_VAT_NUMBER


def test_a_register_failure_after_the_customer_insert_leaves_neither_committed(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    """The other half of design §7 item 5's acceptance: the customer `confirm_
    import` creates for a brand-new counterparty and the invoice write share one
    commit. A declared-gap conflict on the invoice's own number -- one of `import_
    issued`'s register rules, raised *after* the customer has already been
    flushed -- must roll back the customer too, never leave it on file with no
    invoice pointing at it."""
    service = _svc(db_session, local_storage)
    InvoiceRepository(db_session).add_gap(
        InvoiceRegisterGap(year=2026, number=6, reason="annullata prima della trasmissione")
    )
    document_id = _document(db_session, local_storage, _fixture(CONSULTING))
    before_invoices = _invoice_count(db_session)
    before_customers = db_session.execute(select(func.count()).select_from(Customer)).scalar_one()

    [row] = service.confirm_import(document_id, ADMIN, create_customer=True)

    assert row.outcome == "conflict"
    assert row.invoice is None
    assert _invoice_count(db_session) == before_invoices
    assert (
        db_session.execute(select(func.count()).select_from(Customer)).scalar_one()
        == before_customers
    )
    assert (
        CustomerRepository(db_session).match_by_fiscal_id(
            vat_number=CUSTOMER_VAT_NUMBER, tax_code=None
        )
        is None
    )


def test_a_conflict_on_the_customer_creating_invoice_does_not_poison_the_rest_of_the_batch(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    """The dangling-cache regression: the first invoice in a batch (a `Lotto`) creates
    a customer for a brand-new counterparty, then hits a register-rule conflict of
    its own (a declared gap) and rolls back -- taking that customer with it. The
    *second* invoice, from the same counterparty, must not reuse the now-rolled-
    back id: it gets its own fresh customer and imports normally, rather than
    crashing with a `NotFound` on a dangling id."""
    service = _svc(db_session, local_storage)
    InvoiceRepository(db_session).add_gap(
        InvoiceRegisterGap(year=2026, number=6, reason="annullata prima della trasmissione")
    )
    document_id = _document(db_session, local_storage, _fixture(LOTTO))
    before_customers = db_session.execute(select(func.count()).select_from(Customer)).scalar_one()

    rows = service.confirm_import(document_id, ADMIN, create_customer=True)

    assert [row.outcome for row in rows] == ["conflict", "imported"]
    assert rows[0].invoice is None
    assert rows[1].invoice is not None
    assert rows[1].invoice.number == 7
    after_customers = db_session.execute(select(func.count()).select_from(Customer)).scalar_one()
    # Exactly one new customer survives: the one invoice 6's own rollback undid,
    # plus the one invoice 7 created for itself.
    assert after_customers == before_customers + 1
    created = CustomerRepository(db_session).match_by_fiscal_id(
        vat_number=CUSTOMER_VAT_NUMBER, tax_code=None
    )
    assert created is not None
    assert rows[1].invoice.customer_id == created.id


def test_confirming_the_same_document_twice_the_second_time_is_already_present(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    """The natural-key/hash rule applies to confirm's own prior write too: a second
    confirm of the same document never inserts a second row."""
    service = _svc(db_session, local_storage)
    customer = make_customer_with_address(db_session, vat_number=CUSTOMER_VAT_NUMBER)
    document_id = _document(db_session, local_storage, _fixture(CONSULTING), owner=customer)

    [first] = service.confirm_import(document_id, ADMIN)
    before = _invoice_count(db_session)
    [second] = service.confirm_import(document_id, ADMIN)

    assert first.outcome == "imported"
    assert second.outcome == "already_present"
    assert first.invoice is not None and second.invoice is not None
    assert second.invoice.id == first.invoice.id
    assert _invoice_count(db_session) == before


def test_a_readonly_actor_is_refused(db_session: Session, local_storage: LocalFileStorage) -> None:
    service = _svc(db_session, local_storage)
    document_id = _document(db_session, local_storage, _fixture(CONSULTING))
    readonly = Actor(id=None, type="user", role="readonly")
    with pytest.raises(PermissionDenied):
        service.confirm_import(document_id, readonly)


def test_an_agent_credential_without_full_access_is_forbidden(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    """The credential-level half of the ban (`agent_actions.py::FULL_ACCESS_ACTIONS`),
    proven at the service the tool actually calls -- the tool's own absence on a
    default installation is `test_mcp_invoice_ban.py`'s guarantee, not this one."""
    service = _svc(db_session, local_storage)
    document_id = _document(db_session, local_storage, _fixture(CONSULTING))
    agent = Actor(id=None, type="mcp", role="admin", full_access=False)
    with pytest.raises(AgentForbidden):
        service.confirm_import(document_id, agent)


def test_no_legal_entities_configured_refuses_rather_than_guesses(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    # Since REB-615 every space has its legal entity from the first boot, so «not configured»
    # is a legal entity with neither fiscal id, and the refusal names that field rather
    # than a missing row: the import never guesses which P.IVA is ours. Blanked here
    # rather than assumed, since the race tests commit an identity on the default legal entity.
    db_session.execute(text("UPDATE legal_entities SET vat_number = NULL, tax_code = NULL"))
    document_id = _document(db_session, local_storage, _fixture(CONSULTING))
    with pytest.raises(ValidationFailed) as caught:
        InvoiceService(db_session, local_storage).confirm_import(document_id, ADMIN)
    assert caught.value.details["entity"] == "legal_entity"
    assert caught.value.details["field"] == "vat_number"


def test_vat_nature_is_read_from_the_line_itself_never_reconstructed_by_rate_alone() -> None:
    """Regression: an invoice can carry two `DatiRiepilogo` blocks at the *same*
    rate with a *different* `vat_nature` each -- a mixed-exemption invoice, and
    exactly the case this codebase's own `totals.RecapGroup` already keys on
    the pair `(vat_rate, vat_nature)` for the export side. Reconstructing a
    line's `vat_nature` from `recaps` matched by rate alone would silently tag
    both lines with whichever recap happened to be seen first; this proves
    each line keeps its own, correctly-parsed `vat_nature`, and `legal_reference` is
    looked up by the matching pair, not by rate alone."""
    customer = ParsedInvoiceParty(
        legal_name="Cliente Test",
        address="Via Test 1",
        postal_code="00100",
        city="Roma",
        country="IT",
    )
    supplier = ParsedInvoiceParty(
        legal_name="Emittente Test",
        vat_number=SUPPLIER_VAT_NUMBER,
        address="Via Test 2",
        postal_code="20100",
        city="Milano",
        country="IT",
    )
    invoice = ParsedInvoice(
        number="2026/6",
        issue_date=date(2026, 3, 15),
        sdi_document_type="invoice",
        currency="EUR",
        supplier=supplier,
        customer=customer,
        lines=[
            ParsedInvoiceLine(
                description="Riga non soggetta",
                quantity=Decimal("1"),
                unit_price=Decimal("500.00"),
                total_price=Decimal("500.00"),
                vat_rate=Decimal("0.00"),
                vat_nature="N2.2",
            ),
            ParsedInvoiceLine(
                description="Riga esente",
                quantity=Decimal("1"),
                unit_price=Decimal("300.00"),
                total_price=Decimal("300.00"),
                vat_rate=Decimal("0.00"),
                vat_nature="N4",
            ),
        ],
        recaps=[
            ParsedInvoiceTaxSummary(
                vat_rate=Decimal("0.00"),
                vat_nature="N2.2",
                legal_reference="Regime forfettario, art. 1 commi 54-89 L. 190/2014",
                taxable_amount=Decimal("500.00"),
                vat_amount=Decimal("0.00"),
            ),
            ParsedInvoiceTaxSummary(
                vat_rate=Decimal("0.00"),
                vat_nature="N4",
                legal_reference="Operazioni esenti, art. 10 DPR 633/1972",
                taxable_amount=Decimal("300.00"),
                vat_amount=Decimal("0.00"),
            ),
        ],
        taxable_amount=Decimal("800.00"),
        vat_amount=Decimal("0.00"),
        total=Decimal("800.00"),
        social_security_pension_fund=[],
        payment_terms=[],
        transmission=ParsedInvoiceTransmission(id_transmitter="X", send_sequential="1"),
    )

    data = map_parsed_invoice_to_import(invoice, year=2026, number=6, customer_id=uuid4())

    first, second = data.lines
    assert first.vat_nature == "N2.2"
    assert first.legal_reference == "Regime forfettario, art. 1 commi 54-89 L. 190/2014"
    assert second.vat_nature == "N4"
    assert second.legal_reference == "Operazioni esenti, art. 10 DPR 633/1972"


def test_a_register_rule_conflict_mid_batch_is_reported_and_the_rest_still_lands(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    """`import_issued`'s own four further register rules (declared gaps here) are
    not re-implemented in `confirm_import`'s own pre-check -- they are enforced by
    `import_issued` itself, exactly as the design demands (§5 item 2: one write
    function, never a second set of the same rules). This proves a batch document
    does not lose earlier progress or silently drop the rest when one invoice in
    it hits one of those rules: the first invoice's write survives, the second is
    reported `"conflict"`, and the session stays usable after."""
    service = _svc(db_session, local_storage)
    customer = make_customer_with_address(db_session, vat_number=CUSTOMER_VAT_NUMBER)
    InvoiceRepository(db_session).add_gap(
        InvoiceRegisterGap(year=2026, number=7, reason="annullata prima della trasmissione")
    )
    document_id = _document(db_session, local_storage, _fixture(LOTTO), owner=customer)

    rows = service.confirm_import(document_id, ADMIN)

    assert [row.outcome for row in rows] == ["imported", "conflict"]
    assert rows[0].invoice is not None
    assert rows[0].invoice.number == 6
    assert rows[1].invoice is None
    assert _invoice_count(db_session) == 1
    # The session must still answer a query after the mid-batch Conflict.
    assert len(db_session.execute(select(Invoice)).scalars().all()) == 1
